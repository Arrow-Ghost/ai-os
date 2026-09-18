"""
Monitors: cheap polling that only spends an LLM call when something happens.

The point of this module is cost. A watcher that asks the model "anything
interesting?" every 30 seconds burns your rate limit for nothing. These checks
are pure Python -- stat, read, HTTP -- and stay free until a condition fires.
Only then does the background runner escalate to the brain.

CONTRACT: every check tool returns a string whose FIRST WORD is one of
CHANGED / UNCHANGED / FIRED. `python -m servant watch` parses that word to
decide whether to act, so keep it there if you add a check of your own.

Register monitors with watch.add, then run them with:

    python -m servant watch            # poll forever
    python -m servant watch --once     # single pass, good for cron
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from servant.sdk import Tier, ToolError, tool

MONITORS_KEY = "watch.monitors"
KINDS = {"file", "log", "endpoint"}


def _state_key(kind: str, target: str) -> str:
    digest = hashlib.sha1(f"{kind}:{target}".encode()).hexdigest()[:10]
    return f"watch.state.{digest}"


# --------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------

@tool(
    name="watch.check_file",
    tier=Tier.READ,
    params={"path": "File or directory to check"},
)
def watch_check_file(ctx, path: str) -> str:
    """Has a file changed since the last check? Returns CHANGED or UNCHANGED."""
    target = ctx.check_path(path)
    if not target.exists():
        raise ToolError(f"{target} does not exist")

    stat = target.stat()
    current = {"mtime": stat.st_mtime, "size": stat.st_size}
    key = _state_key("file", str(target))
    previous = ctx.memory.recall(key)
    ctx.memory.remember(key, current)

    if previous is None:
        return f"UNCHANGED baseline recorded for {target.name} ({stat.st_size} bytes)"
    if previous == current:
        return f"UNCHANGED {target.name} (still {stat.st_size} bytes)"

    delta = stat.st_size - previous["size"]
    return f"CHANGED {target.name} size {previous['size']} -> {stat.st_size} ({delta:+d} bytes)"


@tool(
    name="watch.check_log",
    tier=Tier.READ,
    params={
        "path": "Log file to tail",
        "pattern": "Case-insensitive substring to look for, e.g. 'error'",
        "max_lines": "Most matching lines to return",
    },
)
def watch_check_log(ctx, path: str, pattern: str = "error", max_lines: int = 20) -> str:
    """Read new lines appended to a log since the last check and report matches.

    Returns FIRED with the matching lines, or UNCHANGED.
    """
    target = ctx.check_path(path)
    if not target.is_file():
        raise ToolError(f"{target} is not a file")

    key = _state_key("log", str(target))
    offset = int(ctx.memory.recall(key, 0) or 0)
    size = target.stat().st_size

    if size < offset:          # rotated or truncated -- start over
        offset = 0

    with target.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        new_text = fh.read()
        ctx.memory.remember(key, fh.tell())

    if not new_text.strip():
        return f"UNCHANGED no new lines in {target.name}"

    needle = pattern.lower()
    matches = [line for line in new_text.splitlines() if needle in line.lower()]
    if not matches:
        return f"UNCHANGED {len(new_text.splitlines())} new line(s), none matching {pattern!r}"

    shown = matches[:max_lines]
    body = "\n".join(shown)
    more = f"\n... and {len(matches) - len(shown)} more" if len(matches) > len(shown) else ""
    return ctx.scrub(f"FIRED {len(matches)} line(s) matching {pattern!r} in {target.name}:\n{body}{more}")


@tool(
    name="watch.check_endpoint",
    tier=Tier.READ,
    params={"url": "URL to poll", "expect_status": "The status code that means healthy"},
)
def watch_check_endpoint(ctx, url: str, expect_status: int = 200) -> str:
    """Poll an HTTP endpoint. Returns FIRED if it is unhealthy or its status changed."""
    if not url.lower().startswith(("http://", "https://")):
        raise ToolError(f"url must start with http:// or https://, got {url!r}")

    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "servant/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status, detail = response.status, response.reason
    except urllib.error.HTTPError as exc:
        status, detail = exc.code, exc.reason
    except (urllib.error.URLError, TimeoutError) as exc:
        status, detail = 0, str(getattr(exc, "reason", exc))[:120]

    key = _state_key("endpoint", url)
    previous = ctx.memory.recall(key)
    ctx.memory.remember(key, status)

    if status != expect_status:
        return f"FIRED {url} returned {status} {detail} (expected {expect_status})"
    if previous is not None and previous != status:
        return f"CHANGED {url} recovered: {previous} -> {status}"
    return f"UNCHANGED {url} is healthy ({status})"


# --------------------------------------------------------------------------
# The register
# --------------------------------------------------------------------------

@tool(
    name="watch.add",
    tier=Tier.WRITE,
    params={
        "kind": "file | log | endpoint",
        "target": "Path or URL to watch",
        "interval": "Seconds between checks (minimum 5)",
        "pattern": "For kind=log: the substring to match",
        "goal": "Optional. A goal to hand the agent when this fires.",
    },
    undo="watch.remove with the id printed in the result",
)
def watch_add(ctx, kind: str, target: str, interval: int = 60, pattern: str = "error", goal: str = "") -> str:
    """Register a monitor. It runs when you start `python -m servant watch`."""
    if kind not in KINDS:
        raise ToolError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
    if interval < 5:
        raise ToolError("interval must be at least 5 seconds -- be kind to your disk and your rate limit")

    monitor = {
        "id": uuid.uuid4().hex[:6],
        "kind": kind,
        "target": target,
        "interval": int(interval),
        "pattern": pattern,
        "goal": goal,
        "created": time.time(),
    }
    monitors = list(ctx.memory.recall(MONITORS_KEY, []) or [])
    monitors.append(monitor)
    ctx.memory.remember(MONITORS_KEY, monitors)

    ctx.log(f"monitor {monitor['id']} registered: {kind} {target} every {interval}s")
    return (
        f"monitor {monitor['id']} added ({kind} {target}, every {interval}s). "
        f"Start it with: python -m servant watch"
    )


@tool(name="watch.list", tier=Tier.READ)
def watch_list(ctx) -> str:
    """List every registered monitor."""
    monitors = ctx.memory.recall(MONITORS_KEY, []) or []
    if not monitors:
        return "no monitors registered (add one with watch.add)"

    lines = [f"{len(monitors)} monitor(s):"]
    for m in monitors:
        extra = f" pattern={m['pattern']!r}" if m["kind"] == "log" else ""
        goal = f" -> goal: {m['goal']}" if m.get("goal") else ""
        lines.append(f"  [{m['id']}] {m['kind']:<8} {m['target']} every {m['interval']}s{extra}{goal}")
    return "\n".join(lines)


@tool(
    name="watch.remove",
    tier=Tier.WRITE,
    params={"monitor_id": "The id from watch.list"},
    undo="watch.add it again",
)
def watch_remove(ctx, monitor_id: str) -> str:
    """Remove a registered monitor."""
    monitors = list(ctx.memory.recall(MONITORS_KEY, []) or [])
    remaining = [m for m in monitors if m["id"] != monitor_id]
    if len(remaining) == len(monitors):
        raise ToolError(f"no monitor with id {monitor_id!r} (see watch.list)")

    ctx.memory.remember(MONITORS_KEY, remaining)
    return f"monitor {monitor_id} removed ({len(remaining)} left)"
