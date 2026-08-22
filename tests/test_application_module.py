from datetime import datetime, timezone

import pytest

from boss_agent_cli.application import (
	Application,
	ApplicationEvent,
	CurrentStateQuery,
	DomainError,
	ErrorCode,
	PlatformSessionState,
	RequestContext,
	RunEventKind,
	SwitchWorkspaceCommand,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import (
	FakeBossAdapter,
	InMemoryCredentialStore,
	InMemoryWorkspaceStore,
)


def test_local_operator_can_read_current_workspace_state():
	workspace_store = InMemoryWorkspaceStore(
		local_session_id="local-session-1",
		active_workspace=WorkspaceKind.JOB_SEEKING,
	)
	credentials = InMemoryCredentialStore({WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED})
	application = Application(
		workspace_store=workspace_store,
		credential_store=credentials,
		boss=FakeBossAdapter(),
	)

	snapshot = application.query(
		CurrentStateQuery(),
		RequestContext(
			local_session_id="local-session-1",
			correlation_id="request-1",
		),
	)

	assert snapshot.schema_version == "1"
	assert snapshot.active_workspace is WorkspaceKind.JOB_SEEKING
	assert snapshot.platform_session is PlatformSessionState.CONNECTED
	assert snapshot.active_run is None
	assert snapshot.pending_write_intent is None
	assert snapshot.error is None


def test_invalid_query_returns_a_typed_domain_error():
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="local-session-1",
			active_workspace=WorkspaceKind.JOB_SEEKING,
		),
		credential_store=InMemoryCredentialStore(),
		boss=FakeBossAdapter(),
	)
	context = RequestContext(
		local_session_id="local-session-1",
		correlation_id="request-invalid",
	)

	with pytest.raises(DomainError) as raised:
		application.query(object(), context)  # type: ignore[arg-type]

	assert raised.value.code is ErrorCode.INVALID_QUERY
	assert raised.value.correlation_id == "request-invalid"
	assert raised.value.recoverable is False


def test_unknown_local_session_cannot_read_workspace_state():
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="authorized-session",
			active_workspace=WorkspaceKind.RECRUITING,
		),
		credential_store=InMemoryCredentialStore(),
		boss=FakeBossAdapter(),
	)

	with pytest.raises(DomainError) as raised:
		application.query(
			CurrentStateQuery(),
			RequestContext(
				local_session_id="unknown-session",
				correlation_id="request-unauthorized",
			),
		)

	assert raised.value.code is ErrorCode.UNAUTHORIZED_CONTEXT
	assert raised.value.correlation_id == "request-unauthorized"
	assert "unknown-session" not in raised.value.message


def test_platform_probe_failure_becomes_a_safe_recoverable_error():
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="local-session-1",
			active_workspace=WorkspaceKind.JOB_SEEKING,
		),
		credential_store=InMemoryCredentialStore({WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED}),
		boss=FakeBossAdapter(failure=RuntimeError("secret remote response")),
	)

	with pytest.raises(DomainError) as raised:
		application.query(
			CurrentStateQuery(probe_platform=True),
			RequestContext(
				local_session_id="local-session-1",
				correlation_id="request-probe",
			),
		)

	assert raised.value.code is ErrorCode.ADAPTER_UNAVAILABLE
	assert raised.value.recoverable is True
	assert raised.value.recovery_action == "Retry the read-only platform check"
	assert "secret remote response" not in raised.value.message


def test_workspace_switch_command_returns_the_new_active_workspace():
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="local-session-1",
			active_workspace=WorkspaceKind.JOB_SEEKING,
		),
		credential_store=InMemoryCredentialStore(),
		boss=FakeBossAdapter(),
	)

	result = application.execute(
		SwitchWorkspaceCommand(workspace=WorkspaceKind.RECRUITING),
		RequestContext(
			local_session_id="local-session-1",
			correlation_id="request-command",
		),
	)

	assert result.snapshot.active_workspace is WorkspaceKind.RECRUITING
	assert result.snapshot.last_transition == "workspace-switched"


def test_workspace_storage_failure_becomes_a_safe_recoverable_error():
	class FailingWorkspaceStore(InMemoryWorkspaceStore):
		def switch_workspace(self, local_session_id: str, workspace: WorkspaceKind) -> None:
			raise OSError("credential-canary must not escape")

	application = Application(
		workspace_store=FailingWorkspaceStore(
			local_session_id="local-session-1",
			active_workspace=WorkspaceKind.JOB_SEEKING,
		),
		credential_store=InMemoryCredentialStore(),
		boss=FakeBossAdapter(),
	)

	with pytest.raises(DomainError) as raised:
		application.execute(
			SwitchWorkspaceCommand(workspace=WorkspaceKind.RECRUITING),
			RequestContext(
				local_session_id="local-session-1",
				correlation_id="request-storage",
			),
		)

	assert raised.value.code is ErrorCode.STORAGE_UNAVAILABLE
	assert raised.value.recoverable is True
	assert "credential-canary" not in raised.value.message


def test_run_events_resume_after_the_supplied_cursor():
	started = ApplicationEvent(
		cursor=1,
		run_id="run-1",
		kind=RunEventKind.STATE_CHANGED,
		occurred_at=datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc),
		payload=(("state", "running"),),
	)
	waiting = ApplicationEvent(
		cursor=2,
		run_id="run-1",
		kind=RunEventKind.STATE_CHANGED,
		occurred_at=datetime(2026, 8, 22, 9, 1, tzinfo=timezone.utc),
		payload=(("state", "waiting"),),
	)
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="local-session-1",
			active_workspace=WorkspaceKind.JOB_SEEKING,
			run_events={"run-1": (started, waiting)},
		),
		credential_store=InMemoryCredentialStore(),
		boss=FakeBossAdapter(),
	)

	events = application.events(
		"run-1",
		after_cursor=1,
		context=RequestContext(
			local_session_id="local-session-1",
			correlation_id="request-events",
		),
	)

	assert events == (waiting,)
