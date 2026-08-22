from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.application import BossAdapterFailure, ErrorCode, WorkspaceKind
from boss_agent_cli.web.boss_read import BossReadAdapter


class _Sessions:
	def probe_session(self, workspace: WorkspaceKind):
		return "connected"

	def active_credential(self, workspace: WorkspaceKind) -> dict[str, Any] | None:
		return {"cookies": {"wt2": "secret"}, "user_agent": "test"}


def test_recruiting_adapter_keeps_platform_identifiers_server_owned_and_loads_context_on_demand() -> None:
	calls: list[tuple[str, str, dict[str, Any]]] = []

	def transport(method: str, url: str, payload: dict[str, Any], credential: dict[str, Any]):
		calls.append((method, url, payload))
		if url == ep.BOSS_JOB_LIST_URL:
			return {
				"code": 0,
				"zpData": {"jobList": [{"encryptJobId": "enc-job-1", "jobName": "后端工程师", "statusDesc": "招聘中"}]},
			}
		if url == ep.BOSS_FRIEND_LIST_URL:
			return {
				"code": 0,
				"zpData": {
					"friendList": [
						{
							"friendId": 12345,
							"name": "招聘对象甲",
							"encryptUid": "enc-geek-secret",
							"securityId": "security-secret",
							"expectPositionName": "Python 工程师",
						}
					]
				},
			}
		if url == ep.BOSS_VIEW_GEEK_URL:
			return {
				"code": 0,
				"zpData": {
					"geekDetailInfo": {
						"geekBaseInfo": {"name": "招聘对象甲", "degreeCategory": "本科", "workYearDesc": "5年"},
						"showExpectPosition": {"positionName": "Python 工程师", "salaryDesc": "30-40K"},
					}
				},
			}
		if url == ep.BOSS_CHAT_HISTORY_URL:
			return {"code": 0, "zpData": {"messages": [{"content": "您好，我对岗位感兴趣"}]}}
		raise AssertionError(url)

	adapter = BossReadAdapter(_Sessions(), recruiting_transport=transport)
	openings = adapter.list_openings()
	assert openings[0].reference == "enc-job-1"
	batches = tuple(adapter.inbound_applicants("enc-job-1", cancel_requested=lambda: False))
	prospect = batches[0].items[0]
	assert prospect.display_name == "招聘对象甲"
	assert "12345" not in repr(prospect)
	assert "security-secret" not in repr(prospect)
	assert [call[1] for call in calls] == [ep.BOSS_JOB_LIST_URL, ep.BOSS_FRIEND_LIST_URL]

	context = adapter.prospect_context("enc-job-1", prospect.reference)
	assert "本科" in context.resume_text
	assert context.chat_messages == ("您好，我对岗位感兴趣",)
	assert [call[1] for call in calls] == [
		ep.BOSS_JOB_LIST_URL,
		ep.BOSS_FRIEND_LIST_URL,
		ep.BOSS_VIEW_GEEK_URL,
		ep.BOSS_CHAT_HISTORY_URL,
	]
	assert "security-secret" not in repr(context)


@pytest.mark.parametrize(
	("platform_code", "expected"),
	[(37, ErrorCode.AUTHENTICATION_EXPIRED), (9, ErrorCode.RATE_LIMITED), (36, ErrorCode.PLATFORM_RISK_CONTROL)],
)
def test_recruiting_adapter_maps_platform_stop_codes(platform_code: int, expected: ErrorCode) -> None:
	adapter = BossReadAdapter(
		_Sessions(),
		recruiting_transport=lambda method, url, payload, credential: {"code": platform_code},
	)

	with pytest.raises(BossAdapterFailure) as raised:
		adapter.list_openings()

	assert raised.value.code is expected


def test_recruiting_reply_uses_server_owned_friend_id_and_executes_transport_once() -> None:
	writes: list[tuple[int, str]] = []

	def read_transport(method: str, url: str, payload: dict[str, Any], credential: dict[str, Any]):
		return {
			"code": 0,
			"zpData": {
				"friendList": [
					{
						"friendId": 98765,
						"name": "招聘对象甲",
						"encryptUid": "enc-geek-secret",
						"securityId": "security-secret",
					}
				]
			},
		}

	def write_transport(friend_id: int, message: str, credential: dict[str, Any]):
		writes.append((friend_id, message))
		return {"code": 0, "message": "Success"}

	adapter = BossReadAdapter(
		_Sessions(), recruiting_transport=read_transport, recruiting_write_transport=write_transport
	)
	prospect = tuple(adapter.inbound_applicants("opening-1", cancel_requested=lambda: False))[0].items[0]

	adapter.send_recruiting_reply(prospect.reference, "仅发送一次")

	assert writes == [(98765, "仅发送一次")]
	assert "98765" not in repr(prospect)


@pytest.mark.parametrize(
	("platform_code", "expected"),
	[
		(37, ErrorCode.AUTHENTICATION_EXPIRED),
		(9, ErrorCode.RATE_LIMITED),
		(36, ErrorCode.PLATFORM_RISK_CONTROL),
		(-1, ErrorCode.UNCERTAIN_REMOTE_OUTCOME),
	],
)
def test_recruiting_reply_failure_is_typed_and_never_retried(platform_code: int, expected: ErrorCode) -> None:
	writes = 0

	def read_transport(method: str, url: str, payload: dict[str, Any], credential: dict[str, Any]):
		return {"code": 0, "zpData": {"friendList": [{"friendId": 98765, "encryptUid": "geek-1"}]}}

	def write_transport(friend_id: int, message: str, credential: dict[str, Any]):
		nonlocal writes
		writes += 1
		return {"code": platform_code, "message": "sensitive platform response must not escape"}

	adapter = BossReadAdapter(
		_Sessions(), recruiting_transport=read_transport, recruiting_write_transport=write_transport
	)
	prospect = tuple(adapter.inbound_applicants("opening-1", cancel_requested=lambda: False))[0].items[0]

	with pytest.raises(BossAdapterFailure) as raised:
		adapter.send_recruiting_reply(prospect.reference, "仅发送一次")

	assert raised.value.code is expected
	assert "sensitive platform response" not in str(raised.value)
	assert writes == 1


@pytest.mark.parametrize(
	("status_code", "expected"),
	[(401, ErrorCode.AUTHENTICATION_EXPIRED), (403, ErrorCode.AUTHENTICATION_EXPIRED), (429, ErrorCode.RATE_LIMITED)],
)
def test_recruiting_http_status_is_mapped_before_parsing_non_json(
	monkeypatch, status_code: int, expected: ErrorCode
) -> None:
	class Response:
		def __init__(self) -> None:
			self.status_code = status_code

		def json(self):
			raise ValueError("HTML response")

		def raise_for_status(self) -> None:
			raise AssertionError("status should be mapped before raise_for_status")

	monkeypatch.setattr("boss_agent_cli.web.boss_read.httpx.get", lambda *args, **kwargs: Response())
	adapter = BossReadAdapter(_Sessions())

	with pytest.raises(BossAdapterFailure) as raised:
		adapter.list_openings()

	assert raised.value.code is expected
