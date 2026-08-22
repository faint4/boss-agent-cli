"""Authoritative application module implementation."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Protocol

from boss_agent_cli.application.contracts import (
	ApplicationEvent,
	ApplicationStateSnapshot,
	CancelRunCommand,
	CancelWriteIntentCommand,
	CommandResult,
	ConfirmWriteIntentCommand,
	ConnectPlatformSessionCommand,
	CurrentStateQuery,
	DomainError,
	DomainErrorDetails,
	ErrorCode,
	InboundApplicant,
	InspectJobCommand,
	InspectRecruitingProspectCommand,
	JobDetailView,
	JobSearchBatch,
	JobSearchGoal,
	JobSeekingState,
	JobSourceDetail,
	JobSummary,
	LoadRecruitingOpeningsCommand,
	LogoutPlatformSessionCommand,
	PlatformSessionState,
	PrepareJobGreetingCommand,
	PrepareRecruitingReplyCommand,
	RequestContext,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RecruitingState,
	RunEventKind,
	RunSummary,
	SetShortlistedCommand,
	SelectRecruitingOpeningCommand,
	StartInboundApplicantsCommand,
	StartJobSearchCommand,
	SwitchWorkspaceCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
	WriteIntentState,
	WriteIntentSummary,
)


class WorkspaceStore(Protocol):
	def active_workspace(self, local_session_id: str) -> WorkspaceKind: ...
	def switch_workspace(self, local_session_id: str, workspace: WorkspaceKind) -> None: ...
	def sensitive_content_present(self, workspace: WorkspaceKind) -> bool: ...
	def last_transition(self, local_session_id: str) -> str | None: ...
	def read_events(self, run_id: str) -> tuple[ApplicationEvent, ...]: ...
	def load_job_search_goal(self) -> JobSearchGoal | None: ...
	def save_job_search_goal(self, goal: JobSearchGoal) -> None: ...
	def load_job_shortlist(self) -> tuple[JobSummary, ...]: ...
	def save_job_shortlist(self, items: tuple[JobSummary, ...]) -> None: ...
	def load_recruiting_opening(self) -> RecruitingOpening | None: ...
	def save_recruiting_opening(self, opening: RecruitingOpening) -> None: ...


class CredentialStore(Protocol):
	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState: ...
	def activate(self, workspace: WorkspaceKind) -> None: ...
	def begin_connect(self, workspace: WorkspaceKind) -> None: ...
	def begin_logout(self, workspace: WorkspaceKind) -> None: ...
	def session_revision(self, workspace: WorkspaceKind) -> str: ...


class BossAdapterFailure(RuntimeError):
	"""Typed failure at the external BOSS boundary."""

	def __init__(self, code: ErrorCode, message: str = "BOSS read failed") -> None:
		super().__init__(message)
		self.code = code


class BossAdapter(Protocol):
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState: ...
	def search_jobs(
		self,
		goal: JobSearchGoal,
		*,
		cancel_requested: Callable[[], bool],
	) -> Iterable[JobSearchBatch]: ...
	def job_detail(self, reference: str) -> JobSourceDetail: ...
	def send_greeting(self, reference: str, message: str) -> None: ...
	def send_recruiting_reply(self, reference: str, message: str) -> None: ...
	def list_openings(self) -> tuple[RecruitingOpening, ...]: ...
	def inbound_applicants(
		self,
		opening_reference: str,
		*,
		cancel_requested: Callable[[], bool],
	) -> Iterable[RecruitingApplicantBatch]: ...
	def prospect_context(
		self,
		opening_reference: str,
		prospect_reference: str,
	) -> RecruitingProspectContext: ...


@dataclass(frozen=True)
class _WriteIntent:
	summary: WriteIntentSummary
	message: str
	session_revision: str
	operation: str


_RECOVERY_ACTIONS = {
	ErrorCode.AUTHENTICATION_EXPIRED: "Reconnect BOSS, then retry the read-only action",
	ErrorCode.RATE_LIMITED: "Wait before retrying the read-only action manually",
	ErrorCode.PLATFORM_RISK_CONTROL: "Complete verification in BOSS, then retry manually",
	ErrorCode.ADAPTER_UNAVAILABLE: "Check the BOSS connection, then retry the read-only action",
}


class Application:
	"""Deep module shared by Web, CLI, and MCP adapters."""

	def __init__(
		self,
		*,
		workspace_store: WorkspaceStore,
		credential_store: CredentialStore,
		boss: BossAdapter,
		run_id_factory: Callable[[], str] | None = None,
		intent_id_factory: Callable[[], str] | None = None,
		clock: Callable[[], datetime] | None = None,
	) -> None:
		self._workspace_store = workspace_store
		self._credential_store = credential_store
		self._boss = boss
		self._run_id_factory = run_id_factory or (lambda: f"search-{secrets.token_urlsafe(12)}")
		self._intent_id_factory = intent_id_factory or (lambda: f"write-{secrets.token_urlsafe(18)}")
		self._clock = clock or (lambda: datetime.now(timezone.utc))
		self._lock = threading.RLock()
		self._active_run: RunSummary | None = None
		self._run_workspace: WorkspaceKind | None = None
		self._events: dict[str, list[ApplicationEvent]] = {}
		self._cancel_events: dict[str, threading.Event] = {}
		self._job_results: tuple[JobSummary, ...] = ()
		self._selected_job: JobDetailView | None = None
		self._selected_job_session_revision: str | None = None
		self._opening_results: tuple[RecruitingOpening, ...] = ()
		self._applicant_results: tuple[InboundApplicant, ...] = ()
		self._selected_prospect: RecruitingProspectContext | None = None
		self._last_error: DomainErrorDetails | None = None
		self._write_intent: _WriteIntent | None = None

	def _session_revision(self, workspace: WorkspaceKind) -> str:
		provider = getattr(self._credential_store, "session_revision", None)
		if provider is None:
			return self._credential_store.platform_session_state(workspace).value
		return str(provider(workspace))

	def _invalidate_write_intent(self, message: str) -> None:
		with self._lock:
			if self._write_intent is None or self._write_intent.summary.state is not WriteIntentState.PENDING:
				return
			self._write_intent = replace(
				self._write_intent,
				summary=replace(
					self._write_intent.summary,
					state=WriteIntentState.CANCELLED,
					outcome_message=message,
				),
			)
			if self._write_intent.summary.workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()

	def _active_workspace(self, context: RequestContext) -> WorkspaceKind:
		try:
			return self._workspace_store.active_workspace(context.local_session_id)
		except PermissionError as exc:
			raise DomainError(
				code=ErrorCode.UNAUTHORIZED_CONTEXT,
				message="Local session is not authorized",
				correlation_id=context.correlation_id,
				recoverable=False,
			) from exc

	def _require_job_workspace(self, workspace: WorkspaceKind, context: RequestContext) -> None:
		if workspace is not WorkspaceKind.JOB_SEEKING:
			raise DomainError(
				code=ErrorCode.WORKSPACE_MISMATCH,
				message="This action belongs to the Job-Seeking workspace",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Switch to the Job-Seeking workspace",
			)

	def _require_recruiting_workspace(self, workspace: WorkspaceKind, context: RequestContext) -> None:
		if workspace is not WorkspaceKind.RECRUITING:
			raise DomainError(
				code=ErrorCode.WORKSPACE_MISMATCH,
				message="This action belongs to the Recruiting workspace",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Switch to the Recruiting workspace",
			)

	def _clear_recruiting_sensitive(self) -> None:
		with self._lock:
			self._applicant_results = ()
			self._selected_prospect = None

	def _emit(self, run_id: str, kind: RunEventKind, **payload: object) -> None:
		with self._lock:
			events = self._events.setdefault(run_id, [])
			events.append(
				ApplicationEvent(
					cursor=len(events) + 1,
					run_id=run_id,
					kind=kind,
					occurred_at=datetime.now(timezone.utc),
					payload=tuple(payload.items()),
				)
			)

	def _adapter_error(self, failure: BossAdapterFailure, correlation_id: str) -> DomainErrorDetails:
		code = failure.code if failure.code in _RECOVERY_ACTIONS else ErrorCode.ADAPTER_UNAVAILABLE
		messages = {
			ErrorCode.AUTHENTICATION_EXPIRED: "BOSS session expired; the run was safely paused",
			ErrorCode.RATE_LIMITED: "BOSS rate limit reached; no automatic retry was attempted",
			ErrorCode.PLATFORM_RISK_CONTROL: "BOSS requested verification; no bypass was attempted",
			ErrorCode.ADAPTER_UNAVAILABLE: "BOSS is unavailable for this read-only action",
		}
		return DomainErrorDetails(
			code=code,
			message=messages[code],
			recoverable=True,
			recovery_action=_RECOVERY_ACTIONS[code],
			correlation_id=correlation_id,
		)

	def _start_search_worker(
		self,
		*,
		run_id: str,
		goal: JobSearchGoal,
		correlation_id: str,
		cancel_event: threading.Event,
	) -> None:
		def work() -> None:
			try:
				for batch in self._boss.search_jobs(goal, cancel_requested=cancel_event.is_set):
					if cancel_event.is_set():
						break
					progress = max(0, min(100, batch.progress))
					with self._lock:
						known = {item.reference: item for item in self._job_results}
						known.update({item.reference: item for item in batch.items})
						self._job_results = tuple(known.values())
						self._active_run = RunSummary(run_id=run_id, state="running", progress=progress)
						result_count = len(self._job_results)
					self._emit(run_id, RunEventKind.PROGRESS, progress=progress, result_count=result_count)
				with self._lock:
					current_progress = (self._active_run.progress if self._active_run else 0) or 0
					state = "cancelled" if cancel_event.is_set() else "completed"
					self._active_run = RunSummary(
						run_id=run_id,
						state=state,
						progress=current_progress if state == "cancelled" else 100,
					)
				self._emit(run_id, RunEventKind.STATE_CHANGED, state=state)
			except BossAdapterFailure as failure:
				self._record_search_failure(run_id, self._adapter_error(failure, correlation_id))
			except (OSError, RuntimeError) as failure:
				details = self._adapter_error(
					BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE, str(failure)),
					correlation_id,
				)
				self._record_search_failure(run_id, details)

		threading.Thread(target=work, name=f"boss-{run_id}", daemon=True).start()

	def _record_search_failure(self, run_id: str, details: DomainErrorDetails) -> None:
		with self._lock:
			progress = self._active_run.progress if self._active_run else 0
			self._active_run = RunSummary(run_id=run_id, state="recovery", progress=progress)
			self._last_error = details
		self._emit(run_id, RunEventKind.RECOVERY_REQUIRED, code=details.code.value)

	def execute(
		self,
		command: (
			SwitchWorkspaceCommand
			| ConnectPlatformSessionCommand
			| LogoutPlatformSessionCommand
			| UpdateJobSearchGoalCommand
			| StartJobSearchCommand
			| CancelRunCommand
			| InspectJobCommand
			| SetShortlistedCommand
			| PrepareJobGreetingCommand
			| PrepareRecruitingReplyCommand
			| ConfirmWriteIntentCommand
			| CancelWriteIntentCommand
			| LoadRecruitingOpeningsCommand
			| SelectRecruitingOpeningCommand
			| StartInboundApplicantsCommand
			| InspectRecruitingProspectCommand
		),
		context: RequestContext,
	) -> CommandResult:
		workspace = self._active_workspace(context)
		if isinstance(command, SwitchWorkspaceCommand):
			self._invalidate_write_intent("工作区已切换，本次确认已取消。")
			if workspace is WorkspaceKind.RECRUITING:
				with self._lock:
					if self._active_run is not None and self._run_workspace is workspace:
						cancel_event = self._cancel_events.get(self._active_run.run_id)
						if cancel_event is not None:
							cancel_event.set()
				self._clear_recruiting_sensitive()
			try:
				self._workspace_store.switch_workspace(context.local_session_id, command.workspace)
				self._credential_store.activate(command.workspace)
			except PermissionError as exc:
				raise DomainError(
					code=ErrorCode.UNAUTHORIZED_CONTEXT,
					message="Local session is not authorized",
					correlation_id=context.correlation_id,
					recoverable=False,
				) from exc
			except OSError as exc:
				raise DomainError(
					code=ErrorCode.STORAGE_UNAVAILABLE,
					message="Workspace storage is unavailable",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Retry the workspace switch",
				) from exc
			return CommandResult(snapshot=self.query(CurrentStateQuery(), context))
		if isinstance(command, ConnectPlatformSessionCommand):
			self._invalidate_write_intent("平台会话正在重新连接，本次确认已取消。")
			if workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()
			self._credential_store.begin_connect(workspace)
			return CommandResult(snapshot=self.query(CurrentStateQuery(), context))
		if isinstance(command, LogoutPlatformSessionCommand):
			self._invalidate_write_intent("平台会话已退出，本次确认已取消。")
			if workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()
			self._credential_store.begin_logout(workspace)
			return CommandResult(snapshot=self.query(CurrentStateQuery(), context))
		if isinstance(command, UpdateJobSearchGoalCommand):
			self._require_job_workspace(workspace, context)
			if not command.goal.objective.strip() or not command.goal.keyword.strip():
				raise DomainError(
					code=ErrorCode.INVALID_COMMAND,
					message="Objective and keyword are required",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Complete the goal and keyword fields",
				)
			try:
				self._workspace_store.save_job_search_goal(command.goal)
			except OSError as exc:
				raise DomainError(
					code=ErrorCode.STORAGE_UNAVAILABLE,
					message="Job-search goal could not be saved",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Retry saving the goal",
				) from exc
			return CommandResult(snapshot=self.query(CurrentStateQuery(), context))
		if isinstance(command, StartJobSearchCommand):
			return self._start_job_search(workspace, context)
		if isinstance(command, CancelRunCommand):
			return self._cancel_run(command, workspace, context)
		if isinstance(command, InspectJobCommand):
			return self._inspect_job(command, workspace, context)
		if isinstance(command, SetShortlistedCommand):
			return self._set_shortlisted(command, workspace, context)
		if isinstance(command, PrepareJobGreetingCommand):
			return self._prepare_job_greeting(command, workspace, context)
		if isinstance(command, PrepareRecruitingReplyCommand):
			return self._prepare_recruiting_reply(command, workspace, context)
		if isinstance(command, ConfirmWriteIntentCommand):
			return self._confirm_write_intent(command, workspace, context)
		if isinstance(command, CancelWriteIntentCommand):
			return self._cancel_write_intent(command, workspace, context)
		if isinstance(command, LoadRecruitingOpeningsCommand):
			return self._load_recruiting_openings(workspace, context)
		if isinstance(command, SelectRecruitingOpeningCommand):
			return self._select_recruiting_opening(command, workspace, context)
		if isinstance(command, StartInboundApplicantsCommand):
			return self._start_inbound_applicants(workspace, context)
		if isinstance(command, InspectRecruitingProspectCommand):
			return self._inspect_recruiting_prospect(command, workspace, context)
		raise DomainError(
			code=ErrorCode.UNSUPPORTED_COMMAND,
			message=f"Command {type(command).__name__} is not available yet",
			correlation_id=context.correlation_id,
			recoverable=False,
		)

	def _require_connected(self, workspace: WorkspaceKind, context: RequestContext) -> None:
		if self._credential_store.platform_session_state(workspace) is not PlatformSessionState.CONNECTED:
			raise DomainError(
				code=ErrorCode.AUTHENTICATION_REQUIRED,
				message="Connect BOSS before this remote read",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Connect the BOSS platform session",
			)

	def _load_recruiting_openings(
		self,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_recruiting_workspace(workspace, context)
		self._require_connected(workspace, context)
		try:
			openings = self._boss.list_openings()
		except BossAdapterFailure as failure:
			details = self._adapter_error(failure, context.correlation_id)
			with self._lock:
				self._last_error = details
			raise DomainError(
				code=details.code,
				message=details.message,
				correlation_id=details.correlation_id,
				recoverable=True,
				recovery_action=details.recovery_action,
			) from failure
		with self._lock:
			self._opening_results = openings
			self._last_error = None
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context))

	def _select_recruiting_opening(
		self,
		command: SelectRecruitingOpeningCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_recruiting_workspace(workspace, context)
		with self._lock:
			opening = next((item for item in self._opening_results if item.reference == command.reference), None)
		if opening is None:
			raise DomainError(
				code=ErrorCode.INVALID_COMMAND,
				message="Only a visible recruiting opening can be selected",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Load recruiting openings first",
			)
		with self._lock:
			current = self._workspace_store.load_recruiting_opening()
			if current is not None and current.reference != opening.reference:
				self._invalidate_write_intent("招聘职位已改变，本次确认已取消。")
		self._workspace_store.save_recruiting_opening(opening)
		self._clear_recruiting_sensitive()
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=opening.reference)

	def _start_inbound_applicants(
		self,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_recruiting_workspace(workspace, context)
		self._require_connected(workspace, context)
		opening = self._workspace_store.load_recruiting_opening()
		if opening is None:
			raise DomainError(
				code=ErrorCode.INVALID_COMMAND,
				message="Select an opening before loading Inbound Applicants",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Load and select a recruiting opening",
			)
		with self._lock:
			if self._active_run is not None and self._active_run.state in {"running", "cancelling"}:
				raise DomainError(
					code=ErrorCode.INVALID_TRANSITION,
					message="A remote read is already running",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Cancel or wait for the active read",
				)
			run_id = self._run_id_factory()
			cancel_event = threading.Event()
			self._cancel_events[run_id] = cancel_event
			self._run_workspace = workspace
			self._active_run = RunSummary(run_id=run_id, state="running", progress=0)
			self._applicant_results = ()
			self._selected_prospect = None
			self._last_error = None
			self._events[run_id] = []
		self._emit(run_id, RunEventKind.STATE_CHANGED, state="running")
		self._start_applicant_worker(
			run_id=run_id,
			opening_reference=opening.reference,
			correlation_id=context.correlation_id,
			cancel_event=cancel_event,
		)
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=run_id)

	def _start_applicant_worker(
		self,
		*,
		run_id: str,
		opening_reference: str,
		correlation_id: str,
		cancel_event: threading.Event,
	) -> None:
		def work() -> None:
			try:
				for batch in self._boss.inbound_applicants(
					opening_reference,
					cancel_requested=cancel_event.is_set,
				):
					if cancel_event.is_set():
						break
					progress = max(0, min(100, batch.progress))
					with self._lock:
						known = {item.reference: item for item in self._applicant_results}
						known.update({item.reference: item for item in batch.items})
						self._applicant_results = tuple(known.values())
						self._active_run = RunSummary(run_id=run_id, state="running", progress=progress)
						count = len(self._applicant_results)
					self._emit(run_id, RunEventKind.PROGRESS, progress=progress, result_count=count)
				with self._lock:
					progress = (self._active_run.progress if self._active_run else 0) or 0
					state = "cancelled" if cancel_event.is_set() else "completed"
					self._active_run = RunSummary(
						run_id=run_id,
						state=state,
						progress=progress if state == "cancelled" else 100,
					)
					if state == "cancelled":
						self._clear_recruiting_sensitive()
				self._emit(run_id, RunEventKind.STATE_CHANGED, state=state)
			except BossAdapterFailure as failure:
				self._record_recruiting_failure(run_id, self._adapter_error(failure, correlation_id))
			except (OSError, RuntimeError) as failure:
				self._record_recruiting_failure(
					run_id,
					self._adapter_error(
						BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE, str(failure)),
						correlation_id,
					),
				)

		threading.Thread(target=work, name=f"boss-{run_id}", daemon=True).start()

	def _record_recruiting_failure(self, run_id: str, details: DomainErrorDetails) -> None:
		with self._lock:
			progress = self._active_run.progress if self._active_run else 0
			self._active_run = RunSummary(run_id=run_id, state="recovery", progress=progress)
			self._selected_prospect = None
			self._last_error = details
		self._emit(run_id, RunEventKind.RECOVERY_REQUIRED, code=details.code.value)

	def _inspect_recruiting_prospect(
		self,
		command: InspectRecruitingProspectCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_recruiting_workspace(workspace, context)
		self._require_connected(workspace, context)
		opening = self._workspace_store.load_recruiting_opening()
		with self._lock:
			visible = command.reference in {item.reference for item in self._applicant_results}
		if opening is None or not visible:
			raise DomainError(
				code=ErrorCode.INVALID_COMMAND,
				message="Only a visible Inbound Applicant can be inspected",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Load Inbound Applicants first",
			)
		with self._lock:
			if (
				self._write_intent is not None
				and self._write_intent.summary.workspace is WorkspaceKind.RECRUITING
				and self._write_intent.summary.target_reference != command.reference
			):
				self._invalidate_write_intent("招聘对象已改变，本次确认已取消。")
		try:
			prospect = self._boss.prospect_context(opening.reference, command.reference)
		except BossAdapterFailure as failure:
			details = self._adapter_error(failure, context.correlation_id)
			with self._lock:
				self._selected_prospect = None
				self._last_error = details
			raise DomainError(
				code=details.code,
				message=details.message,
				correlation_id=details.correlation_id,
				recoverable=True,
				recovery_action=details.recovery_action,
			) from failure
		with self._lock:
			self._selected_prospect = prospect
			self._last_error = None
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=command.reference)

	def _start_job_search(self, workspace: WorkspaceKind, context: RequestContext) -> CommandResult:
		self._require_job_workspace(workspace, context)
		if self._credential_store.platform_session_state(workspace) is not PlatformSessionState.CONNECTED:
			raise DomainError(
				code=ErrorCode.AUTHENTICATION_REQUIRED,
				message="Connect BOSS before starting a search",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Connect the BOSS platform session",
			)
		goal = self._workspace_store.load_job_search_goal()
		if goal is None:
			raise DomainError(
				code=ErrorCode.INVALID_COMMAND,
				message="Save a job-search goal before searching",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Define and save the job-search goal",
			)
		with self._lock:
			if self._active_run is not None and self._active_run.state in {"running", "cancelling"}:
				raise DomainError(
					code=ErrorCode.INVALID_TRANSITION,
					message="A search is already running",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Cancel or wait for the active search",
				)
			run_id = self._run_id_factory()
			cancel_event = threading.Event()
			self._cancel_events[run_id] = cancel_event
			self._run_workspace = workspace
			self._active_run = RunSummary(run_id=run_id, state="running", progress=0)
			self._job_results = ()
			self._selected_job = None
			self._selected_job_session_revision = None
			self._last_error = None
			self._events[run_id] = []
		self._emit(run_id, RunEventKind.STATE_CHANGED, state="running")
		self._start_search_worker(
			run_id=run_id,
			goal=goal,
			correlation_id=context.correlation_id,
			cancel_event=cancel_event,
		)
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=run_id)

	def _cancel_run(
		self,
		command: CancelRunCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		with self._lock:
			cancel_event = self._cancel_events.get(command.run_id)
			if (
				cancel_event is None
				or self._active_run is None
				or self._active_run.run_id != command.run_id
				or self._run_workspace is not workspace
			):
				raise DomainError(
					code=ErrorCode.RUN_NOT_FOUND,
					message="Remote read run was not found",
					correlation_id=context.correlation_id,
					recoverable=False,
				)
			if self._active_run.state not in {"running", "cancelling"}:
				raise DomainError(
					code=ErrorCode.INVALID_TRANSITION,
					message="Only a running remote read can be cancelled",
					correlation_id=context.correlation_id,
					recoverable=False,
				)
			cancel_event.set()
			self._active_run = RunSummary(
				run_id=command.run_id,
				state="cancelling",
				progress=self._active_run.progress,
			)
			if workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()
		self._emit(command.run_id, RunEventKind.STATE_CHANGED, state="cancelling")
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=command.run_id)

	def _inspect_job(
		self,
		command: InspectJobCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_job_workspace(workspace, context)
		with self._lock:
			if command.reference not in {item.reference for item in self._job_results}:
				raise DomainError(
					code=ErrorCode.INVALID_COMMAND,
					message="Only a visible search result can be inspected",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Search for the job first",
				)
		try:
			detail = self._boss.job_detail(command.reference)
		except BossAdapterFailure as failure:
			details = self._adapter_error(failure, context.correlation_id)
			raise DomainError(
				code=details.code,
				message=details.message,
				correlation_id=details.correlation_id,
				recoverable=True,
				recovery_action=details.recovery_action,
			) from failure
		with self._lock:
			self._selected_job = JobDetailView(
				source=detail,
				match_reasons=self._match_reasons(self._workspace_store.load_job_search_goal(), detail),
			)
			self._selected_job_session_revision = self._session_revision(workspace)
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=command.reference)

	def _set_shortlisted(
		self,
		command: SetShortlistedCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_job_workspace(workspace, context)
		items = {item.reference: item for item in self._workspace_store.load_job_shortlist()}
		if command.shortlisted:
			candidates = {item.reference: item for item in self._job_results}
			if self._selected_job is not None:
				candidates[self._selected_job.source.job.reference] = self._selected_job.source.job
			job = candidates.get(command.reference)
			if job is None:
				raise DomainError(
					code=ErrorCode.INVALID_COMMAND,
					message="Only a visible job can be added to the shortlist",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Search for or inspect the job first",
				)
			items[job.reference] = job
		else:
			items.pop(command.reference, None)
		self._workspace_store.save_job_shortlist(tuple(items.values()))
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=command.reference)

	def _prepare_job_greeting(
		self,
		command: PrepareJobGreetingCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_job_workspace(workspace, context)
		if self._credential_store.platform_session_state(workspace) is not PlatformSessionState.CONNECTED:
			raise DomainError(
				code=ErrorCode.AUTHENTICATION_REQUIRED,
				message="Connect BOSS before preparing a greeting",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Connect the BOSS platform session",
			)
		message = command.message.strip()
		if not message or len(message) > 1000:
			raise DomainError(
				code=ErrorCode.INVALID_COMMAND,
				message="Greeting must contain between 1 and 1000 characters",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Review the greeting text",
			)
		with self._lock:
			selected = self._selected_job
			if selected is None or selected.source.job.reference != command.reference:
				raise DomainError(
					code=ErrorCode.INVALID_COMMAND,
					message="Only the currently inspected job can receive a greeting",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Inspect the job before preparing the greeting",
				)
			if self._selected_job_session_revision != self._session_revision(workspace):
				raise DomainError(
					code=ErrorCode.AUTHENTICATION_EXPIRED,
					message="BOSS session changed after the job was inspected",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Inspect the job again before preparing a greeting",
				)
			if self._write_intent is not None and self._write_intent.summary.state in {
				WriteIntentState.PENDING,
				WriteIntentState.EXECUTING,
			}:
				raise DomainError(
					code=ErrorCode.INVALID_TRANSITION,
					message="Resolve the active write confirmation before preparing another",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Confirm or cancel the active write confirmation",
				)
			job = selected.source.job
			summary = WriteIntentSummary(
				intent_id=self._intent_id_factory(),
				workspace=workspace,
				target_reference=job.reference,
				target_label=f"{job.title} · {job.company}",
				action="发送 BOSS 招呼",
				payload_preview=message,
				warnings=("确认后将立即向 BOSS 发送一条招呼，且不会自动重试。",),
				expires_at=self._clock() + timedelta(minutes=5),
				state=WriteIntentState.PENDING,
			)
			self._write_intent = _WriteIntent(
				summary=summary,
				message=message,
				session_revision=self._session_revision(workspace),
				operation="job-greeting",
			)
			self._last_error = None
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=summary.intent_id)

	def _prepare_recruiting_reply(
		self,
		command: PrepareRecruitingReplyCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		self._require_recruiting_workspace(workspace, context)
		self._require_connected(workspace, context)
		message = command.message.strip()
		if not message or len(message) > 1000:
			raise DomainError(
				code=ErrorCode.INVALID_COMMAND,
				message="Recruiting reply must contain between 1 and 1000 characters",
				correlation_id=context.correlation_id,
				recoverable=True,
				recovery_action="Review the recruiting reply text",
			)
		with self._lock:
			selected = self._selected_prospect
			opening = self._workspace_store.load_recruiting_opening()
			if selected is None or selected.prospect.reference != command.reference or opening is None:
				raise DomainError(
					code=ErrorCode.INVALID_COMMAND,
					message="Only the currently inspected Recruiting Prospect can receive a reply",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Inspect the Recruiting Prospect before preparing a reply",
				)
			if self._write_intent is not None and self._write_intent.summary.state in {
				WriteIntentState.PENDING,
				WriteIntentState.EXECUTING,
			}:
				raise DomainError(
					code=ErrorCode.INVALID_TRANSITION,
					message="Resolve the active write confirmation before preparing another",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Confirm or cancel the active write confirmation",
				)
			summary = WriteIntentSummary(
				intent_id=self._intent_id_factory(),
				workspace=workspace,
				target_reference=selected.prospect.reference,
				target_label=selected.prospect.display_name,
				context_label=opening.title,
				destination_label="BOSS 招聘沟通会话",
				action="回复 BOSS 招聘沟通",
				payload_preview=message,
				warnings=("确认后将立即向这一位招聘对象回复一次，且不会自动重试。",),
				expires_at=self._clock() + timedelta(minutes=5),
				state=WriteIntentState.PENDING,
			)
			self._write_intent = _WriteIntent(
				summary=summary,
				message=message,
				session_revision=self._session_revision(workspace),
				operation="recruiting-reply",
			)
			self._last_error = None
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=summary.intent_id)

	def _write_error(
		self,
		*,
		code: ErrorCode,
		context: RequestContext,
		message: str,
		recovery_action: str | None,
	) -> DomainError:
		return DomainError(
			code=code,
			message=message,
			correlation_id=context.correlation_id,
			recoverable=recovery_action is not None,
			recovery_action=recovery_action,
		)

	def _require_pending_intent(
		self,
		intent_id: str,
		context: RequestContext,
	) -> _WriteIntent:
		intent = self._write_intent
		if intent is None or intent.summary.intent_id != intent_id:
			raise self._write_error(
				code=ErrorCode.WRITE_INTENT_MISSING,
				context=context,
				message="Write confirmation was not found",
				recovery_action=None,
			)
		if intent.summary.state is not WriteIntentState.PENDING:
			raise self._write_error(
				code=ErrorCode.WRITE_INTENT_CONSUMED,
				context=context,
				message="Write confirmation has already been consumed",
				recovery_action=None,
			)
		return intent

	def _confirm_write_intent(
		self,
		command: ConfirmWriteIntentCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		with self._lock:
			intent = self._require_pending_intent(command.intent_id, context)
			if self._clock() >= intent.summary.expires_at:
				self._write_intent = replace(
					intent,
					summary=replace(
						intent.summary,
						state=WriteIntentState.EXPIRED,
						outcome_message="确认已过期，没有执行平台写入。",
					),
				)
				raise self._write_error(
					code=ErrorCode.WRITE_INTENT_EXPIRED,
					context=context,
					message="Write confirmation expired; nothing was sent",
					recovery_action="Prepare a new confirmation",
				)
			if workspace is not intent.summary.workspace:
				self._invalidate_write_intent("工作区已改变，没有执行平台写入。")
				raise self._write_error(
					code=ErrorCode.WORKSPACE_MISMATCH,
					context=context,
					message="Workspace changed after preparation; nothing was sent",
					recovery_action="Return to the job and prepare a new confirmation",
				)
			if (
				self._credential_store.platform_session_state(workspace) is not PlatformSessionState.CONNECTED
				or self._session_revision(workspace) != intent.session_revision
			):
				self._invalidate_write_intent("平台会话已改变，没有执行平台写入。")
				raise self._write_error(
					code=ErrorCode.AUTHENTICATION_EXPIRED,
					context=context,
					message="BOSS session changed after preparation; nothing was sent",
					recovery_action="Reconnect if needed and prepare a new confirmation",
				)
			self._write_intent = replace(
				intent,
				summary=replace(intent.summary, state=WriteIntentState.EXECUTING),
			)
		try:
			if intent.operation == "recruiting-reply":
				self._boss.send_recruiting_reply(intent.summary.target_reference, intent.message)
			else:
				self._boss.send_greeting(intent.summary.target_reference, intent.message)
		except BossAdapterFailure as failure:
			return self._record_write_failure(intent, failure, context)
		except (OSError, RuntimeError) as failure:
			return self._record_write_failure(
				intent,
				BossAdapterFailure(ErrorCode.UNCERTAIN_REMOTE_OUTCOME, str(failure)),
				context,
			)
		with self._lock:
			self._write_intent = replace(
				intent,
				summary=replace(
					intent.summary,
					state=WriteIntentState.SUCCEEDED,
					outcome_message=(
						"回复已发送。" if intent.operation == "recruiting-reply" else "招呼已发送。"
					),
				),
			)
			self._last_error = None
			if intent.summary.workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=intent.summary.intent_id)

	def _record_write_failure(
		self,
		intent: _WriteIntent,
		failure: BossAdapterFailure,
		context: RequestContext,
	) -> CommandResult:
		uncertain = failure.code is ErrorCode.UNCERTAIN_REMOTE_OUTCOME
		code = (
			failure.code
			if failure.code
			in {
				ErrorCode.AUTHENTICATION_EXPIRED,
				ErrorCode.RATE_LIMITED,
				ErrorCode.PLATFORM_RISK_CONTROL,
				ErrorCode.UNSUPPORTED_CAPABILITY,
				ErrorCode.UNCERTAIN_REMOTE_OUTCOME,
			}
			else ErrorCode.UNCERTAIN_REMOTE_OUTCOME
		)
		message = (
			"发送结果不确定。请到 BOSS 官方页面核对；系统不会自动重试。"
			if uncertain or code is ErrorCode.UNCERTAIN_REMOTE_OUTCOME
			else "BOSS 拒绝了本次发送，没有自动重试。"
		)
		state = WriteIntentState.UNCERTAIN if code is ErrorCode.UNCERTAIN_REMOTE_OUTCOME else WriteIntentState.REJECTED
		with self._lock:
			self._write_intent = replace(
				intent,
				summary=replace(intent.summary, state=state, outcome_message=message),
			)
			self._last_error = DomainErrorDetails(
				code=code,
				message=message,
				recoverable=True,
				recovery_action=(
					"在 BOSS 官方页面核对是否已发送；不要直接重试"
					if code is ErrorCode.UNCERTAIN_REMOTE_OUTCOME
					else _RECOVERY_ACTIONS.get(code, "Review the BOSS response before preparing a new confirmation")
				),
				correlation_id=context.correlation_id,
			)
			if intent.summary.workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()
		raise self._write_error(
			code=code,
			context=context,
			message=message,
			recovery_action=self._last_error.recovery_action,
		)

	def _cancel_write_intent(
		self,
		command: CancelWriteIntentCommand,
		workspace: WorkspaceKind,
		context: RequestContext,
	) -> CommandResult:
		with self._lock:
			intent = self._require_pending_intent(command.intent_id, context)
			if workspace is not intent.summary.workspace:
				raise self._write_error(
					code=ErrorCode.WORKSPACE_MISMATCH,
					context=context,
					message="Write confirmation belongs to another workspace",
					recovery_action=None,
				)
			self._write_intent = replace(
				intent,
				summary=replace(
					intent.summary,
					state=WriteIntentState.CANCELLED,
					outcome_message="已取消，没有执行平台写入。",
				),
			)
			if intent.summary.workspace is WorkspaceKind.RECRUITING:
				self._clear_recruiting_sensitive()
		return CommandResult(snapshot=self.query(CurrentStateQuery(), context), resource_ref=intent.summary.intent_id)

	@staticmethod
	def _match_reasons(goal: JobSearchGoal | None, detail: JobSourceDetail) -> tuple[str, ...]:
		if goal is None:
			return ()
		reasons: list[str] = []
		job = detail.job
		if goal.keyword and goal.keyword.casefold() in f"{job.title} {detail.description}".casefold():
			reasons.append(f"职位原文包含关键词“{goal.keyword}”")
		if goal.city and goal.city.casefold() in job.location.casefold():
			reasons.append(f"职位地点包含“{goal.city}”")
		if goal.salary and goal.salary == job.salary:
			reasons.append(f"职位原文薪资为“{goal.salary}”")
		if goal.experience and goal.experience == job.experience:
			reasons.append(f"职位原文经验要求为“{goal.experience}”")
		if goal.education and goal.education == job.education:
			reasons.append(f"职位原文学历要求为“{goal.education}”")
		return tuple(reasons)

	def query(self, query: CurrentStateQuery, context: RequestContext) -> ApplicationStateSnapshot:
		if not isinstance(query, CurrentStateQuery):
			raise DomainError(
				code=ErrorCode.INVALID_QUERY,
				message="Unsupported application query",
				correlation_id=context.correlation_id,
				recoverable=False,
			)
		workspace = self._active_workspace(context)
		platform_session = self._credential_store.platform_session_state(workspace)
		if query.probe_platform:
			try:
				platform_session = self._boss.probe_session(workspace)
			except (OSError, RuntimeError) as exc:
				raise DomainError(
					code=ErrorCode.ADAPTER_UNAVAILABLE,
					message="BOSS is unavailable for a read-only session check",
					correlation_id=context.correlation_id,
					recoverable=True,
					recovery_action="Retry the read-only platform check",
				) from exc
		with self._lock:
			if (
				self._write_intent is not None
				and self._write_intent.summary.state is WriteIntentState.PENDING
				and self._clock() >= self._write_intent.summary.expires_at
			):
				self._write_intent = replace(
					self._write_intent,
					summary=replace(
						self._write_intent.summary,
						state=WriteIntentState.EXPIRED,
						outcome_message="确认已过期，没有执行平台写入。",
					),
				)
			active_run = self._active_run if self._run_workspace is workspace else None
			if self._last_error is not None and self._last_error.code in {
				ErrorCode.AUTHENTICATION_EXPIRED,
				ErrorCode.RATE_LIMITED,
				ErrorCode.PLATFORM_RISK_CONTROL,
			}:
				platform_session = PlatformSessionState.RECOVERY
			job_seeking = None
			recruiting = None
			if workspace is WorkspaceKind.JOB_SEEKING:
				job_seeking = JobSeekingState(
					goal=self._workspace_store.load_job_search_goal(),
					results=self._job_results,
					selected_job=self._selected_job,
					shortlist=self._workspace_store.load_job_shortlist(),
				)
				recruiting = None
			else:
				recruiting = RecruitingState(
					openings=self._opening_results,
					selected_opening=self._workspace_store.load_recruiting_opening(),
					applicants=self._applicant_results,
					selected_prospect=self._selected_prospect,
				)
			selected_reference = None
			if job_seeking is not None and self._selected_job is not None:
				selected_reference = self._selected_job.source.job.reference
			elif recruiting is not None:
				selected_reference = (
					self._selected_prospect.prospect.reference
					if self._selected_prospect is not None
					else (recruiting.selected_opening.reference if recruiting.selected_opening is not None else None)
				)
			local_decision = None
			if job_seeking is not None and selected_reference is not None:
				if any(item.reference == selected_reference for item in job_seeking.shortlist):
					local_decision = "shortlisted"
			return ApplicationStateSnapshot(
				schema_version="1",
				active_workspace=workspace,
				platform_session=platform_session,
				active_run=active_run,
				selected_reference=selected_reference,
				local_decision=local_decision,
				sensitive_content_present=(
					self._workspace_store.sensitive_content_present(workspace)
					or (
						workspace is WorkspaceKind.RECRUITING
						and bool(self._applicant_results or self._selected_prospect)
					)
				),
				pending_write_intent=(
					self._write_intent.summary
					if self._write_intent is not None and self._write_intent.summary.workspace is workspace
					else None
				),
				last_transition=self._workspace_store.last_transition(context.local_session_id),
				error=self._last_error,
				job_seeking=job_seeking,
				recruiting=recruiting,
			)

	def events(
		self,
		run_id: str,
		*,
		after_cursor: int,
		context: RequestContext,
	) -> tuple[ApplicationEvent, ...]:
		self._active_workspace(context)
		with self._lock:
			if run_id in self._events:
				return tuple(event for event in self._events[run_id] if event.cursor > after_cursor)
		try:
			events = self._workspace_store.read_events(run_id)
		except KeyError as exc:
			raise DomainError(
				code=ErrorCode.RUN_NOT_FOUND,
				message="Recoverable Run was not found",
				correlation_id=context.correlation_id,
				recoverable=False,
			) from exc
		return tuple(event for event in events if event.cursor > after_cursor)
