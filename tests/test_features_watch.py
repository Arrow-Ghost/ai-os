"""Monitors and the background runner. No network, no LLM."""

import urllib.error

from servant.contracts import Status


# -- file -----------------------------------------------------------------

def test_file_baseline_then_change(agent, feature, tmp_path):
    feature("watch")
    target = tmp_path / "build.txt"
    target.write_text("one")

    first = agent.call_tool("watch.check_file", {"path": str(target)})
    assert first.output.startswith("UNCHANGED"), "first check records a baseline"

    second = agent.call_tool("watch.check_file", {"path": str(target)})
    assert second.output.startswith("UNCHANGED")

    target.write_text("one and two")
    third = agent.call_tool("watch.check_file", {"path": str(target)})
    assert third.output.startswith("CHANGED")
    assert "+8 bytes" in third.output


# -- log ------------------------------------------------------------------

def test_log_only_reads_new_lines(agent, feature, tmp_path):
    feature("watch")
    log = tmp_path / "app.log"
    log.write_text("INFO start\nINFO working\n")

    first = agent.call_tool("watch.check_log", {"path": str(log), "pattern": "ERROR"})
    assert first.output.startswith("UNCHANGED")

    log.write_text(log.read_text() + "ERROR boom\n")
    second = agent.call_tool("watch.check_log", {"path": str(log), "pattern": "ERROR"})
    assert second.output.startswith("FIRED")
    assert "ERROR boom" in second.output
    assert "INFO start" not in second.output, "old lines must not be re-reported"

    third = agent.call_tool("watch.check_log", {"path": str(log), "pattern": "ERROR"})
    assert third.output.startswith("UNCHANGED"), "the same error must not fire twice"


def test_log_handles_rotation(agent, feature, tmp_path):
    """A truncated file must not seek past its end and go silent forever."""
    feature("watch")
    log = tmp_path / "app.log"
    log.write_text("INFO " + "x" * 500 + "\n")
    agent.call_tool("watch.check_log", {"path": str(log)})

    log.write_text("ERROR after rotation\n")      # rotated: smaller than before
    result = agent.call_tool("watch.check_log", {"path": str(log), "pattern": "ERROR"})
    assert result.output.startswith("FIRED")


def test_log_scrubs_matched_lines(agent, feature, tmp_path):
    feature("watch")
    log = tmp_path / "app.log"
    log.write_text("")
    agent.call_tool("watch.check_log", {"path": str(log)})

    log.write_text("ERROR auth failed token=sk-abcdefghijklmnopqrst\n")
    result = agent.call_tool("watch.check_log", {"path": str(log), "pattern": "ERROR"})
    assert "sk-abcdefghij" not in result.output


# -- endpoint -------------------------------------------------------------

def _fake_urlopen(status):
    class FakeResponse:
        def __init__(self):
            self.status, self.reason = status, "OK"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda *a, **k: FakeResponse()


def test_endpoint_healthy_then_down(agent, feature, monkeypatch):
    mod = feature("watch")
    monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen(200))
    healthy = agent.call_tool("watch.check_endpoint", {"url": "https://x.test"})
    assert healthy.output.startswith("UNCHANGED")

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://x.test", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    down = agent.call_tool("watch.check_endpoint", {"url": "https://x.test"})
    assert down.output.startswith("FIRED")
    assert "503" in down.output


def test_endpoint_reports_recovery(agent, feature, monkeypatch):
    mod = feature("watch")

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://x.test", 500, "err", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    agent.call_tool("watch.check_endpoint", {"url": "https://x.test"})

    monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen(200))
    recovered = agent.call_tool("watch.check_endpoint", {"url": "https://x.test"})
    assert recovered.output.startswith("CHANGED")
    assert "recovered" in recovered.output


# -- the register ---------------------------------------------------------

def test_add_list_remove(agent, feature, tmp_path):
    feature("watch")
    added = agent.call_tool(
        "watch.add", {"kind": "file", "target": str(tmp_path), "interval": 30}
    )
    assert added.status is Status.OK
    monitor_id = added.output.split()[1]

    listed = agent.call_tool("watch.list", {})
    assert monitor_id in listed.output

    removed = agent.call_tool("watch.remove", {"monitor_id": monitor_id})
    assert removed.status is Status.OK
    assert "no monitors registered" in agent.call_tool("watch.list", {}).output


def test_add_rejects_bad_input(agent, feature, tmp_path):
    feature("watch")
    bad_kind = agent.call_tool("watch.add", {"kind": "telepathy", "target": "x"})
    assert bad_kind.status is Status.ERROR

    too_fast = agent.call_tool("watch.add", {"kind": "file", "target": str(tmp_path), "interval": 1})
    assert too_fast.status is Status.ERROR
    assert "at least 5 seconds" in too_fast.error


def test_remove_unknown_id_errors(agent, feature):
    feature("watch")
    result = agent.call_tool("watch.remove", {"monitor_id": "nope00"})
    assert result.status is Status.ERROR


# -- the runner -----------------------------------------------------------

def test_runner_fires_and_notifies(yes_agent, feature, tmp_path, monkeypatch):
    """A firing monitor must reach the human, and polls must stay budget-free."""
    feature("watch")
    notify = feature("notify")
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/notify-send")

    sent = []
    monkeypatch.setattr(
        notify.subprocess, "run",
        lambda cmd, **k: sent.append(cmd) or __import__("subprocess").CompletedProcess(cmd, 0, b"", b""),
    )

    log = tmp_path / "app.log"
    log.write_text("")
    yes_agent.call_tool("watch.add", {"kind": "log", "target": str(log), "interval": 5, "pattern": "ERROR"})

    from servant.watcher import run_once

    first = run_once(yes_agent, quiet=True)
    assert first.fires == 0

    log.write_text("ERROR something broke\n")
    second = run_once(yes_agent, quiet=True)
    assert second.fires == 1
    assert sent, "a firing monitor must produce a notification"


def test_runner_survives_more_polls_than_the_step_budget(yes_agent, feature, tmp_path):
    """A daemon must not stop itself after max_steps polls."""
    feature("watch")
    target = tmp_path / "f.txt"
    target.write_text("x")
    yes_agent.call_tool("watch.add", {"kind": "file", "target": str(target), "interval": 5})
    yes_agent.budget.max_steps = 2

    from servant.watcher import run_once

    for _ in range(5):
        stats = run_once(yes_agent, quiet=True)
        assert stats.errors == 0, "budget must be reset per tick, not per daemon lifetime"


def test_runner_stops_on_killswitch(yes_agent, feature, tmp_path):
    import pytest

    from servant.governance import Stopped

    feature("watch")
    target = tmp_path / "f.txt"
    target.write_text("x")
    yes_agent.call_tool("watch.add", {"kind": "file", "target": str(target), "interval": 5})
    yes_agent.killswitch.engage("operator stop")

    from servant.watcher import run_once

    with pytest.raises(Stopped):
        run_once(yes_agent, quiet=True)
