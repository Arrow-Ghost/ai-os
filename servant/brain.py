"""
The brain: turns a goal + the available tools into the next decision.

Two implementations ship with the skeleton:

  GroqBrain     real inference over Groq's OpenAI-compatible endpoint.
                Needs a key and `pip install openai`.
  OfflineBrain  no network, no key. Returns a scripted sequence of decisions.
                This is what tests use and what juniors use on day one, so
                nobody is blocked on an API key to build a feature.

Every outbound message passes through the redactor first. That is not
optional and features cannot turn it off.

Key rotation: set GROQ_API_KEYS to a comma-separated list and the brain moves
to the next key on a rate limit. Groq's free tier limits are low enough that
one key runs out mid-demo; several do not.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any, Protocol

from .contracts import Decision, Finish, ToolCall

SYSTEM_PROMPT = """You are a careful assistant operating the user's laptop through a fixed set of tools.

Rules you follow without exception:
- Use a tool, or finish. Never claim you did something you did not do through a tool.
- Prefer the least powerful tool that accomplishes the step.
- If the request is ambiguous, or you are guessing about something irreversible, finish and ask instead of acting.
- State your confidence honestly. Low confidence is useful information, not a failure.
- You cannot spend money, install software, or modify your own code. Do not try.

Work one step at a time. After each tool result, decide the next step.
Call exactly one tool per turn. Before a tool call, write one short line saying why, ending with
your confidence, e.g. "Listing the folder first. confidence: 0.8". When the goal is done, or you
need the human, reply in plain text with no tool call."""


class Brain(Protocol):
    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision: ...
    def complete(self, prompt: str, *, smart: bool = False) -> str: ...
    def see(self, image_path: str | Path, prompt: str) -> str: ...
    def transcribe(self, audio_path: str | Path) -> str: ...


class BrainUnavailable(RuntimeError):
    """The brain cannot answer: no key, no network, bad model name, rate limit."""


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
            "Offline brain: no plan available. Add GROQ_API_KEY to .env (brain.provider "
            "auto or groq in config/policy.yaml) to think for real, or run a tool directly "
            "with `python -m servant call <tool> k=v`."
        )

    def complete(self, prompt: str, *, smart: bool = False) -> str:  # noqa: ARG002
        self.calls += 1
        return f"[offline brain] would have answered: {prompt[:160]}"

    def see(self, image_path: str | Path, prompt: str) -> str:
        self.calls += 1
        return f"[offline brain] would have looked at {Path(image_path).name} for: {prompt[:120]}"

    def transcribe(self, audio_path: str | Path) -> str:
        self.calls += 1
        return f"[offline brain] would have transcribed {Path(audio_path).name}"


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------

class GroqBrain:
    """Groq via the OpenAI-compatible API. Model names live in config.

    `client` is for tests: pass a fake and no key or network is needed.
    """

    def __init__(self, config, redactor, *, client=None):
        self._keys = _load_keys(config)
        if client is None and not self._keys:
            raise BrainUnavailable(
                "No Groq key found. Copy .env.example to .env and set GROQ_API_KEY "
                "(or GROQ_API_KEYS for several), or set brain.provider: auto / offline "
                "in config/policy.yaml."
            )
        self._base_url = config.get("brain.base_url")
        self._key_index = 0
        if client is not None:
            self._OpenAI = None
            self._client = client
        else:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise BrainUnavailable("pip install openai -- required for the groq provider") from exc
            self._OpenAI = OpenAI
            self._client = self._make_client()

        self._redactor = redactor
        self.model_fast = config.get("brain.model_fast")
        self.model_smart = config.get("brain.model_smart")
        self.model_vision = config.get("brain.model_vision")
        self.model_transcribe = config.get("brain.model_transcribe")
        self.temperature = float(config.get("brain.temperature", 0.2))
        self.max_retries = max(1, int(config.get("brain.max_retries", 3)))
        self.max_image_bytes = int(config.get("brain.max_image_bytes", 4_000_000))
        self.calls = 0
        self.rotations = 0

    # -- public ------------------------------------------------------------
    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision:
        to_wire, from_wire = _name_maps(tools)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Goal: {goal}"},
            *_rename_history(history, to_wire),
        ]
        response = self._chat(messages, tools=_rename_tools(tools, to_wire), smart=True)
        choice = response.choices[0].message
        text = (choice.content or "").strip()

        calls = getattr(choice, "tool_calls", None)
        if calls:
            call = calls[0]  # one step at a time; parallel extras are dropped
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            decision = ToolCall(
                tool=from_wire.get(call.function.name, call.function.name),
                args=args if isinstance(args, dict) else {},
                rationale=text[:400],
                confidence=_parse_confidence(text),
            )
            if getattr(call, "id", None):
                decision.id = call.id
            return decision
        return Finish(text or "(no answer)")

    def complete(self, prompt: str, *, smart: bool = False) -> str:
        response = self._chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            smart=smart,
        )
        return (response.choices[0].message.content or "").strip()

    def see(self, image_path: str | Path, prompt: str) -> str:
        """Ask the vision model about an image on disk."""
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"no such image: {path}")

        raw = path.read_bytes()
        if len(raw) > self.max_image_bytes:
            raise ValueError(
                f"{path.name} is {len(raw) / 1e6:.1f}MB, over the "
                f"{self.max_image_bytes / 1e6:.1f}MB limit. Downscale it first."
            )

        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data_url = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        # The image is NOT redactable -- whatever is on screen goes to Groq.
        # That is why screen.* tools are gated and say so in their docstrings.
        response = self._chat(
            [{"role": "user", "content": [
                {"type": "text", "text": self._scrub(prompt)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            model=self.model_vision,
        )
        return (response.choices[0].message.content or "").strip()

    def transcribe(self, audio_path: str | Path) -> str:
        """Speech to text via Groq-hosted Whisper. No local model needed."""
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"no such audio file: {path}")

        last: Exception | None = None
        for _ in range(max(1, len(self._keys))):
            try:
                self.calls += 1
                with path.open("rb") as fh:
                    result = self._client.audio.transcriptions.create(
                        model=self.model_transcribe, file=fh
                    )
                return (getattr(result, "text", "") or "").strip()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if not self._rotate_key(exc):
                    break
        raise BrainUnavailable(f"transcription failed: {_describe(last)}")

    # -- internals ---------------------------------------------------------
    def _make_client(self):
        client = self._OpenAI(api_key=self._keys[self._key_index], base_url=self._base_url)
        # The SDK retries on its own by default; ours (with key rotation) would stack on top.
        return client.with_options(max_retries=0) if hasattr(client, "with_options") else client

    def _rotate_key(self, exc: Exception) -> bool:
        """Move to the next key if this looks like a rate limit. True if rotated."""
        if self._OpenAI is None or len(self._keys) < 2 or not _is_rate_limit(exc):
            return False
        self._key_index = (self._key_index + 1) % len(self._keys)
        self._client = self._make_client()
        self.rotations += 1
        return True

    def _scrub(self, value: Any) -> Any:
        return self._redactor.scrub(value) if self._redactor else value

    def _chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        smart: bool = False,
        model: str | None = None,
    ):
        kwargs: dict[str, Any] = {
            "model": model or (self.model_smart if smart else self.model_fast),
            "messages": self._scrub(messages),
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        last: Exception | None = None
        attempts = self.max_retries + len(self._keys)
        for attempt in range(attempts):
            try:
                self.calls += 1
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                last = exc
                if self._rotate_key(exc):
                    continue  # fresh key, try again immediately
                if not _retryable(exc) or attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8))  # Groq free tier rate-limits hard; back off
        raise BrainUnavailable(f"Groq call failed ({len(self._keys)} key(s)): {_describe(last)}")


def _load_keys(config) -> list[str]:
    """GROQ_API_KEYS (comma separated) wins; GROQ_API_KEY is the single-key form."""
    many = config.secret("GROQ_API_KEYS") or ""
    keys = [k.strip() for k in many.split(",") if k.strip()]
    if not keys:
        one = config.secret("GROQ_API_KEY")
        keys = [one.strip()] if one else []
    seen, unique = set(), []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def _is_rate_limit(exc: Exception) -> bool:
    """True only for limits another key would actually clear.

    413 "Request too large" is deliberately NOT included. It means this single
    request exceeds the per-minute token allowance, which is a property of the
    request, not the key -- rotating would burn every key on the same payload.
    Only 429 (too many requests) and quota exhaustion are worth a fresh key.
    """
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()

    if "too large" in text or "reduce your message size" in text:
        return False
    if status == 429:
        return True
    return "rate limit" in text or "429" in text or "quota" in text


def _retryable(exc: Exception) -> bool:
    """Retry rate limits, server errors, dropped connections, and Groq's
    `tool_use_failed` (the model emitted a malformed call; a retry usually
    fixes it). A bad key or a bad request fails the same way every time."""
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # connection error or timeout
    if status == 429 or status >= 500:
        return True
    return status == 400 and "tool_use_failed" in str(exc)


def _describe(exc: Exception | None) -> str:
    hints = {
        401: "the API key was rejected -- check GROQ_API_KEY in .env",
        404: "unknown model -- check the brain.model_* names in config/policy.yaml",
        429: "rate limited -- wait a minute, add keys to GROQ_API_KEYS, or lower budgets.max_llm_calls",
    }
    status = getattr(exc, "status_code", None)
    return hints.get(status, f"{type(exc).__name__}: {str(exc)[:300]}")


# -- tool names on the wire -------------------------------------------------
# Function names must match ^[a-zA-Z0-9_-]{1,64}$ and ours are dotted
# ("files.list"). The brain translates at the boundary, in both directions.

def _wire_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "__", name)[:64]


def _name_maps(tools: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    to_wire = {t["function"]["name"]: _wire_name(t["function"]["name"]) for t in tools}
    from_wire = {wire: real for real, wire in to_wire.items()}
    if len(from_wire) != len(to_wire):
        raise ValueError("two tool names collide once made API-safe; rename one")
    return to_wire, from_wire


def _rename_tools(tools: list[dict], to_wire: dict[str, str]) -> list[dict]:
    return [{**t, "function": {**t["function"], "name": to_wire[t["function"]["name"]]}} for t in tools]


def _rename_history(history: list[dict], to_wire: dict[str, str]) -> list[dict]:
    out = []
    for msg in history:
        if msg.get("tool_calls"):
            msg = {**msg, "tool_calls": [
                {**c, "function": {**c["function"], "name": to_wire.get(c["function"]["name"], _wire_name(c["function"]["name"]))}}
                for c in msg["tool_calls"]
            ]}
        out.append(msg)
    return out


def _parse_confidence(text: str) -> float:
    match = re.search(r"confidence[:\s]+([01]?(?:\.\d+)?)", text or "", re.IGNORECASE)
    if not match or not match.group(1).strip("."):
        return 0.5
    return max(0.0, min(1.0, float(match.group(1))))


def build_brain(config, redactor):
    """Factory driven by config. Unknown providers fail loudly, not silently."""
    provider = (config.get("brain.provider") or "offline").lower()
    if provider == "auto":  # groq when a key is present, offline otherwise
        provider = "groq" if _load_keys(config) else "offline"
    if provider == "offline":
        return OfflineBrain()
    if provider == "groq":
        return GroqBrain(config, redactor)
    raise ValueError(f"unknown brain.provider: {provider!r} (expected 'auto', 'groq' or 'offline')")
