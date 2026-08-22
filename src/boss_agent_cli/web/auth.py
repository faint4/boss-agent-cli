"""Per-process authentication for the local Web shell."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable


class StartupAuthenticator:
	"""Exchange one fragment-delivered bootstrap token for one memory-only session."""

	def __init__(
		self,
		*,
		bootstrap_token: str | None = None,
		local_session_id: str | None = None,
		session_token_factory: Callable[[], str] | None = None,
		bootstrap_ttl_seconds: float = 60.0,
		clock: Callable[[], float] = time.monotonic,
	) -> None:
		self._bootstrap_token = bootstrap_token or secrets.token_urlsafe(32)
		self._local_session_id = local_session_id or secrets.token_urlsafe(32)
		self._session_token_factory = session_token_factory or (lambda: secrets.token_urlsafe(32))
		self._bootstrap_expires_at = clock() + bootstrap_ttl_seconds
		self._clock = clock
		self._session_token: str | None = None
		self._bootstrap_consumed = False
		self._lock = threading.Lock()

	@property
	def bootstrap_token(self) -> str:
		return self._bootstrap_token

	@property
	def local_session_id(self) -> str:
		return self._local_session_id

	def exchange(self, presented_token: str) -> str | None:
		with self._lock:
			if self._bootstrap_consumed or self._clock() >= self._bootstrap_expires_at:
				return None
			if not secrets.compare_digest(presented_token, self._bootstrap_token):
				return None
			self._bootstrap_consumed = True
			self._session_token = self._session_token_factory()
			return self._session_token

	def authorize(self, authorization: str | None) -> bool:
		if authorization is None or not authorization.startswith("Bearer "):
			return False
		presented_token = authorization.removeprefix("Bearer ")
		with self._lock:
			return self._session_token is not None and secrets.compare_digest(presented_token, self._session_token)

	def invalidate(self) -> None:
		with self._lock:
			self._bootstrap_consumed = True
			self._session_token = None
