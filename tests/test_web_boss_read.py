from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.application import BossAdapterFailure, ErrorCode, JobSearchGoal, PlatformSessionState, WorkspaceKind
from boss_agent_cli.web.boss_read import BossReadAdapter


class _Sessions:
	def probe_session(self, workspace: WorkspaceKind) -> PlatformSessionState:
		return PlatformSessionState.CONNECTED

	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None:
		return {"cookies": {"wt2": "secret"}, "user_agent": "test"}


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
