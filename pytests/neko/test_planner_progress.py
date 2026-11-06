import asyncio

import pytest

from src.llm_models.request_progress import observe_planner, report_progress


@pytest.mark.asyncio
async def test_concurrent_requests_and_inherited_context_cleanup():
    events = [[], []]
    release = asyncio.Event()
    children = []

    async def request(index):
        async def late():
            await release.wait()
            report_progress("maisaka.planner", "late", "stale")

        children.append(asyncio.create_task(late()))
        report_progress("maisaka.planner", "请求中", str(index))
        await asyncio.sleep(0)
        report_progress("maisaka.context_compaction", "ignored", "summary")
        report_progress("maisaka.planner", "切换候选", str(index))
        return index

    result = await asyncio.gather(
        *[observe_planner(request(i), lambda phase, model, i=i: events[i].append((phase, model))) for i in range(2)]
    )
    release.set()
    await asyncio.gather(*children)
    assert result == [0, 1]
    for i in range(2):
        assert events[i] == [("请求中", str(i)), ("切换候选", str(i)), ("响应已返回", "")]


@pytest.mark.asyncio
async def test_cancellation_and_observer_failure_preserve_execution():
    events = []
    entered = asyncio.Event()

    async def pending():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(observe_planner(pending(), lambda phase, model: events.append(phase)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["已取消"]
    report_progress("maisaka.planner", "late", "")
    assert events == ["已取消"]

    def broken(*args):
        raise RuntimeError("display broken")

    async def success():
        report_progress("maisaka.planner", "请求中", "model")
        return 42

    assert await observe_planner(success(), broken) == 42


@pytest.mark.asyncio
async def test_failure_reports_terminal_state_and_preserves_error():
    events = []

    async def failure():
        raise ValueError("original")

    with pytest.raises(ValueError, match="original"):
        await observe_planner(failure(), lambda phase, model: events.append(phase))
    assert events == ["请求失败"]
