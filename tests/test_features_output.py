"""Track B features: notifications, clipboard, web. All offline."""

import subprocess

import pytest

from servant.contracts import Status, Tier


# -- notify ---------------------------------------------------------------

def test_notify_sends_and_scrubs(agent, feature, monkeypatch):
    mod = feature("notify")
    captured = {}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/notify-send")

    result = agent.call_tool(
        "notify.send", {"title": "key leak", "body": "token=sk-abcdefghijklmnopqrst"}
    )
    assert result.status is Status.OK
    assert "sk-abcdefghij" not in " ".join(captured["cmd"]), "secret reached the lock screen"


def test_notify_rejects_bad_urgency(agent, feature, monkeypatch):
    mod = feature("notify")
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/notify-send")

    result = agent.call_tool("notify.send", {"title": "t", "urgency": "screaming"})
    assert result.status is Status.ERROR
    assert "urgency" in result.error


def test_notify_reports_missing_binary(agent, feature, monkeypatch):
    mod = feature("notify")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    result = agent.call_tool("notify.send", {"title": "t"})
    assert result.status is Status.ERROR
    assert "notify-send" in result.error


# -- clipboard ------------------------------------------------------------

def test_clipboard_read_scrubs(agent, feature, monkeypatch):
    mod = feature("clipboard")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"password=hunter2secret", b""),
    )

    result = agent.call_tool("clipboard.read", {})
    assert result.status is Status.OK
    assert "hunter2secret" not in result.output


def test_clipboard_write_never_captures_output(agent, feature, monkeypatch):
    """wl-copy forks a daemon; capturing its pipes makes a success look like a hang."""
    mod = feature("clipboard")
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    agent.call_tool("clipboard.write", {"text": "hello"})

    assert seen.get("stdout") == subprocess.DEVNULL
    assert seen.get("stderr") == subprocess.DEVNULL
    assert "capture_output" not in seen


def test_clipboard_errors_without_a_backend(agent, feature, monkeypatch):
    mod = feature("clipboard")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    result = agent.call_tool("clipboard.read", {})
    assert result.status is Status.ERROR
    assert "clipboard tool" in result.error


# -- web ------------------------------------------------------------------

def test_web_fetch_rejects_non_http(agent, feature):
    feature("web")
    result = agent.call_tool("web.fetch", {"url": "file:///etc/passwd"})
    assert result.status is Status.ERROR
    assert "http" in result.error


def test_html_to_text_drops_scripts_and_keeps_structure(feature):
    mod = feature("web")
    parser = mod._TextExtractor()
    parser.feed(
        "<html><head><title>Docs</title><style>p{color:red}</style></head>"
        "<body><h1>Install</h1><script>evil()</script><p>Run pip install.</p>"
        "<p>Then restart.</p></body></html>"
    )
    text = parser.text()

    assert parser.title == "Docs"
    assert "evil()" not in text and "color:red" not in text
    assert "Install" in text and "Run pip install." in text
    assert text.count("\n") >= 2, "block elements should produce line breaks"


def test_web_fetch_scrubs_page_contents(agent, feature, monkeypatch):
    """A page with a key in a code sample must not hand it to the LLM."""
    mod = feature("web")

    class FakeResponse:
        status, headers = 200, {"Content-Type": "text/html; charset=utf-8"}

        def read(self, _=None):
            return b"<html><body><p>export GROQ_API_KEY=gsk_aaaaaaaaaaaaaaaaaaaa</p></body></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: FakeResponse())

    result = agent.call_tool("web.fetch", {"url": "https://example.com"})
    assert result.status is Status.OK
    assert "gsk_aaaaaaaa" not in result.output


# -- tiers ----------------------------------------------------------------

@pytest.mark.parametrize(
    "module,tool_name,expected",
    [
        ("notify", "notify.send", Tier.WRITE),
        ("clipboard", "clipboard.read", Tier.READ),
        ("clipboard", "clipboard.write", Tier.WRITE),
        ("web", "web.fetch", Tier.READ),
        ("screen", "screen.capture", Tier.READ),
        ("screen", "screen.describe", Tier.DANGER),
        ("screen", "screen.read_text", Tier.DANGER),
        ("speech", "speech.record", Tier.DANGER),
        ("speech", "speech.transcribe", Tier.DANGER),
    ],
)
def test_declared_tiers(feature, module, tool_name, expected):
    """Anything that leaves the machine unredactable, or opens a sensor, must ask."""
    from servant.registry import REGISTRY

    feature(module)
    assert REGISTRY.get(tool_name).tier is expected
