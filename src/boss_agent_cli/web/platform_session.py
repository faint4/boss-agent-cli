"""Workspace-bound BOSS Platform Session lifecycle."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from typing import Any

from boss_agent_cli.application import PlatformSessionState, WorkspaceKind
from boss_agent_cli.web.dpapi import CredentialProtectionError, CredentialProtector
from boss_agent_cli.web.workspace import WorkspaceRegistry


LoginProvider = Callable[[WorkspaceKind], dict[str, Any]]


class WorkspaceSessionStore:
	def __init__(self, *, registry: WorkspaceRegistry, protector: CredentialProtector) -> None:
		self._registry = registry
		self._protector = protector

	def _purpose(self, workspace: WorkspaceKind) -> bytes:
		return f"BossAgent/platform-session/{workspace.value}".encode("ascii")

	def save(self, workspace: WorkspaceKind, credential: bytes) -> None:
		path = self._registry.ensure_workspace(workspace).credential
		ciphertext = self._protector.protect(credential, purpose=self._purpose(workspace))
		temporary = path.with_suffix(".tmp")
		temporary.write_bytes(ciphertext)
		os.chmod(temporary, 0o600)
		os.replace(temporary, path)

	def load(self, workspace: WorkspaceKind) -> bytes | None:
		path = self._registry.paths(workspace).credential
		if not path.is_file():
			return None
		return self._protector.unprotect(path.read_bytes(), purpose=self._purpose(workspace))

	def delete(self, workspace: WorkspaceKind) -> None:
		self._registry.paths(workspace).credential.unlink(missing_ok=True)


class PlatformSessionManager:
	"""Keep only the active Workspace credential decrypted in process memory."""

	def __init__(
		self,
		*,
		registry: WorkspaceRegistry,
		store: WorkspaceSessionStore,
		login_provider: LoginProvider,
	) -> None:
		self._registry = registry
		self._store = store
		self._login_provider = login_provider
		self._active_workspace = registry.configured_active_workspace
		self._active_credential: bytes | None = None
		self._states = {workspace: PlatformSessionState.DISCONNECTED for workspace in WorkspaceKind}
		self._generation = 0
		self._threads: list[threading.Thread] = []
		self._lock = threading.RLock()
		self.activate(self._active_workspace)

	def _load_active(self, workspace: WorkspaceKind) -> None:
		try:
			credential = self._store.load(workspace)
		except (CredentialProtectionError, OSError):
			self._active_credential = None
			self._states[workspace] = PlatformSessionState.RECOVERY
			return
		self._active_credential = credential
		self._states[workspace] = (
			PlatformSessionState.CONNECTED if credential is not None else PlatformSessionState.DISCONNECTED
		)

	def activate(self, workspace: WorkspaceKind) -> None:
		with self._lock:
			self._generation += 1
			self._active_credential = None
			self._active_workspace = workspace
			self._load_active(workspace)

	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		with self._lock:
			if workspace is not self._active_workspace:
				return PlatformSessionState.DISCONNECTED
			return self._states[workspace]

	def begin_connect(self, workspace: WorkspaceKind) -> None:
		with self._lock:
			if workspace is not self._active_workspace:
				raise PermissionError("cannot connect an inactive workspace")
			if self._states[workspace] in {PlatformSessionState.CONNECTING, PlatformSessionState.STOPPING}:
				return
			self._generation += 1
			generation = self._generation
			self._active_credential = None
			self._states[workspace] = PlatformSessionState.CONNECTING
			thread = threading.Thread(
				target=self._finish_connect,
				args=(workspace, generation),
				name=f"boss-login-{workspace.value}",
				daemon=True,
			)
			self._threads.append(thread)
			thread.start()

	def _finish_connect(self, workspace: WorkspaceKind, generation: int) -> None:
		try:
			credential_data = self._login_provider(workspace)
			cookies = credential_data.get("cookies")
			if not isinstance(cookies, dict) or not cookies:
				raise ValueError("official login returned no session cookies")
			credential_data.pop("_method", None)
			credential = json.dumps(credential_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
			with self._lock:
				if generation != self._generation or workspace is not self._active_workspace:
					return
				self._store.save(workspace, credential)
				self._active_credential = credential
				self._states[workspace] = PlatformSessionState.CONNECTED
		except Exception:  # Login and OS adapters must collapse to one safe recovery state.
			with self._lock:
				if generation == self._generation and workspace is self._active_workspace:
					self._active_credential = None
					self._states[workspace] = PlatformSessionState.RECOVERY

	def begin_logout(self, workspace: WorkspaceKind) -> None:
		with self._lock:
			if workspace is not self._active_workspace:
				raise PermissionError("cannot log out an inactive workspace")
			self._generation += 1
			generation = self._generation
			self._active_credential = None
			self._states[workspace] = PlatformSessionState.STOPPING
			thread = threading.Thread(
				target=self._finish_logout,
				args=(workspace, generation),
				name=f"boss-logout-{workspace.value}",
				daemon=True,
			)
			self._threads.append(thread)
			thread.start()

	def clear(self, workspace: WorkspaceKind) -> None:
		with self._lock:
			if workspace is not self._active_workspace:
				raise PermissionError("cannot clear an inactive workspace session")
			self._generation += 1
			self._active_credential = None
			self._store.delete(workspace)
			self._states[workspace] = PlatformSessionState.DISCONNECTED

	def _finish_logout(self, workspace: WorkspaceKind, generation: int) -> None:
		try:
			self._store.delete(workspace)
		except OSError:
			with self._lock:
				if generation == self._generation and workspace is self._active_workspace:
					self._states[workspace] = PlatformSessionState.RECOVERY
			return
		with self._lock:
			if generation == self._generation and workspace is self._active_workspace:
				self._states[workspace] = PlatformSessionState.DISCONNECTED

	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self.platform_session_state(workspace)

	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None:
		"""Return a defensive in-process copy for the trusted BOSS read adapter."""

		with self._lock:
			if workspace is not self._active_workspace or self._active_credential is None:
				return None
			try:
				value = json.loads(self._active_credential)
			except (UnicodeDecodeError, json.JSONDecodeError):
				return None
			return dict(value) if isinstance(value, dict) else None

	def session_revision(self, workspace: WorkspaceKind) -> str:
		"""Bind a prepared write to the exact active credential generation."""

		with self._lock:
			if workspace is not self._active_workspace:
				return "inactive"
			return str(self._generation)

	def wait_for_idle(self, *, timeout: float) -> bool:
		with self._lock:
			threads = tuple(self._threads)
		for thread in threads:
			thread.join(timeout=timeout)
		with self._lock:
			self._threads = [thread for thread in self._threads if thread.is_alive()]
			return not self._threads
