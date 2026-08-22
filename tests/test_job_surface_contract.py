"""Cross-surface contract tests for the Job-Seeking Core Journey."""

from __future__ import annotations

import json
import http.client
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.application import (
	Application,
	DomainError,
	JobSearchBatch,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	WorkspaceKind,
)
from boss_agent_cli.application.job_surface import JobSeekingSurface
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore, InMemoryWorkspaceStore
from boss_agent_cli.main import cli
from boss_agent_cli.web.auth import StartupAuthenticator
from boss_agent_cli.web.server import LocalWebServer

_mcp_mock = types.ModuleType("mcp")
_mcp_server_mock = types.ModuleType("mcp.server")
_mcp_stdio_mock = types.ModuleType("mcp.server.stdio")
_mcp_types_mock = types.ModuleType("mcp.types")
_mcp_server_mock.Server = MagicMock()
_mcp_stdio_mock.stdio_server = MagicMock()
_mcp_types_mock.TextContent = MagicMock()
_mcp_types_mock.Tool = type("Tool", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
_mcp_mock.server = _mcp_server_mock
_mcp_mock.types = _mcp_types_mock
sys.modules.setdefault("mcp", _mcp_mock)
sys.modules.setdefault("mcp.server", _mcp_server_mock)
sys.modules.setdefault("mcp.server.stdio", _mcp_stdio_mock)
sys.modules.setdefault("mcp.types", _mcp_types_mock)

from boss_agent_cli import mcp_server  # noqa: E402


JOB = JobSummary(
	reference="job-1",
	title="Python 后端工程师",
	company="示例科技",
	location="上海",
	salary="20-40K",
)
GOAL_ACTION = {
	"action": "goal",
	"objective": "寻找后端岗位",
	"keyword": "Python",
	"city": "上海",
}


def _application() -> tuple[Application, FakeBossAdapter]:
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch(items=(JOB,), progress=100),),
		details={"job-1": JobSourceDetail(job=JOB, description="负责 Python 服务")},
	)
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="surface-session",
			active_workspace=WorkspaceKind.JOB_SEEKING,
		),
		credential_store=InMemoryCredentialStore({WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED}),
		boss=boss,
		run_id_factory=lambda: "search-1",
		intent_id_factory=lambda: "intent-1",
	)
	return application, boss


def _surface() -> tuple[JobSeekingSurface, Application, FakeBossAdapter]:
	application, boss = _application()
	surface = JobSeekingSurface(
		application,
		local_session_id="surface-session",
		correlation_id_factory=lambda: "correlation-1",
	)
	return surface, application, boss


def _web_command(
	application: Application,
	tmp_path: Path,
	path: str,
	payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
	static_root = tmp_path / "static"
	static_root.mkdir()
	(static_root / "index.html").write_text("<!doctype html>", encoding="utf-8")
	authenticator = StartupAuthenticator(
		bootstrap_token="bootstrap-token",
		local_session_id="surface-session",
		session_token_factory=lambda: "session-token",
	)
	server = LocalWebServer(static_root=static_root, authenticator=authenticator, application=application)
	server.start()
	try:
		connection = http.client.HTTPConnection(server.host, server.port, timeout=3)
		body = json.dumps({"bootstrap_token": "bootstrap-token"}).encode("utf-8")
		connection.request(
			"POST",
			"/api/v1/session",
			body=body,
			headers={
				"Content-Type": "application/json",
				"Origin": server.origin,
				"Sec-Fetch-Site": "same-origin",
			},
		)
		exchange = connection.getresponse()
		assert exchange.status == 201
		exchange.read()
		connection.close()

		connection = http.client.HTTPConnection(server.host, server.port, timeout=3)
		body = json.dumps({"request_id": "request-1", **payload}).encode("utf-8")
		connection.request(
			"POST",
			path,
			body=body,
			headers={
				"Authorization": "Bearer session-token",
				"Content-Type": "application/json",
				"Origin": server.origin,
				"Sec-Fetch-Site": "same-origin",
			},
		)
		response = connection.getresponse()
		result = response.status, json.loads(response.read())
		connection.close()
		return result
	finally:
		server.close()


def test_cli_mcp_and_web_return_the_same_representative_snapshot(tmp_path: Path, monkeypatch) -> None:
	cli_surface, _, _ = _surface()
	mcp_surface, _, _ = _surface()
	_, web_application, _ = _surface()

	with patch("boss_agent_cli.commands.job.create_job_seeking_surface", return_value=cli_surface):
		cli_result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "job", "--input-json", json.dumps(GOAL_ACTION)],
		)
	monkeypatch.setattr(mcp_server, "_JOB_ROLE", "candidate")
	monkeypatch.setattr(mcp_server, "_job_surface", lambda: mcp_surface)
	mcp_result = mcp_server._run_job(GOAL_ACTION)

	assert cli_result.exit_code == 0
	cli_snapshot = json.loads(cli_result.output)["data"]["snapshot"]
	web_status, web_result = _web_command(
		web_application,
		tmp_path,
		"/api/v1/commands/update-job-search-goal",
		{
			"objective": "寻找后端岗位",
			"keyword": "Python",
			"city": "上海",
			"salary": "",
			"experience": "",
			"education": "",
		},
	)
	assert web_status == 200
	assert cli_snapshot == mcp_result["data"]["snapshot"] == web_result["snapshot"]


def test_cli_mcp_and_web_return_the_same_representative_failure(tmp_path: Path, monkeypatch) -> None:
	payload = {"action": "inspect", "reference": "missing"}
	cli_surface, _, _ = _surface()
	mcp_surface, _, _ = _surface()
	_, web_application, _ = _surface()

	with patch("boss_agent_cli.commands.job.create_job_seeking_surface", return_value=cli_surface):
		cli_result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "job", "--input-json", json.dumps(payload)],
		)
	monkeypatch.setattr(mcp_server, "_JOB_ROLE", "candidate")
	monkeypatch.setattr(mcp_server, "_job_surface", lambda: mcp_surface)
	mcp_result = mcp_server._run_job(payload)
	web_status, web_result = _web_command(
		web_application,
		tmp_path,
		"/api/v1/commands/inspect-job",
		{"reference": "missing"},
	)
	assert web_status == 503
	web_error = web_result["error"]

	cli_error = json.loads(cli_result.output)["error"]
	for field in ("code", "message", "recoverable", "recovery_action"):
		assert cli_error[field] == mcp_result["error"][field] == web_error[field]


def test_cli_run_keeps_state_and_confirms_only_the_server_owned_intent() -> None:
	surface, _, boss = _surface()
	result = surface.invoke(
		{
			"action": "run",
			"steps": [
				GOAL_ACTION,
				{"action": "search"},
				{"action": "inspect", "reference": "$first"},
				{"action": "shortlist", "reference": "$first", "shortlisted": True},
				{"action": "prepare-greeting", "reference": "$first", "message": "您好"},
				{"action": "confirm", "intent_id": "$pending"},
			],
		}
	)

	snapshot = result["snapshot"]
	assert isinstance(snapshot, dict)
	assert snapshot["pending_write_intent"]["state"] == "succeeded"
	assert snapshot["job_seeking"]["shortlist"][0]["reference"] == "job-1"
	assert boss.greeting_calls == [("job-1", "您好")]


def test_confirmation_cannot_replace_payload_or_be_replayed() -> None:
	surface, _, boss = _surface()
	for action in (
		GOAL_ACTION,
		{"action": "search"},
		{"action": "inspect", "reference": "$first"},
		{"action": "prepare-greeting", "reference": "$first", "message": "已审核文本"},
	):
		surface.invoke(action)

	with pytest.raises(DomainError) as forged:
		surface.invoke({"action": "confirm", "intent_id": "$pending", "message": "伪造替换"})
	assert forged.value.code.value == "INVALID_COMMAND"
	assert boss.greeting_calls == []
	confirmed = surface.invoke({"action": "confirm", "intent_id": "$pending"})
	assert confirmed["snapshot"]["pending_write_intent"]["state"] == "succeeded"
	assert boss.greeting_calls == [("job-1", "已审核文本")]
	with pytest.raises(DomainError) as replay:
		surface.invoke({"action": "confirm", "intent_id": "intent-1"})
	assert replay.value.code.value == "WRITE_INTENT_CONSUMED"
	assert boss.greeting_calls == [("job-1", "已审核文本")]


def test_invalid_search_input_does_not_start_remote_work() -> None:
	surface, _, boss = _surface()
	surface.invoke(GOAL_ACTION)

	with pytest.raises(DomainError) as failure:
		surface.invoke({"action": "search", "timeout": "later"})

	assert failure.value.code.value == "INVALID_COMMAND"
	assert boss.search_calls == 0


def test_legacy_goal_defaults_remain_at_the_cli_mcp_surface() -> None:
	surface, _, _ = _surface()
	result = surface.invoke({"action": "goal", "keyword": "Python"})

	goal = result["snapshot"]["job_seeking"]["goal"]
	assert goal == {
		"objective": "Python",
		"keyword": "Python",
		"city": "",
		"salary": "",
		"experience": "",
		"education": "",
	}


def test_unsupported_platform_fails_before_constructing_or_calling_an_adapter(tmp_path: Path, monkeypatch) -> None:
	with patch(
		"boss_agent_cli.commands.job.create_job_seeking_surface",
		side_effect=AssertionError("unsupported CLI platform must fail closed"),
	):
		cli_result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "--platform", "zhilian", "job"],
		)
	assert json.loads(cli_result.output)["error"]["code"] == "UNSUPPORTED_CAPABILITY"

	monkeypatch.setattr(mcp_server, "_JOB_ROLE", "candidate")
	monkeypatch.setattr(mcp_server, "_JOB_PLATFORM", "zhilian")
	monkeypatch.setattr(
		mcp_server,
		"_job_surface",
		lambda: (_ for _ in ()).throw(AssertionError("unsupported MCP platform must fail closed")),
	)
	mcp_result = mcp_server._run_job({"action": "state"})
	assert mcp_result["error"]["code"] == "UNSUPPORTED_CAPABILITY"


def test_surface_adapters_do_not_own_remote_write_policy() -> None:
	root = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli"
	for relative in ("commands/job.py", "mcp_server.py", "application/job_surface.py"):
		source = (root / relative).read_text(encoding="utf-8")
		assert ".send_greeting(" not in source
		assert ".greet(" not in source
