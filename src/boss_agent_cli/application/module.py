"""Authoritative application module implementation."""

from __future__ import annotations

from typing import Protocol

from boss_agent_cli.application.contracts import (
	ApplicationEvent,
	ApplicationStateSnapshot,
	CommandResult,
	ConnectPlatformSessionCommand,
	CurrentStateQuery,
	DomainError,
	ErrorCode,
	PlatformSessionState,
	RequestContext,
	LogoutPlatformSessionCommand,
	SwitchWorkspaceCommand,
	WorkspaceKind,
)


class WorkspaceStore(Protocol):
	def active_workspace(self, local_session_id: str) -> WorkspaceKind: ...
	def switch_workspace(self, local_session_id: str, workspace: WorkspaceKind) -> None: ...
	def sensitive_content_present(self, workspace: WorkspaceKind) -> bool: ...
	def last_transition(self, local_session_id: str) -> str | None: ...
	def read_events(self, run_id: str) -> tuple[ApplicationEvent, ...]: ...


class CredentialStore(Protocol):
	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState: ...
	def activate(self, workspace: WorkspaceKind) -> None: ...
	def begin_connect(self, workspace: WorkspaceKind) -> None: ...
	def begin_logout(self, workspace: WorkspaceKind) -> None: ...


class BossAdapter(Protocol):
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState: ...


class Application:
	"""Deep module shared by Web, CLI, and MCP adapters."""

	def __init__(
		self,
		*,
		workspace_store: WorkspaceStore,
		credential_store: CredentialStore,
		boss: BossAdapter,
	) -> None:
		self._workspace_store = workspace_store
		self._credential_store = credential_store
		self._boss = boss

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

	def execute(
		self,
		command: SwitchWorkspaceCommand | ConnectPlatformSessionCommand | LogoutPlatformSessionCommand,
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
		raise DomainError(
			code=ErrorCode.UNSUPPORTED_COMMAND,
			message=f"Command {type(command).__name__} is not available yet",
			correlation_id=context.correlation_id,
			recoverable=False,
		)

	def query(
		self,
		query: CurrentStateQuery,
		context: RequestContext,
	) -> ApplicationStateSnapshot:
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
		return ApplicationStateSnapshot(
			schema_version="1",
			active_workspace=workspace,
			platform_session=platform_session,
			sensitive_content_present=self._workspace_store.sensitive_content_present(workspace),
			last_transition=self._workspace_store.last_transition(context.local_session_id),
		)

	def events(
		self,
		run_id: str,
		*,
		after_cursor: int,
		context: RequestContext,
	) -> tuple[ApplicationEvent, ...]:
		self._active_workspace(context)
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
