"""Bounded BOSS adapter for reads and explicitly confirmed single writes."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import httpx

from boss_agent_cli.api import endpoints
from boss_agent_cli.api import recruiter_endpoints as recruiter_ep
from boss_agent_cli.api.models import JobDetail, JobItem
from boss_agent_cli.application import (
	BossAdapterFailure,
	ErrorCode,
	InboundApplicant,
	JobSearchBatch,
	JobSearchGoal,
	JobSourceDetail,
	JobSummary,
	PlatformSessionState,
	RecruitingApplicantBatch,
	RecruitingOpening,
	RecruitingProspectContext,
	WorkspaceKind,
)


class CredentialProvider(Protocol):
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState: ...
	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None: ...


ReadTransport = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
WriteTransport = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
RecruitingTransport = Callable[[str, str, dict[str, Any], dict[str, Any]], dict[str, Any]]


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


def _http_write(url: str, data: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
	"""Perform one request. Any transport ambiguity is terminal and never retried."""

	headers = {
		**endpoints.DEFAULT_HEADERS,
		"User-Agent": str(credential.get("user_agent", endpoints.DEFAULT_HEADERS.get("User-Agent", ""))),
		"Referer": endpoints.WEB_GEEK_JOB_URL,
	}
	try:
		response = httpx.post(
			url,
			data=data,
			cookies=credential.get("cookies", {}),
			headers=headers,
			follow_redirects=True,
			timeout=30,
		)
	except httpx.HTTPError as exc:
		raise BossAdapterFailure(ErrorCode.UNCERTAIN_REMOTE_OUTCOME) from exc
	if response.status_code in {401, 403}:
		raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
	if response.status_code == 429:
		raise BossAdapterFailure(ErrorCode.RATE_LIMITED)
	if response.status_code >= 500:
		raise BossAdapterFailure(ErrorCode.UNCERTAIN_REMOTE_OUTCOME)
	if response.status_code >= 400:
		raise BossAdapterFailure(ErrorCode.PLATFORM_RISK_CONTROL)
	try:
		payload = response.json()
	except ValueError as exc:
		raise BossAdapterFailure(ErrorCode.UNCERTAIN_REMOTE_OUTCOME) from exc
	if not isinstance(payload, dict):
		raise BossAdapterFailure(ErrorCode.UNCERTAIN_REMOTE_OUTCOME)
	return payload


def _http_recruiting(
	method: str,
	url: str,
	payload: dict[str, Any],
	credential: dict[str, Any],
) -> dict[str, Any]:
	headers = {
		**recruiter_ep.DEFAULT_HEADERS,
		"User-Agent": str(credential.get("user_agent", recruiter_ep.DEFAULT_HEADERS.get("User-Agent", ""))),
		"Referer": recruiter_ep.REFERER_MAP.get(url, recruiter_ep.WEB_BOSS_CHAT),
	}
	try:
		if method == "GET":
			response = httpx.get(
				url,
				params=payload,
				cookies=credential.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
		else:
			response = httpx.post(
				url,
				data=payload,
				cookies=credential.get("cookies", {}),
				headers=headers,
				follow_redirects=True,
				timeout=30,
			)
	except httpx.HTTPError as exc:
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
	if response.status_code in {401, 403}:
		raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
	if response.status_code == 429:
		raise BossAdapterFailure(ErrorCode.RATE_LIMITED)
	try:
		response.raise_for_status()
	except httpx.HTTPError as exc:
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
	try:
		body = response.json()
	except ValueError as exc:
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE) from exc
	if not isinstance(body, dict):
		raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
	return body


class BossReadAdapter:
	"""Make at most one BOSS request per explicit read or confirmed write action."""

	def __init__(
		self,
		sessions: CredentialProvider,
		*,
		transport: ReadTransport = _http_read,
		write_transport: WriteTransport = _http_write,
		recruiting_transport: RecruitingTransport = _http_recruiting,
	) -> None:
		self._sessions = sessions
		self._transport = transport
		self._write_transport = write_transport
		self._recruiting_transport = recruiting_transport
		self._security_ids: dict[str, str] = {}
		self._prospect_refs: dict[str, dict[str, Any]] = {}

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
		parsed = tuple(JobItem.from_api(item) for item in raw_items if isinstance(item, dict))
		self._security_ids = {item.job_id: item.security_id for item in parsed if item.job_id and item.security_id}
		items = tuple(self._summary(item) for item in parsed)
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

	def send_greeting(self, reference: str, message: str) -> None:
		security_id = self._security_ids.get(reference)
		if not security_id:
			raise BossAdapterFailure(
				ErrorCode.UNSUPPORTED_CAPABILITY,
				"No server-owned security identifier is available for this job",
			)
		try:
			response = self._write_transport(
				endpoints.GREET_URL,
				{"securityId": security_id, "jobId": reference, "greeting": message},
				self._credential(),
			)
		except BossAdapterFailure:
			raise
		except (OSError, RuntimeError) as exc:
			raise BossAdapterFailure(ErrorCode.UNCERTAIN_REMOTE_OUTCOME) from exc
		code = response.get("code")
		if code == endpoints.CODE_STOKEN_EXPIRED:
			raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
		if code == endpoints.CODE_RATE_LIMITED:
			raise BossAdapterFailure(ErrorCode.RATE_LIMITED)
		if code == endpoints.CODE_ACCOUNT_RISK:
			raise BossAdapterFailure(ErrorCode.PLATFORM_RISK_CONTROL)
		if code != endpoints.CODE_SUCCESS:
			raise BossAdapterFailure(ErrorCode.PLATFORM_RISK_CONTROL)

	@staticmethod
	def _recruiting_data(response: dict[str, Any]) -> dict[str, Any] | list[Any]:
		code = response.get("code")
		if code == recruiter_ep.CODE_STOKEN_EXPIRED:
			raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
		if code == recruiter_ep.CODE_RATE_LIMITED:
			raise BossAdapterFailure(ErrorCode.RATE_LIMITED)
		if code == recruiter_ep.CODE_ACCOUNT_RISK:
			raise BossAdapterFailure(ErrorCode.PLATFORM_RISK_CONTROL)
		if code not in {recruiter_ep.CODE_SUCCESS, 200}:
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
		data = response.get("zpData", response.get("data"))
		if not isinstance(data, (dict, list)):
			raise BossAdapterFailure(ErrorCode.ADAPTER_UNAVAILABLE)
		return data

	@staticmethod
	def _items(data: dict[str, Any] | list[Any], *keys: str) -> list[dict[str, Any]]:
		if isinstance(data, list):
			return [item for item in data if isinstance(item, dict)]
		for key in keys:
			value = data.get(key)
			if isinstance(value, list):
				return [item for item in value if isinstance(item, dict)]
		return []

	def _recruiting_read(self, method: str, url: str, payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
		return self._recruiting_data(
			self._recruiting_transport(method, url, payload, self._credential_for(WorkspaceKind.RECRUITING))
		)

	def _credential_for(self, workspace: WorkspaceKind) -> dict[str, Any]:
		credential = self._sessions.active_credential(workspace)
		if credential is None:
			raise BossAdapterFailure(ErrorCode.AUTHENTICATION_EXPIRED)
		return credential

	def list_openings(self) -> tuple[RecruitingOpening, ...]:
		data = self._recruiting_read("GET", recruiter_ep.BOSS_JOB_LIST_URL, {})
		return tuple(
			RecruitingOpening(
				reference=str(item.get("encryptJobId") or item.get("encJobId") or item.get("jobId") or ""),
				title=str(item.get("jobName") or item.get("title") or item.get("name") or "未命名职位"),
				status=str(item.get("statusDesc") or item.get("status") or ""),
			)
			for item in self._items(data, "jobList", "list", "result")
			if item.get("encryptJobId") or item.get("encJobId") or item.get("jobId")
		)

	def inbound_applicants(
		self,
		opening_reference: str,
		*,
		cancel_requested: Callable[[], bool],
	) -> Iterable[RecruitingApplicantBatch]:
		if cancel_requested():
			return
		data = self._recruiting_read(
			"POST",
			recruiter_ep.BOSS_FRIEND_LIST_URL,
			{"labelId": 0, "page": 1, "encJobId": opening_reference},
		)
		items: list[InboundApplicant] = []
		refs: dict[str, dict[str, Any]] = {}
		for raw in self._items(data, "friendList", "list", "result"):
			friend_id = raw.get("friendId")
			geek_id = raw.get("encryptUid") or raw.get("encryptGeekId") or raw.get("geekId")
			if friend_id is None or not geek_id:
				continue
			reference = f"prospect-{secrets.token_urlsafe(12)}"
			display_name = str(raw.get("name") or raw.get("geekName") or "未命名应聘者")
			headline = str(raw.get("expectPositionName") or raw.get("positionName") or raw.get("headline") or "")
			refs[reference] = {
				"friend_id": int(friend_id),
				"geek_id": str(geek_id),
				"security_id": str(raw.get("securityId") or ""),
				"display_name": display_name,
				"headline": headline,
			}
			items.append(
				InboundApplicant(
					reference=reference,
					display_name=display_name,
					headline=headline,
				)
			)
		self._prospect_refs = refs
		if not cancel_requested():
			yield RecruitingApplicantBatch(items=tuple(items), progress=100)

	def prospect_context(
		self,
		opening_reference: str,
		prospect_reference: str,
	) -> RecruitingProspectContext:
		identifiers = self._prospect_refs.get(prospect_reference)
		if identifiers is None:
			raise BossAdapterFailure(ErrorCode.UNSUPPORTED_CAPABILITY)
		resume = self._recruiting_read(
			"GET",
			recruiter_ep.BOSS_VIEW_GEEK_URL,
			{
				"encryptGeekId": identifiers["geek_id"],
				"encryptJobId": opening_reference,
				"securityId": identifiers["security_id"],
			},
		)
		chat = self._recruiting_read(
			"GET",
			recruiter_ep.BOSS_CHAT_HISTORY_URL,
			{"gid": identifiers["friend_id"], "c": 20, "src": 0},
		)
		prospect = next(
			(item for item in self._current_applicants() if item.reference == prospect_reference),
			InboundApplicant(prospect_reference, "招聘对象"),
		)
		return RecruitingProspectContext(
			prospect=prospect,
			resume_text=self._resume_text(resume),
			chat_messages=tuple(
				str(item.get("content") or item.get("text") or item.get("body") or "")
				for item in self._items(chat, "messages", "msgList", "historyMsgList")
				if item.get("content") or item.get("text") or item.get("body")
			),
			contact_details=self._contact_details(resume),
		)

	def _current_applicants(self) -> tuple[InboundApplicant, ...]:
		return tuple(
			InboundApplicant(
				reference=reference,
				display_name=str(values.get("display_name") or "招聘对象"),
				headline=str(values.get("headline") or ""),
			)
			for reference, values in self._prospect_refs.items()
		)

	@staticmethod
	def _resume_sections(data: dict[str, Any] | list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
		if not isinstance(data, dict):
			return {}, {}
		detail = data.get("geekDetailInfo", data)
		if not isinstance(detail, dict):
			return {}, {}
		base = detail.get("geekBaseInfo", {})
		expectation = detail.get("showExpectPosition", {})
		return (
			base if isinstance(base, dict) else {},
			expectation if isinstance(expectation, dict) else {},
		)

	@classmethod
	def _resume_text(cls, data: dict[str, Any] | list[Any]) -> str:
		base, expectation = cls._resume_sections(data)
		values = [
			base.get("degreeCategory"),
			base.get("workYearDesc"),
			base.get("ageDesc"),
			expectation.get("positionName"),
			expectation.get("salaryDesc"),
			expectation.get("locationName"),
		]
		return " · ".join(str(value) for value in values if value)

	@classmethod
	def _contact_details(cls, data: dict[str, Any] | list[Any]) -> tuple[str, ...]:
		base, _ = cls._resume_sections(data)
		return tuple(str(base[key]) for key in ("mobile", "phone", "wechat", "email") if base.get(key))

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
