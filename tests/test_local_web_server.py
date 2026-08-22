from __future__ import annotations

import base64
import http.client
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from boss_agent_cli.web.auth import StartupAuthenticator
from boss_agent_cli.web.server import LocalWebServer


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
	server = LocalWebServer(static_root=_static_root(tmp_path), authenticator=authenticator)
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
	server = LocalWebServer(static_root=_static_root(tmp_path))
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
			f"GET / HTTP/1.1\r\nHost: {server.expected_host}\r\n"
			"Host: attacker.example\r\nConnection: close\r\n\r\n"
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
		resolved_headers = {name: (server.origin if value == "__ORIGIN__" else value) for name, value in headers.items()}
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
	second = LocalWebServer(static_root=_static_root(tmp_path / "second"), authenticator=second_auth)
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
