from __future__ import annotations

import time

import pytest

from boss_agent_cli.application import (
	Application,
	CancelRunCommand,
	CurrentStateQuery,
	DomainError,
	ErrorCode,
	InboundApplicant,
	InspectRecruitingProspectCommand,
	LoadRecruitingOpeningsCommand,
	PlatformSessionState,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RequestContext,
	SelectRecruitingOpeningCommand,
	StartInboundApplicantsCommand,
	SwitchWorkspaceCommand,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


OPENING = RecruitingOpening(reference="opening-1", title="Python 后端工程师", status="招聘中")
APPLICANT = InboundApplicant(reference="prospect-1", display_name="招聘对象甲", headline="5 年 Python 经验")
CONTEXT = RecruitingProspectContext(
	prospect=APPLICANT,
	resume_text="敏感简历 canary-resume-17",
	chat_messages=("应聘者：您好，我对岗位感兴趣。", "招聘者：感谢关注。"),
	contact_details=("手机号 canary-contact-17",),
)


def _context() -> RequestContext:
	return RequestContext(local_session_id="local-session", correlation_id="correlation-17")


def _application(tmp_path, boss: FakeBossAdapter) -> Application:
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=InMemoryCredentialStore(
			{
				WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
				WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED,
			}
		),
		boss=boss,
		run_id_factory=lambda: "recruiting-1",
	)
	application.execute(SwitchWorkspaceCommand(WorkspaceKind.RECRUITING), _context())
	return application


def _wait_for_state(application: Application, state: str):
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == state:
			return snapshot
		time.sleep(0.01)
	raise AssertionError(f"recruiting run did not reach {state}")


def test_operator_selects_opening_browses_applicants_then_explicitly_loads_context(tmp_path) -> None:
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch(items=(APPLICANT,), progress=60),),
		prospect_contexts={"prospect-1": CONTEXT},
	)
	application = _application(tmp_path, boss)

	loaded = application.execute(LoadRecruitingOpeningsCommand(), _context()).snapshot
	assert loaded.recruiting is not None
	assert loaded.recruiting.openings == (OPENING,)
	assert boss.prospect_context_calls == []

	selected = application.execute(SelectRecruitingOpeningCommand("opening-1"), _context()).snapshot
	assert selected.recruiting is not None
	assert selected.recruiting.selected_opening == OPENING
	application.execute(StartInboundApplicantsCommand(), _context())
	completed = _wait_for_state(application, "completed")
	assert completed.recruiting is not None
	assert completed.recruiting.applicants == (APPLICANT,)
	assert boss.prospect_context_calls == []

	inspected = application.execute(InspectRecruitingProspectCommand("prospect-1"), _context()).snapshot
	assert inspected.recruiting is not None
	assert inspected.recruiting.selected_prospect == CONTEXT
	assert inspected.sensitive_content_present is True
	assert boss.prospect_context_calls == ["prospect-1"]


def test_sensitive_recruiting_content_is_memory_only_and_cleared_on_switch_and_restart(tmp_path) -> None:
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch(items=(APPLICANT,), progress=100),),
		prospect_contexts={"prospect-1": CONTEXT},
	)
	application = _application(tmp_path, boss)
	application.execute(LoadRecruitingOpeningsCommand(), _context())
	application.execute(SelectRecruitingOpeningCommand("opening-1"), _context())
	application.execute(StartInboundApplicantsCommand(), _context())
	_wait_for_state(application, "completed")
	application.execute(InspectRecruitingProspectCommand("prospect-1"), _context())

	application.execute(SwitchWorkspaceCommand(WorkspaceKind.JOB_SEEKING), _context())
	returned = application.execute(SwitchWorkspaceCommand(WorkspaceKind.RECRUITING), _context()).snapshot
	assert returned.recruiting is not None
	assert returned.recruiting.selected_opening == OPENING
	assert returned.recruiting.applicants == ()
	assert returned.recruiting.selected_prospect is None
	assert returned.sensitive_content_present is False

	restarted = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED},
		),
		boss=boss,
	)
	snapshot = restarted.query(CurrentStateQuery(), _context())
	assert snapshot.recruiting is not None
	assert snapshot.recruiting.selected_opening == OPENING
	assert snapshot.recruiting.applicants == ()
	assert snapshot.recruiting.selected_prospect is None
	stored = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
	for canary in (
		"招聘对象甲",
		"canary-resume-17",
		"应聘者：您好，我对岗位感兴趣。",
		"canary-contact-17",
	):
		assert canary.encode() not in stored


def test_failed_explicit_inspection_clears_previously_loaded_resume_and_chat(tmp_path) -> None:
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch(items=(APPLICANT,), progress=100),),
		prospect_contexts={"prospect-1": CONTEXT},
	)
	application = _application(tmp_path, boss)
	application.execute(LoadRecruitingOpeningsCommand(), _context())
	application.execute(SelectRecruitingOpeningCommand("opening-1"), _context())
	application.execute(StartInboundApplicantsCommand(), _context())
	_wait_for_state(application, "completed")
	application.execute(InspectRecruitingProspectCommand("prospect-1"), _context())
	boss.search_failure = ErrorCode.AUTHENTICATION_EXPIRED

	with pytest.raises(DomainError):
		application.execute(InspectRecruitingProspectCommand("prospect-1"), _context())

	recovery = application.query(CurrentStateQuery(), _context())
	assert recovery.recruiting is not None
	assert recovery.recruiting.applicants == (APPLICANT,)
	assert recovery.recruiting.selected_prospect is None


@pytest.mark.parametrize(
	"failure_code",
	[
		ErrorCode.AUTHENTICATION_EXPIRED,
		ErrorCode.RATE_LIMITED,
		ErrorCode.PLATFORM_RISK_CONTROL,
	],
)
def test_partial_applicants_stop_in_explicit_recovery_without_loading_context(tmp_path, failure_code) -> None:
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch(items=(APPLICANT,), progress=45),),
		search_failure=failure_code,
		failure_after_batches=1,
	)
	application = _application(tmp_path, boss)
	# Opening load is a separate successful read before the failure is armed.
	boss.search_failure = None
	application.execute(LoadRecruitingOpeningsCommand(), _context())
	application.execute(SelectRecruitingOpeningCommand("opening-1"), _context())
	boss.search_failure = failure_code

	application.execute(StartInboundApplicantsCommand(), _context())
	snapshot = _wait_for_state(application, "recovery")

	assert snapshot.error is not None
	assert snapshot.error.code is failure_code
	assert snapshot.platform_session is PlatformSessionState.RECOVERY
	assert snapshot.recruiting is not None
	assert snapshot.recruiting.applicants == (APPLICANT,)
	assert snapshot.recruiting.selected_prospect is None
	assert boss.prospect_context_calls == []


def test_cancelling_applicant_read_clears_transient_results(tmp_path) -> None:
	import threading

	gate = threading.Event()
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(
			RecruitingApplicantBatch(items=(APPLICANT,), progress=50),
			RecruitingApplicantBatch(items=(InboundApplicant("prospect-2", "招聘对象乙"),), progress=90),
		),
		batch_gate=gate,
	)
	application = _application(tmp_path, boss)
	application.execute(LoadRecruitingOpeningsCommand(), _context())
	application.execute(SelectRecruitingOpeningCommand("opening-1"), _context())
	started = application.execute(StartInboundApplicantsCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.recruiting is not None and snapshot.recruiting.applicants:
			break
		time.sleep(0.01)

	application.execute(CancelRunCommand(started.resource_ref or ""), _context())
	cancelled = _wait_for_state(application, "cancelled")
	gate.set()

	assert cancelled.recruiting is not None
	assert cancelled.recruiting.applicants == ()
	assert cancelled.recruiting.selected_prospect is None
	assert cancelled.sensitive_content_present is False
