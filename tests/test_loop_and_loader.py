from servant.brain import OfflineBrain
from servant.contracts import Finish, Status, Tier, ToolCall
from servant.loader import load_features
from servant.sdk import tool


def test_loop_runs_tools_then_finishes(config):
    from servant.agent import Agent
    from servant.governance import AutoApprover

    calls = []

    @tool(name="t.step", tier=Tier.READ, params={"n": "which step"})
    def t_step(ctx, n: int) -> str:
        """Do a step."""
        calls.append(n)
        return f"did {n}"

    brain = OfflineBrain([
        ToolCall(tool="t.step", args={"n": 1}, confidence=0.9),
        ToolCall(tool="t.step", args={"n": 2}, confidence=0.9),
        Finish("all done"),
    ])
    agent = Agent(config, approver=AutoApprover(announce=False), brain=brain, quiet=True)

    result = agent.run("do two steps")
    assert calls == [1, 2]
    assert result.answer == "all done"
    assert result.steps == 2


def test_loop_stops_when_the_human_declines(agent):
    @tool(name="t.danger", tier=Tier.DANGER)
    def t_danger(ctx) -> str:
        """Irreversible."""
        return "nope"

    agent.brain = OfflineBrain([ToolCall(tool="t.danger", args={}), Finish("unreachable")])
    result = agent.run("delete everything")

    assert result.actions[-1].status is Status.DENIED
    assert "declined" in result.answer


def test_loop_halts_on_killswitch(agent):
    @tool(name="t.read", tier=Tier.READ)
    def t_read(ctx) -> str:
        """Read."""
        return "ok"

    agent.brain = OfflineBrain([ToolCall(tool="t.read", args={})] * 5)
    agent.killswitch.engage("operator hit stop")

    result = agent.run("loop forever")
    assert result.stopped is True
    assert result.steps == 0


def test_loader_reports_a_broken_feature_without_dying(tmp_path):
    features = tmp_path / "features"
    features.mkdir()
    (features / "__init__.py").write_text("")
    (features / "good.py").write_text(
        "from servant.sdk import tool, Tier\n"
        "@tool(name='good.ping', tier=Tier.READ)\n"
        "def ping(ctx) -> str:\n"
        "    '''Ping.'''\n"
        "    return 'pong'\n"
    )
    (features / "broken.py").write_text("import a_module_that_does_not_exist\n")

    report = load_features(features)
    assert "features.good" in report.loaded
    assert any("broken" in name for name in report.failed)
    assert report.tools_added == 1
