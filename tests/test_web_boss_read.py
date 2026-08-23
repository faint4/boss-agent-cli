from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.application import BossAdapterFailure, ErrorCode, JobSearchGoal, PlatformSessionState, WorkspaceKind
from boss_agent_cli.web.boss_read import BossReadAdapter, _http_read


class _Sessions:
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return PlatformSessionState.CONNECTED

	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None:
		return {"cookies": {"wt2": "secret"}, "stoken": "dynamic-token", "user_agent": "test"}


def test_http_read_adds_dynamic_token_without_mutating_caller_params() -> None:
	response = MagicMock()
	response.status_code = 200
	response.json.return_value = {"code": 0, "zpData": {"jobList": []}}
	response.raise_for_status.return_value = None
	params = {"query": "Python", "page": 1}

	with patch("boss_agent_cli.web.boss_read.httpx.get", return_value=response) as get:
		_http_read(
			endpoints.SEARCH_URL,
			params,
			{"cookies": {"wt2": "secret"}, "stoken": "dynamic-token", "user_agent": "test"},
		)

	assert params == {"query": "Python", "page": 1}
	assert get.call_args.kwargs["params"] == {
		"query": "Python",
		"page": 1,
		"__zp_stoken__": "dynamic-token",
	}


def test_http_read_rejects_incomplete_credentials_without_remote_request() -> None:
	with patch("boss_agent_cli.web.boss_read.httpx.get") as get:
		with pytest.raises(BossAdapterFailure) as raised:
			_http_read(
				endpoints.SEARCH_URL,
				{"query": "Python", "page": 1},
				{"cookies": {"wt2": "secret"}, "stoken": "", "user_agent": "test"},
			)

	assert raised.value.code is ErrorCode.AUTHENTICATION_EXPIRED
	get.assert_not_called()


def test_boss_read_adapter_maps_one_bounded_search_without_exposing_security_id() -> None:
	calls: list[tuple[str, dict[str, Any]]] = []

	def transport(url: str, params: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
		calls.append((url, params))
		return {
			"code": 0,
			"zpData": {
				"jobList": [
					{
						"encryptJobId": "job-1",
						"jobName": "Python 工程师",
						"brandName": "示例科技",
						"cityName": "上海",
						"salaryDesc": "20-40K",
						"securityId": "must-not-leak",
					}
				],
			},
		}

	adapter = BossReadAdapter(_Sessions(), transport=transport)
	batches = tuple(
		adapter.search_jobs(
			JobSearchGoal("后端岗位", "Python", city="上海"),
			cancel_requested=lambda: False,
		)
	)

	assert len(calls) == 1
	assert calls[0][0] == endpoints.SEARCH_URL
	assert calls[0][1]["page"] == 1
	assert batches[0].items[0].reference == "job-1"
	assert "security" not in repr(batches[0]).lower()


def test_boss_adapter_sends_one_greeting_with_server_owned_security_id() -> None:
	read_calls: list[tuple[str, dict[str, Any]]] = []
	write_calls: list[tuple[str, dict[str, Any]]] = []

	def read_transport(url: str, params: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
		read_calls.append((url, params))
		return {
			"code": 0,
			"zpData": {
				"jobList": [{"encryptJobId": "job-1", "jobName": "工程师", "securityId": "secret-1"}]
			},
		}

	def write_transport(url: str, data: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
		write_calls.append((url, data))
		return {"code": 0, "zpData": {}}

	adapter = BossReadAdapter(_Sessions(), transport=read_transport, write_transport=write_transport)
	tuple(adapter.search_jobs(JobSearchGoal("后端", "Python"), cancel_requested=lambda: False))

	adapter.send_greeting("job-1", "您好")

	assert len(read_calls) == 1
	assert write_calls == [
		(endpoints.GREET_URL, {"securityId": "secret-1", "jobId": "job-1", "greeting": "您好"})
	]


def test_boss_adapter_reads_detail_with_server_owned_security_id() -> None:
	calls: list[tuple[str, dict[str, Any]]] = []

	def transport(url: str, params: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
		calls.append((url, params))
		if url == endpoints.SEARCH_URL:
			return {
				"code": 0,
				"zpData": {
					"jobList": [{"encryptJobId": "job-1", "jobName": "工程师", "securityId": "secret-1"}]
				},
			}
		return {
			"code": 0,
			"zpData": {
				"jobInfo": {"encryptJobId": "job-1", "jobName": "工程师"},
				"bossInfo": {},
				"brandComInfo": {},
			},
		}

	adapter = BossReadAdapter(_Sessions(), transport=transport)
	tuple(adapter.search_jobs(JobSearchGoal("后端", "Python"), cancel_requested=lambda: False))

	detail = adapter.job_detail("job-1")

	assert detail.job.reference == "job-1"
	assert calls[1] == (
		endpoints.DETAIL_URL,
		{"encryptJobId": "job-1", "securityId": "secret-1"},
	)


def test_boss_adapter_will_not_read_detail_without_server_owned_security_id() -> None:
	reads: list[object] = []
	adapter = BossReadAdapter(
		_Sessions(),
		transport=lambda *args: reads.append(args) or {"code": 0, "zpData": {}},
	)

	with pytest.raises(BossAdapterFailure) as raised:
		adapter.job_detail("forged-job")

	assert raised.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
	assert reads == []


def test_boss_adapter_will_not_write_without_server_owned_security_id() -> None:
	writes: list[object] = []
	adapter = BossReadAdapter(
		_Sessions(),
		transport=lambda url, params, credential: {"code": 0, "zpData": {"jobList": []}},
		write_transport=lambda *args: writes.append(args) or {"code": 0},
	)

	with pytest.raises(BossAdapterFailure) as raised:
		adapter.send_greeting("forged-job", "您好")

	assert raised.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
	assert writes == []


def test_boss_adapter_treats_write_transport_failure_as_uncertain_without_retry() -> None:
	writes = 0

	def write_transport(url: str, data: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
		nonlocal writes
		writes += 1
		raise OSError("connection reset after send")

	adapter = BossReadAdapter(
		_Sessions(),
		transport=lambda url, params, credential: {
			"code": 0,
			"zpData": {"jobList": [{"encryptJobId": "job-1", "securityId": "secret-1"}]},
		},
		write_transport=write_transport,
	)
	tuple(adapter.search_jobs(JobSearchGoal("后端", "Python"), cancel_requested=lambda: False))

	with pytest.raises(BossAdapterFailure) as raised:
		adapter.send_greeting("job-1", "您好")

	assert raised.value.code is ErrorCode.UNCERTAIN_REMOTE_OUTCOME
	assert writes == 1


@pytest.mark.parametrize(
	("platform_code", "expected"),
	[
		(endpoints.CODE_STOKEN_EXPIRED, ErrorCode.AUTHENTICATION_EXPIRED),
		(endpoints.CODE_RATE_LIMITED, ErrorCode.RATE_LIMITED),
		(endpoints.CODE_ACCOUNT_RISK, ErrorCode.PLATFORM_RISK_CONTROL),
	],
)
def test_boss_read_adapter_stops_on_platform_safety_responses(platform_code: int, expected: ErrorCode) -> None:
	adapter = BossReadAdapter(
		_Sessions(),
		transport=lambda url, params, credential: {"code": platform_code, "message": "stop"},
	)

	with pytest.raises(BossAdapterFailure) as raised:
		tuple(adapter.search_jobs(JobSearchGoal("后端", "Python"), cancel_requested=lambda: False))

	assert raised.value.code is expected
