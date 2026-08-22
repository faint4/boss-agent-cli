"""Optional AI adapter for advisory local-Web suggestions."""

from __future__ import annotations

import json

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.ai.service import AIService, AIServiceError
from boss_agent_cli.application import AIAssistanceKind, AIAssistanceRequest, AIProviderConfiguration


class ConfiguredAIAssistant:
	"""Read application-owned AI configuration and return text-only suggestions."""

	def __init__(self, store: AIConfigStore) -> None:
		self._store = store
		self._configuration: AIProviderConfiguration | None = None
		self._api_key: str | None = None

	def configuration(self) -> AIProviderConfiguration:
		if self._configuration is not None:
			return self._configuration
		try:
			config = self._store.load_config()
			provider = str(config["ai_provider"]) if config.get("ai_provider") else None
			model = str(config["ai_model"]) if config.get("ai_model") else None
			endpoint = self._store.get_base_url()
			self._api_key = self._store.get_api_key()
		except (OSError, RuntimeError, TypeError, ValueError):
			self._configuration = AIProviderConfiguration(configured=False)
			return self._configuration
		self._configuration = AIProviderConfiguration(
			configured=bool(provider and model and endpoint and self._api_key),
			provider=provider,
			model=model,
			endpoint=endpoint,
		)
		return self._configuration

	def suggest(self, request: AIAssistanceRequest) -> str:
		configuration = self.configuration()
		api_key = self._api_key
		if not configuration.configured or not configuration.endpoint or not configuration.model or not api_key:
			raise RuntimeError("AI provider is not configured")
		config = self._store.load_config()
		try:
			temperature = float(config.get("ai_temperature", 0.7))
			max_tokens = min(max(int(config.get("ai_max_tokens", 4096)), 1), 4096)
		except (TypeError, ValueError):
			temperature = 0.7
			max_tokens = 4096
		service = AIService(
			configuration.endpoint,
			api_key,
			configuration.model,
			temperature=temperature,
			max_tokens=max_tokens,
		)
		instruction = {
			AIAssistanceKind.JOB_MATCH: "解释职位与求职目标的匹配点和需人工核对的缺口。",
			AIAssistanceKind.JOB_GREETING_DRAFT: "起草一条简洁、诚实、可编辑的求职招呼。",
			AIAssistanceKind.RECRUITING_REPLY_DRAFT: "起草一条尊重、具体、可编辑的招聘回复。",
		}[request.kind]
		messages = [
			{
				"role": "system",
				"content": (
					"你只提供文本建议。不得声称已发送、不得要求调用工具、不得创建或确认任何平台写入。"
				),
			},
			{
				"role": "user",
				"content": f"{instruction}\n仅依据以下数据：\n{json.dumps(dict(request.facts), ensure_ascii=False)}",
			},
		]
		try:
			return service.chat(messages, max_tokens=1200)
		except AIServiceError as exc:
			raise RuntimeError("AI provider request failed") from exc
