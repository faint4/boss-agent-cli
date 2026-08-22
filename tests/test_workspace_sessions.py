from __future__ import annotations

import json
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.application import (
	Application,
	ConnectPlatformSessionCommand,
	CurrentStateQuery,
	LogoutPlatformSessionCommand,
	PlatformSessionState,
	RequestContext,
	SwitchWorkspaceCommand,
	WorkspaceKind,
)
from boss_agent_cli.web.dpapi import CredentialProtectionError, WindowsDPAPIProtector
from boss_agent_cli.web.platform_session import PlatformSessionManager, WorkspaceSessionStore
from boss_agent_cli.web.workspace import WorkspaceRegistry


class PurposeBoundProtector:
	def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
		return purpose + b"\0" + plaintext[::-1]

	def unprotect(self, ciphertext: bytes, *, purpose: bytes) -> bytes:
		prefix = purpose + b"\0"
		if not ciphertext.startswith(prefix):
			raise CredentialProtectionError("credential purpose mismatch")
		return ciphertext[len(prefix) :][::-1]


def _context() -> RequestContext:
	return RequestContext(local_session_id="local-session", correlation_id="request-1")


def _application(
	tmp_path: Path,
	*,
	login_provider: Callable[[WorkspaceKind], dict[str, Any]] | None = None,
) -> tuple[Application, WorkspaceRegistry, PlatformSessionManager]:
	registry = WorkspaceRegistry(tmp_path / "BossAgent", local_session_id="local-session")
	sessions = PlatformSessionManager(
		registry=registry,
		store=WorkspaceSessionStore(registry=registry, protector=PurposeBoundProtector()),
		login_provider=login_provider
		or (lambda workspace: {"workspace": workspace.value, "cookies": {"wt2": "secret"}}),
	)
	application = Application(workspace_store=registry, credential_store=sessions, boss=sessions)
	return application, registry, sessions


def test_workspace_roots_databases_runtime_and_credentials_are_physically_separate(tmp_path: Path):
	registry = WorkspaceRegistry(tmp_path / "BossAgent", local_session_id="local-session")
	job_paths = registry.paths(WorkspaceKind.JOB_SEEKING)
	recruiting_paths = registry.paths(WorkspaceKind.RECRUITING)

	assert job_paths.root != recruiting_paths.root
	assert job_paths.database != recruiting_paths.database
	assert job_paths.cache != recruiting_paths.cache
	assert job_paths.runs != recruiting_paths.runs
	assert job_paths.credential != recruiting_paths.credential
	assert job_paths.database.is_file()
	assert not recruiting_paths.root.exists()

	registry.write_local_state(WorkspaceKind.JOB_SEEKING, "goal", "Python backend")
	registry.switch_workspace("local-session", WorkspaceKind.RECRUITING)

	assert recruiting_paths.database.is_file()
	assert registry.read_local_state(WorkspaceKind.RECRUITING, "goal") is None
	assert registry.read_local_state(WorkspaceKind.JOB_SEEKING, "goal") == "Python backend"


def test_active_workspace_is_persisted_as_non_sensitive_application_state(tmp_path: Path):
	root = tmp_path / "BossAgent"
	registry = WorkspaceRegistry(root, local_session_id="local-session")
	registry.switch_workspace("local-session", WorkspaceKind.RECRUITING)

	restarted = WorkspaceRegistry(root, local_session_id="next-process")

	assert restarted.active_workspace("next-process") is WorkspaceKind.RECRUITING
	assert json.loads((root / "app" / "settings.json").read_text(encoding="utf-8")) == {
		"active_workspace": "recruiting",
		"schema_version": "1",
	}


def test_switch_rejects_an_unknown_local_session_without_touching_the_other_workspace(tmp_path: Path):
	registry = WorkspaceRegistry(tmp_path / "BossAgent", local_session_id="authorized")
	recruiting_root = registry.paths(WorkspaceKind.RECRUITING).root

	with pytest.raises(PermissionError):
		registry.switch_workspace("attacker", WorkspaceKind.RECRUITING)

	assert registry.active_workspace("authorized") is WorkspaceKind.JOB_SEEKING
	assert not recruiting_root.exists()


def test_dpapi_store_binds_ciphertext_to_exactly_one_workspace(tmp_path: Path):
	registry = WorkspaceRegistry(tmp_path / "BossAgent", local_session_id="local-session")
	store = WorkspaceSessionStore(registry=registry, protector=PurposeBoundProtector())
	credential = b'{"cookies":{"wt2":"credential-canary"}}'

	store.save(WorkspaceKind.JOB_SEEKING, credential)
	ciphertext = registry.paths(WorkspaceKind.JOB_SEEKING).credential.read_bytes()

	assert credential not in ciphertext
	assert b"credential-canary" not in ciphertext
	assert store.load(WorkspaceKind.JOB_SEEKING) == credential

	registry.ensure_workspace(WorkspaceKind.RECRUITING)
	shutil.copyfile(
		registry.paths(WorkspaceKind.JOB_SEEKING).credential,
		registry.paths(WorkspaceKind.RECRUITING).credential,
	)
	with pytest.raises(CredentialProtectionError):
		store.load(WorkspaceKind.RECRUITING)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI is only available on Windows")
def test_real_windows_dpapi_uses_current_user_and_purpose_binding():
	protector = WindowsDPAPIProtector()
	plaintext = b"dpapi-credential-canary"
	ciphertext = protector.protect(plaintext, purpose=b"BossAgent/job-seeking")

	assert plaintext not in ciphertext
	assert protector.unprotect(ciphertext, purpose=b"BossAgent/job-seeking") == plaintext
	with pytest.raises(CredentialProtectionError):
		protector.unprotect(ciphertext, purpose=b"BossAgent/recruiting")


def test_platform_session_transitions_and_credentials_do_not_cross_workspaces(tmp_path: Path):
	login_started = threading.Event()
	allow_login = threading.Event()

	def login_provider(workspace: WorkspaceKind) -> dict[str, object]:
		login_started.set()
		assert allow_login.wait(timeout=2)
		return {"workspace": workspace.value, "cookies": {"wt2": "job-secret"}}

	_, registry, sessions = _application(tmp_path, login_provider=login_provider)
	sessions.begin_connect(WorkspaceKind.JOB_SEEKING)
	assert login_started.wait(timeout=2)
	assert sessions.platform_session_state(WorkspaceKind.JOB_SEEKING) is PlatformSessionState.CONNECTING

	allow_login.set()
	assert sessions.wait_for_idle(timeout=2)
	assert sessions.platform_session_state(WorkspaceKind.JOB_SEEKING) is PlatformSessionState.CONNECTED
	assert registry.paths(WorkspaceKind.JOB_SEEKING).credential.is_file()

	sessions.activate(WorkspaceKind.RECRUITING)
	assert sessions.platform_session_state(WorkspaceKind.RECRUITING) is PlatformSessionState.DISCONNECTED

	sessions.activate(WorkspaceKind.JOB_SEEKING)
	assert sessions.platform_session_state(WorkspaceKind.JOB_SEEKING) is PlatformSessionState.CONNECTED


def test_logout_has_a_visible_stopping_state_and_preserves_local_workspace_data(tmp_path: Path):
	application, registry, sessions = _application(tmp_path)
	registry.write_local_state(WorkspaceKind.JOB_SEEKING, "goal", "Keep me")
	sessions.begin_connect(WorkspaceKind.JOB_SEEKING)
	assert sessions.wait_for_idle(timeout=2)

	result = application.execute(LogoutPlatformSessionCommand(), _context())

	assert result.snapshot.platform_session in {PlatformSessionState.STOPPING, PlatformSessionState.DISCONNECTED}
	assert sessions.wait_for_idle(timeout=2)
	assert sessions.platform_session_state(WorkspaceKind.JOB_SEEKING) is PlatformSessionState.DISCONNECTED
	assert not registry.paths(WorkspaceKind.JOB_SEEKING).credential.exists()
	assert registry.read_local_state(WorkspaceKind.JOB_SEEKING, "goal") == "Keep me"


def test_login_failure_enters_recovery_without_writing_a_credential(tmp_path: Path):
	def failed_login(workspace: WorkspaceKind) -> dict[str, object]:
		raise RuntimeError("credential-canary must not escape")

	_, registry, sessions = _application(tmp_path, login_provider=failed_login)
	sessions.begin_connect(WorkspaceKind.JOB_SEEKING)

	assert sessions.wait_for_idle(timeout=2)
	assert sessions.platform_session_state(WorkspaceKind.JOB_SEEKING) is PlatformSessionState.RECOVERY
	assert not registry.paths(WorkspaceKind.JOB_SEEKING).credential.exists()


def test_application_switch_is_explicit_and_clears_sensitive_runtime_state(tmp_path: Path):
	application, registry, sessions = _application(tmp_path)
	registry.mark_sensitive_content(WorkspaceKind.JOB_SEEKING, present=True)
	sessions.begin_connect(WorkspaceKind.JOB_SEEKING)
	assert sessions.wait_for_idle(timeout=2)

	result = application.execute(
		SwitchWorkspaceCommand(workspace=WorkspaceKind.RECRUITING),
		_context(),
	)

	assert result.snapshot.active_workspace is WorkspaceKind.RECRUITING
	assert result.snapshot.platform_session is PlatformSessionState.DISCONNECTED
	assert result.snapshot.last_transition == "workspace-switched"
	assert registry.sensitive_content_present(WorkspaceKind.JOB_SEEKING) is False
	assert application.query(CurrentStateQuery(), _context()).active_workspace is WorkspaceKind.RECRUITING


def test_connect_command_returns_connecting_then_connected_snapshot(tmp_path: Path):
	login_started = threading.Event()
	allow_login = threading.Event()

	def login_provider(workspace: WorkspaceKind) -> dict[str, object]:
		login_started.set()
		assert allow_login.wait(timeout=2)
		return {"workspace": workspace.value, "cookies": {"wt2": "secret"}}

	application, _, sessions = _application(tmp_path, login_provider=login_provider)
	result = application.execute(ConnectPlatformSessionCommand(), _context())

	assert login_started.wait(timeout=2)
	assert result.snapshot.platform_session is PlatformSessionState.CONNECTING
	allow_login.set()
	assert sessions.wait_for_idle(timeout=2)
	assert application.query(CurrentStateQuery(), _context()).platform_session is PlatformSessionState.CONNECTED
