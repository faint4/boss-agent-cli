"""Fixed-root, physically isolated local Web workspaces."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from boss_agent_cli.application import ApplicationEvent, WorkspaceKind


@dataclass(frozen=True)
class WorkspacePaths:
	root: Path
	database: Path
	cache: Path
	runs: Path
	credential: Path


@dataclass
class _RuntimeState:
	sensitive_content_present: bool = False


def default_product_root() -> Path:
	"""Return the current-user application-data root without accepting client input."""

	if sys.platform == "win32":
		local_app_data = os.environ.get("LOCALAPPDATA")
		base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
	else:
		xdg_data_home = os.environ.get("XDG_DATA_HOME")
		base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
	return base / "BossAgent"


class WorkspaceRegistry:
	"""Resolve both role workspaces from fixed enum values and own their lifecycle."""

	def __init__(self, product_root: Path, *, local_session_id: str) -> None:
		self._product_root = product_root.resolve()
		self._local_session_id = local_session_id
		self._app_root = self._product_root / "app"
		self._workspaces_root = self._product_root / "workspaces"
		self._settings_path = self._app_root / "settings.json"
		self._runtime: dict[WorkspaceKind, _RuntimeState] = {}
		self._last_transition: str | None = None
		self._lock = threading.RLock()
		self._app_root.mkdir(parents=True, exist_ok=True)
		self._active_workspace = self._load_active_workspace()
		self.ensure_workspace(self._active_workspace)

	def paths(self, workspace: WorkspaceKind) -> WorkspacePaths:
		root = self._workspaces_root / workspace.value
		return WorkspacePaths(
			root=root,
			database=root / "workspace.sqlite3",
			cache=root / "cache",
			runs=root / "runs",
			credential=root / "credentials" / "platform-session.bin",
		)

	def _load_active_workspace(self) -> WorkspaceKind:
		try:
			payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
			return WorkspaceKind(payload["active_workspace"])
		except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
			return WorkspaceKind.JOB_SEEKING

	def _persist_active_workspace(self, workspace: WorkspaceKind) -> None:
		payload = {"active_workspace": workspace.value, "schema_version": "1"}
		temporary = self._settings_path.with_suffix(".tmp")
		temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
		os.replace(temporary, self._settings_path)

	def _authorize(self, local_session_id: str) -> None:
		if local_session_id != self._local_session_id:
			raise PermissionError("local session is not authorized")

	def ensure_workspace(self, workspace: WorkspaceKind) -> WorkspacePaths:
		paths = self.paths(workspace)
		with self._lock:
			paths.root.mkdir(parents=True, exist_ok=True)
			paths.cache.mkdir(exist_ok=True)
			paths.runs.mkdir(exist_ok=True)
			paths.credential.parent.mkdir(exist_ok=True)
			with sqlite3.connect(paths.database) as connection:
				connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
				connection.execute("CREATE TABLE IF NOT EXISTS local_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
				connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', '1')")
				connection.commit()
			self._runtime.setdefault(workspace, _RuntimeState())
		return paths

	def active_workspace(self, local_session_id: str) -> WorkspaceKind:
		with self._lock:
			self._authorize(local_session_id)
			return self._active_workspace

	@property
	def configured_active_workspace(self) -> WorkspaceKind:
		"""Return the persisted selection for trusted in-process adapters."""

		with self._lock:
			return self._active_workspace

	def switch_workspace(self, local_session_id: str, workspace: WorkspaceKind) -> None:
		with self._lock:
			self._authorize(local_session_id)
			if workspace is self._active_workspace:
				self._last_transition = "workspace-unchanged"
				return
			self._runtime[self._active_workspace].sensitive_content_present = False
			self.ensure_workspace(workspace)
			self._persist_active_workspace(workspace)
			self._active_workspace = workspace
			self._last_transition = "workspace-switched"

	def last_transition(self, local_session_id: str) -> str | None:
		with self._lock:
			self._authorize(local_session_id)
			return self._last_transition

	def mark_sensitive_content(self, workspace: WorkspaceKind, *, present: bool) -> None:
		with self._lock:
			self.ensure_workspace(workspace)
			self._runtime[workspace].sensitive_content_present = present

	def sensitive_content_present(self, workspace: WorkspaceKind) -> bool:
		with self._lock:
			return self._runtime.get(workspace, _RuntimeState()).sensitive_content_present

	def write_local_state(self, workspace: WorkspaceKind, key: str, value: str) -> None:
		paths = self.ensure_workspace(workspace)
		with sqlite3.connect(paths.database) as connection:
			connection.execute("INSERT OR REPLACE INTO local_state(key, value) VALUES (?, ?)", (key, value))
			connection.commit()

	def read_local_state(self, workspace: WorkspaceKind, key: str) -> str | None:
		paths = self.ensure_workspace(workspace)
		with sqlite3.connect(paths.database) as connection:
			row = connection.execute("SELECT value FROM local_state WHERE key = ?", (key,)).fetchone()
		return None if row is None else str(row[0])

	def read_events(self, run_id: str) -> tuple[ApplicationEvent, ...]:
		raise KeyError(run_id)
