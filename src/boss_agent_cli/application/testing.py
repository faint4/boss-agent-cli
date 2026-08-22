"""Deterministic adapters for application-module contract tests."""

from __future__ import annotations

from boss_agent_cli.application.contracts import ApplicationEvent, PlatformSessionState, WorkspaceKind


class InMemoryWorkspaceStore:
	def __init__(
		self,
		*,
		local_session_id: str,
		active_workspace: WorkspaceKind,
		run_events: dict[str, tuple[ApplicationEvent, ...]] | None = None,
	) -> None:
		self._local_session_id = local_session_id
		self._active_workspace = active_workspace
		self._run_events = dict(run_events or {})

	def active_workspace(self, local_session_id: str) -> WorkspaceKind:
		if local_session_id != self._local_session_id:
			raise PermissionError("local session is not authorized")
		return self._active_workspace

	def read_events(self, run_id: str) -> tuple[ApplicationEvent, ...]:
		return self._run_events[run_id]


class InMemoryCredentialStore:
	def __init__(self, states: dict[WorkspaceKind, PlatformSessionState] | None = None) -> None:
		self._states = dict(states or {})

	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self._states.get(workspace, PlatformSessionState.DISCONNECTED)


class FakeBossAdapter:
	def __init__(
		self,
		state: PlatformSessionState = PlatformSessionState.CONNECTED,
		*,
		failure: RuntimeError | None = None,
	) -> None:
		self.state = state
		self.failure = failure
		self.probed_workspaces: list[WorkspaceKind] = []

	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		self.probed_workspaces.append(workspace)
		if self.failure is not None:
			raise self.failure
		return self.state
