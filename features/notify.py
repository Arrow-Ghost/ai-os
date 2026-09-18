"""
Desktop notifications -- how the agent gets your attention.

This is the other half of every background job: a watcher that spots something
is useless if it cannot tell you. Uses notify-send, which is already on KDE.
"""

from __future__ import annotations

import shutil
import subprocess

from servant.sdk import Tier, ToolError, tool

URGENCIES = {"low", "normal", "critical"}


@tool(
    name="notify.send",
    tier=Tier.WRITE,
    params={
        "title": "Short headline, a few words",
        "body": "The detail. Keep it to a sentence or two.",
        "urgency": "low | normal | critical (critical stays on screen until dismissed)",
    },
    undo="dismiss the notification",
)
def notify_send(ctx, title: str, body: str = "", urgency: str = "normal") -> str:
    """Show a desktop notification. Use this to report a finding or ask for attention."""
    if not shutil.which("notify-send"):
        raise ToolError("notify-send is not installed (sudo apt install libnotify-bin)")

    urgency = urgency.lower().strip()
    if urgency not in URGENCIES:
        raise ToolError(f"urgency must be one of {sorted(URGENCIES)}, got {urgency!r}")

    # Never leak a secret onto the lock screen.
    title, body = ctx.scrub(title), ctx.scrub(body)

    try:
        subprocess.run(
            ["notify-send", "--app-name=servant", f"--urgency={urgency}", title, body],
            check=True, capture_output=True, timeout=10,
        )
    except subprocess.CalledProcessError as exc:
        raise ToolError(f"notify-send failed: {exc.stderr.decode()[:200]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("notify-send timed out") from exc

    ctx.log(f"notified: {title}")
    return f"notification shown: {title}"
