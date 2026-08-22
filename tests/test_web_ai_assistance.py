from __future__ import annotations

from boss_agent_cli.application import AIAssistanceKind, AIAssistanceRequest
from boss_agent_cli.web.ai_assistance import ConfiguredAIAssistant


class _Store:
	def __init__(self, *, broken: bool = False) -> None:
		self.broken = broken

	def load_config(self):
		if self.broken:
			raise OSError("configuration unavailable")
		return {
			"ai_provider": "openai",
			"ai_model": "gpt-example",
			"ai_temperature": "invalid",
			"ai_max_tokens": "invalid",
		}

	def get_base_url(self):
		return "https://api.example.test/v1"

	def get_api_key(self):
		return "secret-key"


def test_configured_adapter_sends_text_only_structured_facts(monkeypatch) -> None:
	captured = {}

	class _Service:
		def __init__(self, base_url, api_key, model, temperature, max_tokens):
			captured["configuration"] = (base_url, api_key, model, temperature, max_tokens)

		def chat(self, messages, *, max_tokens):
			captured["messages"] = messages
			captured["request_max_tokens"] = max_tokens
			return "一条可编辑建议"

	monkeypatch.setattr("boss_agent_cli.web.ai_assistance.AIService", _Service)
	adapter = ConfiguredAIAssistant(_Store())
	request = AIAssistanceRequest(
		kind=AIAssistanceKind.JOB_MATCH,
		target_reference="job-1",
		facts=(("job_title", "Python 后端"),),
		data_sent=("当前职位的来源事实",),
	)

	assert adapter.suggest(request) == "一条可编辑建议"
	assert captured["configuration"] == (
		"https://api.example.test/v1",
		"secret-key",
		"gpt-example",
		0.7,
		4096,
	)
	assert "不得创建或确认任何平台写入" in captured["messages"][0]["content"]
	assert "Python 后端" in captured["messages"][1]["content"]


def test_broken_adapter_configuration_is_optional_and_safe() -> None:
	configuration = ConfiguredAIAssistant(_Store(broken=True)).configuration()

	assert configuration.configured is False
	assert configuration.provider is None
	assert configuration.endpoint is None
