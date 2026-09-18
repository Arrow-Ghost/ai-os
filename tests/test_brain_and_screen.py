"""Key rotation and the Wayland capture path. No network."""

import pytest

from servant.brain import GroqBrain, OfflineBrain, _is_rate_limit, _load_keys
from servant.contracts import Status


# -- key loading ----------------------------------------------------------

def test_many_keys_parsed_and_deduped(config, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_a, gsk_b ,gsk_a,, gsk_c ")
    assert _load_keys(config) == ["gsk_a", "gsk_b", "gsk_c"]


def test_single_key_fallback(config, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_only")
    assert _load_keys(config) == ["gsk_only"]


def test_no_key_is_an_explicit_error(config, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No Groq key"):
        GroqBrain(config, None)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (Exception("Error code: 429 - rate limit reached"), True),
        (Exception("Request Entity Too Large"), False),
        (Exception("quota exceeded for this key"), True),
        (Exception("connection reset by peer"), False),
    ],
)
def test_rate_limit_detection(exc, expected):
    assert _is_rate_limit(exc) is expected


def test_rotates_to_the_next_key_on_rate_limit(config, monkeypatch):
    """One key runs out mid-demo; several do not."""
    monkeypatch.setenv("GROQ_API_KEYS", "gsk_one,gsk_two,gsk_three")
    used = []

    class FakeClient:
        def __init__(self, api_key, base_url):  # noqa: ARG002
            self.api_key = api_key
            self.chat = type("C", (), {"completions": self})()

        def create(self, **kwargs):  # noqa: ARG002
            used.append(self.api_key)
            if len(used) < 3:
                raise Exception("Error code: 429 - rate limit reached")
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {
                "content": "done", "tool_calls": None})()})()]})()

    brain = GroqBrain(config, None)
    brain._OpenAI = FakeClient
    brain._client = brain._make_client()

    assert brain.complete("hello") == "done"
    assert used == ["gsk_one", "gsk_two", "gsk_three"], "each 429 should advance the key"
    assert brain.rotations == 2


def test_offline_brain_supports_vision_and_audio(tmp_path):
    """Feature code calling ctx.see/ctx.transcribe must work offline too."""
    brain = OfflineBrain()
    image = tmp_path / "x.png"
    image.write_bytes(b"")
    assert "x.png" in brain.see(image, "what is this?")
    assert "x.png" in brain.transcribe(image)


# -- screen ---------------------------------------------------------------

def test_capture_rejects_unknown_region(agent, feature):
    feature("screen")
    result = agent.call_tool("screen.capture", {"region": "eyeball"})
    assert result.status is Status.ERROR
    assert "region" in result.error


def test_wayland_without_a_backend_explains_itself(agent, feature, monkeypatch):
    """mss silently returns black on Wayland, so we must not fall back to it."""
    mod = feature("screen")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    result = agent.call_tool("screen.capture", {"region": "full"})
    assert result.status is Status.ERROR
    assert "spectacle" in result.error or "grim" in result.error


def test_backend_that_writes_nothing_is_an_error(agent, feature, monkeypatch):
    """Some helpers exit 0 but write no file when the compositor refuses."""
    import subprocess

    mod = feature("screen")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, b"", b"refused"),
    )

    result = agent.call_tool("screen.capture", {"region": "full"})
    assert result.status is Status.ERROR
    assert "no image" in result.error
