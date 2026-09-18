"""GroqBrain against a fake OpenAI client that validates requests like the real API."""

from __future__ import annotations

import importlib
import json
import re
import sys
from types import SimpleNamespace as NS

import pytest

from servant.agent import Agent
from servant.brain import BrainUnavailable, GroqBrain, build_brain
from servant.config import Config
from servant.contracts import Finish, Status, ToolCall
from servant.governance import AutoApprover, Redactor

WIRE_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class FakeAPIError(Exception):
    def __init__(self, status_code, message="boom"):
        super().__init__(message)
        self.status_code = status_code


def tool_reply(name, args, text="", call_id="call_1"):
    call = NS(id=call_id, function=NS(name=name, arguments=json.dumps(args)))
    return NS(choices=[NS(message=NS(content=text, tool_calls=[call]))])


def text_reply(text):
    return NS(choices=[NS(message=NS(content=text, tool_calls=None))])


class FakeClient:
    """Replays scripted replies (or raises scripted errors) and checks each request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        for tool in kwargs.get("tools", []):
            fn = tool["function"]
            assert WIRE_NAME.match(fn["name"]), f"API would reject tool name {fn['name']!r}"
            for prop in fn["parameters"]["properties"].values():
                if prop["type"] == "array":
                    assert "items" in prop, f"{fn['name']}: array without items"
        open_calls = set()
        for msg in kwargs["messages"]:
            for call in msg.get("tool_calls") or []:
                assert WIRE_NAME.match(call["function"]["name"])
                open_calls.add(call["id"])
            if msg["role"] == "tool":
                assert msg["tool_call_id"] in open_calls, "tool result answers no call"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def features(clean_registry):
    for name in [m for m in sys.modules if m == "features" or m.startswith("features.")]:
        del sys.modules[name]
    importlib.import_module("features.files")
    importlib.import_module("features.git_tools")
    yield
    for name in [m for m in sys.modules if m == "features" or m.startswith("features.")]:
        del sys.modules[name]


def brain_with(config, client, retries=3):
    data = {**config.data, "brain": {**config.data["brain"], "max_retries": retries}}
    cfg = Config(data=data, root=config.root, source="test")
    return GroqBrain(cfg, Redactor.from_config(cfg), client=client)


def test_full_loop_through_the_groq_brain(config, tmp_path, monkeypatch):
    monkeypatch.setattr("servant.brain.time.sleep", lambda s: None)
    (tmp_path / "a.txt").write_text("password = hunter2")
    client = FakeClient([
        tool_reply("files__list", {"path": str(tmp_path)}, "Look first. confidence: 0.9", "c1"),
        tool_reply("files__read", {"path": str(tmp_path / "a.txt")}, "Read it. confidence: 0.8", "c2"),
        text_reply("There is one file, a.txt."),
    ])
    agent = Agent(config, approver=AutoApprover(announce=False), brain=brain_with(config, client), quiet=True)

    result = agent.run("what is in the folder?")

    assert result.answer == "There is one file, a.txt."
    assert [a.status for a in result.actions] == [Status.OK, Status.OK]
    last = client.requests[-1]["messages"]
    assert [m["role"] for m in last[2:]] == ["assistant", "tool", "assistant", "tool"]
    assert "a.txt" in last[3]["content"]
    assert "hunter2" not in json.dumps(last), "file contents must be redacted before leaving"
    assert {t["function"]["name"] for t in client.requests[0]["tools"]} >= {"files__list", "git__stage"}


def test_decide_maps_names_back_and_reads_confidence(config):
    client = FakeClient([tool_reply("files__list", {"path": "."}, "Because. confidence: 0.25")])
    brain = brain_with(config, client)
    from servant.registry import REGISTRY

    decision = brain.decide("g", [], REGISTRY.openai_schemas())
    assert isinstance(decision, ToolCall)
    assert decision.tool == "files.list" and decision.confidence == 0.25 and decision.id == "call_1"


def test_low_confidence_from_the_brain_forces_a_human(config, tmp_path):
    client = FakeClient([tool_reply("files__list", {"path": str(tmp_path)}, "Not sure. confidence: 0.1")])
    from servant.governance import DenyAllApprover

    agent = Agent(config, approver=DenyAllApprover(), brain=brain_with(config, client), quiet=True)
    result = agent.run("list it")
    assert result.actions[0].status is Status.DENIED, "a READ tool below the confidence floor must ask"


def test_bad_key_fails_fast_without_retrying(config, monkeypatch):
    slept = []
    monkeypatch.setattr("servant.brain.time.sleep", slept.append)
    client = FakeClient([FakeAPIError(401), text_reply("never reached")])
    with pytest.raises(BrainUnavailable, match="API key was rejected"):
        brain_with(config, client).complete("hi")
    assert len(client.requests) == 1 and slept == []


def test_rate_limit_is_retried(config, monkeypatch):
    monkeypatch.setattr("servant.brain.time.sleep", lambda s: None)
    client = FakeClient([FakeAPIError(429), FakeAPIError(400, "tool_use_failed"), text_reply("ok")])
    assert brain_with(config, client).complete("hi") == "ok"
    assert len(client.requests) == 3


def test_brain_failure_ends_the_run_cleanly(config):
    agent = Agent(config, approver=AutoApprover(announce=False),
                  brain=brain_with(config, FakeClient([FakeAPIError(404)])), quiet=True)
    result = agent.run("anything")
    assert "unknown model" in result.answer and result.steps == 0


def test_explicit_groq_without_a_key_fails_at_startup(config, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    data = {**config.data, "brain": {**config.data["brain"], "provider": "groq"}}
    with pytest.raises(BrainUnavailable, match="No Groq key"):
        build_brain(Config(data=data, root=config.root), None)


def test_auto_provider_picks_by_key(config, monkeypatch):
    data = {**config.data, "brain": {**config.data["brain"], "provider": "auto"}}
    cfg = Config(data=data, root=config.root)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    assert type(build_brain(cfg, None)).__name__ == "OfflineBrain"
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert type(build_brain(cfg, None)).__name__ == "GroqBrain"


def test_offline_script_still_works_with_new_history_shape(agent, tmp_path):
    from servant.brain import OfflineBrain

    agent.brain = OfflineBrain([ToolCall(tool="files.list", args={"path": str(tmp_path)}, confidence=0.9), Finish("done")])
    result = agent.run("list")
    assert result.answer == "done" and result.steps == 1
