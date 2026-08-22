"""Deterministic adapters for application-module contract tests."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable

from boss_agent_cli.application.contracts import (
	ApplicationEvent,
	ErrorCode,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	WorkspaceKind,
)
from boss_agent_cli.application.module import BossAdapterFailure


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
		self._last_transition: str | None = None
		self._sensitive: dict[WorkspaceKind, bool] = {}
		self._job_search_goal: JobSearchGoal | None = None
		self._job_shortlist: tuple[JobSummary, ...] = ()

	def active_workspace(self, local_session_id: str) -> WorkspaceKind:
		if local_session_id != self._local_session_id:
			raise PermissionError("local session is not authorized")
		return self._active_workspace

	def switch_workspace(self, local_session_id: str, workspace: WorkspaceKind) -> None:
		self.active_workspace(local_session_id)
		self._sensitive[self._active_workspace] = False
		self._active_workspace = workspace
		self._last_transition = "workspace-switched"

	def sensitive_content_present(self, workspace: WorkspaceKind) -> bool:
		return self._sensitive.get(workspace, False)

	def last_transition(self, local_session_id: str) -> str | None:
		self.active_workspace(local_session_id)
		return self._last_transition

	def read_events(self, run_id: str) -> tuple[ApplicationEvent, ...]:
		return self._run_events[run_id]

	def load_job_search_goal(self) -> JobSearchGoal | None:
		return self._job_search_goal

	def save_job_search_goal(self, goal: JobSearchGoal) -> None:
		self._job_search_goal = goal

	def load_job_shortlist(self) -> tuple[JobSummary, ...]:
		return self._job_shortlist

	def save_job_shortlist(self, items: tuple[JobSummary, ...]) -> None:
		self._job_shortlist = items


class InMemoryCredentialStore:
	def __init__(self, states: dict[WorkspaceKind, PlatformSessionState] | None = None) -> None:
		self._states = dict(states or {})

	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self._states.get(workspace, PlatformSessionState.DISCONNECTED)

	def activate(self, workspace: WorkspaceKind) -> None:
		self._states.setdefault(workspace, PlatformSessionState.DISCONNECTED)

	def begin_connect(self, workspace: WorkspaceKind) -> None:
		self._states[workspace] = PlatformSessionState.CONNECTING

	def begin_logout(self, workspace: WorkspaceKind) -> None:
		self._states[workspace] = PlatformSessionState.STOPPING


class FakeBossAdapter:
	def __init__(
		self,
		state: PlatformSessionState = PlatformSessionState.CONNECTED,
		*,
		failure: RuntimeError | None = None,
		search_batches: tuple[JobSearchBatch, ...] = (),
		search_failure: ErrorCode | None = None,
		failure_after_batches: int | None = None,
		details: dict[str, JobSourceDetail] | None = None,
		batch_gate: threading.Event | None = None,
	) -> None:
		self.state = state
		self.failure = failure
		self.probed_workspaces: list[WorkspaceKind] = []
		self.search_batches = search_batches
		self.search_failure = search_failure
		self.failure_after_batches = failure_after_batches
		self.details = dict(details or {})
		self.batch_gate = batch_gate
		self.search_calls = 0
		self.detail_calls: list[str] = []

	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		self.probed_workspaces.append(workspace)
		if self.failure is not None:
			raise self.failure
		return self.state

	def search_jobs(
		self,
		goal: JobSearchGoal,
		*,
		cancel_requested: Callable[[], bool],
	) -> Iterable[JobSearchBatch]:
		self.search_calls += 1
		for index, batch in enumerate(self.search_batches):
			if self.batch_gate is not None and index > 0:
				while not self.batch_gate.wait(timeout=0.01):
					if cancel_requested():
						return
			if cancel_requested():
				return
			if self.search_failure is not None and self.failure_after_batches == index:
				raise BossAdapterFailure(self.search_failure)
			yield batch
		if self.search_failure is not None and (
			self.failure_after_batches is None or self.failure_after_batches >= len(self.search_batches)
		):
			raise BossAdapterFailure(self.search_failure)

	def job_detail(self, reference: str) -> JobSourceDetail:
		self.detail_calls.append(reference)
		if self.search_failure is not None:
			raise BossAdapterFailure(self.search_failure)
		try:
			return self.details[reference]
		except KeyError as exc:
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
