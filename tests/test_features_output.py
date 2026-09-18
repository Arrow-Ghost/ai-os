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


# -- search ---------------------------------------------------------------

DDG_FIXTURE = """
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fconsole.groq.com%2Fdocs%2Frate-limits">
    Rate Limits - GroqDocs</a>
  <a class="result__snippet">Groq enforces per-minute request limits.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://example.com/direct">Direct Link Result</a>
  <a class="result__snippet">A result whose href is not wrapped.</a>
</div>
"""


def test_ddg_parser_extracts_and_unwraps_urls(feature):
    mod = feature("web")
    parser = mod._DuckDuckGoParser()
    parser.feed(DDG_FIXTURE)

    assert len(parser.results) == 2
    first = parser.results[0]
    assert "GroqDocs" in first["title"]
    assert first["url"] == "https://console.groq.com/docs/rate-limits", "uddg= must be unwrapped"
    assert "per-minute" in first["snippet"]
    assert parser.results[1]["url"] == "https://example.com/direct", "plain hrefs pass through"


def test_ddg_block_page_is_an_explicit_error(agent, feature, monkeypatch):
    """A landing page with no results must not be reported as 'no results'."""
    mod = feature("web")

    class Blocked:
        status, headers = 200, {"Content-Type": "text/html"}

        def read(self, _=None):
            return b"<html><body><h1>DuckDuckGo</h1></body></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Blocked())
    result = agent.call_tool("web.search", {"query": "anything", "backend": "duckduckgo"})

    assert result.status is Status.ERROR
    assert "blocks datacenter IPs" in result.error or "no results in it" in result.error


def test_google_backend_requires_both_key_and_cse_id(agent, feature, monkeypatch):
    """The API key alone cannot search -- it needs a search engine id."""
    feature("web")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-fake")
    monkeypatch.delenv("GOOGLE_CSE_ID", raising=False)

    result = agent.call_tool("web.search", {"query": "x", "backend": "google"})
    assert result.status is Status.ERROR
    assert "GOOGLE_CSE_ID" in result.error


def test_google_backend_parses_results(agent, feature, monkeypatch):
    import json

    mod = feature("web")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-fake")
    monkeypatch.setenv("GOOGLE_CSE_ID", "cse-fake")

    payload = json.dumps({"items": [
        {"title": "Groq Rate Limits", "link": "https://console.groq.com/docs/rate-limits",
         "snippet": "Requests per minute."},
    ]}).encode()

    class FakeResponse:
        def read(self, _=None):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    result = agent.call_tool("web.search", {"query": "groq limits", "backend": "google"})

    assert result.status is Status.OK
    assert "console.groq.com" in result.output
    assert "via google" in result.output


def test_auto_backend_prefers_google_when_configured(agent, feature, monkeypatch):
    mod = feature("web")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-fake")
    monkeypatch.setenv("GOOGLE_CSE_ID", "cse-fake")
    called = {}

    def fake_google(ctx, query, n):  # noqa: ARG001
        called["google"] = True
        return [{"title": "t", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(mod, "_search_google", fake_google)
    agent.call_tool("web.search", {"query": "x"})
    assert called.get("google") is True
