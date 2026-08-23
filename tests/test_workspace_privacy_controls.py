from datetime import datetime, timezone
import threading
import time
from unittest.mock import Mock

import pytest

from boss_agent_cli.application import (
	Application,
	ClearWorkspaceCommand,
	CurrentStateQuery,
	DomainError,
	JobSearchGoal,
	JobSearchBatch,
	JobSummary,
	PlatformSessionState,
	RequestContext,
	LogoutPlatformSessionCommand,
	StartJobSearchCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import (
	FakeBossAdapter,
	InMemoryCredentialStore,
	InMemoryWorkspaceStore,
)
from boss_agent_cli.job_runtime import CandidateCredentialStore
from boss_agent_cli.recruiting_runtime import RecruitingCredentialStore


def _context() -> RequestContext:
	return RequestContext("local-session", "privacy-20")


@pytest.mark.parametrize(
	("store_factory", "owned_workspace", "other_workspace"),
	[
		(
			lambda auth: CandidateCredentialStore(auth, platform="zhipin"),
			WorkspaceKind.JOB_SEEKING,
			WorkspaceKind.RECRUITING,
		),
		(
			lambda auth: RecruitingCredentialStore(auth, platform="zhipin"),
			WorkspaceKind.RECRUITING,
			WorkspaceKind.JOB_SEEKING,
		),
	],
)
def test_cli_credential_adapters_clear_only_their_owned_workspace(
	store_factory: object,
	owned_workspace: WorkspaceKind,
	other_workspace: WorkspaceKind,
) -> None:
	auth = Mock()
	store = store_factory(auth)  # type: ignore[operator]

	store.clear(other_workspace)
	auth.logout.assert_not_called()
	store.clear(owned_workspace)

	auth.logout.assert_called_once_with()


def test_snapshot_explains_storage_and_default_export_scope_for_both_workspaces() -> None:
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="local-session",
			active_workspace=WorkspaceKind.JOB_SEEKING,
		),
		credential_store=InMemoryCredentialStore(
			{
				WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
				WorkspaceKind.RECRUITING: PlatformSessionState.DISCONNECTED,
			}
		),
		boss=FakeBossAdapter(),
	)

	snapshot = application.query(CurrentStateQuery(), _context())

	assert tuple(item.workspace for item in snapshot.workspace_privacy) == (
		WorkspaceKind.JOB_SEEKING,
		WorkspaceKind.RECRUITING,
	)
	job, recruiting = snapshot.workspace_privacy
	assert job.approximate_bytes == 0
	assert job.retained_categories == (
		"search-goal-and-filters",
		"job-shortlist",
		"recoverable-runs",
		"protected-platform-session",
	)
	assert recruiting.retained_categories == (
		"selected-opening",
		"recoverable-runs",
		"protected-platform-session",
	)
	assert job.default_export_includes == (
		"search-goal-and-filters",
		"job-shortlist",
		"recoverable-run-checkpoints",
	)
	assert recruiting.default_export_includes == (
		"selected-opening",
		"recoverable-run-checkpoints",
	)
	assert job.default_export_excludes == recruiting.default_export_excludes == (
		"credentials-cookies-and-tokens",
		"resumes-contact-details-and-chats",
		"drafts-and-write-intents",
		"logs-and-transient-content",
	)


def test_job_workspace_export_contains_only_the_documented_allowlist() -> None:
	store = InMemoryWorkspaceStore(
		local_session_id="local-session",
		active_workspace=WorkspaceKind.JOB_SEEKING,
	)
	goal = JobSearchGoal("寻找后端岗位", "Python", city="上海")
	job = JobSummary("job-1", "Python 后端工程师", "示例科技", location="上海")
	store.save_job_search_goal(goal)
	store.save_job_shortlist((job,))
	application = Application(
		workspace_store=store,
		credential_store=InMemoryCredentialStore(),
		boss=FakeBossAdapter(),
		clock=lambda: datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc),
	)

	export = application.export_workspace(WorkspaceKind.JOB_SEEKING, _context())

	assert export.schema_version == "1"
	assert export.workspace is WorkspaceKind.JOB_SEEKING
	assert export.generated_at == datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)
	assert export.job_search_goal == goal
	assert export.job_shortlist == (job,)
	assert export.selected_opening is None
	assert export.recoverable_runs == ()
	assert export.excluded_categories == (
		"credentials-cookies-and-tokens",
		"resumes-contact-details-and-chats",
		"drafts-and-write-intents",
		"logs-and-transient-content",
	)


def test_clear_requires_exact_confirmation_and_only_clears_the_selected_workspace() -> None:
	store = InMemoryWorkspaceStore(
		local_session_id="local-session",
		active_workspace=WorkspaceKind.JOB_SEEKING,
	)
	goal = JobSearchGoal("寻找后端岗位", "Python")
	job = JobSummary("job-1", "Python 后端工程师", "示例科技")
	store.save_job_search_goal(goal)
	store.save_job_shortlist((job,))
	credentials = InMemoryCredentialStore(
		{
			WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
			WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED,
		}
	)
	application = Application(
		workspace_store=store,
		credential_store=credentials,
		boss=FakeBossAdapter(),
	)

	with pytest.raises(DomainError):
		application.execute(
			ClearWorkspaceCommand(WorkspaceKind.JOB_SEEKING, confirmation="clear everything"),
			_context(),
		)
	assert application.export_workspace(WorkspaceKind.JOB_SEEKING, _context()).job_search_goal == goal

	cleared = application.execute(
		ClearWorkspaceCommand(WorkspaceKind.JOB_SEEKING, confirmation="clear:job-seeking"),
		_context(),
	).snapshot

	assert cleared.active_workspace is WorkspaceKind.JOB_SEEKING
	assert cleared.platform_session is PlatformSessionState.DISCONNECTED
	assert cleared.job_seeking is not None
	assert cleared.job_seeking.goal is None
	assert cleared.job_seeking.shortlist == ()
	assert credentials.platform_session_state(WorkspaceKind.RECRUITING) is PlatformSessionState.CONNECTED


def test_logout_stops_the_active_read_but_preserves_local_data_and_the_other_session() -> None:
	allow_second_batch = threading.Event()
	store = InMemoryWorkspaceStore(
		local_session_id="local-session",
		active_workspace=WorkspaceKind.JOB_SEEKING,
	)
	credentials = InMemoryCredentialStore(
		{
			WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED,
			WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED,
		}
	)
	goal = JobSearchGoal("寻找后端岗位", "Python")
	application = Application(
		workspace_store=store,
		credential_store=credentials,
		boss=FakeBossAdapter(
			search_batches=(
				JobSearchBatch((JobSummary("job-1", "Python 工程师", "示例科技"),), 50),
				JobSearchBatch((JobSummary("job-2", "Go 工程师", "另一家公司"),), 90),
			),
			batch_gate=allow_second_batch,
		),
		run_id_factory=lambda: "logout-run",
	)
	application.execute(UpdateJobSearchGoalCommand(goal), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.progress == 50:
			break
		time.sleep(0.01)
	else:
		raise AssertionError("read did not reach its checkpoint")

	logged_out = application.execute(LogoutPlatformSessionCommand(), _context()).snapshot
	allow_second_batch.set()

	assert logged_out.platform_session is PlatformSessionState.STOPPING
	assert logged_out.active_run is not None
	assert logged_out.active_run.state == "stopping"
	assert credentials.platform_session_state(WorkspaceKind.RECRUITING) is PlatformSessionState.CONNECTED
	assert application.export_workspace(WorkspaceKind.JOB_SEEKING, _context()).job_search_goal == goal


def test_confirmed_clear_stops_an_active_read_before_removing_workspace_data() -> None:
	allow_second_batch = threading.Event()
	store = InMemoryWorkspaceStore(
		local_session_id="local-session",
		active_workspace=WorkspaceKind.JOB_SEEKING,
	)
	credentials = InMemoryCredentialStore({WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED})
	application = Application(
		workspace_store=store,
		credential_store=credentials,
		boss=FakeBossAdapter(
			search_batches=(
				JobSearchBatch((JobSummary("job-1", "Python 工程师", "示例科技"),), 50),
				JobSearchBatch((JobSummary("job-2", "Go 工程师", "另一家公司"),), 90),
			),
			batch_gate=allow_second_batch,
		),
		run_id_factory=lambda: "clear-run",
	)
	application.execute(UpdateJobSearchGoalCommand(JobSearchGoal("后端", "Python")), _context())
	application.execute(StartJobSearchCommand(), _context())
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), _context())
		if snapshot.active_run is not None and snapshot.active_run.progress == 50:
			break
		time.sleep(0.01)
	else:
		raise AssertionError("read did not reach its checkpoint")

	cleared = application.execute(
		ClearWorkspaceCommand(WorkspaceKind.JOB_SEEKING, confirmation="clear:job-seeking"),
		_context(),
	).snapshot
	allow_second_batch.set()

	assert cleared.active_run is None
	assert cleared.platform_session is PlatformSessionState.DISCONNECTED
	assert cleared.job_seeking is not None
	assert cleared.job_seeking.goal is None
	assert store.list_runs(WorkspaceKind.JOB_SEEKING) == ()
