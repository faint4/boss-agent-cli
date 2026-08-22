"""Cross-surface contract tests for the Recruiting Core Journey."""

from __future__ import annotations

import http.client
import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from boss_agent_cli.application import (
	Application,
	CurrentStateQuery,
	DomainError,
	InboundApplicant,
	PlatformSessionState,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RequestContext,
	WorkspaceKind,
)
from boss_agent_cli.application.recruiting_surface import RecruitingSurface
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


NOW = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
OPENING = RecruitingOpening("opening-1", "Python 后端工程师", "招聘中")
APPLICANT = InboundApplicant("prospect-1", "招聘对象甲", "5 年 Python 经验")
PROSPECT_CONTEXT = RecruitingProspectContext(
	APPLICANT,
	"敏感简历 canary-resume-23",
	("应聘者：您好 canary-chat-23",),
	("手机号 canary-contact-23",),
)
MESSAGE = "您好，感谢投递。方便沟通一下项目经历吗？"
RUN_STEPS = [
	{"action": "openings"},
	{"action": "select-opening", "reference": "$first"},
	{"action": "applicants"},
	{"action": "inspect", "reference": "$first"},
	{"action": "prepare-reply", "reference": "$first", "message": MESSAGE},
]


def _application() -> tuple[Application, FakeBossAdapter]:
	boss = FakeBossAdapter(
		openings=(OPENING,),
		applicant_batches=(RecruitingApplicantBatch((APPLICANT,), 100),),
		prospect_contexts={"prospect-1": PROSPECT_CONTEXT},
	)
	application = Application(
		workspace_store=InMemoryWorkspaceStore(
			local_session_id="surface-session",
			active_workspace=WorkspaceKind.RECRUITING,
		),
		credential_store=InMemoryCredentialStore({WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED}),
		boss=boss,
		run_id_factory=lambda: "recruiting-run-1",
		intent_id_factory=lambda: "intent-23",
		clock=lambda: NOW,
	)
	return application, boss


def _surface() -> tuple[RecruitingSurface, Application, FakeBossAdapter]:
	application, boss = _application()
	return (
		RecruitingSurface(
			application,
			local_session_id="surface-session",
			correlation_id_factory=lambda: "correlation-23",
		),
		application,
		boss,
	)


class _WebClient:
	def __init__(self, application: Application, tmp_path: Path) -> None:
		static_root = tmp_path / f"static-{id(application)}"
		static_root.mkdir()
		(static_root / "index.html").write_text("<!doctype html>", encoding="utf-8")
		authenticator = StartupAuthenticator(
			bootstrap_token="bootstrap-token",
			local_session_id="surface-session",
			session_token_factory=lambda: "session-token",
		)
		self.server = LocalWebServer(
			static_root=static_root,
			authenticator=authenticator,
			application=application,
		)
		self.server.start()
		self._request_number = 0
		status, _ = self._request("/api/v1/session", {"bootstrap_token": "bootstrap-token"}, authenticated=False)
		assert status == 201

	def close(self) -> None:
		self.server.close()

	def _request(
		self,
		path: str,
		payload: dict[str, object],
		*,
		authenticated: bool = True,
	) -> tuple[int, dict[str, object]]:
		connection = http.client.HTTPConnection(self.server.host, self.server.port, timeout=3)
		headers = {
			"Content-Type": "application/json",
			"Origin": self.server.origin,
			"Sec-Fetch-Site": "same-origin",
		}
		if authenticated:
			headers["Authorization"] = "Bearer session-token"
		connection.request("POST", path, body=json.dumps(payload).encode("utf-8"), headers=headers)
		response = connection.getresponse()
		result = response.status, json.loads(response.read())
		connection.close()
		return result

	def command(self, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
		self._request_number += 1
		return self._request(path, {"request_id": f"request-{self._request_number}", **payload})


def _wait_for_applicants(application: Application) -> None:
	deadline = time.monotonic() + 2
	context = RequestContext("surface-session", "test-wait")
	while time.monotonic() < deadline:
		snapshot = application.query(CurrentStateQuery(), context)
		if snapshot.active_run is not None and snapshot.active_run.state == "completed":
			return
		time.sleep(0.01)
	raise AssertionError("applicant read did not complete")


def _web_prepare(client: _WebClient, application: Application) -> dict[str, object]:
	for path, payload in (
		("/api/v1/commands/load-recruiting-openings", {}),
		("/api/v1/commands/select-recruiting-opening", {"reference": "opening-1"}),
		("/api/v1/commands/start-inbound-applicants", {}),
	):
		status, _ = client.command(path, payload)
		assert status == 200
	_wait_for_applicants(application)
	for path, payload in (
		("/api/v1/commands/inspect-recruiting-prospect", {"reference": "prospect-1"}),
		("/api/v1/commands/prepare-recruiting-reply", {"reference": "prospect-1", "message": MESSAGE}),
	):
		status, result = client.command(path, payload)
		assert status == 200
	return result["snapshot"]


def test_cli_mcp_and_web_share_read_prepare_and_confirm_semantics(tmp_path: Path, monkeypatch) -> None:
	cli_surface, _, cli_boss = _surface()
	mcp_surface, _, mcp_boss = _surface()
	web_application, web_boss = _application()

	cli_payload = {"action": "run", "steps": [*RUN_STEPS, {"action": "confirm", "intent_id": "$pending"}]}
	with patch(
		"boss_agent_cli.commands.recruiter.journey.create_recruiting_surface",
		return_value=cli_surface,
	):
		cli_result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "hr", "journey", "--input-json", json.dumps(cli_payload)],
		)
	monkeypatch.setattr(mcp_server, "_RECRUITING_PLATFORM", "zhipin")
	monkeypatch.setattr(mcp_server, "_recruiting_surface", lambda: mcp_surface)
	mcp_result = mcp_server._run_recruiting(cli_payload)
	client = _WebClient(web_application, tmp_path)
	try:
		_web_prepare(client, web_application)
		status, web_result = client.command("/api/v1/write-intents/confirm", {"intent_id": "intent-23"})
		assert status == 200
	finally:
		client.close()

	assert cli_result.exit_code == 0
	cli_snapshot = json.loads(cli_result.output)["data"]["snapshot"]
	assert cli_snapshot == mcp_result["data"]["snapshot"] == web_result["snapshot"]
	assert cli_boss.recruiting_reply_calls == mcp_boss.recruiting_reply_calls == web_boss.recruiting_reply_calls == [
		("prospect-1", MESSAGE)
	]


def test_cli_mcp_and_web_share_cancel_semantics(tmp_path: Path, monkeypatch) -> None:
	cli_surface, _, cli_boss = _surface()
	mcp_surface, _, mcp_boss = _surface()
	web_application, web_boss = _application()
	payload = {"action": "run", "steps": [*RUN_STEPS, {"action": "cancel-write", "intent_id": "$pending"}]}

	with patch(
		"boss_agent_cli.commands.recruiter.journey.create_recruiting_surface",
		return_value=cli_surface,
	):
		cli_result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "hr", "journey", "--input-json", json.dumps(payload)],
		)
	monkeypatch.setattr(mcp_server, "_RECRUITING_PLATFORM", "zhipin")
	monkeypatch.setattr(mcp_server, "_recruiting_surface", lambda: mcp_surface)
	mcp_result = mcp_server._run_recruiting(payload)
	client = _WebClient(web_application, tmp_path)
	try:
		_web_prepare(client, web_application)
		status, web_result = client.command("/api/v1/write-intents/cancel", {"intent_id": "intent-23"})
		assert status == 200
	finally:
		client.close()

	cli_snapshot = json.loads(cli_result.output)["data"]["snapshot"]
	assert cli_snapshot == mcp_result["data"]["snapshot"] == web_result["snapshot"]
	assert cli_boss.recruiting_reply_calls == mcp_boss.recruiting_reply_calls == web_boss.recruiting_reply_calls == []


def test_cli_mcp_and_web_share_representative_failure(tmp_path: Path, monkeypatch) -> None:
	payload = {"action": "select-opening", "reference": "missing"}
	cli_surface, _, _ = _surface()
	mcp_surface, _, _ = _surface()
	web_application, _ = _application()

	with patch(
		"boss_agent_cli.commands.recruiter.journey.create_recruiting_surface",
		return_value=cli_surface,
	):
		cli_result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "hr", "journey", "--input-json", json.dumps(payload)],
		)
	monkeypatch.setattr(mcp_server, "_RECRUITING_PLATFORM", "zhipin")
	monkeypatch.setattr(mcp_server, "_recruiting_surface", lambda: mcp_surface)
	mcp_result = mcp_server._run_recruiting(payload)
	client = _WebClient(web_application, tmp_path)
	try:
		status, web_result = client.command(
			"/api/v1/commands/select-recruiting-opening",
			{"reference": "missing"},
		)
		assert status == 503
	finally:
		client.close()

	cli_error = json.loads(cli_result.output)["error"]
	for field in ("code", "message", "recoverable", "recovery_action"):
		assert cli_error[field] == mcp_result["error"][field] == web_result["error"][field]


def test_confirmation_accepts_only_intent_id_and_cannot_be_replayed() -> None:
	surface, _, boss = _surface()
	for action in RUN_STEPS:
		surface.invoke(action)

	with pytest.raises(DomainError) as forged:
		surface.invoke({"action": "confirm", "intent_id": "$pending", "message": "伪造替换"})
	assert forged.value.code.value == "INVALID_COMMAND"
	assert boss.recruiting_reply_calls == []
	surface.invoke({"action": "confirm", "intent_id": "$pending"})
	assert boss.recruiting_reply_calls == [("prospect-1", MESSAGE)]
	with pytest.raises(DomainError) as replay:
		surface.invoke({"action": "confirm", "intent_id": "intent-23"})
	assert replay.value.code.value == "WRITE_INTENT_CONSUMED"
	assert boss.recruiting_reply_calls == [("prospect-1", MESSAGE)]


def test_run_preflights_all_steps_before_remote_work() -> None:
	surface, _, boss = _surface()
	with pytest.raises(DomainError) as failure:
		surface.invoke(
			{
				"action": "run",
				"steps": [
					{"action": "openings"},
					{"action": "applicants", "timeout": "later"},
				],
			}
		)
	assert failure.value.code.value == "INVALID_COMMAND"
	assert boss.opening_calls == 0


def test_surface_adapters_do_not_persist_sensitive_content_or_own_write_policy() -> None:
	root = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli"
	for relative in (
		"commands/recruiter/journey.py",
		"mcp_server.py",
		"application/recruiting_surface.py",
		"recruiting_runtime.py",
	):
		source = (root / relative).read_text(encoding="utf-8")
		assert ".send_recruiting_reply(" not in source
		assert "save_recruiting" not in source
		assert "write_local_state" not in source


def test_unsupported_mcp_platform_fails_before_constructing_adapter(monkeypatch) -> None:
	monkeypatch.setattr(mcp_server, "_RECRUITING_PLATFORM", "zhilian")
	monkeypatch.setattr(
		mcp_server,
		"_recruiting_surface",
		lambda: (_ for _ in ()).throw(AssertionError("unsupported platform must fail closed")),
	)
	result = mcp_server._run_recruiting({"action": "state"})
	assert result["error"]["code"] == "UNSUPPORTED_CAPABILITY"
