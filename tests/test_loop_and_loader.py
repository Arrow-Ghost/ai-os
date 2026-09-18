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
    features = tmp_path / "features"  # same name as the real one, on purpose
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
    assert "features.example_files" not in report.loaded, (
        "a cached package of the same name must not shadow the requested directory"
    )


# -- loop detection ---------------------------------------------------------

def test_identical_repeated_calls_are_stopped(config):
    """A weak model stuck calling the same tool with the same args must not
    burn the whole turn budget -- seen live with a small local model looping
    on shell.which('ls') seven times."""
    from servant.agent import Agent
    from servant.governance import AutoApprover
    from servant.sdk import tool, Tier

    @tool(name="t.stuck", tier=Tier.READ, params={"n": "arg"})
    def t_stuck(ctx, n: int) -> str:
        """Always the same result."""
        return "no progress"

    # Same call, forever, if nothing stops it.
    brain = OfflineBrain([ToolCall(tool="t.stuck", args={"n": 1})] * 10)
    agent = Agent(config, approver=AutoApprover(announce=False), brain=brain, quiet=True)

    result = agent.run("do the stuck thing")
    assert result.steps == 2, "must stop after 2 identical calls, not exhaust the budget"
    assert "identical arguments" in result.answer


def test_different_arguments_do_not_trigger_loop_detection(config):
    """Calling the same TOOL repeatedly with genuinely different args is fine."""
    from servant.agent import Agent
    from servant.governance import AutoApprover
    from servant.sdk import tool, Tier

    @tool(name="t.counter", tier=Tier.READ, params={"n": "arg"})
    def t_counter(ctx, n: int) -> str:
        """Different each time."""
        return f"got {n}"

    brain = OfflineBrain([
        ToolCall(tool="t.counter", args={"n": 1}),
        ToolCall(tool="t.counter", args={"n": 2}),
        ToolCall(tool="t.counter", args={"n": 3}),
        Finish("done"),
    ])
    agent = Agent(config, approver=AutoApprover(announce=False), brain=brain, quiet=True)

    result = agent.run("count up")
    assert result.steps == 3
    assert result.answer == "done"


def test_a_single_repeat_does_not_trigger_anything(config):
    """Two calls in a row is not a loop -- only three identical calls are."""
    from servant.agent import Agent
    from servant.governance import AutoApprover
    from servant.sdk import tool, Tier

    calls = []

    @tool(name="t.retry", tier=Tier.READ)
    def t_retry(ctx) -> str:
        """Called twice on purpose, then something else."""
        calls.append(1)
        return "ok"

    brain = OfflineBrain([
        ToolCall(tool="t.retry", args={}),
        ToolCall(tool="t.retry", args={}),
        Finish("done after two"),
    ])
    agent = Agent(config, approver=AutoApprover(announce=False), brain=brain, quiet=True)

    result = agent.run("try twice")
    assert len(calls) == 2
    assert result.answer == "done after two"


def test_recovering_after_the_warning_resets_the_streak(config):
    """If the model heeds the warning and changes course, it must not still
    be penalised for the earlier repeat."""
    from servant.agent import Agent
    from servant.governance import AutoApprover
    from servant.sdk import tool, Tier

    @tool(name="t.a", tier=Tier.READ)
    def t_a(ctx) -> str:
        """A."""
        return "a"

    @tool(name="t.b", tier=Tier.READ)
    def t_b(ctx) -> str:
        """B."""
        return "b"

    brain = OfflineBrain([
        ToolCall(tool="t.a", args={}),
        ToolCall(tool="t.a", args={}),  # 2nd identical -> warning fires
        ToolCall(tool="t.b", args={}),  # model changes course
        Finish("recovered"),
    ])
    agent = Agent(config, approver=AutoApprover(announce=False), brain=brain, quiet=True)

    result = agent.run("try a then recover")
    assert result.steps == 3
    assert result.answer == "recovered"
