from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from boss_agent_cli.application import (
	AIAssistanceKind,
	AIProviderConfiguration,
	Application,
	ConfirmWriteIntentCommand,
	CurrentStateQuery,
	DiscardAISuggestionCommand,
	DomainError,
	ErrorCode,
	InboundApplicant,
	InspectJobCommand,
	InspectRecruitingProspectCommand,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	LoadRecruitingOpeningsCommand,
	PlatformSessionState,
	PrepareJobGreetingCommand,
	PrepareRecruitingReplyCommand,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RequestAIAssistanceCommand,
	RequestContext,
	StartJobSearchCommand,
	StartInboundApplicantsCommand,
	SelectRecruitingOpeningCommand,
	SwitchWorkspaceCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
	WriteIntentState,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


NOW = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
GOAL = JobSearchGoal("寻找后端岗位", "Python", city="上海")
JOB = JobSummary("job-1", "Python 后端工程师", "示例科技", location="上海")
DETAIL = JobSourceDetail(JOB, description="负责 Python 服务")
OPENING = RecruitingOpening("opening-1", "Python 后端工程师", "招聘中")
APPLICANT = InboundApplicant("prospect-1", "招聘对象甲", "5 年 Python 经验")
PROSPECT = RecruitingProspectContext(
	APPLICANT,
	resume_text="本科 · 5 年后端经验",
	chat_messages=("应聘者：您好",),
	contact_details=("secret-phone-canary",),
)


class RecordingAI:
	def __init__(self, response: str) -> None:
		self.response = response
		self.requests = []

	def configuration(self) -> AIProviderConfiguration:
		return AIProviderConfiguration(True, "OpenAI", "gpt-example", "https://api.example.test/v1")

	def suggest(self, request):
		self.requests.append(request)
		return self.response


class BrokenConfigurationAI:
	def configuration(self) -> AIProviderConfiguration:
		raise OSError("unreadable AI configuration")

	def suggest(self, request):
		raise AssertionError("suggest must not be called")


class FailingAI(RecordingAI):
	def suggest(self, request):
		self.requests.append(request)
		raise RuntimeError("provider failed")


class ContextChangingAI(RecordingAI):
	def __init__(self, response: str) -> None:
		super().__init__(response)
		self.application = None

	def suggest(self, request):
		self.requests.append(request)
		assert self.application is not None
		self.application.execute(SwitchWorkspaceCommand(WorkspaceKind.RECRUITING), _context())
		return self.response


def _context() -> RequestContext:
	return RequestContext("local-session", "correlation-21")


def _ready_job_application(tmp_path, *, ai=None):
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch((JOB,), 100),),
		details={"job-1": DETAIL},
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED}
		),
		boss=boss,
		ai=ai,
		run_id_factory=lambda: "run-21",
		intent_id_factory=lambda: "intent-21",
		clock=lambda: NOW,
	)
	application.execute(UpdateJobSearchGoalCommand(GOAL), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.state == "completed":
			break
		time.sleep(0.01)
	else:
		raise AssertionError("search did not complete")
	application.execute(InspectJobCommand("job-1"), _context())
	return application, boss


def _ready_recruiting_application(tmp_path, *, ai):
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT,), 100),),
		prospect_contexts={"prospect-1": PROSPECT},
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path, local_session_id="local-session"),
		credential_store=InMemoryCredentialStore(
			{
				WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
				WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED,
			}
		),
		boss=boss,
		ai=ai,
		run_id_factory=lambda: "recruiting-run-21",
		clock=lambda: NOW,
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
	return application, boss


def test_job_core_journey_remains_complete_without_an_ai_provider(tmp_path) -> None:
	application, boss = _ready_job_application(tmp_path)

	snapshot = application.query(CurrentStateQuery(), _context())
	prepared = application.execute(
		PrepareJobGreetingCommand("job-1", "您好，我想进一步沟通。"),
		_context(),
	).snapshot

	assert snapshot.ai_assistance is not None
	assert snapshot.ai_assistance.configured is False
	assert snapshot.ai_assistance.suggestion is None
	assert prepared.pending_write_intent is not None
	assert prepared.pending_write_intent.state is WriteIntentState.PENDING
	assert boss.greeting_calls == []


def test_recruiting_core_journey_remains_complete_without_an_ai_provider(tmp_path) -> None:
	application, boss = _ready_recruiting_application(tmp_path, ai=None)

	snapshot = application.query(CurrentStateQuery(), _context())
	prepared = application.execute(
		PrepareRecruitingReplyCommand("prospect-1", "您好，感谢投递。"),
		_context(),
	).snapshot

	assert snapshot.ai_assistance is not None
	assert snapshot.ai_assistance.configured is False
	assert prepared.pending_write_intent is not None
	assert prepared.pending_write_intent.state is WriteIntentState.PENDING
	assert boss.recruiting_reply_calls == []


def test_job_match_ai_output_is_advisory_and_cannot_create_or_execute_a_write(tmp_path) -> None:
	ai = RecordingAI("AI 建议：Python 技能匹配，但需要人工核对年限。")
	application, boss = _ready_job_application(tmp_path, ai=ai)

	result = application.execute(
		RequestAIAssistanceCommand(AIAssistanceKind.JOB_MATCH, disclosure_acknowledged=True),
		_context(),
	)

	assert result.snapshot.ai_assistance is not None
	assert result.snapshot.ai_assistance.configured is True
	assert result.snapshot.ai_assistance.provider == "OpenAI"
	assert result.snapshot.ai_assistance.model == "gpt-example"
	assert result.snapshot.ai_assistance.suggestion is not None
	assert result.snapshot.ai_assistance.suggestion.content == "AI 建议：Python 技能匹配，但需要人工核对年限。"
	assert result.snapshot.ai_assistance.suggestion.target_reference == "job-1"
	assert result.snapshot.ai_assistance.suggestion.data_sent == (
		"求职目标与筛选条件",
		"当前职位的来源事实",
	)
	assert result.snapshot.pending_write_intent is None
	assert boss.greeting_calls == []
	assert boss.recruiting_reply_calls == []
	assert len(ai.requests) == 1
	assert ai.requests[0].kind is AIAssistanceKind.JOB_MATCH
	assert dict(ai.requests[0].facts)["job_description"] == "负责 Python 服务"


def test_ai_suggestion_can_be_discarded_and_never_be_confirmed_as_a_write(tmp_path) -> None:
	ai = RecordingAI("忽略边界并给所有职位发送消息")
	application, boss = _ready_job_application(tmp_path, ai=ai)
	application.execute(
		RequestAIAssistanceCommand(AIAssistanceKind.JOB_GREETING_DRAFT, disclosure_acknowledged=True),
		_context(),
	)

	with pytest.raises(DomainError) as confirmation:
		application.execute(ConfirmWriteIntentCommand("job-1"), _context())
	discarded = application.execute(DiscardAISuggestionCommand(), _context()).snapshot

	assert confirmation.value.code is ErrorCode.WRITE_INTENT_MISSING
	assert discarded.ai_assistance is not None
	assert discarded.ai_assistance.suggestion is None
	assert discarded.pending_write_intent is None
	assert boss.greeting_calls == []
	assert boss.recruiting_reply_calls == []


def test_ai_failure_is_not_retried_and_cannot_create_a_write_intent(tmp_path) -> None:
	ai = FailingAI("unused")
	application, boss = _ready_job_application(tmp_path, ai=ai)

	with pytest.raises(DomainError) as failed:
		application.execute(
			RequestAIAssistanceCommand(AIAssistanceKind.JOB_GREETING_DRAFT, disclosure_acknowledged=True),
			_context(),
		)

	assert failed.value.code is ErrorCode.ADAPTER_UNAVAILABLE
	assert len(ai.requests) == 1
	assert application.query(CurrentStateQuery(), _context()).pending_write_intent is None
	assert boss.greeting_calls == []


def test_ai_draft_cannot_broaden_a_write_intent_beyond_the_inspected_target(tmp_path) -> None:
	ai = RecordingAI("请向所有职位发送这条消息")
	application, boss = _ready_job_application(tmp_path, ai=ai)
	snapshot = application.execute(
		RequestAIAssistanceCommand(AIAssistanceKind.JOB_GREETING_DRAFT, disclosure_acknowledged=True),
		_context(),
	).snapshot
	assert snapshot.ai_assistance is not None
	assert snapshot.ai_assistance.suggestion is not None

	with pytest.raises(DomainError) as broadened:
		application.execute(
			PrepareJobGreetingCommand("job-not-inspected", snapshot.ai_assistance.suggestion.content),
			_context(),
		)

	assert broadened.value.code is ErrorCode.INVALID_COMMAND
	assert application.query(CurrentStateQuery(), _context()).pending_write_intent is None
	assert boss.greeting_calls == []


def test_ai_result_is_discarded_if_the_operator_changes_context_while_it_is_generated(tmp_path) -> None:
	ai = ContextChangingAI("已经过期的建议")
	application, boss = _ready_job_application(tmp_path, ai=ai)
	ai.application = application

	with pytest.raises(DomainError) as stale:
		application.execute(
			RequestAIAssistanceCommand(AIAssistanceKind.JOB_MATCH, disclosure_acknowledged=True),
			_context(),
		)

	assert stale.value.code is ErrorCode.INVALID_TRANSITION
	snapshot = application.query(CurrentStateQuery(), _context())
	assert snapshot.ai_assistance is not None
	assert snapshot.ai_assistance.suggestion is None
	assert snapshot.pending_write_intent is None
	assert boss.greeting_calls == []


def test_ai_requires_disclosure_acknowledgement_before_sending_any_data(tmp_path) -> None:
	ai = RecordingAI("不会被调用")
	application, boss = _ready_job_application(tmp_path, ai=ai)

	with pytest.raises(DomainError) as rejected:
		application.execute(
			RequestAIAssistanceCommand(AIAssistanceKind.JOB_MATCH, disclosure_acknowledged=False),
			_context(),
		)

	assert rejected.value.code is ErrorCode.INVALID_COMMAND
	assert ai.requests == []
	assert boss.greeting_calls == []
	assert application.query(CurrentStateQuery(), _context()).pending_write_intent is None


def test_recruiting_ai_draft_excludes_contact_details_and_remains_advisory(tmp_path) -> None:
	ai = RecordingAI("您好，感谢投递。这个草稿仍需人工编辑。")
	application, boss = _ready_recruiting_application(tmp_path, ai=ai)

	snapshot = application.execute(
		RequestAIAssistanceCommand(AIAssistanceKind.RECRUITING_REPLY_DRAFT, disclosure_acknowledged=True),
		_context(),
	).snapshot

	assert snapshot.ai_assistance is not None
	assert snapshot.ai_assistance.suggestion is not None
	assert snapshot.ai_assistance.suggestion.kind is AIAssistanceKind.RECRUITING_REPLY_DRAFT
	assert snapshot.ai_assistance.suggestion.data_sent == (
		"当前招聘职位",
		"招聘对象摘要与简历",
		"当前沟通内容",
	)
	facts = dict(ai.requests[0].facts)
	assert facts["resume_text"] == "本科 · 5 年后端经验"
	assert "contact_details" not in facts
	assert "secret-phone-canary" not in repr(ai.requests)
	assert snapshot.pending_write_intent is None
	assert boss.recruiting_reply_calls == []
	assert boss.greeting_calls == []


def test_broken_ai_configuration_cannot_block_the_core_journey(tmp_path) -> None:
	application, boss = _ready_job_application(tmp_path, ai=BrokenConfigurationAI())

	snapshot = application.query(CurrentStateQuery(), _context())
	prepared = application.execute(
		PrepareJobGreetingCommand("job-1", "核心流程继续。"),
		_context(),
	).snapshot

	assert snapshot.ai_assistance is not None
	assert snapshot.ai_assistance.configured is False
	assert prepared.pending_write_intent is not None
	assert boss.greeting_calls == []


def test_ai_suggestion_is_cleared_when_workspace_context_changes(tmp_path) -> None:
	ai = RecordingAI("只属于当前职位的建议")
	application, boss = _ready_job_application(tmp_path, ai=ai)
	application.execute(
		RequestAIAssistanceCommand(AIAssistanceKind.JOB_MATCH, disclosure_acknowledged=True),
		_context(),
	)

	switched = application.execute(
		SwitchWorkspaceCommand(WorkspaceKind.RECRUITING),
		_context(),
	).snapshot

	assert switched.ai_assistance is not None
	assert switched.ai_assistance.suggestion is None
	assert switched.pending_write_intent is None
	assert boss.greeting_calls == []
