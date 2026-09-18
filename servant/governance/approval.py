"""
Human-in-the-loop approval.

The executor calls `approver.request(...)` for anything the policy tiers as
"approve". Swap the implementation to change the UX -- console today, a
Telegram bot or a desktop notification later -- without touching any feature.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ApprovalRequest:
    tool: str
    tier: str
    args: dict
    rationale: str = ""
    confidence: float | None = None
    undo: str | None = None


@dataclass
class ApprovalDecision:
    approved: bool
    note: str = ""


class Approver(Protocol):
    def request(self, req: ApprovalRequest) -> ApprovalDecision: ...
    def confirm(self, question: str) -> bool: ...


class ConsoleApprover:
    """Asks on the terminal. The default for interactive runs.

    If there is nobody there to answer -- stdin is a pipe, a cron job, a
    detached service -- it denies. Failing closed is the only safe direction
    for a question that gates an irreversible action.
    """

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        print("\n" + "=" * 62)
        print(f"  APPROVAL NEEDED  ->  {req.tool}   [tier: {req.tier}]")
        print("=" * 62)
        if req.rationale:
            print(f"  why : {req.rationale}")
        if req.confidence is not None:
            print(f"  conf: {req.confidence:.0%}")
        if req.undo:
            print(f"  undo: {req.undo}")
        print("  args:")
        for line in json.dumps(req.args, indent=2, default=str).splitlines():
            print(f"    {line}")
        print("-" * 62)
        try:
            answer = input("  Run it? [y/N/reason] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("  (no one there to ask -- denying)")
            return ApprovalDecision(False, note="no interactive terminal; denied")
        if answer.lower() in {"y", "yes"}:
            return ApprovalDecision(True)
        return ApprovalDecision(False, note=answer or "operator declined")

    def confirm(self, question: str) -> bool:
        try:
            return input(f"  {question} [y/N] > ").strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            return False


class DenyAllApprover:
    """For tests and CI. Nothing that needs a human ever runs."""

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(False, note="non-interactive: auto-denied")

    def confirm(self, question: str) -> bool:  # noqa: ARG002
        return False


class AutoApprover:
    """--yolo mode. Only for a scratch folder you do not mind losing."""

    def __init__(self, announce: bool = True):
        self.announce = announce

    def request(self, req: ApprovalRequest) -> ApprovalDecision:
        if self.announce:
            print(f"  [auto-approved] {req.tool} {req.args}")
        return ApprovalDecision(True, note="auto-approved (--yes)")

    def confirm(self, question: str) -> bool:
        if self.announce:
            print(f"  [auto-confirmed] {question}")
        return True
