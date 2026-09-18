"""
GeminiBrain, entirely offline against a fake google-genai client.

Live end-to-end confirmation is recorded in the commit message rather than
here: auth (all 6 keys), model listing, and function calling were verified
against the real API before Google's service went into a 503 "high demand"
state mid-build. These tests prove the LOGIC -- rotation, schema translation,
history conversion -- independent of whether the live service is up.
"""

from __future__ import annotations

import json

import pytest

from servant.contracts import Finish, ToolCall
from servant.brain import GeminiBrain, _is_gemini_rate_limit


class FakePart:
    def __init__(self, text=None, function_call=None):
        self.text = text
        self.function_call = function_call


class FakeFunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class FakeCandidate:
    def __init__(self, parts):
        self.content = type("C", (), {"parts": parts})()


class FakeResponse:
    def __init__(self, *, text=None, function_call=None):
        self.text = text
        part = FakePart(text=text, function_call=function_call)
        self.candidates = [FakeCandidate([part])]


class FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if not self._responses:
            raise RuntimeError("FakeModels ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def _brain(config, responses):
    return GeminiBrain(config, redactor=None, client=FakeClient(responses))


# -- basic shapes -----------------------------------------------------------

def test_complete_returns_text(config):
    brain = _brain(config, [FakeResponse(text="hello")])
    assert brain.complete("hi") == "hello"


def test_decide_returns_finish_when_no_function_call(config):
    brain = _brain(config, [FakeResponse(text="all done")])
    decision = brain.decide("goal", [], [])
    assert isinstance(decision, Finish)
    assert decision.message == "all done"


def test_decide_returns_toolcall_on_function_call(config):
    fc = FakeFunctionCall("files__list", {"path": "/tmp"})
    brain = _brain(config, [FakeResponse(text="reasoning here", function_call=fc)])
    tools = [{"type": "function", "function": {
        "name": "files.list", "description": "", "parameters": {"type": "object", "properties": {}},
    }}]
    decision = brain.decide("goal", [], tools)
    assert isinstance(decision, ToolCall)
    assert decision.tool == "files.list", "the wire name must be translated back to the real dotted name"
    assert decision.args == {"path": "/tmp"}


def test_tool_schema_is_translated_to_gemini_function_declarations(config):
    brain = _brain(config, [FakeResponse(text="ok")])
    tools = [{"type": "function", "function": {
        "name": "files.list", "description": "List files",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }}]
    gemini_tools = brain._to_gemini_tools(tools, {"files.list": "files__list"})
    decl = gemini_tools[0].function_declarations[0]
    assert decl.name == "files__list"
    assert decl.parameters_json_schema["properties"]["path"]["type"] == "string"


# -- key rotation -------------------------------------------------------------

def test_rotates_on_429_and_recovers(config, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "key-a,key-b,key-c")
    exhausted = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
    brain = _brain(config, [exhausted, exhausted, FakeResponse(text="finally")])

    assert brain.complete("hi") == "finally"
    assert brain.rotations == 2


def test_does_not_rotate_on_non_rate_limit_errors(config):
    """A genuine server error should retry the SAME key, not burn through all of them."""
    brain = _brain(config, [RuntimeError("500 internal error"), FakeResponse(text="ok")])
    assert brain.complete("hi") == "ok"
    assert brain.rotations == 0


@pytest.mark.parametrize("message,expected", [
    ("429 RESOURCE_EXHAUSTED: quota exceeded", True),
    ("You exceeded your current quota", True),
    ("503 UNAVAILABLE: high demand", False),
    ("404 NOT_FOUND: model deprecated", False),
    ("connection reset by peer", False),
])
def test_rate_limit_detection(message, expected):
    assert _is_gemini_rate_limit(RuntimeError(message)) is expected


def test_gives_up_after_exhausting_retries_and_keys(config, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "key-a,key-b")
    always_fails = RuntimeError("429 RESOURCE_EXHAUSTED")
    brain = _brain(config, [always_fails] * 10)
    with pytest.raises(RuntimeError, match="Gemini call failed"):
        brain.complete("hi")


# -- history conversion -------------------------------------------------------

def test_history_conversion_preserves_tool_call_and_result(config):
    brain = _brain(config, [FakeResponse(text="ok")])
    history = [
        {"role": "assistant", "content": "calling it", "tool_calls": [{
            "id": "1", "type": "function",
            "function": {"name": "files.list", "arguments": json.dumps({"path": "."})},
        }]},
        {"role": "tool", "tool_call_id": "1", "content": "a.py, b.py"},
    ]
    contents = brain._to_gemini_contents("goal", history, {"files.list": "files__list"})

    model_turn = next(c for c in contents if c["role"] == "model")
    assert model_turn["parts"][0]["function_call"]["name"] == "files__list"
    assert model_turn["parts"][0]["function_call"]["args"] == {"path": "."}

    tool_turn = [c for c in contents if "tool result" in json.dumps(c)]
    assert tool_turn, "the tool's result must reach Gemini as a user-role message"


# -- construction -------------------------------------------------------------

def test_no_key_and_no_client_raises_clear_error(config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No Gemini key"):
        GeminiBrain(config, redactor=None)


def test_raw_client_is_none_no_prompt_guard_available(config):
    """Gemini has no equivalent hosted classifier; the injection guard must
    degrade gracefully (proven separately in test_injection tests) rather
    than assume every brain exposes one."""
    brain = _brain(config, [])
    assert brain.raw_client is None


def test_build_brain_prefers_gemini_over_groq_in_auto_mode(config, monkeypatch):
    from servant.brain import build_brain

    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq-key")
    config.data["brain"]["provider"] = "auto"
    config.data["brain"]["local_fallback"] = "false"  # isolate provider SELECTION from fallback-wrapping

    brain = build_brain(config, None)
    assert isinstance(brain, GeminiBrain)
