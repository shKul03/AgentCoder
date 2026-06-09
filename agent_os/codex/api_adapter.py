"""Unified OpenAI-compatible API adapter for LLM tools without native CLIs.

Invoked as a subprocess by the codex wrapper, streaming tokens to stdout
so the existing terminal / streaming architecture works unchanged.

Usage::

    python -m agent_os.codex.api_adapter \
        --tool deepseek --model deepseek-coder \
        --prompt "Refactor the auth module…"

Supported tools and their default endpoints:

    deepseek  https://api.deepseek.com/v1                               DEEPSEEK_API_KEY
    gemini    https://generativelanguage.googleapis.com/v1beta/openai    GEMINI_API_KEY
    qwen      https://dashscope.aliyuncs.com/compatible-mode/v1         DASHSCOPE_API_KEY
    copilot   https://api.githubcopilot.com                             GITHUB_TOKEN
"""

from __future__ import annotations

import argparse
import os
import sys

# (base_url, env_var_for_api_key, default_model)
TOOL_ENDPOINTS: dict[str, tuple[str, str, str]] = {
    "deepseek": (
        "https://api.deepseek.com/v1",
        "DEEPSEEK_API_KEY",
        "deepseek-coder",
    ),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "gemini-2.0-flash",
    ),
    "qwen": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        "qwen-coder-plus",
    ),
    "copilot": (
        "https://api.githubcopilot.com",
        "GITHUB_TOKEN",
        "gpt-4.1",
    ),
}


def _sanitize_model_name(model: str) -> str:
    """Extract a clean model name from an AzureML registry URI if needed.

    The GitHub Models inference API sometimes returns model IDs as full AzureML
    URIs, e.g. ``azureml://registries/azure-openai/models/gpt-4o/versions/2``.
    This causes a 400 'unknown_model' error when passed to the API.  Strip the
    URI down to just the model name component.
    """
    if model.startswith("azureml://"):
        import re as _re
        m = _re.search(r"/models/([^/]+)/versions/", model)
        if m:
            return m.group(1)
    return model


def _get_copilot_token() -> str:
    """Return a GitHub OAuth token suitable for the Copilot API.

    Priority:
    1. ``gh auth token`` — the OAuth token stored by the gh CLI (preferred,
       works with Copilot API which rejects PATs).
    2. ``GITHUB_TOKEN`` env var — set by Agent OS when the user configured a
       token in Settings → AI Tools → Copilot.

    IMPORTANT: ``gh`` echoes back GITHUB_TOKEN/GH_TOKEN if they are present
    in its environment instead of reading the stored OAuth credential.  We
    must strip those vars before invoking ``gh auth token``.
    """
    try:
        import subprocess as _sp
        # Strip PAT vars so gh reads its own keychain OAuth token
        clean_env = {
            k: v for k, v in os.environ.items()
            if k not in ("GITHUB_TOKEN", "GH_TOKEN")
        }
        result = _sp.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
            env=clean_env,
        )
        token = result.stdout.strip()
        if token and result.returncode == 0:
            return token
    except Exception:
        pass
    # Fall back to env var (PAT or manually configured token)
    return os.environ.get("GITHUB_TOKEN", "")


def stream_chat(tool: str, model: str, prompt: str) -> int:
    """Call an OpenAI-compatible endpoint and stream tokens to stdout.

    Returns 0 on success, non-zero on failure.
    """
    # On Windows the default stdout encoding is cp1252 which cannot represent
    # many Unicode characters (box-drawing, emoji, …) returned by LLM APIs.
    # Reconfigure both streams to UTF-8 so output is never corrupted/aborted.
    import io as _io
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    if tool not in TOOL_ENDPOINTS:
        print(f"Error: unknown api_adapter tool '{tool}'", file=sys.stderr)
        return 1

    base_url, env_key, default_model = TOOL_ENDPOINTS[tool]
    if tool == "copilot":
        api_key = _get_copilot_token()
    else:
        api_key = os.environ.get(env_key, "")
    if not api_key:
        print(
            f"Error: {env_key} is not set. "
            f"Configure credentials for {tool} in Settings \u2192 AI Tools.",
            file=sys.stderr,
        )
        return 1

    # Sanitize model name — strip AzureML registry URIs down to the bare model id
    model = _sanitize_model_name(model or default_model)

    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Error: the 'openai' package is required for the API adapter. "
            "Install it with: pip install openai",
            file=sys.stderr,
        )
        return 1

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        # GitHub Copilot API requires these headers to identify the integration.
        # Without them, newer/codex models return "not accessible via /chat/completions".
        default_headers={
            "Copilot-Integration-Id": "agent-os",
            "Editor-Version": "agent-os/1.0",
            "Editor-Plugin-Version": "agent-os/1.0",
        } if tool == "copilot" else {},
    )

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    sys.stdout.write(delta.content)
                    sys.stdout.flush()
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:
        exc_str = str(exc)
        # Always show the real API error message so users know exactly what failed.
        # Also append a hint when it looks like a model availability issue.
        is_model_error = any(
            k in exc_str.lower()
            for k in ("model_not_found", "no model", "unknown model", "not found",
                       "does not exist", "invalid model", "resource not found",
                       "model_not_supported", "unsupported_api_for_model",
                       "not accessible via", "not available for integrator")
        )
        if is_model_error:
            print(
                f"\n[copilot] Model '{model}' is not accessible. "
                "Some models (e.g. gpt-5.2-codex, gpt-5.3-codex, gpt-5.4-mini) are "
                "Copilot inline-completion models and cannot be used via /chat/completions.\n"
                "Use a chat model instead: gpt-5.2, gpt-4.1, gpt-4o, gemini-2.5-pro, etc.\n"
                f"API error: {exc}",
                file=sys.stderr,
            )
        else:
            print(f"\nError calling {tool} API ({base_url}): {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenAI-compatible API adapter for Agent OS",
    )
    parser.add_argument("--tool", required=True, choices=sorted(TOOL_ENDPOINTS))
    parser.add_argument("--model", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--stdin", action="store_true",
                        help="Read prompt from stdin instead of --prompt (avoids Windows cmd-line length limit)")
    args = parser.parse_args()

    prompt = args.prompt
    if args.stdin:
        prompt = sys.stdin.read()

    if not prompt:
        print("Error: no prompt provided (use --prompt or --stdin)", file=sys.stderr)
        sys.exit(1)

    sys.exit(stream_chat(args.tool, args.model, prompt))


if __name__ == "__main__":
    main()
