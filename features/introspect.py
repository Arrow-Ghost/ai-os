"""
Introspection: what the agent has done, and why, and how much room is left.

The audit log was built as the safety backbone in the very first version of
this project, and until now nothing could read it back -- governance you
cannot inspect is not governance, just a file nobody looks at. These tools
read servant/audit.py's hash-chained log directly. They are all READ: looking
at the log changes nothing and never needs approval.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from servant.sdk import Tier, ToolError, tool


def _read_log(ctx, limit: int = 5000) -> list[dict]:
    path = ctx.config.path("audit_log")
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _parse_ts(record: dict) -> float:
    try:
        from datetime import datetime

        return datetime.fromisoformat(record["ts"]).timestamp()
    except (KeyError, ValueError):
        return 0.0


@tool(
    name="agent.history",
    tier=Tier.READ,
    params={
        "minutes": "How far back to look",
        "kind": "Only this kind of entry: gate | act | approval | think | run.start | run.end. Empty means all.",
    },
)
def agent_history(ctx, minutes: int = 60, kind: str = "") -> str:
    """What has the agent done recently? Reads the audit log, not memory."""
    minutes = max(1, min(int(minutes), 10080))  # cap at a week
    cutoff = time.time() - minutes * 60

    rows = [r for r in _read_log(ctx) if _parse_ts(r) >= cutoff]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]

    if not rows:
        return f"nothing in the last {minutes} minute(s)" + (f" of kind {kind!r}" if kind else "")

    lines = [f"{len(rows)} entr(y/ies) in the last {minutes} minute(s):"]
    for row in rows[-100:]:
        payload = row.get("payload", {})
        stamp = row.get("ts", "")[11:19]
        summary = _summarise_entry(row.get("kind", ""), payload)
        lines.append(f"  {stamp}  {row.get('kind',''):<12} {summary}")
    if len(rows) > 100:
        lines.insert(1, f"  (showing the most recent 100 of {len(rows)})")
    return ctx.scrub("\n".join(lines))


def _summarise_entry(kind: str, payload: dict) -> str:
    if kind == "act":
        return f"{payload.get('tool','?')} -> {payload.get('status','?')}"
    if kind == "gate":
        return f"{payload.get('tool','?')} [{payload.get('tier','?')}] -> {payload.get('action','?')}"
    if kind == "approval":
        return f"{payload.get('tool','?')} approved={payload.get('approved')}"
    if kind == "think":
        return f"turn {payload.get('turn','?')}: {payload.get('decision','?')} {payload.get('tool','')}"
    if kind in {"run.start", "run.end"}:
        return str(payload.get("goal") or payload.get("answer") or payload.get("status") or "")[:80]
    return json.dumps(payload, default=str)[:100]


@tool(
    name="agent.explain",
    tier=Tier.READ,
    params={"about": "A tool name or keyword to explain the most recent decision about"},
)
def agent_explain(ctx, about: str) -> str:
    """Why did the agent do (or refuse to do) something? Pulls the gate verdict and rationale from the log."""
    if not about.strip():
        raise ToolError("about is empty -- give a tool name or keyword, e.g. 'files.trash'")

    needle = about.lower()
    rows = _read_log(ctx, limit=5000)
    # Exclude agent.explain's own call from its own search results -- without
    # this, asking to explain anything with no other match would always
    # "find" the very question being asked, because that question's text is
    # sitting in this call's own gate/act log entries.
    matches = [
        r for r in rows
        if r.get("kind") in {"gate", "think", "approval", "act"}
        and r.get("payload", {}).get("tool") != "agent.explain"
        and needle in json.dumps(r.get("payload", {}), default=str).lower()
    ]

    if not matches:
        return f"nothing in the audit log mentions {about!r}"

    # Group the most recent match with its neighbours from the same run, so
    # the full story (think -> gate -> approval -> act) comes back together.
    latest = matches[-1]
    run_id = latest.get("run_id", "")
    story = [r for r in rows if r.get("run_id") == run_id and r.get("kind") in {"gate", "think", "approval", "act"}]
    relevant = [r for r in story if needle in json.dumps(r.get("payload", {}), default=str).lower()]

    lines = [f"most recent match for {about!r} (run {run_id}):"]
    for row in relevant[-6:]:
        payload = row.get("payload", {})
        lines.append(f"\n  [{row.get('kind')}] {row.get('ts','')[11:19]}")
        for field in ("tool", "tier", "action", "reason", "rationale", "confidence", "approved", "note", "status", "error"):
            if field in payload and payload[field] not in (None, ""):
                lines.append(f"    {field}: {str(payload[field])[:200]}")
    return ctx.scrub("\n".join(lines))


@tool(name="budget.status", tier=Tier.READ)
def budget_status(ctx) -> str:
    """How much of this run's step/time budget is used, and how many model calls it has made.

    Worth checking before a long browser or skill-acquisition task: Groq's
    free tier is easy to exhaust, and this is the cheapest way to see the
    ceiling coming before a call fails.
    """
    lines = []
    if ctx.budget is not None:
        elapsed = time.monotonic() - ctx.budget.started
        lines.append(
            f"steps:   {ctx.budget.steps}/{ctx.budget.max_steps}\n"
            f"time:    {elapsed:.0f}s/{ctx.budget.max_seconds:.0f}s"
        )
    else:
        lines.append("no budget attached to this run")

    stats = ctx.brain_stats()
    calls, rotations = stats["calls"], stats["rotations"]
    if calls is not None:
        lines.append(f"LLM calls this run: {calls}")
    if rotations:
        lines.append(f"key rotations this run: {rotations} (a key hit its rate limit)")

    return "\n".join(lines)
