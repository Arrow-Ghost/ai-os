"""
Data contracts shared by every layer of the agent.

Nothing in here imports anything else from `servant`, so it is safe to import
from anywhere. If you are writing a feature you only need `Tier` -- and you
should import it from `servant.sdk`, not from here.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Union


# --------------------------------------------------------------------------
# Permission tiers
# --------------------------------------------------------------------------

class Tier(str, Enum):
    """How dangerous an action is. This is the heart of the governance layer.

    Pick the tier by asking: "if the agent gets this wrong, how bad is it?"

    READ      Nothing changes. Reading a file, listing a folder, an HTTP GET.
    WRITE     Something changes but you can undo it. Writing a file, moving
              something to trash, creating a git branch.
    DANGER    Irreversible, costs money, or is visible to other people.
              Sending mail, deleting without trash, `git push`, paying for
              anything, posting anywhere, installing packages.
    FORBIDDEN Never runs. Reserved for things policy bans outright.
    """

    READ = "read"
    WRITE = "write"
    DANGER = "danger"
    FORBIDDEN = "forbidden"

    @property
    def rank(self) -> int:
        return {"read": 0, "write": 1, "danger": 2, "forbidden": 3}[self.value]


# --------------------------------------------------------------------------
# Tool declaration
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    """One argument of a tool, derived from the function signature."""

    name: str
    type: str = "string"          # string | integer | number | boolean | array | object
    description: str = ""
    required: bool = True
    default: Any = None

    def to_json_schema(self) -> dict:
        schema: dict = {"type": self.type}
        if self.type == "array":
            schema["items"] = {"type": "string"}  # strict validators reject a bare array
        if self.description:
            schema["description"] = self.description
        return schema


@dataclass(frozen=True)
class ToolSpec:
    """A registered capability. Created for you by the @tool decorator."""

    name: str
    description: str
    tier: Tier
    params: tuple[ParamSpec, ...]
    func: Callable[..., Any]
    module: str
    undo: str | None = None          # human-readable "how do I undo this?"
    examples: tuple[str, ...] = ()

    def to_openai_schema(self) -> dict:
        """The shape the LLM sees. OpenAI/Groq function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.to_json_schema() for p in self.params},
                    "required": [p.name for p in self.params if p.required],
                },
            },
        }


# --------------------------------------------------------------------------
# What the brain decides
# --------------------------------------------------------------------------

@dataclass
class ToolCall:
    """The brain wants to run a tool."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    confidence: float = 0.5
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class Finish:
    """The brain is done and has an answer for the user."""

    message: str


Decision = Union[ToolCall, Finish]  # not `|`: this line runs on import, and 3.9 has no type union


# --------------------------------------------------------------------------
# What happens when we act
# --------------------------------------------------------------------------

class Status(str, Enum):
    OK = "ok"                # ran, succeeded
    ERROR = "error"          # ran, raised
    DENIED = "denied"        # human said no
    BLOCKED = "blocked"      # policy said no (forbidden tier, budget, killswitch)
    UNKNOWN_TOOL = "unknown_tool"


@dataclass
class ActionResult:
    status: Status
    output: str = ""
    error: str | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def as_observation(self) -> str:
        """What gets fed back to the brain on the next turn."""
        if self.ok:
            return self.output or "(done, no output)"
        return f"[{self.status.value}] {self.error or self.output or 'no detail'}"


@dataclass
class Event:
    """One line in the audit log."""

    kind: str                       # run.start | think | gate | act | run.end | ...
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    ts: float = field(default_factory=time.time)
