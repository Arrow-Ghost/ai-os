"""
The agent: wiring + the main loop.

    goal -> think -> gate -> act -> observe -> log -> repeat

Everything dangerous already lives in the executor, so this file stays small
and readable on purpose. If you find yourself adding a special case here,
it probably belongs in a tool or in the policy.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .audit import AuditLog
from .brain import build_brain
from .config import Config, load_config
from .context import Context
from .contracts import ActionResult, Finish, Status, ToolCall
from .executor import Budget, Executor
from .governance import (
    Approver,
    ConsoleApprover,
    KillSwitch,
    PolicyEngine,
    Redactor,
    Stopped,
)
from .loader import LoadReport, load_features
from .memory import Memory
from .registry import REGISTRY


@dataclass
class RunResult:
    run_id: str
    goal: str
    answer: str = ""
    steps: int = 0
    stopped: bool = False
    actions: list[ActionResult] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        config: Config,
        *,
        approver: Approver | None = None,
        brain=None,
        quiet: bool = False,
    ):
        self.config = config
        self.quiet = quiet

        self.redactor = Redactor.from_config(config)
        self.audit = AuditLog(config.path("audit_log"), redactor=self.redactor)
        self.memory = Memory(config.path("memory_db"))
        self.killswitch = KillSwitch(config.root / config.get("governance.killswitch_file"))
        self.policy = PolicyEngine(config)
        self.approver = approver or ConsoleApprover()
        self.budget = Budget(config)
        self.registry = REGISTRY
        self.brain = brain if brain is not None else build_brain(config, self.redactor)

        self.executor = Executor(
            registry=self.registry,
            policy=self.policy,
            approver=self.approver,
            audit=self.audit,
            memory=self.memory,
            killswitch=self.killswitch,
            budget=self.budget,
        )
        self.load_report: LoadReport | None = None

    # -- setup -------------------------------------------------------------
    def load_features(self, verbose: bool = False) -> LoadReport:
        features_dir = Path(self.config.get("paths.features_dir", "features"))
        if not features_dir.is_absolute():
            features_dir = self.config.root / features_dir
        self.load_report = load_features(features_dir, verbose=verbose)
        for name, error in self.load_report.failed.items():
            print(f"[loader] FAILED {name}: {error}")
        return self.load_report

    def make_context(self, run_id: str) -> Context:
        return Context(
            run_id=run_id,
            config=self.config,
            memory=self.memory,
            audit=self.audit,
            approver=self.approver,
            brain=self.brain,
            executor=self.executor,
            redactor=self.redactor,
            registry=self.registry,
            killswitch=self.killswitch,
            quiet=self.quiet,
        )

    # -- one tool, directly (how juniors test a feature) -------------------
    def call_tool(self, name: str, args: dict, *, run_id: str | None = None) -> ActionResult:
        run_id = run_id or f"call-{uuid.uuid4().hex[:6]}"
        ctx = self.make_context(run_id)
        self.audit.write("run.start", run_id, mode="direct-call", tool=name, args=args)
        result = self.executor.run(name, args, ctx=ctx, rationale="direct CLI call")
        self.audit.write("run.end", run_id, status=result.status.value)
        return result

    # -- the loop ----------------------------------------------------------
    def run(self, goal: str) -> RunResult:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        result = RunResult(run_id=run_id, goal=goal)

        self.killswitch.install_signal_handler()
        self.budget.reset()
        ctx = self.make_context(run_id)

        self.audit.write("run.start", run_id, goal=goal, tools=len(self.registry))
        self.memory.add_episode(run_id, "user", goal)

        history: list[dict] = []
        schemas = self.registry.openai_schemas()
        max_llm_calls = int(self.config.get("budgets.max_llm_calls", 20))

        try:
            for turn in range(max_llm_calls):
                self.killswitch.check()

                try:
                    decision = self.brain.decide(goal, history, schemas)
                except Exception as exc:  # noqa: BLE001 -- no key, no network, bad model
                    result.answer = f"The brain failed, so nothing more was done: {exc}"
                    self.audit.write("think", run_id, turn=turn, decision="error", error=str(exc))
                    self._say(f"\n[brain error] {exc}")
                    break

                if isinstance(decision, Finish):
                    result.answer = decision.message
                    self._say(f"\n{decision.message}\n")
                    self.audit.write("think", run_id, turn=turn, decision="finish")
                    self.memory.add_episode(run_id, "assistant", decision.message)
                    break

                assert isinstance(decision, ToolCall)
                self.audit.write(
                    "think", run_id, turn=turn, decision="tool",
                    tool=decision.tool, rationale=decision.rationale,
                    confidence=decision.confidence,
                )
                self._say(f"  → {decision.tool}({_brief(decision.args)})")

                action = self.executor.run(
                    decision.tool, decision.args, ctx=ctx,
                    rationale=decision.rationale, confidence=decision.confidence,
                )
                result.actions.append(action)
                result.steps += 1

                observation = action.as_observation()
                # The shape the chat API expects: the assistant's tool call, then
                # a `tool` message answering that exact call id.
                history.extend([
                    {
                        "role": "assistant",
                        "content": decision.rationale or None,
                        "tool_calls": [{
                            "id": decision.id,
                            "type": "function",
                            "function": {"name": decision.tool, "arguments": json.dumps(decision.args)},
                        }],
                    },
                    {"role": "tool", "tool_call_id": decision.id, "content": observation[:4000]},
                ])
                self.memory.add_episode(run_id, "tool", f"{decision.tool} -> {observation[:500]}")

                if action.status is Status.DENIED:
                    result.answer = "Stopped: you declined that step."
                    self._say("  (declined -- ending the run)")
                    break
            else:
                result.answer = f"Hit the turn budget ({max_llm_calls}) without finishing."

        except Stopped as stop:
            result.stopped = True
            result.answer = f"Halted by kill switch: {stop}"
            self._say(f"\n[STOPPED] {stop}")

        self.audit.write(
            "run.end", run_id, steps=result.steps, stopped=result.stopped, answer=result.answer
        )
        return result

    def _say(self, message: str) -> None:
        if not self.quiet:
            print(message)


def build_agent(
    *,
    policy_file: str | Path | None = None,
    approver: Approver | None = None,
    brain=None,
    quiet: bool = False,
    load: bool = True,
) -> Agent:
    """Standard way to construct a fully wired agent."""
    agent = Agent(load_config(policy_file), approver=approver, brain=brain, quiet=quiet)
    if load:
        agent.load_features()
    return agent


def _brief(args: dict) -> str:
    out = []
    for key, value in args.items():
        text = str(value)
        out.append(f"{key}={text[:40] + '…' if len(text) > 40 else text}")
    return ", ".join(out)
