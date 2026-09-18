"""
Clipboard access.

Wayland first (wl-copy/wl-paste), X11 as a fallback (xclip). Reading the
clipboard is tiered READ, but be aware it is one of the likeliest places for a
password to be sitting -- so the contents are scrubbed before they are
returned, and therefore before they can reach the LLM.
"""

from __future__ import annotations

import shutil
import subprocess

from servant.sdk import Tier, ToolError, tool


def _tools() -> tuple[list[str], list[str]]:
    """Return (paste_cmd, copy_cmd) for this session, Wayland preferred."""
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return ["wl-paste", "--no-newline"], ["wl-copy"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"]
    raise ToolError(
        "no clipboard tool found (sudo apt install wl-clipboard, or xclip on X11)"
    )


@tool(
    name="clipboard.read",
    tier=Tier.READ,
    params={"max_chars": "Truncate after this many characters"},
    untrusted=True,
)
def clipboard_read(ctx, max_chars: int = 4000) -> str:
    """Read the current clipboard contents. Secrets are masked before returning."""
    paste, _ = _tools()
    try:
        result = subprocess.run(paste, capture_output=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ToolError("clipboard read timed out") from exc

    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        if "empty" in detail.lower():
            return "(clipboard is empty)"
        raise ToolError(f"clipboard read failed: {detail[:200]}")

    text = result.stdout.decode("utf-8", errors="replace")[:max_chars]
    return ctx.scrub(text) or "(clipboard is empty)"


@tool(
    name="clipboard.write",
    tier=Tier.WRITE,
    params={"text": "What to put on the clipboard"},
    undo="copy something else, or press Ctrl-C on your previous selection",
)
def clipboard_write(ctx, text: str) -> str:
    """Put text on the clipboard. Overwrites whatever was there."""
    _, copy = _tools()
    # wl-copy forks a daemon that owns the selection for as long as it lives
    # (Wayland requires the source client to stay running). If we capture its
    # output, that daemon inherits the pipe, we wait for an EOF that never
    # comes, and a successful copy looks like a timeout. Send the streams to
    # /dev/null so there is no pipe to hold open.
    try:
        subprocess.run(
            copy,
            input=text.encode("utf-8"),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except subprocess.CalledProcessError as exc:
        raise ToolError(f"clipboard write failed (exit {exc.returncode})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("clipboard write timed out") from exc

    ctx.log(f"copied {len(text)} chars to the clipboard")
    return f"copied {len(text)} characters to the clipboard"
