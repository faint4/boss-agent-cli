"""CLI adapter for the shared Job-Seeking application contract."""

from __future__ import annotations

import json
from typing import Any

import click

from boss_agent_cli.application import DomainError
from boss_agent_cli.application.core_journey_contract import JOB_SEEKING_CONTRACT
from boss_agent_cli.application.job_surface import domain_error_payload
from boss_agent_cli.job_runtime import create_job_seeking_surface
from boss_agent_cli.output import emit_error, emit_success


@click.command("job")
@click.option(
	"--input-json",
	default=JOB_SEEKING_CONTRACT.default_payload_json,
	help=JOB_SEEKING_CONTRACT.cli_option_help,
)
@click.pass_context
def job_cmd(ctx: click.Context, input_json: str) -> None:
	"""运行共享应用合同的求职主流程（支持一次调用内的多步骤 run）。"""

	try:
		payload: Any = json.loads(input_json)
	except json.JSONDecodeError:
		emit_error(
			"job",
			code="INVALID_COMMAND",
			message="--input-json must be valid JSON",
			recoverable=True,
			recovery_action="Pass one Job-Seeking action object",
		)
		return
	if not isinstance(payload, dict):
		emit_error(
			"job",
			code="INVALID_COMMAND",
			message="--input-json must contain an object",
			recoverable=True,
			recovery_action="Pass one Job-Seeking action object",
		)
		return
	if ctx.obj.get("platform", "zhipin") != "zhipin":
		emit_error(
			"job",
			code="UNSUPPORTED_CAPABILITY",
			message="The shared Job-Seeking application contract currently supports only zhipin",
			recoverable=True,
			recovery_action="Use --platform zhipin or a legacy platform-specific command",
		)
		return

	surface = create_job_seeking_surface(
		data_dir=ctx.obj["data_dir"],
		platform=ctx.obj.get("platform", "zhipin"),
		logger=ctx.obj.get("logger"),
	)
	try:
		result = surface.invoke(payload)
	except DomainError as exc:
		error = domain_error_payload(exc)
		emit_error(
			"job",
			code=str(error["code"]),
			message=str(error["message"]),
			recoverable=bool(error["recoverable"]),
			recovery_action=(str(error["recovery_action"]) if error["recovery_action"] is not None else None),
			details={"correlation_id": error["correlation_id"]},
		)
		return
	emit_success("job", result)


__all__ = ["job_cmd"]
