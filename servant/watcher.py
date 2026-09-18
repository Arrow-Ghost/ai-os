"""
The background runner: polls registered monitors and escalates when one fires.

Design notes worth keeping:

* Every check goes through `agent.call_tool`, so a background poll is gated,
  budgeted and audited exactly like an interactive action. There is no
  privileged back door for automation.
* Polling is free. The brain is only involved when a monitor fires AND that
  monitor was given a goal. A watcher left running for an hour costs nothing.
* The budget is reset per tick. It exists to bound a single run, not to stop a
  daemon after twelve polls.
* The kill switch is checked every tick, so `touch .servant/STOP` stops the
  watcher the same way it stops everything else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .governance import Stopped

CHECK_TOOL = {
    "file": "watch.check_file",
    "log": "watch.check_log",
    "endpoint": "watch.check_endpoint",
}
FIRING_WORDS = {"CHANGED", "FIRED"}


@dataclass
class TickStats:
    checks: int = 0
    fires: int = 0
    errors: int = 0
    escalations: int = 0
    last: dict[str, str] = field(default_factory=dict)


def _args_for(monitor: dict) -> tuple[str, dict]:
    kind = monitor["kind"]
    tool = CHECK_TOOL.get(kind)
    if tool is None:
        raise ValueError(f"unknown monitor kind: {kind}")
    if kind == "file":
        return tool, {"path": monitor["target"]}
    if kind == "log":
        return tool, {"path": monitor["target"], "pattern": monitor.get("pattern", "error")}
    return tool, {"url": monitor["target"]}


def load_monitors(agent) -> list[dict]:
    return list(agent.memory.recall("watch.monitors", []) or [])


def check_one(agent, monitor: dict, stats: TickStats, *, quiet: bool = False) -> bool:
    """Run one monitor. Returns True if it fired."""
    tool, args = _args_for(monitor)

    # Per-tick reset: the step budget bounds a run, not the daemon's lifetime.
    agent.budget.reset()
    result = agent.call_tool(tool, args)
    stats.checks += 1

    if not result.ok:
        stats.errors += 1
        stats.last[monitor["id"]] = f"error: {result.error}"
        if not quiet:
            print(f"  [{monitor['id']}] ERROR {result.error}")
        return False

    first_word = (result.output.split(None, 1) or [""])[0].upper()
    stats.last[monitor["id"]] = result.output.splitlines()[0][:120]
    fired = first_word in FIRING_WORDS

    if not quiet:
        stamp = time.strftime("%H:%M:%S")
        marker = "!!" if fired else "  "
        print(f"{stamp} {marker} [{monitor['id']}] {result.output.splitlines()[0][:100]}")

    if not fired:
        return False

    stats.fires += 1
    _escalate(agent, monitor, result.output, stats, quiet=quiet)
    return True


def _escalate(agent, monitor: dict, detail: str, stats: TickStats, *, quiet: bool = False) -> None:
    """Tell the human, and hand the agent a goal if this monitor has one."""
    agent.budget.reset()
    agent.call_tool(
        "notify.send",
        {
            "title": f"servant: {monitor['kind']} {monitor['id']} fired",
            "body": detail.splitlines()[0][:180],
            "urgency": "critical" if monitor["kind"] != "file" else "normal",
        },
    )

    goal = (monitor.get("goal") or "").strip()
    if not goal:
        return

    stats.escalations += 1
    if not quiet:
        print(f"     -> escalating to the brain: {goal}")
    agent.budget.reset()
    agent.run(f"{goal}\n\nWhat triggered this:\n{detail[:1500]}")


def run_once(agent, *, quiet: bool = False) -> TickStats:
    """One pass over every monitor. Good for cron."""
    stats = TickStats()
    monitors = load_monitors(agent)
    if not monitors and not quiet:
        print("no monitors registered -- add one with: python -m servant call watch.add ...")
    for monitor in monitors:
        agent.killswitch.check()
        check_one(agent, monitor, stats, quiet=quiet)
    return stats


def run_forever(agent, *, quiet: bool = False) -> TickStats:
    """Poll on each monitor's own interval until stopped.

    Uses APScheduler when available (it handles drift and overlapping jobs);
    falls back to a plain due-time loop so the feature still works if the
    package is not installed.
    """
    stats = TickStats()
    monitors = load_monitors(agent)
    if not monitors:
        print("no monitors registered -- add one with: python -m servant call watch.add ...")
        return stats

    agent.killswitch.install_signal_handler()
    print(f"watching {len(monitors)} monitor(s). Stop with Ctrl-C or: touch .servant/STOP\n")
    for m in monitors:
        print(f"  [{m['id']}] {m['kind']:<8} {m['target']} every {m['interval']}s")
    print()

    try:
        _run_apscheduler(agent, monitors, stats, quiet=quiet)
    except ImportError:
        _run_plain(agent, monitors, stats, quiet=quiet)
    return stats


def _run_apscheduler(agent, monitors: list[dict], stats: TickStats, *, quiet: bool) -> None:
    from apscheduler.schedulers.background import BackgroundScheduler  # noqa: PLC0415

    scheduler = BackgroundScheduler()
    for monitor in monitors:
        scheduler.add_job(
            check_one,
            "interval",
            seconds=monitor["interval"],
            args=[agent, monitor, stats],
            kwargs={"quiet": quiet},
            id=monitor["id"],
            next_run_time=None,          # first run happens below, immediately
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()

    try:
        for monitor in monitors:         # immediate first pass, so you see it work
            agent.killswitch.check()
            check_one(agent, monitor, stats, quiet=quiet)
        while True:
            agent.killswitch.check()
            time.sleep(1)
    except (Stopped, KeyboardInterrupt) as stop:
        print(f"\n[watch] stopping: {stop or 'interrupted'}")
    finally:
        scheduler.shutdown(wait=False)
        _summary(stats)


def _run_plain(agent, monitors: list[dict], stats: TickStats, *, quiet: bool) -> None:
    due = {m["id"]: 0.0 for m in monitors}
    try:
        while True:
            agent.killswitch.check()
            now = time.monotonic()
            for monitor in monitors:
                if now >= due[monitor["id"]]:
                    check_one(agent, monitor, stats, quiet=quiet)
                    due[monitor["id"]] = now + monitor["interval"]
            time.sleep(1)
    except (Stopped, KeyboardInterrupt) as stop:
        print(f"\n[watch] stopping: {stop or 'interrupted'}")
    finally:
        _summary(stats)


def _summary(stats: TickStats) -> None:
    print(
        f"[watch] {stats.checks} check(s), {stats.fires} fire(s), "
        f"{stats.escalations} escalation(s), {stats.errors} error(s)"
    )
