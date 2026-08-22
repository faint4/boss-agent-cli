"""Drift detection for the authoritative Core Journey contract."""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.application.contracts import (
	PrepareRecruitingReplyCommand,
	UpdateJobSearchGoalCommand,
)
from boss_agent_cli.application.core_journey_contract import (
	CORE_JOURNEY_CONTRACTS,
	JOB_SEEKING_CONTRACT,
	RECRUITING_CONTRACT,
	ContractValidationError,
	build_web_command,
)
from boss_agent_cli.commands.schema import SCHEMA_DATA
from boss_agent_cli.main import cli


def _install_mcp_stub() -> None:
	mcp = types.ModuleType("mcp")
	mcp_types = types.ModuleType("mcp.types")
	mcp_types.Tool = type("Tool", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
	mcp.types = mcp_types
	sys.modules.setdefault("mcp", mcp)
	sys.modules.setdefault("mcp.types", mcp_types)


def test_contract_is_the_single_action_and_field_catalog() -> None:
	assert tuple(CORE_JOURNEY_CONTRACTS) == ("job-seeking", "recruiting")
	assert JOB_SEEKING_CONTRACT.action_names == (
		"state",
		"goal",
		"search",
		"cancel-search",
		"inspect",
		"shortlist",
		"prepare-greeting",
		"confirm",
		"cancel-write",
		"run",
	)
	assert RECRUITING_CONTRACT.action_names == (
		"state",
		"openings",
		"select-opening",
		"applicants",
		"cancel-applicants",
		"inspect",
		"prepare-reply",
		"confirm",
		"cancel-write",
		"run",
	)

	job_schema = JOB_SEEKING_CONTRACT.mcp_input_schema()
	assert job_schema["properties"]["action"]["enum"] == list(JOB_SEEKING_CONTRACT.action_names)
	assert job_schema["properties"]["message"]["maxLength"] == 1000
	assert job_schema["properties"]["reference"]["maxLength"] == 128
	assert job_schema["required"] == []


def test_contract_rejects_drift_before_any_adapter_runs() -> None:
	with pytest.raises(ContractValidationError, match="unexpected fields"):
		JOB_SEEKING_CONTRACT.validate_action(
			{"action": "confirm", "intent_id": "intent-1", "message": "forged"}
		)
	with pytest.raises(ContractValidationError, match="message is required"):
		RECRUITING_CONTRACT.validate_action(
			{"action": "prepare-reply", "reference": "prospect-1", "message": ""}
		)
	with pytest.raises(ContractValidationError, match="timeout must be a number"):
		RECRUITING_CONTRACT.validate_action({"action": "applicants", "timeout": True})
	with pytest.raises(ContractValidationError, match="objective is required"):
		JOB_SEEKING_CONTRACT.validate_action(
			{
				"action": "goal",
				"keyword": "Python",
				"city": "",
				"salary": "",
				"experience": "",
				"education": "",
			}
		)


def test_mcp_tools_derive_from_the_authoritative_contract() -> None:
	_install_mcp_stub()
	from boss_agent_cli.mcp_tools import TOOLS

	tools = {tool.name: tool for tool in TOOLS}
	for contract in CORE_JOURNEY_CONTRACTS.values():
		tool = tools[contract.mcp_tool_name]
		assert tool.description.startswith(contract.mcp_description)
		assert tool.inputSchema == contract.mcp_input_schema()


def test_click_and_schema_metadata_derive_from_the_authoritative_contract() -> None:
	job_help = CliRunner().invoke(cli, ["job", "--help"])
	recruiting_help = CliRunner().invoke(cli, ["hr", "journey", "--help"])
	assert job_help.exit_code == recruiting_help.exit_code == 0
	assert JOB_SEEKING_CONTRACT.cli_option_help in job_help.output
	assert RECRUITING_CONTRACT.cli_option_help in recruiting_help.output

	job_schema = SCHEMA_DATA["commands"]["job"]
	assert job_schema["description"] == JOB_SEEKING_CONTRACT.cli_description
	assert job_schema["options"]["--input-json"] == JOB_SEEKING_CONTRACT.cli_option_schema()
	assert (
		SCHEMA_DATA["commands"]["hr"]["subcommands"]["journey"]
		== RECRUITING_CONTRACT.cli_description
	)


def test_web_builds_commands_from_the_same_field_definitions() -> None:
	goal = build_web_command(
		"/api/v1/commands/update-job-search-goal",
		{
			"request_id": "request-1",
			"objective": "寻找后端岗位",
			"keyword": "Python",
		},
	)
	assert isinstance(goal, UpdateJobSearchGoalCommand)
	assert goal.goal.keyword == "Python"
	assert goal.goal.city == ""

	reply = build_web_command(
		"/api/v1/commands/prepare-recruiting-reply",
		{"request_id": "request-2", "reference": "prospect-1", "message": "您好"},
	)
	assert isinstance(reply, PrepareRecruitingReplyCommand)

	with pytest.raises(ContractValidationError, match="unexpected fields"):
		build_web_command(
			"/api/v1/commands/prepare-recruiting-reply",
			{
				"request_id": "request-3",
				"reference": "prospect-1",
				"message": "您好",
				"intent_id": "forged",
			},
		)


def test_documentation_action_metadata_cannot_drift() -> None:
	root = Path(__file__).resolve().parents[1]
	for relative in ("docs/commands.md", "docs/commands.en.md"):
		content = (root / relative).read_text(encoding="utf-8")
		for contract in CORE_JOURNEY_CONTRACTS.values():
			match = re.search(
				rf"<!-- core-journey-actions:{re.escape(contract.name)} -->([^\n]+)",
				content,
			)
			assert match is not None
			assert match.group(1).strip() == ", ".join(contract.action_names)


def test_mcp_aliases_are_confined_to_the_adapter_mapping() -> None:
	from boss_agent_cli.mcp_args import _build_args

	for contract in CORE_JOURNEY_CONTRACTS.values():
		payload = {"action": "state"}
		args = _build_args(contract.mcp_tool_name, payload)
		assert args == [
			*contract.cli_command,
			"--input-json",
			json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
		]

	# Legacy commands remain separate Click adapters; they are not canonical action aliases.
	assert {"search", "detail", "greet"}.issubset(cli.commands)
	assert {"detail", "greet"}.isdisjoint(JOB_SEEKING_CONTRACT.action_names)
	assert "reply" in cli.commands["hr"].commands
	assert "reply" not in RECRUITING_CONTRACT.action_names


def test_surface_modules_no_longer_redeclare_action_field_tables() -> None:
	root = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli" / "application"
	for relative in ("job_surface.py", "recruiting_surface.py"):
		source = (root / relative).read_text(encoding="utf-8")
		assert "_ACTION_FIELDS" not in source
