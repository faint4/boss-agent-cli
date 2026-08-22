"""Current-user Windows DPAPI protection for local Web credentials."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any, Protocol


class CredentialProtectionError(RuntimeError):
	"""Safe failure raised when credentials cannot be protected or recovered."""


class CredentialProtector(Protocol):
	def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes: ...
	def unprotect(self, ciphertext: bytes, *, purpose: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
	_fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_ubyte]]:
	buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
	return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


class WindowsDPAPIProtector:
	"""Protect bytes for the current Windows user, bound to a caller purpose."""

	_UI_FORBIDDEN = 0x1

	def __init__(self) -> None:
		if sys.platform != "win32":
			raise CredentialProtectionError("Windows DPAPI is unavailable")
		self._crypt32: Any = ctypes.WinDLL("crypt32", use_last_error=True)
		self._kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)

	def _call(self, function_name: str, payload: bytes, purpose: bytes) -> bytes:
		payload_blob, payload_buffer = _blob(payload)
		purpose_blob, purpose_buffer = _blob(purpose)
		output_blob = _DataBlob()
		function = getattr(self._crypt32, function_name)
		ok = function(
			ctypes.byref(payload_blob),
			None,
			ctypes.byref(purpose_blob),
			None,
			None,
			self._UI_FORBIDDEN,
			ctypes.byref(output_blob),
		)
		_ = payload_buffer, purpose_buffer
		if not ok:
			raise CredentialProtectionError("Windows could not protect the Platform Session")
		try:
			return ctypes.string_at(output_blob.pbData, output_blob.cbData)
		finally:
			self._kernel32.LocalFree(output_blob.pbData)

	def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
		return self._call("CryptProtectData", plaintext, purpose)

	def unprotect(self, ciphertext: bytes, *, purpose: bytes) -> bytes:
		return self._call("CryptUnprotectData", ciphertext, purpose)


class UnavailableCredentialProtector:
	def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
		raise CredentialProtectionError("Current-user Windows credential protection is unavailable")

	def unprotect(self, ciphertext: bytes, *, purpose: bytes) -> bytes:
		raise CredentialProtectionError("Current-user Windows credential protection is unavailable")


def default_credential_protector() -> CredentialProtector:
	if sys.platform == "win32":
		return WindowsDPAPIProtector()
	return UnavailableCredentialProtector()
