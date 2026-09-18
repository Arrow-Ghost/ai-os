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
- Prefer a PURPOSE-BUILT tool over a general one. If a dedicated tool exists for the job
  (files.list, git.status, sys.battery, ...), use it instead of shell.run -- shell.run is
  for when nothing else covers the case, not a default reach.
- If a tool call fails or gives an unhelpful result, do not repeat the exact same call. Either
  change the arguments meaningfully or pick a different approach.
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

    @property
    def raw_client(self):
        return None  # nothing to screen with offline; guard_and_wrap handles None

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

    @property
    def raw_client(self):
        """The underlying OpenAI-compatible client, for the injection guard only."""
        return self._client

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
    def _extra_chat_kwargs(self) -> dict:
        """Hook for a subclass to add provider-specific request options.

        LocalBrain uses it to request a context window big enough for this
        project's tool schemas (measured at ~6,600 tokens for 63 tools --
        Ollama's default of 4,096 truncates that and the model answers with
        nothing, which looks exactly like a bad model when it is actually a
        starved context window).
        """
        return {}

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
            **self._extra_chat_kwargs(),
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


def _load_keys(config, many_var: str = "GROQ_API_KEYS", one_var: str = "GROQ_API_KEY") -> list[str]:
    """<PREFIX>_API_KEYS (comma separated) wins; <PREFIX>_API_KEY is the single-key form."""
    many = config.secret(many_var) or ""
    keys = [k.strip() for k in many.split(",") if k.strip()]
    if not keys:
        one = config.secret(one_var)
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




# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

class GeminiBrain:
    """Google Gemini via google-genai. A second brain with its OWN rate-limit
    pool, separate from whatever Groq provider is configured -- useful
    specifically because Groq's free tier shares one 8,000 TPM ceiling across
    every key on the account (see docs/ARCHITECTURE.md). Whether several
    Gemini keys share a project-level quota is NOT verified here; rotation is
    built the same way regardless, since it costs nothing if they do share
    one and helps for real if they do not.

    `client` is for tests: pass a fake and no key or network is needed.
    """

    def __init__(self, config, redactor, client=None):
        self._keys = _load_keys(config, "GEMINI_API_KEYS", "GEMINI_API_KEY")
        if not self._keys and client is None:
            raise RuntimeError(
                "No Gemini key found. Set GEMINI_API_KEY (or GEMINI_API_KEYS for "
                "several) in .env, or set brain.provider: offline."
            )
        try:
            from google import genai as _genai
            from google.genai import types as _types
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install google-genai -- required for the gemini provider") from exc

        self._genai, self._types = _genai, _types
        self._redactor = redactor
        self._key_index = 0
        # A test double, if given, stays in place for the whole brain's life:
        # _make_client() must never silently replace it with a real network
        # client when rotation fires, or an injected fake only ever covers
        # the first call.
        self._injected_client = client
        self._client = client if client is not None else self._make_client()

        # "-latest" aliases, not a pinned version: a pinned "gemini-2.5-pro"
        # went 404 mid-build ("no longer available to new users"), which is
        # exactly the failure these aliases exist to avoid. gemini-pro-latest
        # returned 429 RESOURCE_EXHAUSTED immediately on this account's free
        # tier, so "smart" defaults to flash too rather than a model this key
        # cannot actually call -- override in policy.yaml if your tier has pro.
        self.model_fast = config.get("brain.gemini_model_fast", "gemini-flash-latest")
        self.model_smart = config.get("brain.gemini_model_smart", "gemini-flash-latest")
        self.model_vision = config.get("brain.gemini_model_vision", "gemini-flash-latest")
        self.temperature = float(config.get("brain.temperature", 0.2))
        self.max_retries = int(config.get("brain.max_retries", 3))
        self.max_image_bytes = int(config.get("brain.max_image_bytes", 4_000_000))
        self.calls = 0
        self.rotations = 0

    # -- public --------------------------------------------------------
    def decide(self, goal: str, history: list[dict], tools: list[dict]) -> Decision:
        to_wire, from_wire = (_name_maps(tools) if tools else ({}, {}))
        gemini_tools = self._to_gemini_tools(tools, to_wire) if tools else None

        contents = self._to_gemini_contents(goal, history, to_wire)
        response = self._generate(contents, tools=gemini_tools, smart=True)

        call = self._first_function_call(response)
        if call is not None:
            real_name = from_wire.get(call.name, call.name)
            return ToolCall(
                tool=real_name,
                args=dict(call.args or {}),
                rationale=(response.text or "").strip()[:400],
                confidence=_parse_confidence(response.text or ""),
            )
        return Finish((response.text or "").strip() or "(no answer)")

    def complete(self, prompt: str, *, smart: bool = False) -> str:
        response = self._generate([{"role": "user", "parts": [{"text": prompt}]}], smart=smart)
        return (response.text or "").strip()

    def see(self, image_path: str | Path, prompt: str) -> str:
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
        contents = [{"role": "user", "parts": [
            {"text": self._scrub(prompt)},
            {"inline_data": {"mime_type": mime, "data": raw}},
        ]}]
        response = self._generate(contents, model_override=self.model_vision)
        return (response.text or "").strip()

    def transcribe(self, audio_path: str | Path) -> str:
        """Speech to text. Gemini takes audio inline -- no separate upload step."""
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"no such audio file: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "audio/wav"
        contents = [{"role": "user", "parts": [
            {"text": "Transcribe this audio exactly. Output only the transcript, nothing else."},
            {"inline_data": {"mime_type": mime, "data": path.read_bytes()}},
        ]}]
        response = self._generate(contents, model_override=self.model_fast)
        return (response.text or "").strip()

    @property
    def raw_client(self):
        """Gemini has no equivalent hosted prompt-guard classifier; the input
        guard's Groq call is skipped when this is the active brain."""
        return None

    # -- internals -------------------------------------------------------
    def _make_client(self):
        if self._injected_client is not None:
            return self._injected_client

        # google-genai warns "both GOOGLE_API_KEY and GEMINI_API_KEY are set"
        # even though api_key= below always wins (verified: an explicit key
        # overrides a deliberately-broken GOOGLE_API_KEY in the environment).
        # GOOGLE_API_KEY is real and still used by web.search's google
        # backend, so it cannot simply be removed from .env -- hide it from
        # this one call instead, which is cosmetic, not a correctness fix.
        import os

        previous = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            return self._genai.Client(api_key=self._keys[self._key_index])
        finally:
            if previous is not None:
                os.environ["GOOGLE_API_KEY"] = previous

    def _rotate_key(self, exc: Exception) -> bool:
        if len(self._keys) < 2 or not _is_gemini_rate_limit(exc):
            return False
        self._key_index = (self._key_index + 1) % len(self._keys)
        self._client = self._make_client()
        self.rotations += 1
        return True

    def _scrub(self, value):
        return self._redactor.scrub(value) if self._redactor else value

    def _to_gemini_tools(self, tools: list[dict], to_wire: dict):
        declarations = []
        for entry in tools:
            fn = entry["function"]
            declarations.append(self._types.FunctionDeclaration(
                name=to_wire.get(fn["name"], fn["name"]),
                description=fn.get("description", ""),
                parameters_json_schema=fn.get("parameters", {"type": "object", "properties": {}}),
            ))
        return [self._types.Tool(function_declarations=declarations)]

    def _to_gemini_contents(self, goal: str, history: list[dict], to_wire: dict) -> list[dict]:
        """Reuses the same OpenAI-shaped history the Groq brain builds, so the
        agent loop needs no Gemini-specific branch."""
        contents = [{"role": "user", "parts": [{"text": f"{SYSTEM_PROMPT}\n\nGoal: {goal}"}]}]
        for msg in history:
            role = "model" if msg.get("role") == "assistant" else "user"
            if msg.get("tool_calls"):
                parts = []
                for call in msg["tool_calls"]:
                    name = to_wire.get(call["function"]["name"], call["function"]["name"])
                    args = json.loads(call["function"]["arguments"] or "{}")
                    parts.append({"function_call": {"name": name, "args": args}})
                contents.append({"role": "model", "parts": parts})
            elif msg.get("role") == "tool":
                contents.append({"role": "user", "parts": [{"text": f"tool result: {msg['content']}"}]})
            else:
                text = msg.get("content") or ""
                if text:
                    contents.append({"role": role, "parts": [{"text": text}]})
        return contents

    def _first_function_call(self, response):
        try:
            for part in response.candidates[0].content.parts:
                if getattr(part, "function_call", None) is not None:
                    return part.function_call
        except (IndexError, AttributeError):
            pass
        return None

    def _generate(self, contents, *, tools=None, smart: bool = False, model_override: str | None = None):
        model = model_override or (self.model_smart if smart else self.model_fast)
        config_kwargs = {"temperature": self.temperature}
        if tools:
            config_kwargs["tools"] = tools

        safe_contents = self._scrub(contents) if isinstance(contents, str) else contents

        last: Exception | None = None
        attempts = self.max_retries + len(self._keys)
        for attempt in range(max(1, attempts)):
            try:
                self.calls += 1
                return self._client.models.generate_content(
                    model=model, contents=safe_contents,
                    config=self._types.GenerateContentConfig(**config_kwargs),
                )
            except Exception as exc:  # noqa: BLE001
                last = exc
                if self._rotate_key(exc):
                    continue
                if attempt == attempts - 1:
                    break
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Gemini call failed ({attempts} attempt(s), {len(self._keys)} key(s)): {last}")


def _is_gemini_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "quota" in text or "rate limit" in text




# --------------------------------------------------------------------------
# Local (Ollama)
# --------------------------------------------------------------------------

class LocalBrain(GroqBrain):
    """A model running on THIS machine, via Ollama's OpenAI-compatible endpoint.

    Reuses GroqBrain wholesale -- retries, tool-schema translation, wire-name
    mapping -- because Ollama's /v1 endpoint speaks the same protocol. The
    only real differences are: no API key (Ollama does not check the one it
    is given), one "key" so rotation naturally no-ops, and no vision/audio
    model configured, because the model actually installed on this laptop
    (qwen2.5:3b, chosen after testing -- see docs/ARCHITECTURE.md) is
    text-only. see()/transcribe() fail with a clear reason rather than a
    confusing one from a model that was never asked to do that job.

    This exists as a fallback, not a first choice: it runs entirely offline,
    with no per-minute token ceiling and no dependency on Groq or Gemini being
    up, at a real cost in quality and speed versus either cloud model on this
    GPU (4GB VRAM). Use it when both cloud providers are down or you are
    offline, not as the default.
    """

    def __init__(self, config, redactor, *, client=None):
        self._keys = ["ollama"]  # Ollama does not check this; a real key is not needed
        self._base_url = config.get("brain.local_base_url", "http://localhost:11434/v1")
        self._key_index = 0

        if client is not None:
            self._OpenAI = None
            self._client = client
        else:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise BrainUnavailable("pip install openai -- required for the local provider") from exc
            self._OpenAI = OpenAI
            self._client = self._make_client()

        self._redactor = redactor
        self.model_fast = config.get("brain.local_model", "qwen2.5:3b")
        self.model_smart = config.get("brain.local_model", "qwen2.5:3b")
        self.model_vision = None
        self.model_transcribe = None
        self.temperature = float(config.get("brain.temperature", 0.2))
        self.max_retries = max(1, int(config.get("brain.max_retries", 2)))
        self.max_image_bytes = int(config.get("brain.max_image_bytes", 4_000_000))
        self._num_ctx = int(config.get("brain.local_num_ctx", 16384))
        self.calls = 0
        self.rotations = 0  # always 0 -- one key, nothing to rotate to

    def see(self, image_path, prompt: str) -> str:
        raise BrainUnavailable(
            "the local model (qwen2.5:3b) is text-only -- no vision support on this "
            "laptop's GPU. Use brain.provider: gemini or groq for screen.describe / "
            "screen.read_text, or switch to a vision-capable local model if you have "
            "the VRAM for one."
        )

    def transcribe(self, audio_path) -> str:
        raise BrainUnavailable(
            "the local model (qwen2.5:3b) does not take audio input. Use "
            "brain.provider: groq for speech.transcribe (Whisper is hosted there)."
        )

    def _extra_chat_kwargs(self) -> dict:
        # The tool schemas alone run ~6,600 tokens; Ollama's default 4,096
        # context truncates the request and the model answers with nothing,
        # which is indistinguishable from a broken model unless you know to
        # look here. Confirmed live: identical prompt, only this changed, and
        # (no answer) became a real tool call.
        return {"extra_body": {"options": {"num_ctx": self._num_ctx}}}

    @property
    def raw_client(self):
        """No hosted prompt-guard classifier locally; the input guard degrades
        to unscored (still wrapped, just not scored) when this is the active brain."""
        return None




# --------------------------------------------------------------------------
# Fallback wrapper
# --------------------------------------------------------------------------

class FallbackBrain:
    """Tries `primary`; on failure, tries `fallback`. Built after a real Gemini
    503 outage hit mid-development -- the whole run died even though a
    perfectly usable local model was sitting right there. Never silent about
    which brain actually answered: `last_used` and the warning callback both
    exist so a run using the fallback shows it, in the terminal and the log,
    rather than quietly degrading.
    """

    def __init__(self, primary, fallback, *, on_fallback=None):
        self.primary = primary
        self.fallback = fallback
        self.on_fallback = on_fallback
        self.last_used = "primary"

    def _try(self, method: str, *args, **kwargs):
        try:
            result = getattr(self.primary, method)(*args, **kwargs)
            self.last_used = "primary"
            return result
        except Exception as primary_exc:  # noqa: BLE001
            if self.on_fallback:
                self.on_fallback(f"{method}: primary brain failed ({primary_exc}); trying local fallback")
            try:
                result = getattr(self.fallback, method)(*args, **kwargs)
                self.last_used = "fallback"
                return result
            except Exception as fallback_exc:  # noqa: BLE001
                raise RuntimeError(
                    f"both brains failed for {method}() -- primary: {primary_exc} | "
                    f"fallback: {fallback_exc}"
                ) from fallback_exc

    def decide(self, goal, history, tools):
        return self._try("decide", goal, history, tools)

    def complete(self, prompt, *, smart=False):
        return self._try("complete", prompt, smart=smart)

    def see(self, image_path, prompt):
        return self._try("see", image_path, prompt)

    def transcribe(self, audio_path):
        return self._try("transcribe", audio_path)

    @property
    def calls(self):
        return getattr(self.primary, "calls", 0) + getattr(self.fallback, "calls", 0)

    @property
    def rotations(self):
        return getattr(self.primary, "rotations", 0)

    @property
    def raw_client(self):
        """Whichever brain answered most recently -- that is the one whose
        wire format the injection guard's classifier call should match."""
        active = self.primary if self.last_used == "primary" else self.fallback
        return getattr(active, "raw_client", None)


def _ollama_reachable(base_url: str, timeout: float = 0.5) -> bool:
    """Cheap, fast check -- never block startup waiting on a laptop daemon
    that might not be running. Used only to decide whether local fallback is
    worth wiring in at all."""
    import urllib.request

    try:
        urllib.request.urlopen(base_url.replace("/v1", "/api/tags"), timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def build_brain(config, redactor):
    """Factory driven by config. Unknown providers fail loudly, not silently."""
    provider = (config.get("brain.provider") or "offline").lower()
    if provider == "auto":
        # Prefer Gemini when it has a key: it is a SEPARATE rate-limit pool
        # from Groq, which matters because every Groq key on this account
        # shares one 8,000 TPM ceiling (see docs/ARCHITECTURE.md). Falls
        # back to Groq, then offline.
        if _load_keys(config, "GEMINI_API_KEYS", "GEMINI_API_KEY"):
            provider = "gemini"
        elif _load_keys(config):
            provider = "groq"
        else:
            provider = "offline"
    if provider == "local":
        return LocalBrain(config, redactor)
    if provider == "offline":
        return OfflineBrain()
    if provider not in {"groq", "gemini"}:
        raise ValueError(
            f"unknown brain.provider: {provider!r} "
            f"(expected 'auto', 'groq', 'gemini', 'local' or 'offline')"
        )

    primary = GroqBrain(config, redactor) if provider == "groq" else GeminiBrain(config, redactor)

    # Wrap with a local fallback when: the owner has not disabled it, AND
    # Ollama is actually reachable right now. "auto" (the default) only wires
    # this in when it can see Ollama running, so a machine without it
    # installed behaves exactly as before -- no surprise dependency.
    fallback_setting = str(config.get("brain.local_fallback", "auto")).lower()
    base_url = config.get("brain.local_base_url", "http://localhost:11434/v1")
    want_fallback = (
        fallback_setting == "true"
        or (fallback_setting == "auto" and _ollama_reachable(base_url))
    )
    if not want_fallback:
        return primary

    try:
        local = LocalBrain(config, redactor)
    except Exception:  # noqa: BLE001 -- fallback wiring must never block startup
        return primary

    def _warn(message: str) -> None:
        print(f"  [brain] {message}")

    return FallbackBrain(primary, local, on_fallback=_warn)
