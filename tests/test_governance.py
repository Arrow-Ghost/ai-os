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


# -- full access mode -----------------------------------------------------

def test_full_access_runs_danger_without_asking(config, feature):
    """With danger->auto, an irreversible tool must run with no approver call."""
    from servant.agent import Agent
    from servant.brain import OfflineBrain
    from servant.contracts import Status
    from servant.governance import DenyAllApprover
    from servant.sdk import tool as tool_decorator

    config.data["governance"]["tier_policy"]["danger"] = "auto"
    ran = []

    @tool_decorator(name="t.danger", tier=Tier.DANGER)
    def t_danger(ctx) -> str:
        """Irreversible."""
        ran.append(True)
        return "ran"

    # DenyAllApprover would refuse if it were consulted at all.
    agent = Agent(config, approver=DenyAllApprover(), brain=OfflineBrain(), quiet=True)
    result = agent.call_tool("t.danger", {})

    assert result.status is Status.OK
    assert ran == [True]


def test_full_access_still_blocks_forbidden(config, feature):
    """FORBIDDEN is the one slot that survives full access."""
    from servant.agent import Agent
    from servant.brain import OfflineBrain
    from servant.contracts import Status
    from servant.governance import AutoApprover
    from servant.sdk import tool as tool_decorator

    config.data["governance"]["tier_policy"]["danger"] = "auto"

    @tool_decorator(name="t.never", tier=Tier.FORBIDDEN)
    def t_never(ctx) -> str:
        """Never."""
        return "should not happen"

    agent = Agent(config, approver=AutoApprover(announce=False), brain=OfflineBrain(), quiet=True)
    assert agent.call_tool("t.never", {}).status is Status.BLOCKED


def test_full_access_still_obeys_the_killswitch(config):
    """No mode may make the agent unstoppable."""
    from servant.agent import Agent
    from servant.brain import OfflineBrain
    from servant.governance import AutoApprover, Stopped
    from servant.sdk import tool as tool_decorator

    config.data["governance"]["tier_policy"]["danger"] = "auto"

    @tool_decorator(name="t.danger", tier=Tier.DANGER)
    def t_danger(ctx) -> str:
        """Irreversible."""
        return "ran"

    agent = Agent(config, approver=AutoApprover(announce=False), brain=OfflineBrain(), quiet=True)
    agent.killswitch.engage("stop means stop")

    with pytest.raises(Stopped):
        agent.call_tool("t.danger", {})


def test_tier_override_pins_a_tool_back_under_full_access(config):
    """You can still force one tool to ask, even with everything else open."""
    from servant.agent import Agent
    from servant.brain import OfflineBrain
    from servant.contracts import Status
    from servant.governance import DenyAllApprover
    from servant.sdk import tool as tool_decorator

    config.data["governance"]["tier_policy"]["danger"] = "auto"
    config.data["governance"]["tier_overrides"] = {"t.nuke": "forbidden"}

    @tool_decorator(name="t.nuke", tier=Tier.DANGER)
    def t_nuke(ctx) -> str:
        """Very irreversible."""
        return "boom"

    @tool_decorator(name="t.ok", tier=Tier.DANGER)
    def t_ok(ctx) -> str:
        """Ordinary danger."""
        return "fine"

    agent = Agent(config, approver=DenyAllApprover(), brain=OfflineBrain(), quiet=True)
    assert agent.call_tool("t.nuke", {}).status is Status.BLOCKED
    assert agent.call_tool("t.ok", {}).status is Status.OK


def test_config_does_not_share_state_with_the_defaults(tmp_path):
    """A shallow copy would let one Config rewrite the module-level defaults."""
    from servant.config import DEFAULT_POLICY, Config

    first, second = Config(root=tmp_path), Config(root=tmp_path)
    first.data["governance"]["tier_policy"]["danger"] = "auto"

    assert second.get("governance.tier_policy.danger") == "approve"
    assert DEFAULT_POLICY["governance"]["tier_policy"]["danger"] == "approve"
