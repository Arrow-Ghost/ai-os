"""
servant -- a governed agent framework.

Layers, outside in:

    features/           what your team writes. Plain functions + @tool.
    servant/sdk.py      the only thing features import.
    servant/executor.py the one chokepoint every action passes through.
    servant/governance/ tiers, approval, redaction, kill switch.
    servant/agent.py    wiring and the perceive-think-gate-act loop.

The core is deliberately small and closed. The feature layer is deliberately
open. If a feature needs something the SDK does not expose, that is a
conversation about the core -- not a reason to reach around it.
"""

from .agent import Agent, RunResult, build_agent
from .contracts import ActionResult, Status, Tier
from .sdk import Context, ToolError, tool

__version__ = "0.1.0"
__all__ = [
    "Agent", "RunResult", "build_agent", "tool", "Tier",
    "Context", "ToolError", "ActionResult", "Status",
]
