from servant.contracts import Tier
from servant.governance import AUTO, APPROVE, BLOCK, KillSwitch, PolicyEngine, Redactor, Stopped
from servant.registry import REGISTRY
from servant.sdk import tool

import pytest


def _spec(name, tier):
    @tool(name=name, tier=tier)
    def f(ctx) -> str:
        """A tool."""

    return REGISTRY.get(name)


def test_tiers_map_to_actions(config):
    policy = PolicyEngine(config)
    assert policy.evaluate(_spec("a.read", Tier.READ)).action == AUTO
    assert policy.evaluate(_spec("a.write", Tier.WRITE)).action == AUTO
    assert policy.evaluate(_spec("a.danger", Tier.DANGER)).action == APPROVE
    assert policy.evaluate(_spec("a.never", Tier.FORBIDDEN)).action == BLOCK


def test_low_confidence_escalates_to_approval(config):
    policy = PolicyEngine(config)
    spec = _spec("a.read", Tier.READ)
    assert policy.evaluate(spec, confidence=0.9).action == AUTO
    assert policy.evaluate(spec, confidence=0.1).action == APPROVE


def test_owner_override_beats_author(config):
    config.data["governance"]["tier_overrides"] = {"a.read": "danger"}
    policy = PolicyEngine(config)
    assert policy.evaluate(_spec("a.read", Tier.READ)).action == APPROVE


def test_denylist_blocks_everything(config):
    config.data["governance"]["denylist"] = ["a.read"]
    policy = PolicyEngine(config)
    assert policy.evaluate(_spec("a.read", Tier.READ)).action == BLOCK


def test_redactor_masks_secrets(config):
    r = Redactor.from_config(config)
    assert "sk-" not in r.scrub("key=sk-abcdefghijklmnopqrstuvwx")
    assert r.scrub({"a": ["AKIA" + "A" * 16]})["a"][0] == "[REDACTED]"
    assert r.scrub("nothing to see") == "nothing to see"


def test_redactor_guards_forbidden_paths(config):
    r = Redactor.from_config(config)
    assert r.path_is_forbidden("~/.ssh/id_rsa")
    assert not r.path_is_forbidden("/tmp")


def test_killswitch_engages_and_releases(tmp_path):
    ks = KillSwitch(tmp_path / "STOP")
    assert not ks.engaged
    ks.check()

    ks.engage("test")
    assert ks.engaged
    with pytest.raises(Stopped):
        ks.check()

    ks.release()
    assert not ks.engaged


def test_killswitch_reads_flag_file_written_externally(tmp_path):
    """Another process (the CLI, a hotkey) can stop a running agent."""
    ks = KillSwitch(tmp_path / "STOP")
    (tmp_path / "STOP").write_text("from elsewhere")
    with pytest.raises(Stopped, match="from elsewhere"):
        ks.check()


def test_console_approver_denies_when_nobody_can_answer(monkeypatch):
    """A piped or cron run must fail closed, not crash."""
    from servant.governance import ApprovalRequest, ConsoleApprover

    def no_stdin(prompt=""):  # noqa: ARG001
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)
    approver = ConsoleApprover()

    decision = approver.request(ApprovalRequest(tool="t.danger", tier="danger", args={}))
    assert decision.approved is False
    assert approver.confirm("really?") is False
