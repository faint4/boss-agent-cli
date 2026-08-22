"""Secure same-origin HTTP host for the production-built local Web shell."""

from __future__ import annotations

import dataclasses
import json
import mimetypes
import sys
import threading
import webbrowser
from datetime import datetime
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from boss_agent_cli.application import (
	Application,
	CurrentStateQuery,
	DomainError,
	RequestContext,
)
from boss_agent_cli.application.core_journey_contract import (
	WEB_COMMANDS,
	ContractValidationError,
	build_web_command,
)
from boss_agent_cli.web.auth import StartupAuthenticator
from boss_agent_cli.web.runtime import create_application


LOOPBACK_HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 16 * 1024
SECURITY_HEADERS = {
	"Cache-Control": "no-store",
	"Content-Security-Policy": (
		"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
		"connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
	),
	"Cross-Origin-Opener-Policy": "same-origin",
	"Cross-Origin-Resource-Policy": "same-origin",
	"Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
	"Referrer-Policy": "no-referrer",
	"X-Content-Type-Options": "nosniff",
	"X-Frame-Options": "DENY",
}


def _json_default(value: object) -> object:
	if isinstance(value, Enum):
		return value.value
	if isinstance(value, datetime):
		return value.isoformat()
	if dataclasses.is_dataclass(value) and not isinstance(value, type):
		return dataclasses.asdict(value)
	raise TypeError(f"Cannot serialize {type(value).__name__}")


class _LocalHTTPServer(ThreadingHTTPServer):
	daemon_threads = True
	allow_reuse_address = False

	def __init__(self, owner: LocalWebServer) -> None:
		self.owner = owner
		super().__init__((LOOPBACK_HOST, 0), _LocalRequestHandler)


class _LocalRequestHandler(BaseHTTPRequestHandler):
	protocol_version = "HTTP/1.1"
	server_version = "BossLocalWeb"
	sys_version = ""

	@property
	def local_server(self) -> _LocalHTTPServer:
		return self.server  # type: ignore[return-value]

	@property
	def owner(self) -> LocalWebServer:
		return self.local_server.owner

	def log_message(self, format: str, *args: object) -> None:
		# The local shell does not log URLs, headers, bodies, credentials, or personal data.
		return

	def _send_headers(self, *, status: int, content_type: str, content_length: int) -> None:
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(content_length))
		self.send_header("Connection", "close")
		for name, value in SECURITY_HEADERS.items():
			self.send_header(name, value)
		self.end_headers()

	def _send_bytes(self, status: int, body: bytes, content_type: str, *, include_body: bool = True) -> None:
		self._send_headers(status=status, content_type=content_type, content_length=len(body))
		if include_body:
			self.wfile.write(body)

	def _send_json(self, status: int, payload: object, *, include_body: bool = True) -> None:
		body = json.dumps(payload, default=_json_default, separators=(",", ":")).encode("utf-8")
		self._send_bytes(status, body, "application/json", include_body=include_body)

	def _send_error(self, status: int, code: str, message: str, *, include_body: bool = True) -> None:
		self._send_json(status, {"error": {"code": code, "message": message}}, include_body=include_body)

	def _discard_request_body(self) -> None:
		if getattr(self, "_request_body_consumed", False):
			return
		try:
			content_length = int(self.headers.get("Content-Length", "0"))
		except ValueError:
			content_length = 0
		if 0 < content_length <= MAX_REQUEST_BYTES:
			self.rfile.read(content_length)
		self._request_body_consumed = True

	def _valid_host(self) -> bool:
		host_values = self.headers.get_all("Host", failobj=[])
		if len(host_values) != 1 or host_values[0] != self.owner.expected_host:
			return False
		return self.headers.get("Forwarded") is None and self.headers.get("X-Forwarded-Host") is None

	def _validate_origin(self) -> bool:
		origin_values = self.headers.get_all("Origin", failobj=[])
		if origin_values != [self.owner.origin]:
			self._discard_request_body()
			self._send_error(HTTPStatus.FORBIDDEN, "ORIGIN_REJECTED", "Request origin rejected")
			return False
		fetch_site_values = self.headers.get_all("Sec-Fetch-Site", failobj=[])
		if len(fetch_site_values) > 1 or (fetch_site_values and fetch_site_values[0] != "same-origin"):
			self._discard_request_body()
			self._send_error(HTTPStatus.FORBIDDEN, "FETCH_METADATA_REJECTED", "Request context rejected")
			return False
		return True

	def _validate_write(self) -> bool:
		if not self._validate_origin():
			return False
		content_type_values = self.headers.get_all("Content-Type", failobj=[])
		content_type = content_type_values[0].partition(";")[0].strip().lower() if len(content_type_values) == 1 else ""
		if content_type != "application/json":
			self._discard_request_body()
			self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "CONTENT_TYPE_REJECTED", "JSON is required")
			return False
		if self.headers.get("Transfer-Encoding") is not None:
			self._discard_request_body()
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request")
			return False
		return True

	def _authorized(self) -> bool:
		authorization_values = self.headers.get_all("Authorization", failobj=[])
		if len(authorization_values) == 1 and self.owner.authenticator.authorize(authorization_values[0]):
			return True
		self._send_error(HTTPStatus.UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "Authentication required")
		return False

	def _read_json(self) -> dict[str, object] | None:
		content_length_values = self.headers.get_all("Content-Length", failobj=[])
		if len(content_length_values) != 1:
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request")
			return None
		try:
			content_length = int(content_length_values[0])
		except ValueError:
			content_length = -1
		if content_length < 0 or content_length > MAX_REQUEST_BYTES:
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request")
			return None
		try:
			decoded = json.loads(self.rfile.read(content_length))
			self._request_body_consumed = True
		except (UnicodeDecodeError, json.JSONDecodeError):
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_JSON", "Invalid JSON")
			return None
		if not isinstance(decoded, dict):
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request")
			return None
		return decoded

	def _handle_session_exchange(self) -> None:
		payload = self._read_json()
		if payload is None:
			return
		if set(payload) != {"bootstrap_token"} or not isinstance(payload["bootstrap_token"], str):
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request")
			return
		session_token = self.owner.authenticator.exchange(payload["bootstrap_token"])
		if session_token is None:
			self._send_error(HTTPStatus.UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "Authentication required")
			return
		self._send_json(HTTPStatus.CREATED, {"schema_version": "1", "session_token": session_token})

	def _handle_state(self, *, include_body: bool) -> None:
		try:
			snapshot = self.owner.application.query(
				CurrentStateQuery(),
				RequestContext(
					local_session_id=self.owner.authenticator.local_session_id,
					correlation_id=self.owner.new_correlation_id(),
				),
			)
		except DomainError as exc:
			status = HTTPStatus.SERVICE_UNAVAILABLE if exc.recoverable else HTTPStatus.BAD_REQUEST
			self._send_json(
				status,
				{
					"schema_version": "1",
					"error": {
						"code": exc.code,
						"message": exc.message,
						"recoverable": exc.recoverable,
						"recovery_action": exc.recovery_action,
						"correlation_id": exc.correlation_id,
					},
				},
				include_body=include_body,
			)
			return
		self._send_json(
			HTTPStatus.OK,
			{"schema_version": "1", "snapshot": snapshot},
			include_body=include_body,
		)

	def _handle_command(self, path: str) -> None:
		payload = self._read_json()
		if payload is None:
			return
		if path not in WEB_COMMANDS:
			self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Not found")
			return
		try:
			command = build_web_command(path, payload)
		except ContractValidationError:
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request")
			return
		try:
			result = self.owner.application.execute(
				command,
				RequestContext(
					local_session_id=self.owner.authenticator.local_session_id,
					correlation_id=self.owner.new_correlation_id(),
				),
			)
		except DomainError as exc:
			status = HTTPStatus.SERVICE_UNAVAILABLE if exc.recoverable else HTTPStatus.BAD_REQUEST
			self._send_json(
				status,
				{
					"schema_version": "1",
					"error": {
						"code": exc.code,
						"message": exc.message,
						"recoverable": exc.recoverable,
						"recovery_action": exc.recovery_action,
						"correlation_id": exc.correlation_id,
					},
				},
			)
			return
		self._send_json(
			HTTPStatus.OK,
			{"schema_version": "1", "snapshot": result.snapshot, "resource_ref": result.resource_ref},
		)

	def _handle_events(self, path: str, query: str, *, include_body: bool) -> None:
		run_id = unquote(path.removeprefix("/api/v1/runs/").removesuffix("/events"))
		if not run_id or "/" in run_id or len(run_id) > 128:
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request", include_body=include_body)
			return
		try:
			values = dict(part.split("=", 1) for part in query.split("&") if part)
			after_cursor = int(values.get("after_cursor", "0"))
			if set(values) - {"after_cursor"} or after_cursor < 0:
				raise ValueError
		except (ValueError, TypeError):
			self._send_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "Invalid request", include_body=include_body)
			return
		try:
			events = self.owner.application.events(
				run_id,
				after_cursor=after_cursor,
				context=RequestContext(
					local_session_id=self.owner.authenticator.local_session_id,
					correlation_id=self.owner.new_correlation_id(),
				),
			)
		except DomainError as exc:
			self._send_error(HTTPStatus.NOT_FOUND, exc.code.value, exc.message, include_body=include_body)
			return
		self._send_json(
			HTTPStatus.OK,
			{"schema_version": "1", "events": events},
			include_body=include_body,
		)

	def _handle_api(self, method: str, path: str, *, include_body: bool) -> None:
		if method == "POST" and path == "/api/v1/session":
			self._handle_session_exchange()
			return
		if not self._authorized():
			return
		if method in {"GET", "HEAD"} and path == "/api/v1/state":
			self._handle_state(include_body=include_body)
			return
		if method in {"GET", "HEAD"} and path.startswith("/api/v1/runs/") and path.endswith("/events"):
			self._handle_events(path, urlsplit(self.path).query, include_body=include_body)
			return
		if method == "POST" and path.startswith("/api/v1/commands/"):
			self._handle_command(path)
			return
		if method == "POST" and path in {"/api/v1/write-intents/confirm", "/api/v1/write-intents/cancel"}:
			self._handle_command(path)
			return
		self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Not found", include_body=include_body)

	def _static_file(self, path: str) -> Path | None:
		decoded_path = unquote(path)
		if decoded_path == "/":
			return self.owner.static_root / "index.html"
		relative = PurePosixPath(decoded_path.removeprefix("/"))
		if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
			return None
		candidate = self.owner.static_root.joinpath(*relative.parts).resolve()
		try:
			candidate.relative_to(self.owner.static_root)
		except ValueError:
			return None
		return candidate

	def _handle_static(self, method: str, path: str, *, include_body: bool) -> None:
		if method not in {"GET", "HEAD"}:
			self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Not found", include_body=include_body)
			return
		file_path = self._static_file(path)
		if file_path is None or not file_path.is_file():
			self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Not found", include_body=include_body)
			return
		body = file_path.read_bytes()
		content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
		if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
			content_type = f"{content_type}; charset=utf-8"
		self._send_bytes(HTTPStatus.OK, body, content_type, include_body=include_body)

	def _dispatch(self, method: str) -> None:
		include_body = method != "HEAD"
		if not self._valid_host():
			self._discard_request_body()
			self._send_error(HTTPStatus.FORBIDDEN, "HOST_REJECTED", "Request host rejected", include_body=include_body)
			return
		path = urlsplit(self.path).path
		if method == "OPTIONS":
			if not path.startswith("/api/"):
				self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Not found")
				return
			if not self._validate_origin():
				return
			self._send_bytes(HTTPStatus.NO_CONTENT, b"", "application/json")
			return
		if method not in {"GET", "HEAD", "OPTIONS"} and not self._validate_write():
			return
		if path.startswith("/api/"):
			self._handle_api(method, path, include_body=include_body)
		else:
			self._handle_static(method, path, include_body=include_body)

	def do_GET(self) -> None:
		self._dispatch("GET")

	def do_HEAD(self) -> None:
		self._dispatch("HEAD")

	def do_OPTIONS(self) -> None:
		self._dispatch("OPTIONS")

	def do_POST(self) -> None:
		self._dispatch("POST")

	def do_PUT(self) -> None:
		self._dispatch("PUT")

	def do_PATCH(self) -> None:
		self._dispatch("PATCH")

	def do_DELETE(self) -> None:
		self._dispatch("DELETE")


class LocalWebServer:
	"""Own one loopback listener and its per-start authentication state."""

	def __init__(
		self,
		*,
		static_root: Path | None = None,
		authenticator: StartupAuthenticator | None = None,
		application: Application | None = None,
		product_root: Path | None = None,
	) -> None:
		self.authenticator = authenticator or StartupAuthenticator()
		self.application = application or create_application(
			self.authenticator.local_session_id,
			product_root=product_root,
		)
		self.static_root = (static_root or Path(__file__).with_name("static")).resolve()
		if not (self.static_root / "index.html").is_file():
			raise RuntimeError("Production Web assets are missing; build the React application first")
		self._server = _LocalHTTPServer(self)
		self._thread: threading.Thread | None = None

	@property
	def host(self) -> str:
		return str(self._server.server_address[0])

	@property
	def port(self) -> int:
		return int(self._server.server_address[1])

	@property
	def expected_host(self) -> str:
		return f"{LOOPBACK_HOST}:{self.port}"

	@property
	def origin(self) -> str:
		return f"http://{self.expected_host}"

	@property
	def launch_url(self) -> str:
		return f"{self.origin}/#bootstrap={quote(self.authenticator.bootstrap_token, safe='')}"

	def new_correlation_id(self) -> str:
		import secrets

		return secrets.token_urlsafe(18)

	def start(self) -> None:
		if self._thread is not None:
			raise RuntimeError("Local Web server has already started")
		self._thread = threading.Thread(
			target=self._server.serve_forever,
			kwargs={"poll_interval": 0.01},
			name="boss-local-web",
			daemon=True,
		)
		self._thread.start()

	def serve_and_open(self, *, open_browser: Any = webbrowser.open) -> None:
		self.start()
		open_browser(self.launch_url)
		self.wait()

	def wait(self) -> None:
		if self._thread is None:
			raise RuntimeError("Local Web server has not started")
		self._thread.join()

	def close(self) -> None:
		self.authenticator.invalidate()
		if self._thread is not None:
			self._server.shutdown()
			self._thread.join(timeout=3)
			self._thread = None
		self._server.server_close()

	def __enter__(self) -> LocalWebServer:
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc: BaseException | None,
		traceback: TracebackType | None,
	) -> None:
		self.close()


def run() -> None:
	"""Start the production Web shell from the installed source tree."""

	server = LocalWebServer()
	print(f"BOSS local Web is ready at {server.origin}", file=sys.stderr)
	try:
		server.serve_and_open()
	except KeyboardInterrupt:
		pass
	finally:
		server.close()
