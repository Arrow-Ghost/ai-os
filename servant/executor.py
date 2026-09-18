"""
The executor -- the single chokepoint every action passes through.

There is exactly one way for a tool to run in this system, and it is
`Executor.run`. That is what makes the audit log trustworthy: if it is not in
the log, it did not happen. The order below is deliberate and should not be
rearranged without thinking hard about it.

    killswitch -> budget -> lookup -> validate args -> policy verdict
               -> approval -> execute -> record outcome -> audit
"""

from __future__ import annotations

import time
from typing import Any

from .contracts import ActionResult, Status, ToolSpec
from .governance import APPROVE, BLOCK, ApprovalRequest, Stopped


class Budget:
    """Resource bounds. The opposite of resource acquisition."""

    def __init__(self, config):
        self.max_steps = int(config.get("budgets.max_steps", 12))
        self.max_seconds = float(config.get("budgets.max_seconds", 300))
        self.steps = 0
        self.started = time.monotonic()

    def check(self) -> str | None:
        if self.steps >= self.max_steps:
            return f"step budget exhausted ({self.max_steps} tool calls)"
        if time.monotonic() - self.started > self.max_seconds:
            return f"time budget exhausted ({self.max_seconds:.0f}s)"
        return None

    def spend(self) -> None:
        self.steps += 1

    def reset(self) -> None:
        self.steps = 0
        self.started = time.monotonic()


class Executor:
    def __init__(self, *, registry, policy, approver, audit, memory, killswitch, budget):
        self.registry = registry
        self.policy = policy
        self.approver = approver
        self.audit = audit
        self.memory = memory
        self.killswitch = killswitch
        self.budget = budget

    def run(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        ctx,
        rationale: str = "",
        confidence: float | None = None,
    ) -> ActionResult:
        run_id = ctx.run_id

        # 1. Stop means stop. Checked before anything else, every time.
        self.killswitch.check()

        # 2. Budget.
        exhausted = self.budget.check()
        if exhausted:
            return self._finish(run_id, tool_name, args, ActionResult(Status.BLOCKED, error=exhausted))

        # 3. Does the tool exist?
        spec = self.registry.get(tool_name)
        if spec is None:
            close = _did_you_mean(tool_name, self.registry.names())
            return self._finish(
                run_id, tool_name, args,
                ActionResult(Status.UNKNOWN_TOOL, error=f"no such tool: {tool_name}.{close}"),
            )

        # 4. Are the arguments usable?
        problem = _validate(spec, args)
        if problem:
            return self._finish(run_id, tool_name, args, ActionResult(Status.ERROR, error=problem))

        # 5. What does policy say?
        verdict = self.policy.evaluate(spec, confidence)
        self.audit.write(
            "gate", run_id,
            tool=spec.name, tier=verdict.tier.value, action=verdict.action,
            reason=verdict.reason, confidence=confidence, args=args,
        )

        if verdict.action == BLOCK:
            return self._finish(
                run_id, spec.name, args,
                ActionResult(Status.BLOCKED, error=f"blocked by policy: {verdict.reason}"),
            )

        # 6. Ask the human when the policy says to.
        if verdict.action == APPROVE:
            decision = self.approver.request(
                ApprovalRequest(
                    tool=spec.name, tier=verdict.tier.value, args=args,
                    rationale=rationale, confidence=confidence, undo=spec.undo,
                )
            )
            self.audit.write(
                "approval", run_id,
                tool=spec.name, approved=decision.approved, note=decision.note,
            )
            if not decision.approved:
                return self._finish(
                    run_id, spec.name, args,
                    ActionResult(Status.DENIED, error=decision.note or "denied by operator"),
                )

        # 7. Run it.
        return self._finish(run_id, spec.name, args, self._invoke(spec, args, ctx))

    # -- internals ---------------------------------------------------------
    def _invoke(self, spec: ToolSpec, args: dict, ctx) -> ActionResult:
        self.budget.spend()
        started = time.monotonic()
        try:
            output = spec.func(ctx, **args)
            elapsed = int((time.monotonic() - started) * 1000)
            return ActionResult(Status.OK, output=_stringify(output), duration_ms=elapsed)
        except Stopped:
            raise  # never swallow a stop
        except Exception as exc:  # noqa: BLE001 -- a bad feature must not kill the agent
            elapsed = int((time.monotonic() - started) * 1000)
            return ActionResult(
                Status.ERROR, error=f"{type(exc).__name__}: {exc}", duration_ms=elapsed
            )

    def _finish(self, run_id: str, tool: str, args: dict, result: ActionResult) -> ActionResult:
        self.audit.write(
            "act", run_id,
            tool=tool, args=args, status=result.status.value,
            output=result.output[:2000], error=result.error, duration_ms=result.duration_ms,
        )
        self.memory.record_outcome(tool, result.ok, result.error or result.output[:200])
        return result


def _validate(spec: ToolSpec, args: dict) -> str | None:
    known = {p.name for p in spec.params}
    unknown = set(args) - known
    if unknown:
        return f"unexpected argument(s) {sorted(unknown)}; {spec.name} accepts {sorted(known)}"
    missing = [p.name for p in spec.params if p.required and p.name not in args]
    if missing:
        return f"missing required argument(s) {missing} for {spec.name}"
    return None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict, tuple)):
        import json

        return json.dumps(value, indent=2, default=str)
    return str(value)


def _did_you_mean(name: str, candidates: list[str]) -> str:
    import difflib

    close = difflib.get_close_matches(name, candidates, n=3, cutoff=0.5)
    return f" Did you mean: {', '.join(close)}?" if close else ""
