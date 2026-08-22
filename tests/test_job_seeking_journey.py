from __future__ import annotations

import threading
import time

import pytest

from boss_agent_cli.application import (
	Application,
	CancelRunCommand,
	CurrentStateQuery,
	ErrorCode,
	InspectJobCommand,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	RequestContext,
	SetShortlistedCommand,
	StartJobSearchCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


def _context() -> RequestContext:
	return RequestContext(local_session_id="local-session", correlation_id="correlation-1")


def _application(registry: WorkspaceRegistry, boss: FakeBossAdapter | None = None) -> Application:
	return Application(
		workspace_store=registry,
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED},
		),
		boss=boss or FakeBossAdapter(),
		run_id_factory=lambda: "search-1",
	)


def _wait_for_state(application: Application, state: str) -> object:
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == state:
			return snapshot
		time.sleep(0.01)
	raise AssertionError(f"search did not reach {state}")


GOAL = JobSearchGoal(
	objective="寻找后端工程师岗位",
	keyword="Python",
	city="上海",
	salary="20-40K",
	experience="3-5年",
	education="本科",
)
JOB = JobSummary(
	reference="job-1",
	title="Python 后端工程师",
	company="示例科技",
	location="上海·浦东",
	salary="20-40K",
	experience="3-5年",
	education="本科",
)


def test_job_search_goal_and_filters_survive_restart(tmp_path) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")

	result = _application(registry).execute(UpdateJobSearchGoalCommand(goal=GOAL), _context())

	assert result.snapshot.job_seeking is not None
	assert result.snapshot.job_seeking.goal == GOAL
	restarted = _application(WorkspaceRegistry(tmp_path, local_session_id="local-session"))
	snapshot = restarted.query(CurrentStateQuery(), _context())
	assert snapshot.job_seeking is not None
	assert snapshot.job_seeking.goal == GOAL


def test_search_progress_results_detail_reasons_and_shortlist_survive_restart(tmp_path) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch(items=(JOB,), progress=60),),
		details={"job-1": JobSourceDetail(job=JOB, description="负责 Python 服务与 API")},
	)
	application = _application(registry, boss)
	application.execute(UpdateJobSearchGoalCommand(goal=GOAL), _context())

	started = application.execute(StartJobSearchCommand(), _context())
	completed = _wait_for_state(application, "completed")

	assert started.resource_ref == "search-1"
	assert completed.job_seeking is not None
	assert completed.job_seeking.results == (JOB,)
	events = application.events("search-1", after_cursor=0, context=_context())
	assert [event.kind.value for event in events] == ["state-changed", "progress", "state-changed"]
	inspected = application.execute(InspectJobCommand(reference="job-1"), _context()).snapshot
	assert inspected.job_seeking is not None
	assert inspected.job_seeking.selected_job is not None
	assert inspected.job_seeking.selected_job.source.description == "负责 Python 服务与 API"
	assert inspected.job_seeking.selected_job.match_reasons == (
		"职位原文包含关键词“Python”",
		"职位地点包含“上海”",
		"职位原文薪资为“20-40K”",
		"职位原文经验要求为“3-5年”",
		"职位原文学历要求为“本科”",
	)
	application.execute(SetShortlistedCommand(reference="job-1", shortlisted=True), _context())

	restarted = _application(WorkspaceRegistry(tmp_path, local_session_id="local-session"))
	snapshot = restarted.query(CurrentStateQuery(), _context())
	assert snapshot.job_seeking is not None
	assert snapshot.job_seeking.shortlist == (JOB,)


def test_empty_search_is_a_successful_observable_run(tmp_path) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	application = _application(registry, FakeBossAdapter(search_batches=()))
	application.execute(UpdateJobSearchGoalCommand(goal=GOAL), _context())

	application.execute(StartJobSearchCommand(), _context())
	snapshot = _wait_for_state(application, "completed")

	assert snapshot.active_run is not None
	assert snapshot.active_run.progress == 100
	assert snapshot.job_seeking is not None
	assert snapshot.job_seeking.results == ()


def test_running_search_is_cancellable_and_keeps_completed_batches(tmp_path) -> None:
	gate = threading.Event()
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	boss = FakeBossAdapter(
		search_batches=(
			JobSearchBatch(items=(JOB,), progress=50),
			JobSearchBatch(items=(JobSummary("job-2", "Go 工程师", "另一家公司"),), progress=90),
		),
		batch_gate=gate,
	)
	application = _application(registry, boss)
	application.execute(UpdateJobSearchGoalCommand(goal=GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.job_seeking is not None and snapshot.job_seeking.results:
			break
		time.sleep(0.01)

	application.execute(CancelRunCommand(run_id="search-1"), _context())
	stopped = _wait_for_state(application, "stopped")

	assert stopped.active_run is not None
	assert stopped.active_run.state == "stopped"
	assert stopped.job_seeking is not None
	assert stopped.job_seeking.results == (JOB,)
	assert "stopping" in [dict(event.payload).get("state") for event in application.events("search-1", after_cursor=0, context=_context())]
	assert boss.search_calls == 1


@pytest.mark.parametrize(
	"failure_code",
	[
		ErrorCode.AUTHENTICATION_EXPIRED,
		ErrorCode.RATE_LIMITED,
		ErrorCode.PLATFORM_RISK_CONTROL,
	],
)
def test_partial_search_stops_at_typed_recoverable_failures(tmp_path, failure_code: ErrorCode) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch(items=(JOB,), progress=40),),
		search_failure=failure_code,
		failure_after_batches=1,
	)
	application = _application(registry, boss)
	application.execute(UpdateJobSearchGoalCommand(goal=GOAL), _context())

	application.execute(StartJobSearchCommand(), _context())
	snapshot = _wait_for_state(application, "recovery_required")

	assert snapshot.error is not None
	assert snapshot.error.code is failure_code
	assert snapshot.error.recoverable is True
	assert snapshot.platform_session is PlatformSessionState.RECOVERY
	assert snapshot.job_seeking is not None
	assert snapshot.job_seeking.results == (JOB,)
	assert boss.search_calls == 1
