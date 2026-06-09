"""Wrapper for invoking Codex CLI as a subprocess with streaming output capture."""

from __future__ import annotations

import logging
import os
import platform
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Unix-only PTY modules — guarded so the file imports cleanly on Windows.
_IS_WINDOWS = platform.system() == "Windows"
if not _IS_WINDOWS:
    import fcntl
    import pty
    import termios

from .session import CodexResult, CodexSession, SessionType
from .streaming import kill_process, stream_pipe, stream_pty
from .cli_adapter import build_command, executable_name, UnsupportedToolError

logger = logging.getLogger(__name__)


def _set_pty_size(fd: int, rows: int, cols: int) -> None:
    """Set the terminal size on a PTY file descriptor (TIOCSWINSZ). Unix only."""
    if _IS_WINDOWS:
        return
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        pass


# Patterns that indicate a fatal, unrecoverable codex tool-router error.
# When seen in stdout the process will never recover — kill it immediately
# instead of waiting for the full timeout.
_FATAL_STDOUT_PATTERNS: tuple[str, ...] = (
    "failed to parse function arguments",
    "invalid type: sequence, expected a string",
    "invalid type: map, expected a string",
)


class CodexWrapper:
    """Manages Codex CLI subprocess invocations with streaming output and timeout."""

    def __init__(
        self,
        timeout_seconds: int = 300,
        max_retries: int = 2,
        openai_api_key: str = "",
        project_root: str = ".",
        model_routing: dict[str, str] | None = None,
        default_model: str = "",
        cli_routing: dict[str, str] | None = None,
        github_token: str = "",
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._openai_api_key = openai_api_key
        self._project_root = project_root
        self._model_routing = model_routing or {}
        self._default_model = default_model
        self._cli_routing = cli_routing or {}
        self._github_token = github_token
        self._active_sessions: dict[SessionType, CodexSession] = {}

    def execute(
        self,
        prompt: str,
        working_dir: str | Path,
        session_type: SessionType,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> CodexResult:
        """Execute a Codex CLI command with retry logic."""
        last_result = None
        for attempt in range(1, self._max_retries + 2):
            logger.info(
                "Codex exec attempt %d/%d for %s",
                attempt, self._max_retries + 1, session_type.value,
            )
            result = self._run_once(prompt, working_dir, session_type, on_stdout, on_stderr)
            last_result = result

            if result.exit_code == 0:
                return result

            if result.timed_out:
                logger.warning("Codex exec timed out for %s (attempt %d)", session_type.value, attempt)
            else:
                logger.warning(
                    "Codex exec failed for %s with exit code %d (attempt %d)",
                    session_type.value, result.exit_code, attempt,
                )
                # Log the actual output so the error is visible in server logs
                if result.stdout:
                    for line in result.stdout.splitlines()[-30:]:
                        logger.warning("[codex output] %s", line)

            if attempt <= self._max_retries:
                logger.info("Retrying...")

        return last_result  # type: ignore[return-value]

    def _run_once(
        self,
        prompt: str,
        working_dir: str | Path,
        session_type: SessionType,
        on_stdout: Optional[Callable[[str], None]],
        on_stderr: Optional[Callable[[str], None]],
    ) -> CodexResult:
        """Single invocation of codex exec via a pseudo-terminal.

        Uses ``pty.openpty()`` so the child process sees a real TTY and
        produces its full interactive output (permissions, ANSI, progress).
        """
        working_dir = Path(working_dir).resolve()
        if not working_dir.exists():
            working_dir.mkdir(parents=True, exist_ok=True)

        # Build command using the configured CLI tool for this agent
        tool = self._cli_routing.get(session_type.value, "codex")
        model = self._model_routing.get(session_type.value) or self._default_model
        start_time = time.monotonic()
        # On Windows pass the prompt via stdin to avoid the 8191-char cmd-line limit.
        # This applies to 'codex' and all api_adapter-backed tools (copilot, gemini, etc.)
        use_stdin_for_prompt = _IS_WINDOWS
        try:
            cmd = build_command(tool, model, prompt, working_dir=str(working_dir),
                                use_stdin=use_stdin_for_prompt)
        except UnsupportedToolError as exc:
            logger.error("Unsupported tool '%s' for %s: %s", tool, session_type.value, exc)
            return CodexResult(
                exit_code=-1, stdout="", stderr=str(exc),
                duration_seconds=time.monotonic() - start_time,
            )
        _exe = executable_name(tool)

        # Callbacks — merge PTY output into on_stdout; on_stderr receives nothing
        # since the PTY merges both streams (identical to a real terminal).
        combined_cb = on_stdout

        session = CodexSession(
            session_type=session_type,
            _on_stdout=on_stdout,
            _on_stderr=on_stderr,
        )

        try:
            from ..config.env import build_codex_env
            env = build_codex_env(self._openai_api_key, self._project_root)

            # Inject the configured GitHub token as GITHUB_TOKEN so that
            # api_adapter-backed tools (copilot, etc.) use the account
            # configured in Settings UI rather than any inherited VS Code terminal token.
            if self._github_token:
                env["GITHUB_TOKEN"] = self._github_token

            if _IS_WINDOWS:
                # On Windows, npm/pip global CLIs are installed as .cmd wrappers.
                # Popen with shell=False won't resolve them without the extension,
                # so invoke through cmd.exe which handles PATHEXT transparently.
                win_cmd = ["cmd", "/c"] + cmd
                prompt_bytes = prompt.encode("utf-8") if use_stdin_for_prompt else None
                proc = subprocess.Popen(
                    win_cmd,
                    stdin=subprocess.PIPE if use_stdin_for_prompt else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(working_dir),
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                session.process = proc
                session.pid = proc.pid
                self._active_sessions[session_type] = session

                logger.info(
                    "Started %s CLI (PID %d) via pipe for %s",
                    _exe, proc.pid, session_type.value,
                )

                # Writer thread: send prompt bytes then close stdin so codex knows EOF
                if use_stdin_for_prompt and prompt_bytes:
                    def _write_stdin() -> None:
                        try:
                            proc.stdin.write(prompt_bytes)  # type: ignore[union-attr]
                            proc.stdin.close()              # type: ignore[union-attr]
                        except (OSError, BrokenPipeError):
                            pass
                    threading.Thread(target=_write_stdin, daemon=True).start()

                def _decode(line_bytes) -> str:
                    if isinstance(line_bytes, bytes):
                        return line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                    return str(line_bytes).rstrip("\r\n")

                fatal_event = threading.Event()

                def _stream_bytes(pipe, buf, cb):
                    try:
                        for raw in pipe:
                            line = _decode(raw)
                            buf.append(line)
                            if cb:
                                try:
                                    cb(line)
                                except Exception:
                                    pass
                            # Early abort: kill the process immediately on known
                            # fatal codex tool-router parse errors so we don't
                            # hang until the full timeout expires.
                            if any(p in line for p in _FATAL_STDOUT_PATTERNS):
                                if not fatal_event.is_set():
                                    fatal_event.set()
                                    logger.warning(
                                        "Codex fatal tool-router error detected — "
                                        "aborting process %d early: %s",
                                        proc.pid, line.strip(),
                                    )
                                    kill_process(proc)
                                break
                    except (ValueError, OSError):
                        pass

                reader = threading.Thread(
                    target=_stream_bytes,
                    args=(proc.stdout, session.stdout_lines, combined_cb),
                    daemon=True,
                )
                reader.start()

                try:
                    proc.wait(timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "%s CLI (PID %d) timed out after %ds",
                        _exe, proc.pid, self._timeout,
                    )
                    kill_process(proc)
                    reader.join(timeout=5)
                    return CodexResult(
                        exit_code=-1,
                        stdout="\n".join(session.stdout_lines),
                        stderr="",
                        timed_out=True,
                        duration_seconds=time.monotonic() - start_time,
                    )

                reader.join(timeout=10)

                if fatal_event.is_set():
                    return CodexResult(
                        exit_code=1,
                        stdout="\n".join(session.stdout_lines),
                        stderr="",
                        timed_out=False,
                        duration_seconds=time.monotonic() - start_time,
                    )

            else:
                # Unix: PTY gives the child a real TTY (ANSI output, interactive prompts).
                env.setdefault("TERM", "xterm-256color")
                master_fd, slave_fd = pty.openpty()
                _set_pty_size(master_fd, 40, 120)

                proc = subprocess.Popen(
                    cmd,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    cwd=str(working_dir),
                    env=env,
                    close_fds=True,
                    start_new_session=True,
                )
                os.close(slave_fd)

                session.process = proc
                session.pid = proc.pid
                self._active_sessions[session_type] = session

                logger.info(
                    "Started %s CLI (PID %d) via PTY for %s",
                    _exe, proc.pid, session_type.value,
                )

                pty_thread = threading.Thread(
                    target=stream_pty,
                    args=(master_fd, session.stdout_lines, combined_cb),
                    daemon=True,
                )
                pty_thread.start()

                try:
                    proc.wait(timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "%s CLI (PID %d) timed out after %ds",
                        _exe, proc.pid, self._timeout,
                    )
                    kill_process(proc)
                    pty_thread.join(timeout=5)
                    return CodexResult(
                        exit_code=-1,
                        stdout="\n".join(session.stdout_lines),
                        stderr="",
                        timed_out=True,
                        duration_seconds=time.monotonic() - start_time,
                    )

                pty_thread.join(timeout=10)

            return CodexResult(
                exit_code=proc.returncode,
                stdout="\n".join(session.stdout_lines),
                stderr="",
                timed_out=False,
                duration_seconds=time.monotonic() - start_time,
            )

        except FileNotFoundError:
            logger.error("'%s' CLI not found. Is it installed and on PATH?", _exe)
            return CodexResult(exit_code=-127, stdout="", stderr=f"{_exe}: command not found",
                               duration_seconds=time.monotonic() - start_time)
        except Exception as e:
            logger.exception("Unexpected error running %s CLI", _exe)
            return CodexResult(exit_code=-1, stdout="", stderr=str(e),
                               duration_seconds=time.monotonic() - start_time)
        finally:
            self._active_sessions.pop(session_type, None)

    def get_active_session(self, session_type: SessionType) -> Optional[CodexSession]:
        return self._active_sessions.get(session_type)

    def kill_session(self, session_type: SessionType) -> bool:
        session = self._active_sessions.get(session_type)
        if session and session.process:
            kill_process(session.process)
            self._active_sessions.pop(session_type, None)
            return True
        return False
