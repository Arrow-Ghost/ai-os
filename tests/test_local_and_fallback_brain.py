"""
LocalBrain (an Ollama-pointed GroqBrain subclass) and FallbackBrain, the
resilience wrapper built after a real Gemini 503 outage killed a run mid-build.
Entirely offline -- fake clients throughout, no network, no real Ollama needed.
"""

from __future__ import annotations

import pytest

from servant.brain import BrainUnavailable, FallbackBrain, LocalBrain, _ollama_reachable


# -- LocalBrain ---------------------------------------------------------------

class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeCompletion:
    def __init__(self, content=None, tool_calls=None):
        self.choices = [FakeChoice(FakeMessage(content, tool_calls))]


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return self._responses.pop(0)


class FakeChatClient:
    def __init__(self, responses):
        self.chat = type("C", (), {"completions": FakeCompletions(responses)})()


def test_local_brain_needs_no_key(config):
    """The whole point: Ollama does not check the api_key, so construction
    must not demand a real credential the way GroqBrain does."""
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([FakeCompletion(content="hi")]))
    assert brain.complete("hello") == "hi"


def test_local_brain_uses_the_configured_model_and_url(config):
    config.data["brain"]["local_model"] = "qwen2.5:3b"
    config.data["brain"]["local_base_url"] = "http://localhost:11434/v1"
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([FakeCompletion(content="hi")]))
    assert brain.model_fast == "qwen2.5:3b"
    assert brain._base_url == "http://localhost:11434/v1"


def test_local_brain_has_no_vision(config):
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([]))
    with pytest.raises(BrainUnavailable, match="text-only"):
        brain.see("x.png", "what is this")


def test_local_brain_has_no_transcription(config):
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([]))
    with pytest.raises(BrainUnavailable, match="does not take audio"):
        brain.transcribe("x.wav")


def test_local_brain_rotation_is_always_a_noop(config):
    """One key, so _rotate_key (inherited from GroqBrain) must always refuse."""
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([FakeCompletion(content="hi")]))
    assert brain._rotate_key(Exception("429 rate limit")) is False
    assert brain.rotations == 0


def test_local_brain_raw_client_has_no_prompt_guard(config):
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([]))
    assert brain.raw_client is None


# -- FallbackBrain --------------------------------------------------------------

class WorkingBrain:
    calls = 3
    rotations = 0
    raw_client = "primary-client"

    def complete(self, prompt, *, smart=False):
        return "primary answer"

    def decide(self, goal, history, tools):
        return "primary decision"

    def see(self, image_path, prompt):
        return "primary saw it"

    def transcribe(self, audio_path):
        return "primary heard it"


class BrokenBrain:
    calls = 0
    rotations = 0
    raw_client = None

    def complete(self, prompt, *, smart=False):
        raise RuntimeError("503 the service is down")

    def decide(self, goal, history, tools):
        raise RuntimeError("503 the service is down")

    def see(self, image_path, prompt):
        raise RuntimeError("503 the service is down")

    def transcribe(self, audio_path):
        raise RuntimeError("503 the service is down")


class FallbackBrainStub:
    calls = 1
    rotations = 0
    raw_client = "fallback-client"

    def complete(self, prompt, *, smart=False):
        return "fallback answer"

    def decide(self, goal, history, tools):
        return "fallback decision"

    def see(self, image_path, prompt):
        raise RuntimeError("fallback cannot see either")

    def transcribe(self, audio_path):
        raise RuntimeError("fallback cannot hear either")


def test_uses_primary_when_it_works():
    brain = FallbackBrain(WorkingBrain(), FallbackBrainStub())
    assert brain.complete("hi") == "primary answer"
    assert brain.last_used == "primary"


def test_falls_back_when_primary_fails():
    brain = FallbackBrain(BrokenBrain(), FallbackBrainStub())
    assert brain.complete("hi") == "fallback answer"
    assert brain.last_used == "fallback"


def test_fallback_is_never_silent():
    """This is the whole point: a degraded run must be visible, not just working."""
    warnings = []
    brain = FallbackBrain(BrokenBrain(), FallbackBrainStub(), on_fallback=warnings.append)
    brain.complete("hi")
    assert warnings, "on_fallback must fire when the primary fails"
    assert "primary brain failed" in warnings[0]


def test_both_failing_raises_a_combined_error():
    brain = FallbackBrain(BrokenBrain(), BrokenBrain())
    with pytest.raises(RuntimeError, match="both brains failed"):
        brain.complete("hi")


def test_raw_client_reflects_whichever_brain_last_answered():
    """The injection guard needs the ACTIVE brain's client, not always the primary's."""
    brain = FallbackBrain(WorkingBrain(), FallbackBrainStub())
    brain.complete("hi")
    assert brain.raw_client == "primary-client"

    brain2 = FallbackBrain(BrokenBrain(), FallbackBrainStub())
    brain2.complete("hi")
    assert brain2.raw_client == "fallback-client"


def test_calls_sum_across_both_brains():
    brain = FallbackBrain(WorkingBrain(), FallbackBrainStub())
    assert brain.calls == 3 + 1


def test_decide_and_see_and_transcribe_all_fall_back_independently():
    brain = FallbackBrain(BrokenBrain(), FallbackBrainStub())
    assert brain.decide("g", [], []) == "fallback decision"
    # see() fails on BOTH here -- proves failures are per-call, not sticky
    with pytest.raises(RuntimeError, match="both brains failed"):
        brain.see("x.png", "what")


# -- reachability probe ---------------------------------------------------------

def test_ollama_reachable_returns_false_fast_when_nothing_is_listening():
    """Must never hang startup on a laptop daemon that is not running."""
    import time

    start = time.monotonic()
    result = _ollama_reachable("http://127.0.0.1:1/v1", timeout=0.3)
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0, "the reachability check must fail fast, not block startup"


# -- context window (num_ctx) hook -----------------------------------------

def test_local_brain_requests_a_large_context_window(config):
    """63 tool schemas run ~6,600 tokens; Ollama's default 4,096 context
    truncates the request and the model answers with nothing, which looks
    exactly like a bad model unless you know to check this. Verified live:
    the same prompt, only num_ctx changed, and an empty response became a
    real tool call."""
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([FakeCompletion(content="hi")]))
    extra = brain._extra_chat_kwargs()
    assert extra["extra_body"]["options"]["num_ctx"] >= 8192


def test_local_num_ctx_is_configurable(config):
    config.data["brain"]["local_num_ctx"] = 32768
    brain = LocalBrain(config, redactor=None, client=FakeChatClient([FakeCompletion(content="hi")]))
    assert brain._extra_chat_kwargs()["extra_body"]["options"]["num_ctx"] == 32768


def test_groq_brain_does_not_send_num_ctx(config, monkeypatch):
    """The context-window workaround is a LOCAL-only concern; a cloud brain
    sending Ollama-specific options would be a protocol error."""
    from servant.brain import GroqBrain

    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    brain = GroqBrain(config, redactor=None, client=FakeChatClient([FakeCompletion(content="hi")]))
    assert brain._extra_chat_kwargs() == {}
