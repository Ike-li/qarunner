"""Tests that every fake satisfies its Protocol and behaves correctly."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qarunner.adapters.sqlite_store import SqliteStore
from qarunner.errors import RunNotFound
from qarunner.models import (
    CollectResult,
    ProcessResult,
    ReportRef,
    Run,
    RunStatus,
    TestSummary,
)
from qarunner.ports.clock import Clock
from qarunner.ports.collector import ResultCollector
from qarunner.ports.ids import IdGenerator
from qarunner.ports.process import ProcessRunner
from qarunner.ports.reporter import AllureReporter
from qarunner.ports.scheduler import TaskScheduler
from qarunner.ports.store import (
    ProfileStore,
    RunStore,
    ScheduleStore,
    Store,
    UserStore,
)
from tests.fakes.fake_clock import FakeClock
from tests.fakes.fake_collector import FakeResultCollector
from tests.fakes.fake_ids import FakeIdGenerator
from tests.fakes.fake_process import FakeProcessRunner
from tests.fakes.fake_reporter import FakeAllureReporter
from tests.fakes.fake_scheduler import FakeScheduler
from tests.fakes.fake_store import InMemoryRunStore

# ── helpers ─────────────────────────────────────────────────────────────────

_FIXED = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

_SAMPLE_RUN = Run(
    id="id-001",
    status=RunStatus.QUEUED,
    runner="pytest",
    created_by="system",
    tests_path="tests/",
    created_at=_FIXED,
)

_SAMPLE_RESULT = ProcessResult(
    exit_code=0,
    stdout="ok",
    stderr="",
    duration_ms=120,
)

_SAMPLE_COLLECT = CollectResult(
    summary=TestSummary(
        total=1,
        passed=1,
        failed=0,
        skipped=0,
        error=0,
        duration_ms=100,
    ),
    cases=[],
)

_SAMPLE_REPORT = ReportRef(
    allure_results_dir="/tmp/results",
    allure_report_file="/tmp/report.html",
    html_generated=True,
)


# ── 1. Protocol conformance (runtime_checkable) ────────────────────────────


class TestProtocolConformance:
    """Each fake must pass isinstance check against its Protocol."""

    def test_fake_process_runner_is_process_runner(self) -> None:
        fake = FakeProcessRunner(handler=lambda *_: _SAMPLE_RESULT)
        assert isinstance(fake, ProcessRunner)

    def test_fake_clock_is_clock(self) -> None:
        assert isinstance(FakeClock(), Clock)

    def test_fake_id_generator_is_id_generator(self) -> None:
        assert isinstance(FakeIdGenerator(), IdGenerator)

    def test_in_memory_run_store_is_run_store(self) -> None:
        assert isinstance(InMemoryRunStore(), RunStore)

    def test_sqlite_store_implements_full_store_port(self) -> None:
        store = SqliteStore(":memory:")
        assert isinstance(store, Store)
        assert isinstance(store, RunStore)
        assert isinstance(store, UserStore)
        assert isinstance(store, ProfileStore)
        assert isinstance(store, ScheduleStore)

    def test_fake_scheduler_is_task_scheduler(self) -> None:
        assert isinstance(FakeScheduler(), TaskScheduler)

    def test_fake_result_collector_is_result_collector(self) -> None:
        assert isinstance(FakeResultCollector(), ResultCollector)

    def test_fake_allure_reporter_is_allure_reporter(self) -> None:
        assert isinstance(FakeAllureReporter(preset=_SAMPLE_REPORT), AllureReporter)


# ── 2. FakeProcessRunner behaviour ─────────────────────────────────────────


class TestFakeProcessRunner:
    async def test_returns_handler_result(self) -> None:
        fake = FakeProcessRunner(handler=lambda *_: _SAMPLE_RESULT)
        result = await fake.run(["pytest"], "/work")
        assert result == _SAMPLE_RESULT

    async def test_records_calls(self) -> None:
        fake = FakeProcessRunner(handler=lambda *_: _SAMPLE_RESULT)
        await fake.run(["pytest", "-v"], "/work", {"PYTHONDONTWRITEBYTECODE": "1"}, 60)
        assert len(fake.calls) == 1
        cmd, cwd, env, timeout = fake.calls[0]
        assert cmd == ["pytest", "-v"]
        assert cwd == "/work"
        assert env == {"PYTHONDONTWRITEBYTECODE": "1"}
        assert timeout == 60

    async def test_default_env_and_timeout(self) -> None:
        fake = FakeProcessRunner(handler=lambda *_: _SAMPLE_RESULT)
        await fake.run(["echo"], "/tmp")
        _, _, env, timeout = fake.calls[0]
        assert env is None
        assert timeout == 1800

    async def test_multiple_calls_accumulate(self) -> None:
        fake = FakeProcessRunner(handler=lambda *_: _SAMPLE_RESULT)
        await fake.run(["a"], "/a")
        await fake.run(["b"], "/b")
        assert len(fake.calls) == 2


# ── 3. FakeClock behaviour ─────────────────────────────────────────────────


class TestFakeClock:
    def test_returns_current(self) -> None:
        clock = FakeClock(current=_FIXED)
        assert clock.now() == _FIXED

    def test_mutable_current(self) -> None:
        clock = FakeClock()
        new_time = datetime(2030, 1, 1, tzinfo=UTC)
        clock.current = new_time
        assert clock.now() == new_time

    def test_default_time(self) -> None:
        clock = FakeClock()
        assert clock.now().year == 2025


# ── 4. FakeIdGenerator behaviour ────────────────────────────────────────────


class TestFakeIdGenerator:
    def test_sequential_ids(self) -> None:
        gen = FakeIdGenerator()
        assert gen.new_id() == "id-001"
        assert gen.new_id() == "id-002"
        assert gen.new_id() == "id-003"

    def test_starts_at_zero(self) -> None:
        gen = FakeIdGenerator()
        assert gen._counter == 0

    def test_counter_increments(self) -> None:
        gen = FakeIdGenerator()
        gen.new_id()
        assert gen._counter == 1


# ── 5. InMemoryRunStore behaviour ──────────────────────────────────────────


class TestInMemoryRunStore:
    async def test_save_and_get(self) -> None:
        store = InMemoryRunStore()
        await store.save(_SAMPLE_RUN)
        retrieved = await store.get("id-001")
        assert retrieved == _SAMPLE_RUN

    async def test_get_missing_raises_run_not_found(self) -> None:
        store = InMemoryRunStore()
        with pytest.raises(RunNotFound):
            await store.get("nonexistent")

    async def test_list_empty(self) -> None:
        store = InMemoryRunStore()
        assert await store.list() == []

    async def test_list_sorted_descending_by_created_at(self) -> None:
        store = InMemoryRunStore()
        run_a = _SAMPLE_RUN.model_copy(
            update={"id": "a", "created_at": datetime(2025, 1, 1, tzinfo=UTC)}
        )
        run_b = _SAMPLE_RUN.model_copy(
            update={"id": "b", "created_at": datetime(2025, 6, 1, tzinfo=UTC)}
        )
        await store.save(run_a)
        await store.save(run_b)
        result = await store.list()
        assert result[0].id == "b"
        assert result[1].id == "a"

    async def test_save_upserts(self) -> None:
        store = InMemoryRunStore()
        await store.save(_SAMPLE_RUN)
        updated = _SAMPLE_RUN.model_copy(update={"status": RunStatus.RUNNING})
        await store.save(updated)
        retrieved = await store.get("id-001")
        assert retrieved.status == RunStatus.RUNNING

    async def test_run_not_found_is_key_error(self) -> None:
        """RunNotFound inherits KeyError for compatibility."""
        store = InMemoryRunStore()
        with pytest.raises(KeyError):
            await store.get("missing")


# ── 6. FakeScheduler behaviour ──────────────────────────────────────────────


class TestFakeScheduler:
    def test_enqueue_records_call(self) -> None:
        scheduler = FakeScheduler()
        scheduler.enqueue("run-1")
        assert scheduler.enqueued == 1

    def test_enqueue_tracks_multiple(self) -> None:
        scheduler = FakeScheduler()
        scheduler.enqueue("run-1")
        scheduler.enqueue("run-2")
        assert scheduler.enqueued == 2

    def test_cancel_records_key(self) -> None:
        scheduler = FakeScheduler()
        scheduler.enqueue("run-1")
        assert scheduler.cancel("run-1") is True
        assert "run-1" in scheduler.cancelled_keys

    def test_cancel_returns_false_for_unknown_key(self) -> None:
        scheduler = FakeScheduler()
        assert scheduler.cancel("run-1") is False

    async def test_drain_records(self) -> None:
        scheduler = FakeScheduler()
        await scheduler.drain()
        assert scheduler.drained == 1

    async def test_start_and_shutdown(self) -> None:
        scheduler = FakeScheduler()
        assert not scheduler._running
        await scheduler.start()
        assert scheduler._running
        await scheduler.shutdown()
        assert not scheduler._running


# ── 7. FakeResultCollector behaviour ────────────────────────────────────────


class TestFakeResultCollector:
    def test_returns_preset(self) -> None:
        collector = FakeResultCollector(preset=_SAMPLE_COLLECT)
        assert collector.collect("/run") == _SAMPLE_COLLECT

    def test_returns_none_by_default(self) -> None:
        collector = FakeResultCollector()
        assert collector.collect("/run") is None

    def test_records_calls(self) -> None:
        collector = FakeResultCollector()
        collector.collect("/run1")
        collector.collect("/run2")
        assert collector.calls == ["/run1", "/run2"]


# ── 8. FakeAllureReporter behaviour ─────────────────────────────────────────


class TestFakeAllureReporter:
    async def test_returns_preset(self) -> None:
        reporter = FakeAllureReporter(preset=_SAMPLE_REPORT)
        assert await reporter.generate("/run") == _SAMPLE_REPORT

    async def test_records_calls_with_default_enabled(self) -> None:
        reporter = FakeAllureReporter(preset=_SAMPLE_REPORT)
        await reporter.generate("/run")
        assert reporter.calls == [("/run", True)]

    async def test_records_calls_with_enabled_false(self) -> None:
        reporter = FakeAllureReporter(preset=_SAMPLE_REPORT)
        await reporter.generate("/run", enabled=False)
        assert reporter.calls == [("/run", False)]

    async def test_multiple_calls(self) -> None:
        reporter = FakeAllureReporter(preset=_SAMPLE_REPORT)
        await reporter.generate("/a")
        await reporter.generate("/b", enabled=False)
        assert len(reporter.calls) == 2
