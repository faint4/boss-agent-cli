from __future__ import annotations

import time
import threading
from datetime import datetime, timezone

import pytest

from boss_agent_cli.application import (
	Application,
	CancelRunCommand,
	CurrentStateQuery,
	DiscardRunCommand,
	ErrorCode,
	JobSearchBatch,
	JobSearchGoal,
	JobSummary,
	PlatformSessionState,
	RequestContext,
	ResumeRunCommand,
	StartJobSearchCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


NOW = datetime(2026, 8, 22, 13, 0, tzinfo=timezone.utc)
GOAL = JobSearchGoal("寻找后端岗位", "Python", city="上海")
JOB = JobSummary("job-1", "Python 后端工程师", "示例科技", location="上海")


def _context() -> RequestContext:
	return RequestContext("local-session", "correlation-19")


def _application(registry: WorkspaceRegistry, boss: FakeBossAdapter) -> Application:
	return Application(
		workspace_store=registry,
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED},
		),
		boss=boss,
		run_id_factory=lambda: "run-19",
		clock=lambda: NOW,
	)


def _wait_for_terminal(application: Application):
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == "completed":
			return snapshot
		time.sleep(0.01)
	raise AssertionError("run did not complete")


class _PausingWorkspaceRegistry(WorkspaceRegistry):
	def __init__(self, *args, transition_reached: threading.Event, release_transition: threading.Event, **kwargs):
		super().__init__(*args, **kwargs)
		self._transition_reached = transition_reached
		self._release_transition = release_transition

	def _pause_terminal_transition(self, state: str) -> None:
		if state == "completed":
			self._transition_reached.set()
			if not self._release_transition.wait(timeout=5):
				raise AssertionError("terminal transition was not released")

	def save_run(self, summary) -> None:
		super().save_run(summary)
		self._pause_terminal_transition(summary.state)

	def save_run_event(self, summary, event) -> None:
		self._pause_terminal_transition(summary.state)
		super().save_run_event(summary, event)


def test_terminal_snapshot_never_precedes_its_terminal_event(tmp_path) -> None:
	transition_reached = threading.Event()
	release_transition = threading.Event()
	registry = _PausingWorkspaceRegistry(
		tmp_path,
		local_session_id="local-session",
		transition_reached=transition_reached,
		release_transition=release_transition,
	)
	observer = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	allow_terminal = threading.Event()
	application = _application(
		registry,
		FakeBossAdapter(
			search_batches=(JobSearchBatch((JOB,), 50), JobSearchBatch((JOB,), 100)),
			batch_gate=allow_terminal,
		),
	)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	allow_terminal.set()
	assert transition_reached.wait(timeout=2)

	observed_run = observer.load_latest_run(WorkspaceKind.JOB_SEEKING)
	release_transition.set()
	completed = _wait_for_terminal(application)
	completed_events = observer.read_events(WorkspaceKind.JOB_SEEKING, "run-19")

	assert completed.active_run is not None
	assert observed_run is not None
	assert observed_run.state != "completed"
	assert dict(completed_events[-1].payload).get("state") == "completed"


def test_completed_run_snapshot_and_events_survive_restart(tmp_path) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	boss = FakeBossAdapter(search_batches=(JobSearchBatch((JOB,), 100),))
	application = _application(registry, boss)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())

	started = application.execute(StartJobSearchCommand(), _context())
	completed = _wait_for_terminal(application)

	assert started.resource_ref == "run-19"
	assert completed.active_run is not None
	assert completed.active_run.run_id == "run-19"
	assert completed.active_run.workspace is WorkspaceKind.JOB_SEEKING
	assert completed.active_run.kind == "job-search"
	assert completed.active_run.phase == "completed"
	assert completed.active_run.progress == 100
	assert completed.active_run.created_at == NOW
	assert completed.active_run.updated_at == NOW
	assert completed.active_run.result is not None
	assert completed.active_run.result.category == "completed"
	assert completed.active_run.result.item_count == 1
	assert completed.active_run.error is None
	assert completed.active_run.permitted_next_actions == ("start-new-run", "discard")

	restarted = _application(
		WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		FakeBossAdapter(),
	)
	restarted_snapshot = restarted.query(CurrentStateQuery(), _context())
	restarted_events = restarted.events("run-19", after_cursor=0, context=_context())

	assert restarted_snapshot.active_run == completed.active_run
	assert [event.cursor for event in restarted_events] == [1, 2, 3]
	assert all(event.occurred_at == NOW for event in restarted_events)


def test_restart_reconciles_interrupted_run_without_any_remote_write(tmp_path) -> None:
	gate = threading.Event()
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	first_boss = FakeBossAdapter(
		search_batches=(
			JobSearchBatch((JOB,), 50),
			JobSearchBatch((JobSummary("job-2", "Go 工程师", "另一家公司"),), 90),
		),
		batch_gate=gate,
	)
	application = _application(registry, first_boss)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.progress == 50:
			break
		time.sleep(0.01)
	else:
		gate.set()
		raise AssertionError("run did not reach its persisted checkpoint")

	restarted_boss = FakeBossAdapter()
	restarted = _application(
		WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		restarted_boss,
	)
	reconciled = restarted.query(CurrentStateQuery(), _context())
	gate.set()

	assert reconciled.active_run is not None
	assert reconciled.active_run.run_id == "run-19"
	assert reconciled.active_run.state == "recovery_required"
	assert reconciled.active_run.phase == "reconcile"
	assert reconciled.active_run.progress == 50
	assert reconciled.active_run.error is not None
	assert reconciled.active_run.error.code.value == "CANCELLATION_REQUESTED"
	assert reconciled.active_run.permitted_next_actions == ("resume", "discard")
	assert restarted_boss.search_calls == 0
	assert restarted_boss.greeting_calls == []
	assert restarted_boss.recruiting_reply_calls == []


def test_explicit_resume_refetches_safe_read_with_same_run_id_and_never_writes(tmp_path) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	failing_boss = FakeBossAdapter(search_failure=ErrorCode.ADAPTER_UNAVAILABLE)
	application = _application(registry, failing_boss)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		failed = application.query(CurrentStateQuery(), _context())
		if failed.active_run is not None and failed.active_run.state == "recovery_required":
			break
		time.sleep(0.01)
	else:
		raise AssertionError("run did not enter recovery")

	resumed_boss = FakeBossAdapter(search_batches=(JobSearchBatch((JOB,), 100),))
	restarted = _application(
		WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		resumed_boss,
	)
	restarted.query(CurrentStateQuery(), _context())
	resuming = restarted.execute(ResumeRunCommand("run-19"), _context())
	completed = _wait_for_terminal(restarted)

	assert resuming.resource_ref == "run-19"
	assert completed.active_run is not None
	assert completed.active_run.run_id == "run-19"
	assert completed.active_run.state == "completed"
	assert completed.active_run.error is None
	assert resumed_boss.search_calls == 1
	assert resumed_boss.greeting_calls == []
	assert resumed_boss.recruiting_reply_calls == []
	assert [event.cursor for event in restarted.events("run-19", after_cursor=0, context=_context())] == [1, 2, 3, 4, 5]


def test_cancellation_is_observed_by_adapter_and_persists_a_stopped_checkpoint(tmp_path) -> None:
	gate = threading.Event()
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	boss = FakeBossAdapter(
		search_batches=(
			JobSearchBatch((JOB,), 50),
			JobSearchBatch((JobSummary("job-2", "Go 工程师", "另一家公司"),), 90),
		),
		batch_gate=gate,
	)
	application = _application(registry, boss)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.progress == 50:
			break
		time.sleep(0.01)
	else:
		raise AssertionError("run did not reach cancellable progress")

	application.execute(CancelRunCommand("run-19"), _context())
	gate.set()
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		stopped = application.query(CurrentStateQuery(), _context())
		if stopped.active_run is not None and stopped.active_run.state == "stopped":
			break
		time.sleep(0.01)
	else:
		raise AssertionError("run did not stop")

	assert stopped.active_run is not None
	assert stopped.active_run.phase == "stopped"
	assert stopped.active_run.progress == 50
	assert stopped.active_run.permitted_next_actions == ("resume", "discard")
	assert [dict(event.payload).get("state") for event in application.events("run-19", after_cursor=0, context=_context())] == [
		"running",
		None,
		"stopping",
		"stopped",
	]
	assert boss.search_calls == 1

	restarted = _application(WorkspaceRegistry(tmp_path, local_session_id="local-session"), FakeBossAdapter())
	assert restarted.query(CurrentStateQuery(), _context()).active_run == stopped.active_run


@pytest.mark.parametrize(
	("code", "actions"),
	[
		(ErrorCode.AUTHENTICATION_EXPIRED, ("reconnect", "resume", "discard")),
		(ErrorCode.RATE_LIMITED, ("wait", "resume", "discard")),
		(ErrorCode.PLATFORM_RISK_CONTROL, ("open-official-boss", "resume", "discard")),
		(ErrorCode.ADAPTER_UNAVAILABLE, ("resume", "discard")),
	],
)
def test_recovery_failures_expose_distinct_authoritative_actions(tmp_path, code, actions) -> None:
	application = _application(
		WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		FakeBossAdapter(search_failure=code),
	)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == "recovery_required":
			break
		time.sleep(0.01)
	else:
		raise AssertionError("run did not enter recovery")

	assert snapshot.active_run is not None
	assert snapshot.active_run.error is not None
	assert snapshot.active_run.error.code is code
	assert snapshot.active_run.permitted_next_actions == actions


def test_discard_removes_run_and_its_event_history(tmp_path) -> None:
	registry = WorkspaceRegistry(tmp_path, local_session_id="local-session")
	application = _application(registry, FakeBossAdapter(search_batches=(JobSearchBatch((JOB,), 100),)))
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	_wait_for_terminal(application)

	discarded = application.execute(DiscardRunCommand("run-19"), _context())
	restarted = _application(WorkspaceRegistry(tmp_path, local_session_id="local-session"), FakeBossAdapter())

	assert discarded.snapshot.active_run is None
	assert restarted.query(CurrentStateQuery(), _context()).active_run is None
	with pytest.raises(KeyError):
		registry.read_events(WorkspaceKind.JOB_SEEKING, "run-19")
