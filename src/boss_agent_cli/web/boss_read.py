"""Bounded, read-only BOSS adapter for the local Web journey."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol

import httpx

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.models import JobDetail, JobItem
from boss_agent_cli.application import (
	BossAdapterFailure,
	ErrorCode,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	WorkspaceKind,
)


class CredentialProvider(Protocol):
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState: ...
	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None: ...


ReadTransport = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _http_read(url: str, params: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
	headers = {
		**endpoints.DEFAULT_HEADERS,
		"User-Agent": str(credential.get("user_agent", endpoints.DEFAULT_HEADERS.get("User-Agent", ""))),
		"Referer": endpoints.WEB_GEEK_JOB_URL,
	}
	try:
		response = httpx.get(
			url,
			params=params,
			cookies=credential.get("cookies", {}),
			headers=headers,
			follow_redirects=True,
			timeout=30,
		)
		payload = response.json()
	except (httpx.HTTPError, ValueError) as exc:
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
	if isinstance(payload, dict) and payload.get("code") in {
		endpoints.CODE_STOKEN_EXPIRED,
		endpoints.CODE_RATE_LIMITED,
		endpoints.CODE_ACCOUNT_RISK,
	}:
		return payload
	if response.status_code in {401, 403}:
		raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
	if response.status_code == 429:
		raise BossAdapterFailure(ErrorCode.RATE_LIMITED)
	try:
		response.raise_for_status()
	except httpx.HTTPError as exc:
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
	if not isinstance(payload, dict):
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
	return payload


class BossReadAdapter:
	"""Make at most one BOSS request per explicit search/detail action."""

	def __init__(self, sessions: CredentialProvider, *, transport: ReadTransport = _http_read) -> None:
		self._sessions = sessions
		self._transport = transport

	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return self._sessions.probe_session(workspace)

	def _credential(self) -> dict[str, Any]:
		credential = self._sessions.active_credential(WorkspaceKind.JOB_SEEKING)
		if credential is None:
			raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
		return credential

	@staticmethod
	def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
		code = response.get("code")
		if code == endpoints.CODE_STOKEN_EXPIRED:
			raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
		if code == endpoints.CODE_RATE_LIMITED:
			raise BossAdapterFailure(ErrorCode.RATE_LIMITED)
		if code == endpoints.CODE_ACCOUNT_RISK:
			raise BossAdapterFailure(ErrorCode.PLATFORM_RISK_CONTROL)
		if code != endpoints.CODE_SUCCESS:
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
		data = response.get("zpData")
		if not isinstance(data, dict):
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
		return data

	def search_jobs(
		self,
		goal: JobSearchGoal,
		*,
		cancel_requested: Callable[[], bool],
	) -> Iterable[JobSearchBatch]:
		if cancel_requested():
			return
		params: dict[str, Any] = {"query": goal.keyword, "page": 1}
		lookups = (
			("city", goal.city, endpoints.CITY_CODES),
			("salary", goal.salary, endpoints.SALARY_CODES),
			("experience", goal.experience, endpoints.EXPERIENCE_CODES),
			("degree", goal.education, endpoints.EDUCATION_CODES),
		)
		for name, value, lookup in lookups:
			if value and value in lookup:
				params[name] = lookup[value]
		data = self._unwrap(self._transport(endpoints.SEARCH_URL, params, self._credential()))
		raw_items = data.get("jobList", [])
		if not isinstance(raw_items, list):
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
		items = tuple(self._summary(JobItem.from_api(item)) for item in raw_items if isinstance(item, dict))
		if not cancel_requested():
			yield JobSearchBatch(items=items, progress=100)

	def job_detail(self, reference: str) -> JobSourceDetail:
		data = self._unwrap(
			self._transport(endpoints.DETAIL_URL, {"encryptJobId": reference}, self._credential()),
		)
		detail = JobDetail.from_api(data)
		if not detail.job_id:
			detail.job_id = reference
		return JobSourceDetail(
			job=JobSummary(
				reference=detail.job_id,
				title=detail.title,
				company=detail.company,
				location=detail.city,
				salary=detail.salary,
				experience=detail.experience,
				education=detail.education,
			),
			description=detail.description,
			company_stage=str(detail.company_info.get("stage", "")),
			company_size=str(detail.company_info.get("scale", "")),
			recruiter=" · ".join(part for part in (detail.boss_name, detail.boss_title) if part),
		)

	@staticmethod
	def _summary(item: JobItem) -> JobSummary:
		return JobSummary(
			reference=item.job_id,
			title=item.title,
			company=item.company,
			location="·".join(part for part in (item.city, item.district) if part),
			salary=item.salary,
			experience=item.experience,
			education=item.education,
		)
