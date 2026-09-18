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

Work one step at a time. After each tool result, decide the next step."""


class Brain(Protocol):
    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision: ...
    def complete(self, prompt: str, *, smart: bool = False) -> str: ...
    def see(self, image_path: str | Path, prompt: str) -> str: ...
    def transcribe(self, audio_path: str | Path) -> str: ...


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
            "config/policy.yaml (and a key in .env) to think for real, or run a "
            "tool directly with `python -m servant call <tool> k=v`."
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
    """Groq via the OpenAI-compatible API. Model names live in config."""

    def __init__(self, config, redactor):
        self._keys = _load_keys(config)
        if not self._keys:
            raise RuntimeError(
                "No Groq key found. Copy .env.example to .env and set GROQ_API_KEY "
                "(or GROQ_API_KEYS for several), or set brain.provider: offline "
                "in config/policy.yaml."
            )
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install openai -- required for the groq provider") from exc

        self._OpenAI = OpenAI
        self._base_url = config.get("brain.base_url")
        self._key_index = 0
        self._client = self._make_client()

        self._redactor = redactor
        self.model_fast = config.get("brain.model_fast")
        self.model_smart = config.get("brain.model_smart")
        self.model_vision = config.get("brain.model_vision")
        self.model_transcribe = config.get("brain.model_transcribe")
        self.temperature = float(config.get("brain.temperature", 0.2))
        self.max_retries = int(config.get("brain.max_retries", 3))
        self.max_image_bytes = int(config.get("brain.max_image_bytes", 4_000_000))
        self.calls = 0
        self.rotations = 0

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
        for _ in range(len(self._keys)):
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
        raise RuntimeError(f"transcription failed: {last}")

    # -- internals ---------------------------------------------------------
    def _make_client(self):
        return self._OpenAI(api_key=self._keys[self._key_index], base_url=self._base_url)

    def _rotate_key(self, exc: Exception) -> bool:
        """Move to the next key if this looks like a rate limit. True if rotated."""
        if len(self._keys) < 2 or not _is_rate_limit(exc):
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
            except Exception as exc:  # noqa: BLE001 -- retry rate limits and transient errors
                last = exc
                if self._rotate_key(exc):
                    continue  # fresh key, try again immediately
                if attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Groq call failed ({attempts} attempts, {len(self._keys)} key(s)): {last}")


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
    status = getattr(exc, "status_code", None)
    if status in (429, 413):
        return True
    text = str(exc).lower()
    return "rate limit" in text or "429" in text or "quota" in text


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
