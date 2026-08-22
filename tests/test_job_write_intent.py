from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from boss_agent_cli.application import (
	Application,
	CancelWriteIntentCommand,
	ConfirmWriteIntentCommand,
	CurrentStateQuery,
	DomainError,
	ErrorCode,
	InspectJobCommand,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	PrepareJobGreetingCommand,
	RequestContext,
	StartJobSearchCommand,
	SwitchWorkspaceCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
	WriteIntentState,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


NOW = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
GOAL = JobSearchGoal(objective="寻找后端岗位", keyword="Python", city="上海")
JOB = JobSummary(reference="job-1", title="Python 后端工程师", company="示例科技", location="上海")
MESSAGE = "您好，我对这个岗位很感兴趣，希望进一步沟通。"


def _context(correlation_id: str = "correlation-1") -> RequestContext:
	return RequestContext(local_session_id="local-session", correlation_id=correlation_id)


def _ready_application(
	tmp_path,
	*,
	clock=lambda: NOW,
	boss: FakeBossAdapter | None = None,
	credentials: InMemoryCredentialStore | None = None,
) -> tuple[Application, FakeBossAdapter, InMemoryCredentialStore]:
	boss = boss or FakeBossAdapter(
		search_batches=(JobSearchBatch(items=(JOB,), progress=100),),
		details={"job-1": JobSourceDetail(job=JOB, description="负责 Python 服务")},
	)
	credentials = credentials or InMemoryCredentialStore(
		{WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED}
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=credentials,
		boss=boss,
		run_id_factory=lambda: "search-1",
		intent_id_factory=lambda: "intent-1",
		clock=clock,
	)
	application.execute(UpdateJobSearchGoalCommand(goal=GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == "completed":
			break
		time.sleep(0.01)
	else:
		raise AssertionError("search did not complete")
	application.execute(InspectJobCommand(reference="job-1"), _context())
	return application, boss, credentials


def test_preparation_is_local_and_shows_complete_confirmation_preview(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)

	snapshot = application.execute(
		PrepareJobGreetingCommand(reference="job-1", message=MESSAGE), _context()
	).snapshot

	assert boss.greeting_calls == []
	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.intent_id == "intent-1"
	assert snapshot.pending_write_intent.target_reference == "job-1"
	assert snapshot.pending_write_intent.target_label == "Python 后端工程师 · 示例科技"
	assert snapshot.pending_write_intent.action == "发送 BOSS 招呼"
	assert snapshot.pending_write_intent.payload_preview == MESSAGE
	assert snapshot.pending_write_intent.warnings == ("确认后将立即向 BOSS 发送一条招呼，且不会自动重试。",)
	assert snapshot.pending_write_intent.expires_at == NOW + timedelta(minutes=5)
	assert snapshot.pending_write_intent.state is WriteIntentState.PENDING


def test_confirm_performs_exactly_one_write_and_duplicate_click_is_consumed(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)
	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())

	confirmed = application.execute(ConfirmWriteIntentCommand("intent-1"), _context()).snapshot

	assert boss.greeting_calls == [("job-1", MESSAGE)]
	assert confirmed.pending_write_intent is not None
	assert confirmed.pending_write_intent.state is WriteIntentState.SUCCEEDED
	with pytest.raises(DomainError) as duplicate:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context("duplicate"))
	assert duplicate.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.greeting_calls == [("job-1", MESSAGE)]


def test_active_confirmation_cannot_be_replaced_with_a_second_payload(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)
	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())

	with pytest.raises(DomainError) as replacement:
		application.execute(PrepareJobGreetingCommand("job-1", "替换后的内容"), _context("replacement"))

	assert replacement.value.code is ErrorCode.INVALID_TRANSITION
	snapshot = application.query(CurrentStateQuery(), _context())
	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.payload_preview == MESSAGE
	assert boss.greeting_calls == []


def test_concurrent_duplicate_confirmation_is_at_most_once(tmp_path) -> None:
	gate = threading.Event()
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch(items=(JOB,), progress=100),),
		details={"job-1": JobSourceDetail(job=JOB)},
		greeting_gate=gate,
	)
	application, boss, _ = _ready_application(tmp_path, boss=boss)
	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())
	errors: list[DomainError] = []

	def confirm(correlation_id: str) -> None:
		try:
			application.execute(ConfirmWriteIntentCommand("intent-1"), _context(correlation_id))
		except DomainError as exc:
			errors.append(exc)

	first = threading.Thread(target=confirm, args=("first",))
	second = threading.Thread(target=confirm, args=("second",))
	first.start()
	deadline = time.monotonic() + 2
	while not boss.greeting_calls and time.monotonic() < deadline:
		time.sleep(0.01)
	second.start()
	second.join(timeout=2)
	gate.set()
	first.join(timeout=2)

	assert boss.greeting_calls == [("job-1", MESSAGE)]
	assert [error.code for error in errors] == [ErrorCode.WRITE_INTENT_CONSUMED]


def test_expiry_cancellation_session_change_and_restart_never_write(tmp_path) -> None:
	current = [NOW]
	application, boss, credentials = _ready_application(tmp_path, clock=lambda: current[0])
	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())
	current[0] += timedelta(minutes=6)
	with pytest.raises(DomainError) as expired:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context())
	assert expired.value.code is ErrorCode.WRITE_INTENT_EXPIRED
	assert application.query(CurrentStateQuery(), _context()).pending_write_intent.state is WriteIntentState.EXPIRED

	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())
	application.execute(CancelWriteIntentCommand("intent-1"), _context())
	with pytest.raises(DomainError) as cancelled:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context())
	assert cancelled.value.code is ErrorCode.WRITE_INTENT_CONSUMED

	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())
	credentials.rotate(WorkspaceKind.JOB_SEEKING)
	with pytest.raises(DomainError) as changed:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context())
	assert changed.value.code is ErrorCode.AUTHENTICATION_EXPIRED

	restarted = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=credentials,
		boss=boss,
	)
	with pytest.raises(DomainError) as missing:
		restarted.execute(ConfirmWriteIntentCommand("intent-1"), _context())
	assert missing.value.code is ErrorCode.WRITE_INTENT_MISSING
	assert boss.greeting_calls == []


def test_refresh_preserves_pending_intent_and_workspace_switch_invalidates_it(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)
	prepared = application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context()).snapshot
	refreshed = application.query(CurrentStateQuery(), _context())
	assert refreshed.pending_write_intent == prepared.pending_write_intent

	application.execute(SwitchWorkspaceCommand(WorkspaceKind.RECRUITING), _context())
	application.execute(SwitchWorkspaceCommand(WorkspaceKind.JOB_SEEKING), _context())
	with pytest.raises(DomainError) as consumed:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context())
	assert consumed.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.greeting_calls == []


@pytest.mark.parametrize(
	("failure", "state", "code"),
	[
		(ErrorCode.PLATFORM_RISK_CONTROL, WriteIntentState.REJECTED, ErrorCode.PLATFORM_RISK_CONTROL),
		(ErrorCode.RATE_LIMITED, WriteIntentState.REJECTED, ErrorCode.RATE_LIMITED),
		(ErrorCode.UNCERTAIN_REMOTE_OUTCOME, WriteIntentState.UNCERTAIN, ErrorCode.UNCERTAIN_REMOTE_OUTCOME),
	],
)
def test_rejection_and_uncertain_outcome_are_consumed_and_visible(tmp_path, failure, state, code) -> None:
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch(items=(JOB,), progress=100),),
		details={"job-1": JobSourceDetail(job=JOB)},
		greeting_failure=failure,
	)
	application, boss, _ = _ready_application(tmp_path, boss=boss)
	application.execute(PrepareJobGreetingCommand("job-1", MESSAGE), _context())

	with pytest.raises(DomainError) as failed:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context())

	assert failed.value.code is code
	assert boss.greeting_calls == [("job-1", MESSAGE)]
	assert application.query(CurrentStateQuery(), _context()).pending_write_intent.state is state
	with pytest.raises(DomainError) as duplicate:
		application.execute(ConfirmWriteIntentCommand("intent-1"), _context())
	assert duplicate.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.greeting_calls == [("job-1", MESSAGE)]
