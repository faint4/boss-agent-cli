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
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RunSummary,
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
		self._runs: dict[WorkspaceKind, RunSummary] = {}
		self._last_transition: str | None = None
		self._sensitive: dict[WorkspaceKind, bool] = {}
		self._job_search_goal: JobSearchGoal | None = None
		self._job_shortlist: tuple[JobSummary, ...] = ()
		self._recruiting_opening: RecruitingOpening | None = None

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

	def approximate_usage(self, workspace: WorkspaceKind) -> int:
		return 0

	def clear_workspace(self, workspace: WorkspaceKind) -> None:
		if workspace is WorkspaceKind.JOB_SEEKING:
			self._job_search_goal = None
			self._job_shortlist = ()
		else:
			self._recruiting_opening = None
		run = self._runs.pop(workspace, None)
		if run is not None:
			self._run_events.pop(run.run_id, None)
		self._sensitive[workspace] = False
		self._last_transition = "workspace-cleared"

	def save_run(self, summary: RunSummary) -> None:
		if summary.workspace is None:
			raise ValueError("run workspace is required")
		self._runs[summary.workspace] = summary

	def save_run_event(self, summary: RunSummary, event: ApplicationEvent) -> None:
		self.save_run(summary)
		self.append_event(summary.workspace, event)  # type: ignore[arg-type]

	def load_latest_run(self, workspace: WorkspaceKind) -> RunSummary | None:
		return self._runs.get(workspace)

	def list_runs(self, workspace: WorkspaceKind) -> tuple[RunSummary, ...]:
		run = self._runs.get(workspace)
		return () if run is None else (run,)

	def append_event(self, workspace: WorkspaceKind, event: ApplicationEvent) -> None:
		self._run_events[event.run_id] = (*self._run_events.get(event.run_id, ()), event)

	def read_events(self, workspace: WorkspaceKind, run_id: str) -> tuple[ApplicationEvent, ...]:
		return self._run_events[run_id]

	def discard_run(self, workspace: WorkspaceKind, run_id: str) -> None:
		if workspace in self._runs and self._runs[workspace].run_id == run_id:
			del self._runs[workspace]
		self._run_events.pop(run_id, None)

	def load_job_search_goal(self) -> JobSearchGoal | None:
		return self._job_search_goal

	def save_job_search_goal(self, goal: JobSearchGoal) -> None:
		self._job_search_goal = goal

	def load_job_shortlist(self) -> tuple[JobSummary, ...]:
		return self._job_shortlist

	def save_job_shortlist(self, items: tuple[JobSummary, ...]) -> None:
		self._job_shortlist = items

	def load_recruiting_opening(self) -> RecruitingOpening | None:
		return self._recruiting_opening

	def save_recruiting_opening(self, opening: RecruitingOpening) -> None:
		self._recruiting_opening = opening


class InMemoryCredentialStore:
	def __init__(self, states: dict[WorkspaceKind, PlatformSessionState] | None = None) -> None:
		self._states = dict(states or {})
		self._revisions = {workspace: 1 for workspace in self._states}

	def platform_session_state(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self._states.get(workspace, PlatformSessionState.DISCONNECTED)

	def activate(self, workspace: WorkspaceKind) -> None:
		self._states.setdefault(workspace, PlatformSessionState.DISCONNECTED)
		self.rotate(workspace)

	def begin_connect(self, workspace: WorkspaceKind) -> None:
		self._states[workspace] = PlatformSessionState.CONNECTING
		self.rotate(workspace)

	def begin_logout(self, workspace: WorkspaceKind) -> None:
		self._states[workspace] = PlatformSessionState.STOPPING
		self.rotate(workspace)

	def clear(self, workspace: WorkspaceKind) -> None:
		self._states[workspace] = PlatformSessionState.DISCONNECTED
		self.rotate(workspace)

	def session_revision(self, workspace: WorkspaceKind) -> str:
		return str(self._revisions.get(workspace, 0))

	def rotate(self, workspace: WorkspaceKind) -> None:
		self._revisions[workspace] = self._revisions.get(workspace, 0) + 1


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
		greeting_failure: ErrorCode | None = None,
		greeting_gate: threading.Event | None = None,
		recruiting_reply_failure: ErrorCode | None = None,
		recruiting_reply_gate: threading.Event | None = None,
		openings: tuple[RecruitingOpening, ...] = (),
		applicant_batches: tuple[RecruitingApplicantBatch, ...] = (),
		prospect_contexts: dict[str, RecruitingProspectContext] | None = None,
	) -> None:
		self.state = state
		self.failure = failure
		self.probed_workspaces: list[WorkspaceKind] = []
		self.search_batches = search_batches
		self.search_failure = search_failure
		self.failure_after_batches = failure_after_batches
		self.details = dict(details or {})
		self.batch_gate = batch_gate
		self.greeting_failure = greeting_failure
		self.greeting_gate = greeting_gate
		self.recruiting_reply_failure = recruiting_reply_failure
		self.recruiting_reply_gate = recruiting_reply_gate
		self.openings = openings
		self.applicant_batches = applicant_batches
		self.prospect_contexts = dict(prospect_contexts or {})
		self.search_calls = 0
		self.detail_calls: list[str] = []
		self.greeting_calls: list[tuple[str, str]] = []
		self.opening_calls = 0
		self.applicant_calls: list[str] = []
		self.prospect_context_calls: list[str] = []
		self.recruiting_reply_calls: list[tuple[str, str]] = []

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

	def send_greeting(self, reference: str, message: str) -> None:
		self.greeting_calls.append((reference, message))
		if self.greeting_gate is not None:
			self.greeting_gate.wait(timeout=2)
		if self.greeting_failure is not None:
			raise BossAdapterFailure(self.greeting_failure)

	def send_recruiting_reply(self, reference: str, message: str) -> None:
		self.recruiting_reply_calls.append((reference, message))
		if self.recruiting_reply_gate is not None:
			self.recruiting_reply_gate.wait(timeout=2)
		if self.recruiting_reply_failure is not None:
			raise BossAdapterFailure(self.recruiting_reply_failure)

	def list_openings(self) -> tuple[RecruitingOpening, ...]:
		self.opening_calls += 1
		if self.search_failure is not None:
			raise BossAdapterFailure(self.search_failure)
		return self.openings

	def inbound_applicants(
		self,
		opening_reference: str,
		*,
		cancel_requested: Callable[[], bool],
	) -> Iterable[RecruitingApplicantBatch]:
		self.applicant_calls.append(opening_reference)
		for index, batch in enumerate(self.applicant_batches):
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
			self.failure_after_batches is None or self.failure_after_batches >= len(self.applicant_batches)
		):
			raise BossAdapterFailure(self.search_failure)

	def prospect_context(self, opening_reference: str, prospect_reference: str) -> RecruitingProspectContext:
		self.prospect_context_calls.append(prospect_reference)
		if self.search_failure is not None:
			raise BossAdapterFailure(self.search_failure)
		try:
			return self.prospect_contexts[prospect_reference]
		except KeyError as exc:
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
