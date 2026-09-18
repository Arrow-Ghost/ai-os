"""
The brain: turns a goal + the available tools into the next decision.

Two implementations ship with the skeleton:

  GroqBrain     real inference over Groq's OpenAI-compatible endpoint.
                Needs GROQ_API_KEY and `pip install openai`.
  OfflineBrain  no network, no key. Returns a scripted sequence of decisions.
                This is what tests use and what juniors use on day one, so
                nobody is blocked on an API key to build a feature.

Every outbound message passes through the redactor first. That is not
optional and features cannot turn it off.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .contracts import Decision, Finish, ToolCall

SYSTEM_PROMPT = """You are a careful assistant operating the user's laptop through a fixed set of tools.

Rules you follow without exception:
- Use a tool, or finish. Never claim you did something you did not do through a tool.
- Prefer the least powerful tool that accomplishes the step.
- If the request is ambiguous, or you are guessing about something irreversible, finish and ask instead of acting.
- State your confidence honestly. Low confidence is useful information, not a failure.
- You cannot spend money, install software, or modify your own code. Do not try.

Work one step at a time. After each tool result, decide the next step."""


class Brain(Protocol):
    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision: ...
    def complete(self, prompt: str, *, smart: bool = False) -> str: ...


# --------------------------------------------------------------------------
# Offline
# --------------------------------------------------------------------------

class OfflineBrain:
    """A brain that never calls a network. Feed it a script, or get a polite no."""

    def __init__(self, script: list[Decision] | None = None):
        self.script = list(script or [])
        self.calls = 0

    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision:  # noqa: ARG002
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return Finish(
            "Offline brain: no plan available. Set brain.provider=groq in "
            "config/policy.yaml (and GROQ_API_KEY in .env) to think for real, "
            "or run a tool directly with `python -m servant call <tool> k=v`."
        )

    def complete(self, prompt: str, *, smart: bool = False) -> str:  # noqa: ARG002
        self.calls += 1
        return f"[offline brain] would have answered: {prompt[:160]}"


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------

class GroqBrain:
    """Groq via the OpenAI-compatible API. Model names live in config."""

    def __init__(self, config, redactor):
        api_key = config.secret("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key, "
                "or set brain.provider: offline in config/policy.yaml."
            )
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install openai -- required for the groq provider") from exc

        self._client = OpenAI(api_key=api_key, base_url=config.get("brain.base_url"))
        self._redactor = redactor
        self.model_fast = config.get("brain.model_fast")
        self.model_smart = config.get("brain.model_smart")
        self.temperature = float(config.get("brain.temperature", 0.2))
        self.max_retries = int(config.get("brain.max_retries", 3))
        self.calls = 0

    # -- public ------------------------------------------------------------
    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Goal: {goal}"},
            *history,
            {
                "role": "system",
                "content": (
                    "Call exactly one tool for the next step, or reply in plain text if the "
                    "goal is complete or you need the human. When you call a tool, begin your "
                    "text with a one-line reason and a confidence like 'confidence: 0.8'."
                ),
            },
        ]
        response = self._chat(messages, tools=tools, smart=True)
        choice = response.choices[0].message

        calls = getattr(choice, "tool_calls", None)
        if calls:
            call = calls[0]
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            text = choice.content or ""
            return ToolCall(
                tool=call.function.name,
                args=args,
                rationale=text.strip()[:400],
                confidence=_parse_confidence(text),
            )
        return Finish((choice.content or "").strip() or "(no answer)")

    def complete(self, prompt: str, *, smart: bool = False) -> str:
        response = self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            smart=smart,
        )
        return (response.choices[0].message.content or "").strip()

    # -- internals ---------------------------------------------------------
    def _chat(self, messages: list[dict], *, tools: list[dict] | None = None, smart: bool = False):
        import time

        safe = self._redactor.scrub(messages) if self._redactor else messages
        kwargs: dict[str, Any] = {
            "model": self.model_smart if smart else self.model_fast,
            "messages": safe,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self.calls += 1
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 -- retry on rate limits and transient errors
                last = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2 ** attempt)  # Groq free tier rate-limits hard; back off
        raise RuntimeError(f"Groq call failed after {self.max_retries} attempts: {last}")


def _parse_confidence(text: str) -> float:
    import re

    match = re.search(r"confidence[:\s]+([01](?:\.\d+)?)", text or "", re.IGNORECASE)
    if not match:
        return 0.5
    return max(0.0, min(1.0, float(match.group(1))))


def build_brain(config, redactor):
    """Factory driven by config. Unknown providers fail loudly, not silently."""
    provider = (config.get("brain.provider") or "offline").lower()
    if provider == "offline":
        return OfflineBrain()
    if provider == "groq":
        return GroqBrain(config, redactor)
    raise ValueError(f"unknown brain.provider: {provider!r} (expected 'groq' or 'offline')")
