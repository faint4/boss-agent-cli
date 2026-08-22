"""Production wiring for in-process CLI and MCP Job-Seeking surfaces."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from boss_agent_cli.application import Application, PlatformSessionState, WorkspaceKind
from boss_agent_cli.application.job_surface import JobSeekingSurface
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.output import Logger
from boss_agent_cli.web.boss_read import BossReadAdapter
from boss_agent_cli.web.workspace import WorkspaceRegistry


class CandidateCredentialStore:
	"""Adapt the established ``boss login`` credential store to ``Application``."""

	def __init__(self, auth: AuthManager, *, platform: str) -> None:
		self._auth = auth
		self._platform = platform

	def _credential(self) -> dict[str, Any] | None:
		if self._platform != "zhipin":
			return None
		return self._auth.check_status()

	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None:
		if workspace is not WorkspaceKind.JOB_SEEKING:
			return None
		credential = self._credential()
		return None if credential is None else dict(credential)

	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self.platform_session_state(workspace)

	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		if workspace is not WorkspaceKind.JOB_SEEKING or self._credential() is None:
			return PlatformSessionState.DISCONNECTED
		return PlatformSessionState.CONNECTED

	def activate(self, workspace: WorkspaceKind) -> None:
		return

	def begin_connect(self, workspace: WorkspaceKind) -> None:
		# CLI/MCP keep the established explicit `boss login` entry point.  Merely
		# invoking a contract action must never open an authentication browser.
		return

	def begin_logout(self, workspace: WorkspaceKind) -> None:
		if workspace is WorkspaceKind.JOB_SEEKING:
			self._auth.logout()

	def session_revision(self, workspace: WorkspaceKind) -> str:
		credential = self.active_credential(workspace)
		if credential is None:
			return "disconnected"
		encoded = json.dumps(credential, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
		return hashlib.sha256(encoded).hexdigest()


def create_job_seeking_surface(
	*,
	data_dir: Path,
	platform: str = "zhipin",
	logger: Logger | None = None,
	local_session_id: str | None = None,
) -> JobSeekingSurface:
	"""Create one long-lived shared application contract for a surface process."""

	# BossReadAdapter is deliberately bounded to the BOSS Web contract.  Other
	# candidate platforms keep their legacy commands until they gain an equivalent
	# application adapter.
	if platform != "zhipin":
		raise ValueError("the shared Job-Seeking application contract currently supports only zhipin")
	resolved_session_id = local_session_id or f"surface-{secrets.token_urlsafe(18)}"
	registry = WorkspaceRegistry(data_dir / "application", local_session_id=resolved_session_id)
	if registry.configured_active_workspace is not WorkspaceKind.JOB_SEEKING:
		registry.switch_workspace(resolved_session_id, WorkspaceKind.JOB_SEEKING)
	auth = AuthManager(data_dir, logger=logger, platform=platform)
	credentials = CandidateCredentialStore(auth, platform=platform)
	application = Application(
		workspace_store=registry,
		credential_store=credentials,
		boss=BossReadAdapter(credentials),
	)
	return JobSeekingSurface(
		application,
		local_session_id=resolved_session_id,
		correlation_id_factory=lambda: f"surface-{secrets.token_urlsafe(12)}",
	)


__all__ = ["CandidateCredentialStore", "create_job_seeking_surface"]
