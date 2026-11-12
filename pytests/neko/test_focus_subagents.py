"""Focus monitor projection, using synthetic dispatch and the real bounded SQLite ledger."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import pytest
from sqlmodel import Session, create_engine

from src.common.database.database_model import MaisakaMonitorEventRecord
from src.core.tooling import ToolInvocation, ToolSpec
from src.maisaka.builtin_tool import subagent
from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext
from src.maisaka.monitor import event_store, events
from src.maisaka.subagent_tasks import SubAgentTaskManager


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'monitor.db').as_posix()}")
    MaisakaMonitorEventRecord.__table__.create(engine)

    @contextmanager
    def session_scope(*, auto_commit=True):
        with Session(engine) as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr(event_store, "get_db_session", session_scope)
    monkeypatch.setattr(event_store, "_should_cleanup_monitor_events", lambda: False)
    yield event_store.record_monitor_event
    engine.dispose()


def start(ledger, member, call, task="subagent-2", **extra):
    return ledger(
        "subagent.started",
        {
            "session_id": member,
            "task_id": task,
            "call_id": call,
            "timestamp": 10,
            **extra,
        },
    )


def planner(ledger, group, member, call):
    return ledger(
        "planner.finalized",
        {
            "session_id": group,
            "run_id": "synthetic",
            "timestamp": 11,
            "tools": [{"tool_name": "subagent", "session_id": member, "tool_call_id": call}],
        },
    )


def history(group):
    return event_store.load_session_subagent_events(session_id=group, before_event_id=1)


def test_legacy_join_is_member_call_exact_and_keeps_restart_boundaries(ledger):
    a = start(ledger, "a", "call-a")
    b = start(ledger, "b", "call-b")
    start(ledger, "a", "unrelated", task="subagent-3")
    start(ledger, "other", "call-a")  # Same call ID, different real member.
    planner(ledger, "focus:group:old", "a", "call-a")
    planner(ledger, "focus:group:old", "b", "call-b")
    result = ledger("subagent.finished", {"session_id": "b", "task_id": "subagent-2", "status": "failed"})
    restart = ledger("session.start", {"session_id": "a", "timestamp": 20})
    start(ledger, "a", "after-restart", focus_scope_id="group:new")
    ledger("subagent.finished", {"session_id": "a", "task_id": "subagent-2", "status": "completed"})
    selected = history("focus:group:old")
    assert [event["data"]["event_id"] for event in selected] == [
        a["event_id"],
        b["event_id"],
        result["event_id"],
        restart["event_id"],
    ]
    assert [event["data"]["session_id"] for event in selected] == ["a", "b", "b", "a"]
    assert selected[0]["data"]["focus_scope_id"] == "group:old"
    # Read-only enrichment: stored member history retains the original legacy payload.
    raw = event_store.load_session_subagent_events(session_id="a", before_event_id=1000)
    assert "focus_scope_id" not in raw[0]["data"]
    assert len(history("focus:group:new")) == 2


def test_explicit_dispatch_wins_and_ambiguous_or_missing_legacy_evidence_is_not_guessed(ledger):
    explicit = start(ledger, "a", "explicit", focus_scope_id="group:old")
    start(ledger, "b", "ordinary", focus_scope_id="")
    start(ledger, "c", "ambiguous")
    start(ledger, "d", "reused")
    ledger("session.start", {"session_id": "d"})
    start(ledger, "d", "reused")
    start(ledger, "e", "no-card")
    planner(ledger, "focus:group:new", "f", "card-before-start")
    start(ledger, "f", "card-before-start")  # A retained older tool card cannot own a newer dispatch.
    for member, call in [("a", "explicit"), ("b", "ordinary"), ("c", "ambiguous"), ("d", "reused")]:
        planner(ledger, "focus:group:new", member, call)
    planner(ledger, "focus:group:old", "c", "ambiguous")
    assert [event["data"]["event_id"] for event in history("focus:group:old")] == [explicit["event_id"]]
    assert history("focus:group:new") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stopped", "completed", "failed", "ordinary"])
async def test_real_dispatch_keeps_member_execution_and_captured_scope(ledger, monkeypatch, outcome):
    async def broadcast(kind, payload):
        ledger(kind, payload)

    monkeypatch.setattr(events, "_broadcast", broadcast)
    pending = asyncio.Event()
    release = asyncio.Event()
    runtime = SimpleNamespace(
        session_id="member",
        log_prefix="synthetic",
        _focus_group=SimpleNamespace(scope_id="group:current"),
        _tool_registry=SimpleNamespace(
            list_tools=AsyncMock(return_value=[ToolSpec(name="search", description="test")])
        ),
        _subagent_tasks=SubAgentTaskManager(),
        deliver_subagent_result=lambda task: None,
    )

    async def model(**kwargs):
        pending.set()
        await release.wait()
        if outcome == "failed":
            raise ValueError("synthetic failure")
        return SimpleNamespace(content="synthetic conclusion", tool_calls=[])

    runtime.run_sub_agent = model
    engine = SimpleNamespace(
        _build_tool_availability_context=lambda: None, _build_tool_execution_context=lambda task: None
    )
    ctx = BuiltinToolRuntimeContext(engine, runtime)
    invocation = ToolInvocation(
        tool_name="subagent",
        arguments={"task": "synthetic", "tools": ["search"]},
        call_id="dispatch",
        metadata={} if outcome == "ordinary" else {"focus_scope_id": "group:dispatch"},
    )
    result = await subagent.handle_tool(ctx, invocation)
    assert result.success
    await pending.wait()
    runtime._focus_group = SimpleNamespace(scope_id="group:moved")
    if outcome == "stopped":
        stopped = await subagent.handle_stop_tool(
            ctx, ToolInvocation(tool_name="subagent_stop", arguments={"task_id": "subagent-1"})
        )
        assert stopped.success
        await runtime._subagent_tasks.cancel_all()
    else:
        release.set()
        await runtime._subagent_tasks.get("subagent-1").asyncio_task
    selected = (
        event_store.load_session_subagent_events(session_id="member", before_event_id=1000)
        if outcome == "ordinary"
        else history("focus:group:dispatch")
    )
    assert selected[0]["event"] == "subagent.started"
    assert selected[-1]["event"] == "subagent.finished"
    if outcome in {"completed", "ordinary"}:
        assert selected[1]["event"] == "subagent.round"
    assert all(event["data"]["session_id"] == "member" for event in selected)
    assert selected[-1]["data"]["status"] == ("completed" if outcome == "ordinary" else outcome)
    assert history("focus:group:moved") == []
    if outcome == "ordinary":
        assert selected[0]["data"]["focus_scope_id"] == ""
        assert history("focus:group:dispatch") == []


@pytest.mark.asyncio
async def test_focus_history_supplement_runs_even_with_no_older_group_page(ledger, monkeypatch):
    from src.webui.routers.websocket import unified

    start(ledger, "member", "dispatch", focus_scope_id="group:only")
    send_event = AsyncMock()
    monkeypatch.setattr(unified.websocket_manager, "send_event", send_event)
    monkeypatch.setattr(unified.websocket_manager, "send_response", AsyncMock())
    await unified._handle_maisaka_monitor_call(
        "synthetic",
        {
            "id": "request",
            "method": "history.load",
            "data": {"session_id": "focus:group:only"},
        },
    )
    supplement = send_event.call_args.kwargs
    assert supplement["event"] == "subagent.history"
    assert supplement["data"]["events"][0]["data"]["session_id"] == "member"
