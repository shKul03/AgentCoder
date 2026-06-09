"""Output streaming helpers for Codex CLI subprocesses."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Optional

# Regex to strip ANSI escape sequences (CSI codes, charset selectors, OSC)
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b[()][A-Za-z]|\x1b\][^\x07]*\x07')


def stream_pipe(
    pipe,
    line_buffer: list[str],
    callback: Optional[Callable[[str], None]],
) -> None:
    """Read lines from a pipe, store them, and optionally invoke a callback."""
    if pipe is None:
        return
    try:
        for line in pipe:
            stripped = line.rstrip("\n")
            line_buffer.append(stripped)
            if callback:
                try:
                    callback(stripped)
                except Exception:
                    pass  # Don't let callback errors break streaming
    except (ValueError, OSError):
        pass  # Pipe closed


def stream_pty(
    master_fd: int,
    line_buffer: list[str],
    callback: Optional[Callable[[str], None]],
) -> None:
    """Read from a PTY master file descriptor, store lines, invoke callback.

    Unlike ``stream_pipe``, this reads raw bytes from a pseudo-terminal and
    decodes them, capturing the full terminal experience including ANSI codes.
    The caller is responsible for *not* closing ``master_fd`` before this
    function finishes — it is closed here on exit.
    """
    partial = ""
    try:
        while True:
            try:
                data = os.read(master_fd, 8192)
            except OSError:
                break
            if not data:
                break

            text = partial + data.decode("utf-8", errors="replace")
            # Split on newlines; handle \r\n and standalone \n
            *lines, partial = text.split("\n")

            for line in lines:
                stripped = _ANSI_RE.sub("", line.rstrip("\r"))
                line_buffer.append(stripped)
                if callback:
                    try:
                        callback(stripped)
                    except Exception:
                        pass

        # Flush any remaining partial line
        if partial:
            stripped = _ANSI_RE.sub("", partial.rstrip("\r"))
            if stripped:
                line_buffer.append(stripped)
                if callback:
                    try:
                        callback(stripped)
                    except Exception:
                        pass
    except Exception:
        pass
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def kill_process(proc: subprocess.Popen) -> None:
    """Gracefully terminate, then force-kill a process (cross-platform).

    On Windows, uses ``taskkill /F /T`` to kill the entire process tree
    (including children spawned by ``cmd /c``).
    """
    import platform as _platform
    try:
        if _platform.system() == "Windows" and proc.pid:
            # Kill the entire process tree so cmd /c + codex children all die.
            import subprocess as _sp
            try:
                _sp.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass  # taskkill not available — fall back to terminate
            # Reap the original proc handle regardless
            try:
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        else:
            proc.terminate()          # SIGTERM on Unix
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except (ProcessLookupError, OSError):
        pass  # Already dead
