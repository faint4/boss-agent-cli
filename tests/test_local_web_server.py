from __future__ import annotations

import base64
import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from boss_agent_cli.application import (
	Application,
	CurrentStateQuery,
	InboundApplicant,
	InspectJobCommand,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	RequestContext,
	StartJobSearchCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
)
from boss_agent_cli.application.testing import FakeBossAdapter, InMemoryCredentialStore
from boss_agent_cli.web.auth import StartupAuthenticator
from boss_agent_cli.web.dpapi import CredentialProtectionError
from boss_agent_cli.web.runtime import create_application
from boss_agent_cli.web.server import LocalWebServer
from boss_agent_cli.web.workspace import WorkspaceRegistry


SECURITY_HEADERS = {
	"Cache-Control": "no-store",
	"Content-Security-Policy": (
		"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
		"connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
	),
	"Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
	"Referrer-Policy": "no-referrer",
	"X-Content-Type-Options": "nosniff",
	"X-Frame-Options": "DENY",
}


def _static_root(tmp_path: Path) -> Path:
	root = tmp_path / "static"
	(root / "assets").mkdir(parents=True)
	(root / "index.html").write_text("<!doctype html><main>local shell</main>", encoding="utf-8")
	(root / "assets" / "app.js").write_text("console.log('ready')", encoding="utf-8")
	return root


@contextmanager
def _running_server(tmp_path: Path) -> Iterator[LocalWebServer]:
	authenticator = StartupAuthenticator(
		bootstrap_token="bootstrap-token",
		local_session_id="local-session-1",
		session_token_factory=lambda: "session-token",
	)
	server = LocalWebServer(
		static_root=_static_root(tmp_path),
		authenticator=authenticator,
		product_root=tmp_path / "data",
	)
	server.start()
	try:
		yield server
	finally:
		server.close()


def _request(
	server: LocalWebServer,
	method: str,
	path: str,
	*,
	headers: dict[str, str] | None = None,
	body: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], bytes]:
	request_headers = dict(headers or {})
	payload = None
	if body is not None:
		payload = json.dumps(body).encode("utf-8")
		request_headers.setdefault("Content-Type", "application/json")
	conn = http.client.HTTPConnection(server.host, server.port, timeout=3)
	conn.request(method, path, body=payload, headers=request_headers)
	response = conn.getresponse()
	data = response.read()
	result = response.status, dict(response.getheaders()), data
	conn.close()
	return result


def _exchange(server: LocalWebServer) -> str:
	status, _, body = _request(
		server,
		"POST",
		"/api/v1/session",
		headers={"Origin": server.origin, "Sec-Fetch-Site": "same-origin"},
		body={"bootstrap_token": "bootstrap-token"},
	)
	assert status == 201
	return str(json.loads(body)["session_token"])


class _PurposeBoundProtector:
	def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
		return purpose + b"\0" + plaintext[::-1]

	def unprotect(self, ciphertext: bytes, *, purpose: bytes) -> bytes:
		prefix = purpose + b"\0"
		if not ciphertext.startswith(prefix):
			raise CredentialProtectionError("credential purpose mismatch")
		return ciphertext[len(prefix) :][::-1]


def _command_headers(server: LocalWebServer, token: str) -> dict[str, str]:
	return {
		"Authorization": f"Bearer {token}",
		"Origin": server.origin,
		"Sec-Fetch-Site": "same-origin",
	}


def test_server_uses_an_ephemeral_ipv4_loopback_port(tmp_path: Path):
	with _running_server(tmp_path) as server:
		assert server.host == "127.0.0.1"
		assert 0 < server.port < 65536
		assert server.origin == f"http://127.0.0.1:{server.port}"


def test_default_startup_secrets_have_at_least_256_bits_and_expire():
	now = [10.0]
	authenticator = StartupAuthenticator(bootstrap_ttl_seconds=1, clock=lambda: now[0])
	decoded = base64.urlsafe_b64decode(authenticator.bootstrap_token + "==")

	assert len(decoded) >= 32
	now[0] = 11.0
	assert authenticator.exchange(authenticator.bootstrap_token) is None


def test_browser_is_opened_only_after_the_listener_is_ready(tmp_path: Path):
	server = LocalWebServer(static_root=_static_root(tmp_path), product_root=tmp_path / "data")
	opened = threading.Event()
	opened_urls: list[str] = []

	def open_browser(url: str) -> bool:
		with socket.create_connection((server.host, server.port), timeout=1):
			pass
		opened_urls.append(url)
		opened.set()
		return True

	thread = threading.Thread(target=server.serve_and_open, kwargs={"open_browser": open_browser}, daemon=True)
	thread.start()
	try:
		assert opened.wait(timeout=2)
		parts = urlsplit(opened_urls[0])
		assert f"{parts.scheme}://{parts.netloc}" == server.origin
		assert parts.query == ""
		assert set(parse_qs(parts.fragment)) == {"bootstrap"}
	finally:
		server.close()
		thread.join(timeout=2)


def test_static_shell_is_anonymous_but_has_security_headers(tmp_path: Path):
	with _running_server(tmp_path) as server:
		status, headers, body = _request(server, "GET", "/")

	assert status == 200
	assert body == b"<!doctype html><main>local shell</main>"
	for name, value in SECURITY_HEADERS.items():
		assert headers[name] == value
	assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize(
	("headers", "expected_status"),
	[
		({"Host": "attacker.example"}, 403),
		({"Host": "localhost:1234"}, 403),
		({"Forwarded": "host=attacker.example"}, 403),
		({"X-Forwarded-Host": "attacker.example"}, 403),
	],
)
def test_host_and_proxy_host_attacks_are_rejected_before_routing(
	tmp_path: Path,
	headers: dict[str, str],
	expected_status: int,
):
	with _running_server(tmp_path) as server:
		status, response_headers, _ = _request(server, "GET", "/", headers=headers)

	assert status == expected_status
	assert response_headers["Cache-Control"] == "no-store"


def test_duplicate_host_headers_are_rejected(tmp_path: Path):
	with _running_server(tmp_path) as server:
		request = (
			f"GET / HTTP/1.1\r\nHost: {server.expected_host}\r\nHost: attacker.example\r\nConnection: close\r\n\r\n"
		).encode("ascii")
		with socket.create_connection((server.host, server.port), timeout=2) as connection:
			connection.sendall(request)
			response = connection.recv(4096)

	assert response.startswith(b"HTTP/1.1 403")


def test_api_requires_the_in_memory_startup_session(tmp_path: Path):
	with _running_server(tmp_path) as server:
		status, _, body = _request(server, "GET", "/api/v1/state")
		token = _exchange(server)
		authenticated_status, headers, authenticated_body = _request(
			server,
			"GET",
			"/api/v1/state",
			headers={"Authorization": f"Bearer {token}"},
		)

	assert status == 401
	assert json.loads(body) == {"error": {"code": "AUTHENTICATION_REQUIRED", "message": "Authentication required"}}
	assert authenticated_status == 200
	assert headers["Content-Type"] == "application/json"
	snapshot = json.loads(authenticated_body)["snapshot"]
	assert snapshot["schema_version"] == "1"
	assert snapshot["active_workspace"] == "job-seeking"
	assert snapshot["platform_session"] == "disconnected"
	assert snapshot["active_run"] is None


def test_bootstrap_token_is_single_use_and_errors_do_not_leak_it(tmp_path: Path):
	with _running_server(tmp_path) as server:
		wrong_status, _, wrong_body = _request(
			server,
			"POST",
			"/api/v1/session",
			headers={"Origin": server.origin},
			body={"bootstrap_token": "wrong-bootstrap"},
		)
		token = _exchange(server)
		replay_status, _, replay_body = _request(
			server,
			"POST",
			"/api/v1/session",
			headers={"Origin": server.origin},
			body={"bootstrap_token": "bootstrap-token"},
		)

	assert token == "session-token"
	assert wrong_status == 401
	assert replay_status == 401
	assert b"wrong-bootstrap" not in wrong_body
	assert b"bootstrap-token" not in replay_body


@pytest.mark.parametrize(
	("headers", "expected_status"),
	[
		({"Content-Type": "application/json"}, 403),
		({"Origin": "https://attacker.example", "Content-Type": "application/json"}, 403),
		({"Origin": "null", "Content-Type": "application/json"}, 403),
		(
			{"Origin": "__ORIGIN__", "Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"},
			403,
		),
		(
			{"Origin": "__ORIGIN__", "Sec-Fetch-Site": "same-site", "Content-Type": "application/json"},
			403,
		),
		({"Origin": "__ORIGIN__", "Content-Type": "text/plain"}, 415),
		({"Origin": "__ORIGIN__", "Content-Type": "application/x-www-form-urlencoded"}, 415),
	],
)
def test_write_request_origin_fetch_metadata_and_content_type_are_enforced(
	tmp_path: Path,
	headers: dict[str, str],
	expected_status: int,
):
	with _running_server(tmp_path) as server:
		resolved_headers = {
			name: (server.origin if value == "__ORIGIN__" else value) for name, value in headers.items()
		}
		status, response_headers, _ = _request(
			server,
			"POST",
			"/api/v1/session",
			headers=resolved_headers,
			body={"bootstrap_token": "bootstrap-token"},
		)

	assert status == expected_status
	assert "Access-Control-Allow-Origin" not in response_headers


def test_safe_methods_do_not_consume_the_bootstrap_token(tmp_path: Path):
	with _running_server(tmp_path) as server:
		options_status, options_headers, options_body = _request(
			server,
			"OPTIONS",
			"/api/v1/session",
			headers={"Origin": server.origin},
		)
		head_status, _, head_body = _request(server, "HEAD", "/")
		token = _exchange(server)

	assert options_status == 204
	assert options_body == b""
	assert "Access-Control-Allow-Origin" not in options_headers
	assert head_status == 200
	assert head_body == b""
	assert token == "session-token"


def test_cross_origin_preflight_is_rejected_without_cors_permission(tmp_path: Path):
	with _running_server(tmp_path) as server:
		status, headers, _ = _request(
			server,
			"OPTIONS",
			"/api/v1/session",
			headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
		)

	assert status == 403
	assert "Access-Control-Allow-Origin" not in headers


def test_tokens_from_a_previous_process_are_rejected(tmp_path: Path):
	with _running_server(tmp_path / "first") as first:
		old_token = _exchange(first)

	second_auth = StartupAuthenticator(
		bootstrap_token="second-bootstrap",
		local_session_id="local-session-2",
		session_token_factory=lambda: "second-session",
	)
	second = LocalWebServer(
		static_root=_static_root(tmp_path / "second"),
		authenticator=second_auth,
		product_root=tmp_path / "second-data",
	)
	second.start()
	try:
		status, _, _ = _request(
			second,
			"GET",
			"/api/v1/state",
			headers={"Authorization": f"Bearer {old_token}"},
		)
	finally:
		second.close()

	assert status == 401


def test_browser_bridge_routes_are_not_exposed(tmp_path: Path):
	with _running_server(tmp_path) as server:
		command_status, _, _ = _request(server, "GET", "/command")
		extension_status, _, _ = _request(server, "GET", "/ext")

	assert command_status == 404
	assert extension_status == 404


def test_authenticated_command_routes_switch_connect_and_logout_the_active_workspace(tmp_path: Path):
	login_started = threading.Event()
	allow_login = threading.Event()
	authenticator = StartupAuthenticator(
		bootstrap_token="bootstrap-token",
		local_session_id="local-session-commands",
		session_token_factory=lambda: "session-token",
	)
	application = create_application(
		authenticator.local_session_id,
		product_root=tmp_path / "data",
		protector=_PurposeBoundProtector(),
		login_provider=lambda workspace: (
			login_started.set(),
			allow_login.wait(timeout=2),
			{"workspace": workspace.value, "cookies": {"wt2": "secret"}},
		)[2],
	)
	server = LocalWebServer(
		static_root=_static_root(tmp_path),
		authenticator=authenticator,
		application=application,
	)
	server.start()
	try:
		token = _exchange(server)
		headers = _command_headers(server, token)
		switch_status, _, switch_body = _request(
			server,
			"POST",
			"/api/v1/commands/switch-workspace",
			headers=headers,
			body={"request_id": "switch-1", "workspace": "recruiting"},
		)
		connect_status, _, connect_body = _request(
			server,
			"POST",
			"/api/v1/commands/connect-platform-session",
			headers=headers,
			body={"request_id": "connect-1"},
		)
		assert login_started.wait(timeout=2)
		allow_login.set()
		deadline = time.monotonic() + 2
		state = "connecting"
		while state == "connecting" and time.monotonic() < deadline:
			_, _, state_body = _request(
				server,
				"GET",
				"/api/v1/state",
				headers={"Authorization": f"Bearer {token}"},
			)
			state = json.loads(state_body)["snapshot"]["platform_session"]
		logout_status, _, logout_body = _request(
			server,
			"POST",
			"/api/v1/commands/logout-platform-session",
			headers=headers,
			body={"request_id": "logout-1"},
		)
	finally:
		server.close()

	assert switch_status == 200
	assert json.loads(switch_body)["snapshot"]["active_workspace"] == "recruiting"
	assert connect_status == 200
	assert json.loads(connect_body)["snapshot"]["platform_session"] == "connecting"
	assert state == "connected"
	assert logout_status == 200
	assert json.loads(logout_body)["snapshot"]["platform_session"] in {"stopping", "disconnected"}


@pytest.mark.parametrize(
	("path", "body"),
	[
		(
			"/api/v1/commands/switch-workspace",
			{"request_id": "switch-invalid", "workspace": "../../outside"},
		),
		(
			"/api/v1/commands/switch-workspace",
			{"request_id": "switch-extra", "workspace": "recruiting", "root": "C:/outside"},
		),
		("/api/v1/commands/connect-platform-session", {"request_id": "connect-extra", "cookies": {}}),
		("/api/v1/commands/logout-platform-session", {}),
	],
)
def test_command_routes_reject_unknown_workspaces_and_fields(
	tmp_path: Path,
	path: str,
	body: dict[str, object],
):
	with _running_server(tmp_path) as server:
		token = _exchange(server)
		status, _, response_body = _request(
			server,
			"POST",
			path,
			headers=_command_headers(server, token),
			body=body,
		)

	assert status == 400
	assert json.loads(response_body) == {"error": {"code": "INVALID_REQUEST", "message": "Invalid request"}}
	assert not (tmp_path / "outside").exists()


def test_cross_origin_command_cannot_switch_or_create_the_other_workspace(tmp_path: Path):
	with _running_server(tmp_path) as server:
		token = _exchange(server)
		status, _, _ = _request(
			server,
			"POST",
			"/api/v1/commands/switch-workspace",
			headers={
				"Authorization": f"Bearer {token}",
				"Origin": "https://attacker.example",
				"Sec-Fetch-Site": "cross-site",
			},
			body={"request_id": "cross-origin", "workspace": "recruiting"},
		)

	assert status == 403
	assert not (tmp_path / "data" / "workspaces" / "recruiting").exists()


def test_authenticated_job_journey_api_exposes_progress_detail_shortlist_and_events(tmp_path: Path):
	authenticator = StartupAuthenticator(
		bootstrap_token="bootstrap-token",
		local_session_id="local-session-api",
		session_token_factory=lambda: "session-token",
	)
	job = JobSummary("job-1", "Python 后端工程师", "示例科技", "上海", "20-40K", "3-5年", "本科")
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path / "data", local_session_id="local-session-api"),
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED},
		),
		boss=FakeBossAdapter(
			search_batches=(JobSearchBatch((job,), 100),),
			details={"job-1": JobSourceDetail(job, "负责 Python 服务")},
		),
		run_id_factory=lambda: "search-api-1",
	)
	server = LocalWebServer(
		static_root=_static_root(tmp_path),
		authenticator=authenticator,
		application=application,
	)
	server.start()
	try:
		token = _exchange(server)
		headers = _command_headers(server, token)
		goal_status, _, _ = _request(
			server,
			"POST",
			"/api/v1/commands/update-job-search-goal",
			headers=headers,
			body={
				"request_id": "goal-1",
				"objective": "寻找后端岗位",
				"keyword": "Python",
				"city": "上海",
				"salary": "20-40K",
				"experience": "3-5年",
				"education": "本科",
			},
		)
		start_status, _, _ = _request(
			server,
			"POST",
			"/api/v1/commands/start-job-search",
			headers=headers,
			body={"request_id": "start-1"},
		)
		deadline = time.monotonic() + 2
		state_body = b""
		while time.monotonic() < deadline:
			_, _, state_body = _request(
				server,
				"GET",
				"/api/v1/state",
				headers={"Authorization": f"Bearer {token}"},
			)
			if json.loads(state_body)["snapshot"]["active_run"]["state"] == "completed":
				break
		inspect_status, _, inspect_body = _request(
			server,
			"POST",
			"/api/v1/commands/inspect-job",
			headers=headers,
			body={"request_id": "inspect-1", "reference": "job-1"},
		)
		shortlist_status, _, shortlist_body = _request(
			server,
			"POST",
			"/api/v1/commands/set-shortlisted",
			headers=headers,
			body={"request_id": "shortlist-1", "reference": "job-1", "shortlisted": True},
		)
		events_status, event_headers, events_body = _request(
			server,
			"GET",
			"/api/v1/runs/search-api-1/events?after_cursor=0",
			headers={"Authorization": f"Bearer {token}"},
		)
		resumed_status, _, resumed_body = _request(
			server,
			"GET",
			"/api/v1/runs/search-api-1/events",
			headers={"Authorization": f"Bearer {token}", "Last-Event-ID": "2"},
		)
		run_status, _, run_body = _request(
			server,
			"GET",
			"/api/v1/runs/search-api-1",
			headers={"Authorization": f"Bearer {token}"},
		)
		conflict_status, _, conflict_body = _request(
			server,
			"GET",
			"/api/v1/runs/search-api-1/events?after_cursor=1",
			headers={"Authorization": f"Bearer {token}", "Last-Event-ID": "2"},
		)
	finally:
		server.close()

	assert (goal_status, start_status, inspect_status, shortlist_status, events_status) == (200, 200, 200, 200, 200)
	assert json.loads(state_body)["snapshot"]["job_seeking"]["results"][0]["reference"] == "job-1"
	assert json.loads(inspect_body)["snapshot"]["job_seeking"]["selected_job"]["match_reasons"]
	assert json.loads(shortlist_body)["snapshot"]["job_seeking"]["shortlist"][0]["reference"] == "job-1"
	assert event_headers["Content-Type"] == "text/event-stream; charset=utf-8"
	assert events_body.count(b"event: run-event\n") == 3
	assert b"event: snapshot\n" in events_body
	assert b'"active_run":{"run_id":"search-api-1","state":"completed"' in events_body
	assert resumed_status == 200
	assert b"id: 1\n" not in resumed_body and b"id: 2\n" not in resumed_body
	assert b"id: 3\n" in resumed_body and b"event: snapshot\n" in resumed_body
	assert run_status == 200
	assert json.loads(run_body)["run"]["run_id"] == "search-api-1"
	assert conflict_status == 400
	assert json.loads(conflict_body)["error"]["code"] == "INVALID_REQUEST"


def test_job_journey_api_rejects_extra_fields_without_starting_a_search(tmp_path: Path):
	with _running_server(tmp_path) as server:
		token = _exchange(server)
		status, _, body = _request(
			server,
			"POST",
			"/api/v1/commands/start-job-search",
			headers=_command_headers(server, token),
			body={"request_id": "start-1", "retry_risk_control": True},
		)

	assert status == 400
	assert json.loads(body)["error"]["code"] == "INVALID_REQUEST"


def test_recruiting_journey_api_is_explicit_and_rejects_extra_fields(tmp_path: Path):
	authenticator = StartupAuthenticator(
		bootstrap_token="bootstrap-token",
		local_session_id="local-session-recruiting-api",
		session_token_factory=lambda: "session-token",
	)
	opening = RecruitingOpening("opening-1", "Python 后端工程师", "招聘中")
	applicant = InboundApplicant("prospect-1", "招聘对象甲", "5 年 Python 经验")
	boss = FakeBossAdapter(
		openings=(opening,),
		applicant_batches=(RecruitingApplicantBatch((applicant,), 100),),
		prospect_contexts={
			"prospect-1": RecruitingProspectContext(
				applicant,
				resume_text="仅在明确查看后加载的简历",
				chat_messages=("应聘者：您好",),
				contact_details=("手机号已保护",),
			),
		},
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path / "data", local_session_id="local-session-recruiting-api"),
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.RECRUITING: PlatformSessionState.CONNECTED},
		),
		boss=boss,
		run_id_factory=lambda: "recruiting-api-1",
		intent_id_factory=lambda: "intent-reply-api",
	)
	server = LocalWebServer(static_root=_static_root(tmp_path), authenticator=authenticator, application=application)
	server.start()
	try:
		token = _exchange(server)
		headers = _command_headers(server, token)
		statuses = []
		for path, body in (
			("/api/v1/commands/switch-workspace", {"request_id": "switch-1", "workspace": "recruiting"}),
			("/api/v1/commands/load-recruiting-openings", {"request_id": "openings-1"}),
			("/api/v1/commands/select-recruiting-opening", {"request_id": "select-1", "reference": "opening-1"}),
			("/api/v1/commands/start-inbound-applicants", {"request_id": "applicants-1"}),
		):
			status, _, _ = _request(server, "POST", path, headers=headers, body=body)
			statuses.append(status)
		deadline = time.monotonic() + 2
		state_body = b""
		while time.monotonic() < deadline:
			_, _, state_body = _request(server, "GET", "/api/v1/state", headers={"Authorization": f"Bearer {token}"})
			if json.loads(state_body)["snapshot"]["active_run"]["state"] == "completed":
				break
		assert boss.prospect_context_calls == []
		inspect_status, _, inspect_body = _request(
			server,
			"POST",
			"/api/v1/commands/inspect-recruiting-prospect",
			headers=headers,
			body={"request_id": "inspect-1", "reference": "prospect-1"},
		)
		prepare_status, _, prepare_body = _request(
			server,
			"POST",
			"/api/v1/commands/prepare-recruiting-reply",
			headers=headers,
			body={"request_id": "prepare-reply-1", "reference": "prospect-1", "message": "您好，仅回复一次"},
		)
		forged_reply_status, _, _ = _request(
			server,
			"POST",
			"/api/v1/commands/prepare-recruiting-reply",
			headers=headers,
			body={
				"request_id": "prepare-reply-2",
				"reference": "prospect-1",
				"message": "替换消息",
				"friend_id": 98765,
			},
		)
		confirm_status, _, _ = _request(
			server,
			"POST",
			"/api/v1/write-intents/confirm",
			headers=headers,
			body={"request_id": "confirm-reply-1", "intent_id": "intent-reply-api"},
		)
		rejected_status, _, rejected_body = _request(
			server,
			"POST",
			"/api/v1/commands/load-recruiting-openings",
			headers=headers,
			body={"request_id": "openings-2", "raw_platform_id": "must-not-pass"},
		)
	finally:
		server.close()

	assert statuses == [200, 200, 200, 200]
	assert json.loads(state_body)["snapshot"]["recruiting"]["applicants"] == [
		{"reference": "prospect-1", "display_name": "招聘对象甲", "headline": "5 年 Python 经验"},
	]
	assert inspect_status == 200
	assert (
		json.loads(inspect_body)["snapshot"]["recruiting"]["selected_prospect"]["resume_text"]
		== "仅在明确查看后加载的简历"
	)
	assert boss.prospect_context_calls == ["prospect-1"]
	assert prepare_status == 200
	prepared = json.loads(prepare_body)["snapshot"]["pending_write_intent"]
	assert prepared["context_label"] == "Python 后端工程师"
	assert prepared["target_label"] == "招聘对象甲"
	assert prepared["destination_label"] == "BOSS 招聘沟通会话"
	assert prepared["payload_preview"] == "您好，仅回复一次"
	assert forged_reply_status == 400
	assert confirm_status == 200
	assert boss.recruiting_reply_calls == [("prospect-1", "您好，仅回复一次")]
	assert rejected_status == 400
	assert json.loads(rejected_body)["error"]["code"] == "INVALID_REQUEST"


def test_write_intent_routes_whitelist_fields_and_confirm_exactly_once(tmp_path: Path):
	authenticator = StartupAuthenticator(
		bootstrap_token="bootstrap-token",
		local_session_id="local-session-write",
		session_token_factory=lambda: "session-token",
	)
	job = JobSummary("job-1", "Python 后端工程师", "示例科技", "上海")
	boss = FakeBossAdapter(
		search_batches=(JobSearchBatch((job,), 100),),
		details={"job-1": JobSourceDetail(job)},
	)
	application = Application(
		workspace_store=WorkspaceRegistry(tmp_path / "data", local_session_id="local-session-write"),
		credential_store=InMemoryCredentialStore(
			{WorkspaceKind.JOB_SEEKING: PlatformSessionState.CONNECTED},
		),
		boss=boss,
		intent_id_factory=lambda: "intent-api-1",
	)
	context = RequestContext("local-session-write", "setup")
	application.execute(UpdateJobSearchGoalCommand(JobSearchGoal("后端", "Python")), context)
	application.execute(StartJobSearchCommand(), context)
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		if application.query(CurrentStateQuery(), context).active_run.state == "completed":
			break
		time.sleep(0.01)
	application.execute(InspectJobCommand("job-1"), context)
	server = LocalWebServer(
		static_root=_static_root(tmp_path),
		authenticator=authenticator,
		application=application,
	)
	server.start()
	try:
		token = _exchange(server)
		headers = _command_headers(server, token)
		prepare_status, _, prepare_body = _request(
			server,
			"POST",
			"/api/v1/commands/prepare-job-greeting",
			headers=headers,
			body={"request_id": "prepare-1", "reference": "job-1", "message": "您好"},
		)
		forged_status, _, _ = _request(
			server,
			"POST",
			"/api/v1/write-intents/confirm",
			headers=headers,
			body={
				"request_id": "confirm-forged",
				"intent_id": "intent-api-1",
				"message": "替换内容",
			},
		)
		confirm_status, _, confirm_body = _request(
			server,
			"POST",
			"/api/v1/write-intents/confirm",
			headers=headers,
			body={"request_id": "confirm-1", "intent_id": "intent-api-1"},
		)
		duplicate_status, _, duplicate_body = _request(
			server,
			"POST",
			"/api/v1/write-intents/confirm",
			headers=headers,
			body={"request_id": "confirm-2", "intent_id": "intent-api-1"},
		)
	finally:
		server.close()

	assert prepare_status == 200
	assert json.loads(prepare_body)["snapshot"]["pending_write_intent"]["payload_preview"] == "您好"
	assert forged_status == 400
	assert confirm_status == 200
	assert json.loads(confirm_body)["snapshot"]["pending_write_intent"]["state"] == "succeeded"
	assert duplicate_status == 400
	assert json.loads(duplicate_body)["error"]["code"] == "WRITE_INTENT_CONSUMED"
	assert boss.greeting_calls == [("job-1", "您好")]
