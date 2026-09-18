"""Memory recall/remember/forget, audit introspection, budget status, and ask.question."""

from servant.contracts import Status, Tier


# -- memory -----------------------------------------------------------------

def test_remember_and_recall_roundtrip(agent, feature):
    feature("memory_tools")
    agent.call_tool("memory.remember", {"key": "project.name", "value": "ai-servant"})

    result = agent.call_tool("memory.recall", {"query": "ai-servant"})
    assert result.status is Status.OK
    assert "project.name" in result.output
    assert "ai-servant" in result.output


def test_recall_with_no_query_lists_everything(agent, feature):
    feature("memory_tools")
    agent.call_tool("memory.remember", {"key": "a", "value": "first"})
    agent.call_tool("memory.remember", {"key": "b", "value": "second"})

    result = agent.call_tool("memory.recall", {})
    assert "first" in result.output and "second" in result.output


def test_recall_finds_nothing_gracefully(agent, feature):
    feature("memory_tools")
    result = agent.call_tool("memory.recall", {"query": "nonexistent"})
    assert result.status is Status.OK
    assert "nothing remembered" in result.output


def test_forget_removes_it(agent, feature):
    feature("memory_tools")
    agent.call_tool("memory.remember", {"key": "temp", "value": "x"})
    assert agent.call_tool("memory.forget", {"key": "temp"}).status is Status.OK
    assert "nothing remembered" in agent.call_tool("memory.recall", {"query": "temp"}).output


def test_forget_unknown_key_errors(agent, feature):
    feature("memory_tools")
    result = agent.call_tool("memory.forget", {"key": "never-existed"})
    assert result.status is Status.ERROR


def test_memory_facts_are_namespaced_away_from_internal_state(agent, feature):
    """memory.recall must not surface plumbing like watch.monitors."""
    feature("memory_tools")
    agent.memory.remember("watch.monitors", [{"id": "x"}])
    agent.call_tool("memory.remember", {"key": "note", "value": "hello there"})

    result = agent.call_tool("memory.recall", {})
    assert "note" in result.output
    assert "watch.monitors" not in result.output


def test_remember_tier_is_write_not_danger(feature):
    from servant.registry import REGISTRY

    feature("memory_tools")
    assert REGISTRY.get("memory.remember").tier is Tier.WRITE
    assert REGISTRY.get("memory.forget").tier is Tier.WRITE
    assert REGISTRY.get("memory.recall").tier is Tier.READ


# -- introspection ------------------------------------------------------------

def test_agent_history_reads_the_real_audit_log(agent, feature):
    feature("introspect")
    feature("memory_tools")
    agent.call_tool("memory.remember", {"key": "x", "value": "y"})  # generates audit entries

    result = agent.call_tool("agent.history", {"minutes": 60})
    assert result.status is Status.OK
    assert "memory.remember" in result.output


def test_agent_history_filters_by_kind(agent, feature):
    feature("introspect")
    feature("memory_tools")
    agent.call_tool("memory.remember", {"key": "x", "value": "y"})

    only_gates = agent.call_tool("agent.history", {"minutes": 60, "kind": "gate"})
    assert "gate" in only_gates.output or "entr" in only_gates.output


def test_agent_history_empty_window(agent, feature):
    feature("introspect")
    result = agent.call_tool("agent.history", {"minutes": 1})
    assert result.status is Status.OK


def test_agent_explain_finds_the_relevant_gate_decision(agent, feature):
    feature("introspect")
    feature("memory_tools")
    agent.call_tool("memory.remember", {"key": "x", "value": "y"})

    result = agent.call_tool("agent.explain", {"about": "memory.remember"})
    assert result.status is Status.OK
    assert "memory.remember" in result.output


def test_agent_explain_empty_query_errors(agent, feature):
    feature("introspect")
    result = agent.call_tool("agent.explain", {"about": "  "})
    assert result.status is Status.ERROR


def test_agent_explain_nothing_found(agent, feature):
    feature("introspect")
    result = agent.call_tool("agent.explain", {"about": "totally-unmentioned-xyz"})
    assert result.status is Status.OK
    assert "nothing" in result.output.lower()


def test_budget_status_reports_current_run(agent, feature):
    feature("introspect")
    result = agent.call_tool("budget.status", {})
    assert result.status is Status.OK
    assert "steps:" in result.output


def test_introspection_tools_are_all_read_tier(feature):
    from servant.registry import REGISTRY

    feature("introspect")
    for name in ("agent.history", "agent.explain", "budget.status"):
        assert REGISTRY.get(name).tier is Tier.READ


# -- ask.question -------------------------------------------------------------

def test_ask_question_returns_the_answer(agent, feature):
    """The approver's ask_open is what actually collects the text."""
    feature("ask")

    class YesTextApprover:
        def request(self, req): raise NotImplementedError
        def confirm(self, q): return True
        def ask_open(self, q): return "use pytest, not unittest"

    agent.approver = YesTextApprover()
    agent.executor.approver = YesTextApprover()  # the executor holds its own reference
    result = agent.call_tool("ask.question", {"question": "which test framework?"})
    assert result.status is Status.OK
    assert "pytest" in result.output


def test_ask_question_fails_clearly_with_nobody_to_answer(agent, feature):
    """DenyAllApprover's ask_open returns None -- must not silently return ''."""
    result = agent.call_tool("ask.question", {"question": "anything?"})
    feature("ask")
    result = agent.call_tool("ask.question", {"question": "anything?"})
    assert result.status is Status.ERROR
    assert "nobody was there" in result.error


def test_ask_question_is_read_tier(feature):
    from servant.registry import REGISTRY

    feature("ask")
    assert REGISTRY.get("ask.question").tier is Tier.READ


def test_ask_question_rejects_empty(agent, feature):
    feature("ask")
    result = agent.call_tool("ask.question", {"question": "   "})
    assert result.status is Status.ERROR


# -- calendar (scaffolding only -- no OAuth in a test run) -------------------

def test_calendar_fails_with_clear_setup_instructions_when_unconfigured(agent, feature):
    feature("calendar_tools")
    result = agent.call_tool("calendar.list", {})
    assert result.status is Status.ERROR
    assert "calendar_setup" in result.error


def test_calendar_tiers(feature):
    from servant.registry import REGISTRY

    feature("calendar_tools")
    assert REGISTRY.get("calendar.list").tier is Tier.READ
    assert REGISTRY.get("calendar.list").untrusted is True
    assert REGISTRY.get("calendar.create").tier is Tier.DANGER


def test_calendar_create_rejects_bad_datetime(agent, feature, monkeypatch):
    feature("calendar_tools")
    import features.calendar_tools as cal
    (agent.config.path("state_dir") / "calendar_token.json").parent.mkdir(parents=True, exist_ok=True)
    (agent.config.path("state_dir") / "calendar_token.json").write_text("{}")
    monkeypatch.setattr(cal, "_service", lambda ctx: None)

    from servant.governance import AutoApprover
    agent.approver = AutoApprover(announce=False)
    agent.executor.approver = AutoApprover(announce=False)

    result = agent.call_tool("calendar.create", {"summary": "x", "start": "not-a-date"})
    assert result.status is Status.ERROR
    assert "ISO format" in result.error
