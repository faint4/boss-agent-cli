"""Authoritative public metadata for both Core Journey interfaces.

The application command dataclasses own behavior. This catalog owns their public
transport fields, validation constraints, action names, and adapter metadata so
Click, MCP, Web, schema output, and documentation cannot grow parallel contracts.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from boss_agent_cli.application.contracts import (
	ApplicationCommand,
	CancelRunCommand,
	CancelWriteIntentCommand,
	ConnectPlatformSessionCommand,
	ConfirmWriteIntentCommand,
	DomainError,
	InspectJobCommand,
	InspectRecruitingProspectCommand,
	JobSearchGoal,
	LoadRecruitingOpeningsCommand,
	LogoutPlatformSessionCommand,
	PrepareJobGreetingCommand,
	PrepareRecruitingReplyCommand,
	SelectRecruitingOpeningCommand,
	SetShortlistedCommand,
	StartInboundApplicantsCommand,
	StartJobSearchCommand,
	SwitchWorkspaceCommand,
	UpdateJobSearchGoalCommand,
	WorkspaceKind,
)


class ContractValidationError(ValueError):
	"""A public adapter payload diverged from the application contract."""


@dataclass(frozen=True)
class FieldDefinition:
	name: str
	type: str
	description: str
	required: bool = True
	default: object | None = None
	minimum_length: int | None = None
	maximum_length: int | None = None
	enum: tuple[str, ...] = ()
	items_type: str | None = None

	def json_schema(self) -> dict[str, object]:
		schema: dict[str, object] = {"type": self.type, "description": self.description}
		if self.default is not None:
			schema["default"] = self.default
		if self.minimum_length is not None:
			key = "minItems" if self.type == "array" else "minLength"
			schema[key] = self.minimum_length
		if self.maximum_length is not None:
			key = "maxItems" if self.type == "array" else "maxLength"
			schema[key] = self.maximum_length
		if self.enum:
			schema["enum"] = list(self.enum)
		if self.items_type is not None:
			schema["items"] = {"type": self.items_type}
		return schema

	def validate(self, payload: Mapping[str, object]) -> None:
		if self.name not in payload:
			if self.required:
				raise ContractValidationError(f"{self.name} is required")
			return
		value = payload[self.name]
		valid_type = {
			"string": isinstance(value, str),
			"boolean": isinstance(value, bool),
			"number": isinstance(value, (int, float)) and not isinstance(value, bool),
			"array": isinstance(value, Sequence) and not isinstance(value, (str, bytes)),
		}.get(self.type, False)
		if not valid_type:
			raise ContractValidationError(f"{self.name} must be a {self.type}")
		if isinstance(value, str) and self.minimum_length is not None and not value.strip():
			raise ContractValidationError(f"{self.name} is required")
		if isinstance(value, (str, list, tuple)):
			if self.minimum_length is not None and len(value) < self.minimum_length:
				raise ContractValidationError(f"{self.name} is required")
			if self.maximum_length is not None and len(value) > self.maximum_length:
				raise ContractValidationError(f"{self.name} is too long")
		if self.enum and value not in self.enum:
			raise ContractValidationError(f"{self.name} is not supported")


@dataclass(frozen=True)
class ActionDefinition:
	name: str
	description: str
	fields: tuple[FieldDefinition, ...] = ()


@dataclass(frozen=True)
class CoreJourneyContract:
	name: str
	cli_command: tuple[str, ...]
	cli_description: str
	cli_option_help: str
	mcp_tool_name: str
	mcp_description: str
	actions: tuple[ActionDefinition, ...]

	@property
	def action_names(self) -> tuple[str, ...]:
		return tuple(action.name for action in self.actions)

	@property
	def default_payload_json(self) -> str:
		return '{"action":"state"}'

	def action(self, name: str) -> ActionDefinition:
		for action in self.actions:
			if action.name == name:
				return action
		raise ContractValidationError("action is not supported")

	def validate_action(self, payload: Mapping[str, object]) -> str:
		action_value = payload.get("action", "state")
		if not isinstance(action_value, str):
			raise ContractValidationError("action is not supported")
		action = self.action(action_value)
		allowed = {"action", *(field.name for field in action.fields)}
		unexpected = set(payload) - allowed
		if unexpected:
			raise ContractValidationError(
				f"unexpected fields for {action.name}: {', '.join(sorted(unexpected))}"
			)
		for field in action.fields:
			field.validate(payload)
		if action.name == "run":
			steps = payload["steps"]
			if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
				raise ContractValidationError("steps must be a non-empty array")
			for step in steps:
				if not isinstance(step, Mapping) or step.get("action") == "run":
					raise ContractValidationError("each step must be a non-run action object")
				self.validate_action(step)
		return action.name

	def mcp_input_schema(self) -> dict[str, object]:
		properties: dict[str, object] = {
			"action": {
				"type": "string",
				"enum": list(self.action_names),
				"default": "state",
				"description": "共享应用合同 action",
			}
		}
		for action in self.actions:
			for field in action.fields:
				schema = field.json_schema()
				previous = properties.get(field.name)
				if previous is not None and previous != schema:
					raise RuntimeError(f"conflicting schema for {self.name}.{field.name}")
				properties[field.name] = schema
		return {"type": "object", "properties": properties, "required": []}

	def cli_option_schema(self) -> dict[str, object]:
		return {
			"type": "string",
			"default": self.default_payload_json,
			"description": self.cli_option_help,
		}


REFERENCE = FieldDefinition(
	"reference",
	"string",
	"应用层对象 reference；CLI/MCP 兼容入口可使用 $first",
	minimum_length=1,
	maximum_length=128,
)
INTENT_ID = FieldDefinition(
	"intent_id",
	"string",
	"应用层生成的 Write Intent ID；CLI/MCP 兼容入口可使用 $pending",
	minimum_length=1,
	maximum_length=128,
)
RUN_ID = FieldDefinition(
	"run_id",
	"string",
	"要取消的 Recoverable Run ID",
	minimum_length=1,
	maximum_length=128,
)
MESSAGE = FieldDefinition(
	"message",
	"string",
	"待人工确认的单条完整消息",
	minimum_length=1,
	maximum_length=1000,
)
TIMEOUT = FieldDefinition(
	"timeout",
	"number",
	"CLI/MCP 等待只读 run 到达终态的秒数，最大等待 120 秒",
	required=False,
	default=30,
)
STEPS = FieldDefinition(
	"steps",
	"array",
	"action=run 时按顺序执行的非 run action 对象",
	minimum_length=1,
	items_type="object",
)

OBJECTIVE = FieldDefinition("objective", "string", "求职目标", minimum_length=1)
KEYWORD = FieldDefinition("keyword", "string", "搜索关键词", minimum_length=1, maximum_length=1000)
CITY = FieldDefinition("city", "string", "城市", required=False, default="")
SALARY = FieldDefinition("salary", "string", "薪资范围", required=False, default="")
EXPERIENCE = FieldDefinition("experience", "string", "经验要求", required=False, default="")
EDUCATION = FieldDefinition("education", "string", "学历要求", required=False, default="")
SHORTLISTED = FieldDefinition("shortlisted", "boolean", "是否加入本地 Job Shortlist", default=True)


JOB_SEEKING_CONTRACT = CoreJourneyContract(
	name="job-seeking",
	cli_command=("job",),
	cli_description=(
		"通过 Web 同款共享应用合同运行求职主流程；单次 action 适合 MCP，"
		"CLI 完整流程使用 run + steps 保持进程内状态"
	),
	cli_option_help="Job-Seeking action object；action=run 时传 steps 数组",
	mcp_tool_name="boss_job",
	mcp_description="通过与本地 Web 相同的应用合同运行求职主流程；MCP 进程会保留搜索、详情和待确认写入状态",
	actions=(
		ActionDefinition("state", "读取 Job-Seeking 当前状态"),
		ActionDefinition("goal", "更新求职目标", (OBJECTIVE, KEYWORD, CITY, SALARY, EXPERIENCE, EDUCATION)),
		ActionDefinition("search", "启动职位搜索并等待终态", (TIMEOUT,)),
		ActionDefinition("cancel-search", "取消职位搜索", (RUN_ID,)),
		ActionDefinition("inspect", "查看一个职位", (REFERENCE,)),
		ActionDefinition("shortlist", "更新本地 Job Shortlist", (REFERENCE, SHORTLISTED)),
		ActionDefinition("prepare-greeting", "准备一个招呼 Write Intent", (REFERENCE, MESSAGE)),
		ActionDefinition("confirm", "确认一个 Write Intent", (INTENT_ID,)),
		ActionDefinition("cancel-write", "取消一个 Write Intent", (INTENT_ID,)),
		ActionDefinition("run", "在同一进程中执行多步 Core Journey", (STEPS,)),
	),
)


RECRUITING_CONTRACT = CoreJourneyContract(
	name="recruiting",
	cli_command=("hr", "journey"),
	cli_description=(
		"共享招聘主流程（openings → applicants → inspect → prepare-reply → confirm/cancel）"
	),
	cli_option_help="Recruiting action object；action=run 时传 steps 数组",
	mcp_tool_name="boss_hr_journey",
	mcp_description=(
		"通过与本地 Web 相同的应用合同运行招聘主流程；读取候选人上下文后，"
		"回复必须先 prepare-reply 再 confirm，MCP 进程仅在内存中保留敏感上下文"
	),
	actions=(
		ActionDefinition("state", "读取 Recruiting 当前状态"),
		ActionDefinition("openings", "读取招聘职位"),
		ActionDefinition("select-opening", "选择一个招聘职位", (REFERENCE,)),
		ActionDefinition("applicants", "启动 Inbound Applicant 读取并等待终态", (TIMEOUT,)),
		ActionDefinition("cancel-applicants", "取消 Inbound Applicant 读取", (RUN_ID,)),
		ActionDefinition("inspect", "按需读取一个 Recruiting Prospect", (REFERENCE,)),
		ActionDefinition("prepare-reply", "准备一个回复 Write Intent", (REFERENCE, MESSAGE)),
		ActionDefinition("confirm", "确认一个 Write Intent", (INTENT_ID,)),
		ActionDefinition("cancel-write", "取消一个 Write Intent", (INTENT_ID,)),
		ActionDefinition("run", "在同一进程中执行多步 Core Journey", (STEPS,)),
	),
)


CORE_JOURNEY_CONTRACTS = {
	JOB_SEEKING_CONTRACT.name: JOB_SEEKING_CONTRACT,
	RECRUITING_CONTRACT.name: RECRUITING_CONTRACT,
}
CORE_JOURNEY_BY_MCP_TOOL = {
	contract.mcp_tool_name: contract for contract in CORE_JOURNEY_CONTRACTS.values()
}


WORKSPACE = FieldDefinition(
	"workspace",
	"string",
	"要切换到的 Workspace",
	minimum_length=1,
	enum=tuple(kind.value for kind in WorkspaceKind),
)
REQUEST_ID = FieldDefinition(
	"request_id",
	"string",
	"客户端生成的请求标识",
	minimum_length=1,
	maximum_length=128,
)


@dataclass(frozen=True)
class WebCommandDefinition:
	path: str
	fields: tuple[FieldDefinition, ...]
	factory: Callable[[Mapping[str, object]], ApplicationCommand]

	def build(self, payload: Mapping[str, object]) -> ApplicationCommand:
		allowed = {REQUEST_ID.name, *(field.name for field in self.fields)}
		unexpected = set(payload) - allowed
		if unexpected:
			raise ContractValidationError(
				f"unexpected fields for {self.path}: {', '.join(sorted(unexpected))}"
			)
		required = {REQUEST_ID.name, *(field.name for field in self.fields if field.required)}
		missing = required - set(payload)
		if missing:
			raise ContractValidationError(f"{', '.join(sorted(missing))} is required")
		REQUEST_ID.validate(payload)
		for field in self.fields:
			field.validate(payload)
		return self.factory(payload)


def _job_goal(payload: Mapping[str, object]) -> UpdateJobSearchGoalCommand:
	return UpdateJobSearchGoalCommand(
		goal=JobSearchGoal(
			objective=str(payload["objective"]),
			keyword=str(payload["keyword"]),
			city=str(payload.get("city", "")),
			salary=str(payload.get("salary", "")),
			experience=str(payload.get("experience", "")),
			education=str(payload.get("education", "")),
		)
	)


WEB_COMMANDS = {
	definition.path: definition
	for definition in (
		WebCommandDefinition(
			"/api/v1/commands/switch-workspace",
			(WORKSPACE,),
			lambda payload: SwitchWorkspaceCommand(WorkspaceKind(str(payload["workspace"]))),
		),
		WebCommandDefinition(
			"/api/v1/commands/connect-platform-session", (), lambda payload: ConnectPlatformSessionCommand()
		),
		WebCommandDefinition(
			"/api/v1/commands/logout-platform-session", (), lambda payload: LogoutPlatformSessionCommand()
		),
		WebCommandDefinition(
			"/api/v1/commands/update-job-search-goal",
			(OBJECTIVE, KEYWORD, CITY, SALARY, EXPERIENCE, EDUCATION),
			_job_goal,
		),
		WebCommandDefinition(
			"/api/v1/commands/start-job-search", (), lambda payload: StartJobSearchCommand()
		),
		WebCommandDefinition(
			"/api/v1/commands/load-recruiting-openings", (), lambda payload: LoadRecruitingOpeningsCommand()
		),
		WebCommandDefinition(
			"/api/v1/commands/select-recruiting-opening",
			(REFERENCE,),
			lambda payload: SelectRecruitingOpeningCommand(str(payload["reference"])),
		),
		WebCommandDefinition(
			"/api/v1/commands/start-inbound-applicants", (), lambda payload: StartInboundApplicantsCommand()
		),
		WebCommandDefinition(
			"/api/v1/commands/inspect-recruiting-prospect",
			(REFERENCE,),
			lambda payload: InspectRecruitingProspectCommand(str(payload["reference"])),
		),
		WebCommandDefinition(
			"/api/v1/commands/cancel-run",
			(RUN_ID,),
			lambda payload: CancelRunCommand(str(payload["run_id"])),
		),
		WebCommandDefinition(
			"/api/v1/commands/inspect-job",
			(REFERENCE,),
			lambda payload: InspectJobCommand(str(payload["reference"])),
		),
		WebCommandDefinition(
			"/api/v1/commands/set-shortlisted",
			(REFERENCE, SHORTLISTED),
			lambda payload: SetShortlistedCommand(str(payload["reference"]), bool(payload["shortlisted"])),
		),
		WebCommandDefinition(
			"/api/v1/commands/prepare-job-greeting",
			(REFERENCE, MESSAGE),
			lambda payload: PrepareJobGreetingCommand(str(payload["reference"]), str(payload["message"])),
		),
		WebCommandDefinition(
			"/api/v1/commands/prepare-recruiting-reply",
			(REFERENCE, MESSAGE),
			lambda payload: PrepareRecruitingReplyCommand(str(payload["reference"]), str(payload["message"])),
		),
		WebCommandDefinition(
			"/api/v1/write-intents/confirm",
			(INTENT_ID,),
			lambda payload: ConfirmWriteIntentCommand(str(payload["intent_id"])),
		),
		WebCommandDefinition(
			"/api/v1/write-intents/cancel",
			(INTENT_ID,),
			lambda payload: CancelWriteIntentCommand(str(payload["intent_id"])),
		),
	)
}


def build_web_command(path: str, payload: Mapping[str, object]) -> ApplicationCommand:
	try:
		definition = WEB_COMMANDS[path]
	except KeyError as exc:
		raise ContractValidationError("command path is not supported") from exc
	return definition.build(payload)


def json_value(value: object) -> object:
	"""Convert application values to the stable JSON-compatible representation."""

	if isinstance(value, Enum):
		return value.value
	if isinstance(value, datetime):
		return value.isoformat()
	if dataclasses.is_dataclass(value) and not isinstance(value, type):
		return {key: json_value(item) for key, item in dataclasses.asdict(value).items()}
	if isinstance(value, Mapping):
		return {str(key): json_value(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [json_value(item) for item in value]
	return value


def domain_error_payload(error: DomainError) -> dict[str, object]:
	"""Return one transport-neutral domain error shape for CLI and MCP."""

	return {
		"code": error.code.value,
		"message": error.message,
		"recoverable": error.recoverable,
		"recovery_action": error.recovery_action,
		"correlation_id": error.correlation_id,
	}


__all__ = [
	"CORE_JOURNEY_BY_MCP_TOOL",
	"CORE_JOURNEY_CONTRACTS",
	"JOB_SEEKING_CONTRACT",
	"RECRUITING_CONTRACT",
	"WEB_COMMANDS",
	"ContractValidationError",
	"CoreJourneyContract",
	"build_web_command",
	"domain_error_payload",
	"json_value",
]
