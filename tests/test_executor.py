"""The executor is the chokepoint. These tests are the ones that must never break."""

from servant.contracts import Status, Tier
from servant.sdk import ToolError, tool


def test_read_tool_runs_without_asking(agent):
    @tool(name="t.read", tier=Tier.READ)
    def t_read(ctx) -> str:
        """Read something."""
        return "data"

    result = agent.call_tool("t.read", {})
    assert result.status is Status.OK
    assert result.output == "data"


def test_danger_tool_is_denied_without_a_human(agent):
    ran = []

    @tool(name="t.danger", tier=Tier.DANGER)
    def t_danger(ctx) -> str:
        """Do something irreversible."""
        ran.append(True)
        return "boom"

    result = agent.call_tool("t.danger", {})
    assert result.status is Status.DENIED
    assert ran == [], "the function must not execute when approval is denied"


def test_danger_tool_runs_when_approved(yes_agent):
    @tool(name="t.danger", tier=Tier.DANGER)
    def t_danger(ctx) -> str:
        """Do something irreversible."""
        return "approved"

    assert yes_agent.call_tool("t.danger", {}).status is Status.OK


def test_forbidden_tier_is_blocked_even_with_auto_approval(yes_agent):
    """--yes must not be able to unlock a forbidden tool."""

    @tool(name="t.never", tier=Tier.FORBIDDEN)
    def t_never(ctx) -> str:
        """Never allowed."""
        return "should not happen"

    result = yes_agent.call_tool("t.never", {})
    assert result.status is Status.BLOCKED


def test_feature_exception_is_contained(agent):
    @tool(name="t.boom", tier=Tier.READ)
    def t_boom(ctx) -> str:
        """Raise."""
        raise ToolError("the file was not there")

    result = agent.call_tool("t.boom", {})
    assert result.status is Status.ERROR
    assert "the file was not there" in result.error


def test_unknown_tool_suggests_a_close_match(agent):
    @tool(name="files.list", tier=Tier.READ)
    def files_list(ctx) -> str:
        """List."""

    result = agent.call_tool("files.lst", {})
    assert result.status is Status.UNKNOWN_TOOL
    assert "files.list" in result.error


def test_bad_arguments_are_rejected_before_execution(agent):
    ran = []

    @tool(name="t.args", tier=Tier.READ)
    def t_args(ctx, path: str) -> str:
        """Needs path."""
        ran.append(True)

    missing = agent.call_tool("t.args", {})
    unexpected = agent.call_tool("t.args", {"path": "/tmp", "nope": 1})

    assert missing.status is Status.ERROR and "missing" in missing.error
    assert unexpected.status is Status.ERROR and "unexpected" in unexpected.error
    assert ran == []


def test_step_budget_blocks_further_actions(agent):
    @tool(name="t.cheap", tier=Tier.READ)
    def t_cheap(ctx) -> str:
        """Cheap."""
        return "ok"

    agent.budget.max_steps = 2
    assert agent.call_tool("t.cheap", {}).status is Status.OK
    assert agent.call_tool("t.cheap", {}).status is Status.OK
    assert agent.call_tool("t.cheap", {}).status is Status.BLOCKED


def test_killswitch_stops_execution(agent):
    import pytest

    from servant.governance import Stopped

    @tool(name="t.read", tier=Tier.READ)
    def t_read(ctx) -> str:
        """Read."""
        return "data"

    agent.killswitch.engage("test halt")
    with pytest.raises(Stopped):
        agent.call_tool("t.read", {})


def test_every_action_is_audited(agent):
    @tool(name="t.read", tier=Tier.READ)
    def t_read(ctx) -> str:
        """Read."""
        return "data"

    agent.call_tool("t.read", {})
    kinds = [row["kind"] for row in agent.audit.read()]
    assert "gate" in kinds and "act" in kinds


def test_ctx_call_goes_through_governance(agent):
    """A tool calling another tool is still gated."""

    @tool(name="t.inner", tier=Tier.DANGER)
    def t_inner(ctx) -> str:
        """Dangerous inner tool."""
        return "inner ran"

    @tool(name="t.outer", tier=Tier.READ)
    def t_outer(ctx) -> str:
        """Calls the inner tool."""
        return ctx.call("t.inner").status.value

    result = agent.call_tool("t.outer", {})
    assert result.output == "denied"
