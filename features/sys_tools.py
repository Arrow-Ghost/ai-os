"""
sys.* -- what is this machine doing right now. Read-only; needs psutil.

    sys.processes  READ  the heaviest running processes
    sys.disk       READ  free space per drive
    sys.battery    READ  charge and whether it is plugged in

psutil is imported inside each tool, so the feature still loads (and says
what to install) on a machine that does not have it yet.
"""

from __future__ import annotations

import time

from servant.sdk import Tier, ToolError, tool

from ._util import stop_requested


def _psutil():
    try:
        import psutil
    except ImportError:
        raise ToolError("psutil is not installed -- pip install psutil") from None
    return psutil


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f}GB"


@tool(
    name="sys.processes",
    tier=Tier.READ,
    params={
        "sort": "'memory' or 'cpu'",
        "limit": "How many processes to show (max 50)",
        "name": "Only processes whose name contains this text",
    },
)
def sys_processes(ctx, sort: str = "memory", limit: int = 15, name: str = "") -> str:
    """List the running processes using the most memory or CPU. Use when the machine feels slow."""
    ps = _psutil()
    sort = sort.lower().strip()
    if sort not in {"memory", "cpu"}:
        raise ToolError("sort must be 'memory' or 'cpu'")
    limit = max(1, min(int(limit), 50))

    procs = []
    for p in ps.process_iter(["pid", "name", "memory_info"]):
        pname = p.info["name"] or "?"
        if name and name.lower() not in pname.lower():
            continue
        procs.append(p)

    if sort == "cpu":
        # cpu_percent needs two samples; the first call only primes the counter.
        for p in procs:
            try:
                p.cpu_percent(None)
            except (ps.NoSuchProcess, ps.AccessDenied):
                pass
        time.sleep(0.5)
        if stop_requested(ctx):
            raise ToolError("stopped by the kill switch")

    rows = []
    for p in procs:
        try:
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            cpu = p.cpu_percent(None) / ps.cpu_count() if sort == "cpu" else None
        except (ps.NoSuchProcess, ps.AccessDenied):
            continue
        rows.append((p.info["pid"], p.info["name"] or "?", mem, cpu))

    rows.sort(key=lambda r: (r[3] if sort == "cpu" else r[2]) or 0, reverse=True)
    if not rows:
        return f"no processes match {name!r}" if name else "no processes visible"

    vm = ps.virtual_memory()
    lines = [
        f"CPU {ps.cpu_percent(interval=None):.0f}%   RAM {_gb(vm.used)} / {_gb(vm.total)} ({vm.percent:.0f}%)",
        f"{len(rows)} processes{' matching ' + repr(name) if name else ''}, top {min(limit, len(rows))} by {sort}:",
        f"  {'PID':>7}  {'MEM':>8}  {'CPU':>5}  NAME",
    ]
    for pid, pname, mem, cpu in rows[:limit]:
        cpu_s = f"{cpu:.1f}%" if cpu is not None else "-"
        lines.append(f"  {pid:>7}  {mem / 1024**2:>6.0f}MB  {cpu_s:>5}  {pname}")
    return "\n".join(lines)


@tool(name="sys.disk", tier=Tier.READ, params={"path": "Only report the drive holding this path"})
def sys_disk(ctx, path: str = "") -> str:
    """Show used and free space on each drive. Use before downloading or copying something big."""
    ps = _psutil()
    if path:
        target = ctx.check_path(path)
        u = ps.disk_usage(str(target))
        return f"{target}: {_gb(u.free)} free of {_gb(u.total)} ({u.percent:.0f}% used)"

    lines = [f"  {'DRIVE':<12} {'FREE':>9} {'TOTAL':>9}  USED  FS"]
    for part in ps.disk_partitions(all=False):
        try:
            u = ps.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue  # empty card readers, CD drives, locked volumes
        flag = "  <-- low" if u.percent >= 90 else ""
        lines.append(f"  {part.mountpoint:<12} {_gb(u.free):>9} {_gb(u.total):>9}  {u.percent:>3.0f}%  {part.fstype}{flag}")
    return "\n".join(lines) if len(lines) > 1 else "no readable drives"


@tool(name="sys.battery", tier=Tier.READ)
def sys_battery(ctx) -> str:
    """Show battery charge, whether it is plugged in, and time left."""
    ps = _psutil()
    b = ps.sensors_battery()
    if b is None:
        return "no battery (desktop, or not reported by this OS)"
    if b.power_plugged:
        state = "plugged in" + (", charging" if b.percent < 100 else ", full")
    else:
        state = "on battery"
    left = ""
    if not b.power_plugged and b.secsleft not in (ps.POWER_TIME_UNKNOWN, ps.POWER_TIME_UNLIMITED) and b.secsleft > 0:
        h, m = divmod(b.secsleft // 60, 60)
        left = f", about {h}h{m:02d}m left"
    return f"battery {b.percent:.0f}%, {state}{left}"
