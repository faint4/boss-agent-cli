"""Authoritative application module implementation."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Protocol

from boss_agent_cli.application.contracts import (
	ApplicationEvent,
	ApplicationStateSnapshot,
	CancelRunCommand,
	CommandResult,
	ConnectPlatformSessionCommand,
	CurrentStateQuery,
	DomainError,
	DomainErrorDetails,
	ErrorCode,
	InspectJobCommand,
	JobDetailView,
	JobSearchBatch,
	JobSearchGoal,
	JobSeekingState,
	JobSourceDetail,
	JobSummary,
	LogoutPlatformSessionCommand,
	PlatformSessionState,
	RequestContext,
	RunEventKind,
	RunSummary,
	SetShortlistedCommand,
	StartJobSearchCommand,
	SwitchWorkspaceCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
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


class CredentialStore(Protocol):
	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState: ...
	def activate(self, workspace: WorkspaceKind) -> None: ...
	def begin_connect(self, workspace: WorkspaceKind) -> None: ...
	def begin_logout(self, workspace: WorkspaceKind) -> None: ...


class BossAdapterFailure(RuntimeError):
	"""Typed failure at the external BOSS read boundary."""

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


_RECOVERY_ACTIONS = {
	ErrorCode.AUTHENTICATION_EXPIRED: "Reconnect BOSS, then retry the search",
	ErrorCode.RATE_LIMITED: "Wait before retrying the search manually",
	ErrorCode.PLATFORM_RISK_CONTROL: "Complete verification in BOSS, then retry",
	ErrorCode.ADAPTER_UNAVAILABLE: "Check the BOSS connection, then retry",
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
	) -> None:
		self._workspace_store = workspace_store
		self._credential_store = credential_store
		self._boss = boss
		self._run_id_factory = run_id_factory or (lambda: f"search-{secrets.token_urlsafe(12)}")
		self._lock = threading.RLock()
		self._active_run: RunSummary | None = None
		self._run_workspace: WorkspaceKind | None = None
		self._events: dict[str, list[ApplicationEvent]] = {}
		self._cancel_events: dict[str, threading.Event] = {}
		self._job_results: tuple[JobSummary, ...] = ()
		self._selected_job: JobDetailView | None = None
		self._last_error: DomainErrorDetails | None = None

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
		),
		context: RequestContext,
	) -> CommandResult:
		workspace = self._active_workspace(context)
		if isinstance(command, SwitchWorkspaceCommand):
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
			self._credential_store.begin_connect(workspace)
			return CommandResult(snapshot=self.query(CurrentStateQuery(), context))
		if isinstance(command, LogoutPlatformSessionCommand):
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
		raise DomainError(
			code=ErrorCode.UNSUPPORTED_COMMAND,
			message=f"Command {type(command).__name__} is not available yet",
			correlation_id=context.correlation_id,
			recoverable=False,
		)

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
		self._require_job_workspace(workspace, context)
		with self._lock:
			cancel_event = self._cancel_events.get(command.run_id)
			if cancel_event is None or self._active_run is None or self._active_run.run_id != command.run_id:
				raise DomainError(
					code=ErrorCode.RUN_NOT_FOUND,
					message="Search run was not found",
					correlation_id=context.correlation_id,
					recoverable=False,
				)
			if self._active_run.state not in {"running", "cancelling"}:
				raise DomainError(
					code=ErrorCode.INVALID_TRANSITION,
					message="Only a running search can be cancelled",
					correlation_id=context.correlation_id,
					recoverable=False,
				)
			cancel_event.set()
			self._active_run = RunSummary(
				run_id=command.run_id,
				state="cancelling",
				progress=self._active_run.progress,
			)
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
			active_run = self._active_run if self._run_workspace is workspace else None
			if self._last_error is not None and self._last_error.code in {
				ErrorCode.AUTHENTICATION_EXPIRED,
				ErrorCode.RATE_LIMITED,
				ErrorCode.PLATFORM_RISK_CONTROL,
			}:
				platform_session = PlatformSessionState.RECOVERY
			job_seeking = None
			if workspace is WorkspaceKind.JOB_SEEKING:
				job_seeking = JobSeekingState(
					goal=self._workspace_store.load_job_search_goal(),
					results=self._job_results,
					selected_job=self._selected_job,
					shortlist=self._workspace_store.load_job_shortlist(),
				)
			selected_reference = self._selected_job.source.job.reference if self._selected_job is not None else None
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
				sensitive_content_present=self._workspace_store.sensitive_content_present(workspace),
				last_transition=self._workspace_store.last_transition(context.local_session_id),
				error=self._last_error,
				job_seeking=job_seeking,
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
