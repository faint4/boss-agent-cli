"""Production wiring for in-process CLI and MCP Recruiting surfaces."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from boss_agent_cli.application import Application, PlatformSessionState, WorkspaceKind
from boss_agent_cli.application.recruiting_surface import RecruitingSurface
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.output import Logger
from boss_agent_cli.web.boss_read import BossReadAdapter
from boss_agent_cli.web.workspace import WorkspaceRegistry


class RecruitingCredentialStore:
	"""Adapt the established ``boss login`` credential to the Recruiting workspace."""

	def __init__(self, auth: AuthManager, *, platform: str, cdp_url: str | None = None) -> None:
		self._auth = auth
		self._platform = platform
		self._cdp_url = cdp_url

	def _credential(self) -> dict[str, Any] | None:
		if self._platform != "zhipin":
			return None
		credential = self._auth.check_status()
		if credential is None:
			return None
		result = dict(credential)
		if self._cdp_url:
			result["cdp_url"] = self._cdp_url
		return result

	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None:
		if workspace is not WorkspaceKind.RECRUITING:
			return None
		return self._credential()

	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self.platform_session_state(workspace)

	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		if workspace is not WorkspaceKind.RECRUITING or self._credential() is None:
			return PlatformSessionState.DISCONNECTED
		return PlatformSessionState.CONNECTED

	def activate(self, workspace: WorkspaceKind) -> None:
		return

	def begin_connect(self, workspace: WorkspaceKind) -> None:
		# Contract actions never open an authentication browser implicitly.
		return

	def begin_logout(self, workspace: WorkspaceKind) -> None:
		if workspace is WorkspaceKind.RECRUITING:
			self._auth.logout()

	def session_revision(self, workspace: WorkspaceKind) -> str:
		credential = self.active_credential(workspace)
		if credential is None:
			return "disconnected"
		encoded = json.dumps(credential, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
		return hashlib.sha256(encoded).hexdigest()


def create_recruiting_surface(
	*,
	data_dir: Path,
	platform: str = "zhipin",
	logger: Logger | None = None,
	cdp_url: str | None = None,
	local_session_id: str | None = None,
) -> RecruitingSurface:
	"""Create one long-lived shared Recruiting application contract."""

	if platform != "zhipin":
		raise ValueError("the shared Recruiting application contract currently supports only zhipin")
	resolved_session_id = local_session_id or f"surface-{secrets.token_urlsafe(18)}"
	registry = WorkspaceRegistry(data_dir / "application", local_session_id=resolved_session_id)
	if registry.configured_active_workspace is not WorkspaceKind.RECRUITING:
		registry.switch_workspace(resolved_session_id, WorkspaceKind.RECRUITING)
	auth = AuthManager(data_dir, logger=logger, platform=platform)
	credentials = RecruitingCredentialStore(auth, platform=platform, cdp_url=cdp_url)
	application = Application(
		workspace_store=registry,
		credential_store=credentials,
		boss=BossReadAdapter(credentials),
	)
	return RecruitingSurface(
		application,
		local_session_id=resolved_session_id,
		correlation_id_factory=lambda: f"surface-{secrets.token_urlsafe(12)}",
	)


__all__ = ["RecruitingCredentialStore", "create_recruiting_surface"]
