"""Public values exposed by the application module interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class WorkspaceKind(str, Enum):
	JOB_SEEKING = "job-seeking"
	RECRUITING = "recruiting"


class PlatformSessionState(str, Enum):
	DISCONNECTED = "disconnected"
	CONNECTING = "connecting"
	CONNECTED = "connected"
	STOPPING = "stopping"
	RECOVERY = "recovery"


class WriteIntentState(str, Enum):
	PENDING = "pending"
	EXECUTING = "executing"
	SUCCEEDED = "succeeded"
	REJECTED = "rejected"
	EXPIRED = "expired"
	CANCELLED = "cancelled"
	UNCERTAIN = "uncertain"


class ErrorCode(str, Enum):
	INVALID_COMMAND = "INVALID_COMMAND"
	INVALID_QUERY = "INVALID_QUERY"
	INVALID_TRANSITION = "INVALID_TRANSITION"
	UNAUTHORIZED_CONTEXT = "UNAUTHORIZED_CONTEXT"
	WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
	AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
	AUTHENTICATION_EXPIRED = "AUTHENTICATION_EXPIRED"
	RATE_LIMITED = "RATE_LIMITED"
	PLATFORM_RISK_CONTROL = "PLATFORM_RISK_CONTROL"
	WRITE_INTENT_MISSING = "WRITE_INTENT_MISSING"
	WRITE_INTENT_EXPIRED = "WRITE_INTENT_EXPIRED"
	WRITE_INTENT_CONSUMED = "WRITE_INTENT_CONSUMED"
	ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
	UNSUPPORTED_COMMAND = "UNSUPPORTED_COMMAND"
	RUN_NOT_FOUND = "RUN_NOT_FOUND"
	CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
	UNCERTAIN_REMOTE_OUTCOME = "UNCERTAIN_REMOTE_OUTCOME"
	STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
	UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"


class RunEventKind(str, Enum):
	STATE_CHANGED = "state-changed"
	PROGRESS = "progress"
	RECOVERY_REQUIRED = "recovery-required"


class DomainError(RuntimeError):
	"""Safe, transport-independent application failure."""

	def __init__(
		self,
		*,
		code: ErrorCode,
		message: str,
		correlation_id: str,
		recoverable: bool,
		recovery_action: str | None = None,
	) -> None:
		super().__init__(message)
		self.code = code
		self.message = message
		self.correlation_id = correlation_id
		self.recoverable = recoverable
		self.recovery_action = recovery_action


@dataclass(frozen=True)
class RequestContext:
	local_session_id: str
	correlation_id: str


@dataclass(frozen=True)
class CurrentStateQuery:
	probe_platform: bool = False


@dataclass(frozen=True)
class SwitchWorkspaceCommand:
	workspace: WorkspaceKind


@dataclass(frozen=True)
class ConnectPlatformSessionCommand:
	pass


@dataclass(frozen=True)
class LogoutPlatformSessionCommand:
	pass


@dataclass(frozen=True)
class JobSearchGoal:
	objective: str
	keyword: str
	city: str = ""
	salary: str = ""
	experience: str = ""
	education: str = ""


@dataclass(frozen=True)
class UpdateJobSearchGoalCommand:
	goal: JobSearchGoal


@dataclass(frozen=True)
class StartJobSearchCommand:
	pass


@dataclass(frozen=True)
class CancelRunCommand:
	run_id: str


@dataclass(frozen=True)
class InspectJobCommand:
	reference: str


@dataclass(frozen=True)
class SetShortlistedCommand:
	reference: str
	shortlisted: bool


@dataclass(frozen=True)
class PrepareJobGreetingCommand:
	reference: str
	message: str


@dataclass(frozen=True)
class ConfirmWriteIntentCommand:
	intent_id: str


@dataclass(frozen=True)
class CancelWriteIntentCommand:
	intent_id: str


@dataclass(frozen=True)
class JobSummary:
	reference: str
	title: str
	company: str
	location: str = ""
	salary: str = ""
	experience: str = ""
	education: str = ""


@dataclass(frozen=True)
class JobSourceDetail:
	job: JobSummary
	description: str = ""
	company_stage: str = ""
	company_size: str = ""
	recruiter: str = ""


@dataclass(frozen=True)
class JobDetailView:
	source: JobSourceDetail
	match_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobSearchBatch:
	items: tuple[JobSummary, ...]
	progress: int


@dataclass(frozen=True)
class JobSeekingState:
	goal: JobSearchGoal | None = None
	results: tuple[JobSummary, ...] = ()
	selected_job: JobDetailView | None = None
	shortlist: tuple[JobSummary, ...] = ()


@dataclass(frozen=True)
class RunSummary:
	run_id: str
	state: str
	progress: int | None = None
	wait_reason: str | None = None


@dataclass(frozen=True)
class WriteIntentSummary:
	intent_id: str
	workspace: WorkspaceKind
	target_reference: str
	expires_at: datetime
	target_label: str = ""
	action: str = ""
	payload_preview: str = ""
	warnings: tuple[str, ...] = ()
	state: WriteIntentState = WriteIntentState.PENDING
	outcome_message: str | None = None


@dataclass(frozen=True)
class DomainErrorDetails:
	code: ErrorCode
	message: str
	recoverable: bool
	recovery_action: str | None
	correlation_id: str
	field_errors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ApplicationStateSnapshot:
	schema_version: str
	active_workspace: WorkspaceKind
	platform_session: PlatformSessionState
	active_run: RunSummary | None = None
	selected_reference: str | None = None
	local_decision: str | None = None
	sensitive_content_present: bool = False
	pending_write_intent: WriteIntentSummary | None = None
	last_transition: str | None = None
	error: DomainErrorDetails | None = None
	job_seeking: JobSeekingState | None = None


@dataclass(frozen=True)
class CommandResult:
	snapshot: ApplicationStateSnapshot
	resource_ref: str | None = None


@dataclass(frozen=True)
class ApplicationEvent:
	cursor: int
	run_id: str
	kind: RunEventKind
	occurred_at: datetime
	payload: tuple[tuple[str, object], ...] = ()
