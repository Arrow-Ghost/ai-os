"""
================================================================
 THE FEATURE SDK -- this is the only module a feature imports.
================================================================

Write a feature in three steps:

    # features/hello.py
    from servant.sdk import tool, Tier

    @tool(tier=Tier.READ, params={"name": "who to greet"})
    def hello_greet(ctx, name: str = "world") -> str:
        '''Say hello to someone.'''
        ctx.log(f"greeting {name}")
        return f"hello {name}"

Drop the file in features/, run `python -m servant tools`, and it is live.
Nothing to register, no imports to update, no core file to edit.

Rules the framework enforces for you:
  * first argument is always `ctx`
  * every argument needs a type hint (str/int/float/bool/list/dict)
  * you must pick a Tier -- see docs/FEATURE_GUIDE.md
  * return a string (or anything str()-able); raise on failure
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .context import Context
from .contracts import ActionResult, Status, Tier, ToolSpec
from .registry import REGISTRY, build_spec

__all__ = ["tool", "Tier", "Context", "ActionResult", "Status", "ToolError", "ToolSpec"]


class ToolError(Exception):
    """Raise this for an expected failure (bad input, file missing, API down).

    The message is shown to the human and fed back to the brain, so write it
    for a reader: what failed, and what would fix it.
    """


def tool(
    _func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    tier: Tier = Tier.READ,
    params: dict[str, str] | None = None,
    undo: str | None = None,
    examples: Iterable[str] = (),
    untrusted: bool = False,
):
    """Register a function as a capability of the agent.

    Args:
        name:        what the LLM calls it. Convention: "<area>.<verb>",
                     e.g. "files.move". Defaults to the function name with
                     underscores turned into dots.
        description: one line, written for the LLM. Defaults to the docstring's
                     first paragraph. Say what it does AND when to use it.
        tier:        READ (no side effects) | WRITE (reversible) |
                     DANGER (irreversible, costs money, or other people see it).
                     When unsure, pick the higher one. Nobody was ever fired
                     for asking permission.
        params:      {arg_name: "description for the LLM"}. Types come from
                     your type hints.
        undo:        one line telling the human how to reverse this, shown in
                     the approval prompt. Required in spirit for WRITE/DANGER.
        examples:    sample invocations, shown in `python -m servant tools -v`.
        untrusted:   True if this tool's return value is text pulled in from
                     outside the machine (a web page, an email, a screenshot,
                     the clipboard). The agent loop wraps that output so the
                     model treats it as DATA, never as an instruction to obey.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = build_spec(
            func,
            name=name,
            description=description,
            tier=tier,
            params=params,
            undo=undo,
            examples=examples,
            untrusted=untrusted,
        )
        REGISTRY.add(spec)
        func.__tool_spec__ = spec  # type: ignore[attr-defined]
        return func

    return decorator(_func) if _func is not None else decorator
