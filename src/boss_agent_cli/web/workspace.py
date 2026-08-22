"""Fixed-root, physically isolated local Web workspaces."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from boss_agent_cli.application import (
	ApplicationEvent,
	DomainErrorDetails,
	ErrorCode,
	JobSearchGoal,
	JobSummary,
	RecruitingOpening,
	RunEventKind,
	RunResult,
	RunSummary,
	WorkspaceKind,
)


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
				connection.execute(
					"CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)"
				)
				connection.execute(
					"CREATE TABLE IF NOT EXISTS run_events ("
					"run_id TEXT NOT NULL, cursor INTEGER NOT NULL, event TEXT NOT NULL, "
					"PRIMARY KEY (run_id, cursor))"
				)
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

	@staticmethod
	def _run_payload(summary: RunSummary) -> dict[str, object]:
		return {
			"run_id": summary.run_id,
			"state": summary.state,
			"progress": summary.progress,
			"wait_reason": summary.wait_reason,
			"workspace": summary.workspace.value if summary.workspace is not None else None,
			"kind": summary.kind,
			"phase": summary.phase,
			"created_at": summary.created_at.isoformat() if summary.created_at is not None else None,
			"updated_at": summary.updated_at.isoformat() if summary.updated_at is not None else None,
			"result": (
				{
					"category": summary.result.category,
					"item_count": summary.result.item_count,
					"remote_write_may_have_occurred": summary.result.remote_write_may_have_occurred,
				}
				if summary.result is not None
				else None
			),
			"error": (
				{
					"code": summary.error.code.value,
					"message": summary.error.message,
					"recoverable": summary.error.recoverable,
					"recovery_action": summary.error.recovery_action,
					"correlation_id": summary.error.correlation_id,
					"field_errors": summary.error.field_errors,
				}
				if summary.error is not None
				else None
			),
			"permitted_next_actions": summary.permitted_next_actions,
		}

	@staticmethod
	def _run_from_payload(payload: dict[str, object]) -> RunSummary:
		result_payload = payload.get("result")
		error_payload = payload.get("error")
		result = None
		if isinstance(result_payload, dict):
			result = RunResult(
				category=str(result_payload.get("category", "")),
				item_count=int(result_payload.get("item_count", 0)),
				remote_write_may_have_occurred=bool(result_payload.get("remote_write_may_have_occurred", False)),
			)
		error = None
		if isinstance(error_payload, dict):
			field_values = error_payload.get("field_errors", ())
			field_errors = (
				tuple((str(item[0]), str(item[1])) for item in field_values if isinstance(item, (list, tuple)) and len(item) == 2)
				if isinstance(field_values, (list, tuple))
				else ()
			)
			error = DomainErrorDetails(
				code=ErrorCode(str(error_payload["code"])),
				message=str(error_payload.get("message", "")),
				recoverable=bool(error_payload.get("recoverable", False)),
				recovery_action=(
					str(error_payload["recovery_action"]) if error_payload.get("recovery_action") is not None else None
				),
				correlation_id=str(error_payload.get("correlation_id", "")),
				field_errors=field_errors,
			)
		workspace_value = payload.get("workspace")
		created_value = payload.get("created_at")
		updated_value = payload.get("updated_at")
		progress_value = payload.get("progress")
		actions_value = payload.get("permitted_next_actions", ())
		return RunSummary(
			run_id=str(payload["run_id"]),
			state=str(payload["state"]),
			progress=int(str(progress_value)) if progress_value is not None else None,
			wait_reason=str(payload["wait_reason"]) if payload.get("wait_reason") is not None else None,
			workspace=WorkspaceKind(str(workspace_value)) if workspace_value is not None else None,
			kind=str(payload.get("kind", "")),
			phase=str(payload.get("phase", "")),
			created_at=datetime.fromisoformat(str(created_value)) if created_value is not None else None,
			updated_at=datetime.fromisoformat(str(updated_value)) if updated_value is not None else None,
			result=result,
			error=error,
			permitted_next_actions=(
				tuple(map(str, actions_value)) if isinstance(actions_value, (list, tuple)) else ()
			),
		)

	def save_run(self, summary: RunSummary) -> None:
		if summary.workspace is None or summary.updated_at is None:
			raise ValueError("persisted run requires workspace and updated_at")
		paths = self.ensure_workspace(summary.workspace)
		payload = json.dumps(self._run_payload(summary), ensure_ascii=False, sort_keys=True)
		with sqlite3.connect(paths.database) as connection:
			connection.execute(
				"INSERT OR REPLACE INTO runs(run_id, snapshot, updated_at) VALUES (?, ?, ?)",
				(summary.run_id, payload, summary.updated_at.isoformat()),
			)
			connection.execute(
				"INSERT OR REPLACE INTO local_state(key, value) VALUES ('active-run-id', ?)",
				(summary.run_id,),
			)
			connection.commit()

	def load_latest_run(self, workspace: WorkspaceKind) -> RunSummary | None:
		paths = self.ensure_workspace(workspace)
		with sqlite3.connect(paths.database) as connection:
			marker = connection.execute(
				"SELECT value FROM local_state WHERE key = 'active-run-id'"
			).fetchone()
			if marker is not None:
				if not str(marker[0]):
					return None
				row = connection.execute("SELECT snapshot FROM runs WHERE run_id = ?", (str(marker[0]),)).fetchone()
			else:
				# Compatibility for workspaces created before the active Run marker existed.
				row = connection.execute("SELECT snapshot FROM runs ORDER BY updated_at DESC LIMIT 1").fetchone()
		if row is None:
			return None
		return self._run_from_payload(json.loads(str(row[0])))

	def append_event(self, workspace: WorkspaceKind, event: ApplicationEvent) -> None:
		paths = self.ensure_workspace(workspace)
		payload = json.dumps(
			{
				"cursor": event.cursor,
				"run_id": event.run_id,
				"kind": event.kind.value,
				"occurred_at": event.occurred_at.isoformat(),
				"payload": event.payload,
			},
			ensure_ascii=False,
			sort_keys=True,
		)
		with sqlite3.connect(paths.database) as connection:
			connection.execute(
				"INSERT OR REPLACE INTO run_events(run_id, cursor, event) VALUES (?, ?, ?)",
				(event.run_id, event.cursor, payload),
			)
			connection.commit()

	def read_events(self, workspace: WorkspaceKind, run_id: str) -> tuple[ApplicationEvent, ...]:
		paths = self.ensure_workspace(workspace)
		with sqlite3.connect(paths.database) as connection:
			rows = connection.execute(
				"SELECT event FROM run_events WHERE run_id = ? ORDER BY cursor",
				(run_id,),
			).fetchall()
		if not rows:
			raise KeyError(run_id)
		events = []
		for row in rows:
			payload = json.loads(str(row[0]))
			events.append(
				ApplicationEvent(
					cursor=int(payload["cursor"]),
					run_id=str(payload["run_id"]),
					kind=RunEventKind(str(payload["kind"])),
					occurred_at=datetime.fromisoformat(str(payload["occurred_at"])),
					payload=tuple(tuple(item) for item in payload.get("payload", ())),
				)
			)
		return tuple(events)

	def discard_run(self, workspace: WorkspaceKind, run_id: str) -> None:
		paths = self.ensure_workspace(workspace)
		with sqlite3.connect(paths.database) as connection:
			connection.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
			connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
			connection.execute(
				"UPDATE local_state SET value = '' WHERE key = 'active-run-id' AND value = ?",
				(run_id,),
			)
			connection.commit()

	def save_job_search_goal(self, goal: JobSearchGoal) -> None:
		self.write_local_state(
			WorkspaceKind.JOB_SEEKING,
			"job-search-goal",
			json.dumps(
				{
					"objective": goal.objective,
					"keyword": goal.keyword,
					"city": goal.city,
					"salary": goal.salary,
					"experience": goal.experience,
					"education": goal.education,
				},
				ensure_ascii=False,
				sort_keys=True,
			),
		)

	def load_job_search_goal(self) -> JobSearchGoal | None:
		value = self.read_local_state(WorkspaceKind.JOB_SEEKING, "job-search-goal")
		if value is None:
			return None
		try:
			payload = json.loads(value)
			return JobSearchGoal(
				objective=str(payload["objective"]),
				keyword=str(payload["keyword"]),
				city=str(payload.get("city", "")),
				salary=str(payload.get("salary", "")),
				experience=str(payload.get("experience", "")),
				education=str(payload.get("education", "")),
			)
		except (json.JSONDecodeError, KeyError, TypeError, ValueError):
			return None

	def save_job_shortlist(self, items: tuple[JobSummary, ...]) -> None:
		self.write_local_state(
			WorkspaceKind.JOB_SEEKING,
			"job-shortlist",
			json.dumps(
				[
					{
						"reference": item.reference,
						"title": item.title,
						"company": item.company,
						"location": item.location,
						"salary": item.salary,
						"experience": item.experience,
						"education": item.education,
					}
					for item in items
				],
				ensure_ascii=False,
				sort_keys=True,
			),
		)

	def load_job_shortlist(self) -> tuple[JobSummary, ...]:
		value = self.read_local_state(WorkspaceKind.JOB_SEEKING, "job-shortlist")
		if value is None:
			return ()
		try:
			payload = json.loads(value)
			if not isinstance(payload, list):
				return ()
			return tuple(
				JobSummary(
					reference=str(item["reference"]),
					title=str(item["title"]),
					company=str(item["company"]),
					location=str(item.get("location", "")),
					salary=str(item.get("salary", "")),
					experience=str(item.get("experience", "")),
					education=str(item.get("education", "")),
				)
				for item in payload
				if isinstance(item, dict)
			)
		except (json.JSONDecodeError, KeyError, TypeError, ValueError):
			return ()

	def save_recruiting_opening(self, opening: RecruitingOpening) -> None:
		self.write_local_state(
			WorkspaceKind.RECRUITING,
			"selected-opening",
			json.dumps(
				{"reference": opening.reference, "title": opening.title, "status": opening.status},
				ensure_ascii=False,
				sort_keys=True,
			),
		)

	def load_recruiting_opening(self) -> RecruitingOpening | None:
		value = self.read_local_state(WorkspaceKind.RECRUITING, "selected-opening")
		if value is None:
			return None
		try:
			payload = json.loads(value)
			return RecruitingOpening(
				reference=str(payload["reference"]),
				title=str(payload["title"]),
				status=str(payload.get("status", "")),
			)
		except (json.JSONDecodeError, KeyError, TypeError, ValueError):
			return None
