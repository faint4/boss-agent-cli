"""Safe, disconnected application adapters for the first local Web shell."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from boss_agent_cli.application import Application, WorkspaceKind
from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.auth.browser import login_via_browser
from boss_agent_cli.web.dpapi import CredentialProtector, default_credential_protector
from boss_agent_cli.web.boss_read import BossReadAdapter
from boss_agent_cli.web.platform_session import LoginProvider, PlatformSessionManager, WorkspaceSessionStore
from boss_agent_cli.web.workspace import WorkspaceRegistry, default_product_root
from boss_agent_cli.web.ai_assistance import ConfiguredAIAssistant


def _official_boss_login(workspace: WorkspaceKind) -> dict[str, Any]:
	"""Open a dedicated official BOSS login window without inspecting daily-browser cookies."""

	return login_via_browser(timeout=300, platform="zhipin")


def create_application(
	local_session_id: str,
	*,
	product_root: Path | None = None,
	protector: CredentialProtector | None = None,
	login_provider: LoginProvider | None = None,
) -> Application:
	"""Create isolated Web workspaces without enabling Browser Bridge."""

	resolved_root = product_root or default_product_root()
	registry = WorkspaceRegistry(resolved_root, local_session_id=local_session_id)
	sessions = PlatformSessionManager(
		registry=registry,
		store=WorkspaceSessionStore(registry=registry, protector=protector or default_credential_protector()),
		login_provider=login_provider or _official_boss_login,
	)
	return Application(
		workspace_store=registry,
		credential_store=sessions,
		boss=BossReadAdapter(sessions),
		ai=ConfiguredAIAssistant(AIConfigStore(resolved_root)),
	)
