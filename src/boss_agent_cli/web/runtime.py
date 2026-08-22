"""Safe, disconnected application adapters for the first local Web shell."""

from __future__ import annotations

from boss_agent_cli.application import Application, ApplicationEvent, PlatformSessionState, WorkspaceKind


class _LocalWorkspaceStore:
	def __init__(self, local_session_id: str) -> None:
		self._local_session_id = local_session_id

	def active_workspace(self, local_session_id: str) -> WorkspaceKind:
		if local_session_id != self._local_session_id:
			raise PermissionError("local session is not authorized")
		return WorkspaceKind.JOB_SEEKING

	def read_events(self, run_id: str) -> tuple[ApplicationEvent, ...]:
		raise KeyError(run_id)


class _DisconnectedCredentialStore:
	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return PlatformSessionState.DISCONNECTED


class _UnavailableBossAdapter:
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		raise RuntimeError("BOSS adapter is not connected to the local Web shell")


def create_application(local_session_id: str) -> Application:
	"""Create the shared application seam without enabling Browser Bridge or remote access."""

	return Application(
		workspace_store=_LocalWorkspaceStore(local_session_id),
		credential_store=_DisconnectedCredentialStore(),
		boss=_UnavailableBossAdapter(),
	)
