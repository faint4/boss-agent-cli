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
	InboundApplicant,
	InspectRecruitingProspectCommand,
	LoadRecruitingOpeningsCommand,
	PlatformSessionState,
	PrepareRecruitingReplyCommand,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RequestContext,
	SelectRecruitingOpeningCommand,
	StartInboundApplicantsCommand,
	SwitchWorkspaceCommand,
	WorkspaceKind,
	WriteIntentState,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


NOW = datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc)
OPENING = RecruitingOpening("opening-1", "Python 后端工程师", "招聘中")
APPLICANT = InboundApplicant("prospect-1", "招聘对象甲", "5 年 Python 经验")
CONTEXT = RecruitingProspectContext(APPLICANT, "本科 · 5年", ("应聘者：您好",), ("手机号已保护",))
MESSAGE = "您好，感谢投递。方便沟通一下项目经历吗？"


def _context(correlation_id: str = "correlation-18") -> RequestContext:
	return RequestContext("local-session", correlation_id)


def _ready_application(tmp_path, *, boss: FakeBossAdapter | None = None, clock=lambda: NOW):
	boss = boss or FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT,), 100),),
		prospect_contexts={"prospect-1": CONTEXT},
	)
	credentials = InMemoryCredentialStore(
		{
			WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
			WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED,
		}
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=credentials,
		boss=boss,
		run_id_factory=lambda: "recruiting-run-1",
		intent_id_factory=lambda: "intent-18",
		clock=clock,
	)
	application.execute(SwitchWorkspaceCommand(WorkspaceKind.RECRUITING), _context())
	application.execute(LoadRecruitingOpeningsCommand(), _context())
	application.execute(SelectRecruitingOpeningCommand("opening-1"), _context())
	application.execute(StartInboundApplicantsCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == "completed":
			break
		time.sleep(0.01)
	else:
		raise AssertionError("applicant read did not complete")
	application.execute(InspectRecruitingProspectCommand("prospect-1"), _context())
	return application, boss, credentials


def test_reply_preparation_is_local_and_identifies_opening_applicant_destination_and_message(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)

	snapshot = application.execute(
		PrepareRecruitingReplyCommand("prospect-1", MESSAGE),
		_context(),
	).snapshot

	assert boss.recruiting_reply_calls == []
	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.intent_id == "intent-18"
	assert snapshot.pending_write_intent.workspace is WorkspaceKind.RECRUITING
	assert snapshot.pending_write_intent.target_reference == "prospect-1"
	assert snapshot.pending_write_intent.target_label == "招聘对象甲"
	assert snapshot.pending_write_intent.context_label == "Python 后端工程师"
	assert snapshot.pending_write_intent.destination_label == "BOSS 招聘沟通会话"
	assert snapshot.pending_write_intent.action == "回复 BOSS 招聘沟通"
	assert snapshot.pending_write_intent.payload_preview == MESSAGE
	assert snapshot.pending_write_intent.expires_at == NOW + timedelta(minutes=5)
	assert snapshot.pending_write_intent.state is WriteIntentState.PENDING
	stored = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
	assert MESSAGE.encode() not in stored


def test_confirm_sends_one_reply_and_duplicate_confirmation_cannot_resend(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())

	confirmed = application.execute(ConfirmWriteIntentCommand("intent-18"), _context()).snapshot

	assert boss.recruiting_reply_calls == [("prospect-1", MESSAGE)]
	assert confirmed.sensitive_content_present is False
	assert confirmed.recruiting is not None
	assert confirmed.recruiting.selected_prospect is None
	assert boss.greeting_calls == []
	assert confirmed.pending_write_intent is not None
	assert confirmed.pending_write_intent.state is WriteIntentState.SUCCEEDED
	assert confirmed.pending_write_intent.outcome_message == "回复已发送。"
	with pytest.raises(DomainError) as duplicate:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context("duplicate"))
	assert duplicate.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.recruiting_reply_calls == [("prospect-1", MESSAGE)]


def test_cancelling_reply_intent_never_sends_and_clears_sensitive_context(tmp_path) -> None:
	application, boss, _ = _ready_application(tmp_path)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())

	cancelled = application.execute(CancelWriteIntentCommand("intent-18"), _context()).snapshot

	assert cancelled.pending_write_intent is not None
	assert cancelled.pending_write_intent.state is WriteIntentState.CANCELLED
	assert cancelled.sensitive_content_present is False
	assert cancelled.recruiting is not None
	assert cancelled.recruiting.selected_prospect is None
	with pytest.raises(DomainError) as consumed:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())
	assert consumed.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.recruiting_reply_calls == []
	assert boss.greeting_calls == []


def test_session_change_invalidates_reply_without_sending_and_enters_safe_context_state(tmp_path) -> None:
	application, boss, credentials = _ready_application(tmp_path)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())
	credentials.rotate(WorkspaceKind.RECRUITING)

	with pytest.raises(DomainError) as changed:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())

	assert changed.value.code is ErrorCode.AUTHENTICATION_EXPIRED
	snapshot = application.query(CurrentStateQuery(), _context())
	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.state is WriteIntentState.CANCELLED
	assert snapshot.sensitive_content_present is False
	assert snapshot.recruiting is not None
	assert snapshot.recruiting.selected_prospect is None
	assert boss.recruiting_reply_calls == []


def test_opening_change_invalidates_pending_reply_without_sending(tmp_path) -> None:
	second_opening = RecruitingOpening("opening-2", "Go 后端工程师", "招聘中")
	boss = FakeBossAdapter(
		openings=(OPENING, second_opening),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT,), 100),),
		prospect_contexts={"prospect-1": CONTEXT},
	)
	application, boss, _ = _ready_application(tmp_path, boss=boss)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())

	snapshot = application.execute(SelectRecruitingOpeningCommand("opening-2"), _context()).snapshot

	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.state is WriteIntentState.CANCELLED
	assert snapshot.sensitive_content_present is False
	assert snapshot.recruiting is not None
	assert snapshot.recruiting.selected_prospect is None
	with pytest.raises(DomainError) as consumed:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())
	assert consumed.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.recruiting_reply_calls == []


def test_inspecting_another_applicant_invalidates_pending_reply_without_sending(tmp_path) -> None:
	second = InboundApplicant("prospect-2", "招聘对象乙", "3 年 Go 经验")
	second_context = RecruitingProspectContext(second, "本科 · 3年", (), ())
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT, second), 100),),
		prospect_contexts={"prospect-1": CONTEXT, "prospect-2": second_context},
	)
	application, boss, _ = _ready_application(tmp_path, boss=boss)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())

	snapshot = application.execute(InspectRecruitingProspectCommand("prospect-2"), _context()).snapshot

	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.state is WriteIntentState.CANCELLED
	assert snapshot.recruiting is not None
	assert snapshot.recruiting.selected_prospect == second_context
	with pytest.raises(DomainError) as consumed:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())
	assert consumed.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.recruiting_reply_calls == []


@pytest.mark.parametrize(
	("failure", "intent_state"),
	[
		(ErrorCode.AUTHENTICATION_EXPIRED, WriteIntentState.REJECTED),
		(ErrorCode.RATE_LIMITED, WriteIntentState.REJECTED),
		(ErrorCode.PLATFORM_RISK_CONTROL, WriteIntentState.REJECTED),
		(ErrorCode.UNCERTAIN_REMOTE_OUTCOME, WriteIntentState.UNCERTAIN),
	],
)
def test_reply_failure_is_consumed_clears_sensitive_context_and_never_retries(tmp_path, failure, intent_state) -> None:
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT,), 100),),
		prospect_contexts={"prospect-1": CONTEXT},
		recruiting_reply_failure=failure,
	)
	application, boss, _ = _ready_application(tmp_path, boss=boss)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())

	with pytest.raises(DomainError) as failed:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())

	assert failed.value.code is failure
	snapshot = application.query(CurrentStateQuery(), _context())
	assert snapshot.pending_write_intent is not None
	assert snapshot.pending_write_intent.state is intent_state
	assert snapshot.sensitive_content_present is False
	assert snapshot.recruiting is not None
	assert snapshot.recruiting.selected_prospect is None
	with pytest.raises(DomainError) as duplicate:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context("duplicate"))
	assert duplicate.value.code is ErrorCode.WRITE_INTENT_CONSUMED
	assert boss.recruiting_reply_calls == [("prospect-1", MESSAGE)]


def test_concurrent_duplicate_reply_confirmation_executes_at_most_once(tmp_path) -> None:
	gate = threading.Event()
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT,), 100),),
		prospect_contexts={"prospect-1": CONTEXT},
		recruiting_reply_gate=gate,
	)
	application, boss, _ = _ready_application(tmp_path, boss=boss)
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())
	errors: list[DomainError] = []

	def confirm(correlation_id: str) -> None:
		try:
			application.execute(ConfirmWriteIntentCommand("intent-18"), _context(correlation_id))
		except DomainError as exc:
			errors.append(exc)

	first = threading.Thread(target=confirm, args=("first",))
	second = threading.Thread(target=confirm, args=("second",))
	first.start()
	deadline = time.monotonic() + 2
	while not boss.recruiting_reply_calls and time.monotonic() < deadline:
		time.sleep(0.01)
	second.start()
	second.join(timeout=2)
	gate.set()
	first.join(timeout=2)

	assert boss.recruiting_reply_calls == [("prospect-1", MESSAGE)]
	assert [error.code for error in errors] == [ErrorCode.WRITE_INTENT_CONSUMED]


def test_expired_workspace_changed_and_restarted_reply_intents_never_write(tmp_path) -> None:
	current = [NOW]
	application, boss, credentials = _ready_application(tmp_path, clock=lambda: current[0])
	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())
	current[0] += timedelta(minutes=6)
	with pytest.raises(DomainError) as expired:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())
	assert expired.value.code is ErrorCode.WRITE_INTENT_EXPIRED

	application.execute(PrepareRecruitingReplyCommand("prospect-1", MESSAGE), _context())
	application.execute(SwitchWorkspaceCommand(WorkspaceKind.JOB_SEEKING), _context())
	with pytest.raises(DomainError) as changed:
		application.execute(ConfirmWriteIntentCommand("intent-18"), _context())
	assert changed.value.code is ErrorCode.WRITE_INTENT_CONSUMED

	application.execute(SwitchWorkspaceCommand(WorkspaceKind.RECRUITING), _context())
	restarted = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=credentials,
		boss=boss,
	)
	with pytest.raises(DomainError) as missing:
		restarted.execute(ConfirmWriteIntentCommand("intent-18"), _context())
	assert missing.value.code is ErrorCode.WRITE_INTENT_MISSING
	assert boss.recruiting_reply_calls == []
