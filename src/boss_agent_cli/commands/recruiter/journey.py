"""CLI adapter for the shared Recruiting application contract."""

from __future__ import annotations

import json
from typing import Any

import click

from boss_agent_cli.application import DomainError
from boss_agent_cli.application.recruiting_surface import domain_error_payload
from boss_agent_cli.output import emit_error, emit_success
from boss_agent_cli.recruiting_runtime import create_recruiting_surface


@click.command("journey")
@click.option(
	"--input-json",
	default='{"action":"state"}',
	help="共享招聘 action，或含 steps 的 run；不传时查询当前状态",
)
@click.pass_context
def recruiting_journey_cmd(ctx: click.Context, input_json: str) -> None:
	"""运行与本地 Web 相同的招聘主流程（写入前必须显式确认）。"""

	try:
		payload: Any = json.loads(input_json)
	except json.JSONDecodeError:
		emit_error(
			"hr-journey",
			code="INVALID_COMMAND",
			message="--input-json must be valid JSON",
			recoverable=True,
			recovery_action="Pass one Recruiting action object",
		)
		return
	if not isinstance(payload, dict):
		emit_error(
			"hr-journey",
			code="INVALID_COMMAND",
			message="--input-json must contain an object",
			recoverable=True,
			recovery_action="Pass one Recruiting action object",
		)
		return

	surface = create_recruiting_surface(
		data_dir=ctx.obj["data_dir"],
		platform=ctx.obj.get("platform", "zhipin"),
		logger=ctx.obj.get("logger"),
		cdp_url=ctx.obj.get("cdp_url"),
	)
	try:
		result = surface.invoke(payload)
	except DomainError as exc:
		error = domain_error_payload(exc)
		emit_error(
			"hr-journey",
			code=str(error["code"]),
			message=str(error["message"]),
			recoverable=bool(error["recoverable"]),
			recovery_action=(str(error["recovery_action"]) if error["recovery_action"] is not None else None),
			details={"correlation_id": error["correlation_id"]},
		)
		return
	emit_success("hr-journey", result)


__all__ = ["recruiting_journey_cmd"]
