"""Thin Recruiting surface contract shared by CLI and MCP adapters.

The application module owns workflow, privacy, and write-confirmation policy. This
module only validates transport-friendly actions and invokes the same commands and
query used by the local Web surface.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import Enum

from boss_agent_cli.application.contracts import (
	ApplicationStateSnapshot,
	CancelRunCommand,
	CancelWriteIntentCommand,
	ConfirmWriteIntentCommand,
	CurrentStateQuery,
	DomainError,
	ErrorCode,
	InspectRecruitingProspectCommand,
	LoadRecruitingOpeningsCommand,
	PrepareRecruitingReplyCommand,
	RequestContext,
	SelectRecruitingOpeningCommand,
	StartInboundApplicantsCommand,
)
from boss_agent_cli.application.module import Application


RECRUITING_ACTIONS = (
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
_TERMINAL_RUN_STATES = frozenset({"cancelled", "completed", "recovery"})
_ACTION_FIELDS = {
	"state": frozenset({"action"}),
	"openings": frozenset({"action"}),
	"select-opening": frozenset({"action", "reference"}),
	"applicants": frozenset({"action", "timeout"}),
	"cancel-applicants": frozenset({"action", "run_id"}),
	"inspect": frozenset({"action", "reference"}),
	"prepare-reply": frozenset({"action", "reference", "message"}),
	"confirm": frozenset({"action", "intent_id"}),
	"cancel-write": frozenset({"action", "intent_id"}),
	"run": frozenset({"action", "steps"}),
}


def json_value(value: object) -> object:
	"""Convert application values to a stable JSON-compatible representation."""

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


class RecruitingSurface:
	"""Parse and render only; delegate all state transitions to ``Application``."""

	def __init__(
		self,
		application: Application,
		*,
		local_session_id: str,
		correlation_id_factory: Callable[[], str],
	) -> None:
		self.application = application
		self._local_session_id = local_session_id
		self._correlation_id_factory = correlation_id_factory

	def _context(self) -> RequestContext:
		return RequestContext(
			local_session_id=self._local_session_id,
			correlation_id=self._correlation_id_factory(),
		)

	@staticmethod
	def _invalid(context: RequestContext, message: str) -> DomainError:
		return DomainError(
			code=ErrorCode.INVALID_COMMAND,
			message=message,
			correlation_id=context.correlation_id,
			recoverable=True,
			recovery_action="Review the Recruiting action input",
		)

	@staticmethod
	def _required_text(
		payload: Mapping[str, object],
		field: str,
		context: RequestContext,
		*,
		max_length: int = 128,
	) -> str:
		value = payload.get(field)
		if not isinstance(value, str) or not value.strip() or len(value) > max_length:
			raise RecruitingSurface._invalid(context, f"{field} is required")
		return value

	def _wait_for_applicants(self, context: RequestContext, timeout: float) -> ApplicationStateSnapshot:
		deadline = time.monotonic() + max(0.01, min(timeout, 120.0))
		while time.monotonic() < deadline:
			snapshot = self.application.query(CurrentStateQuery(), context)
			run = snapshot.active_run
			if run is not None and run.state in _TERMINAL_RUN_STATES:
				return snapshot
			time.sleep(0.01)
		raise DomainError(
			code=ErrorCode.INVALID_TRANSITION,
			message="Inbound Applicant read is still running",
			correlation_id=context.correlation_id,
			recoverable=True,
			recovery_action="Query state or cancel the active applicant read",
		)

	def _opening_reference(self, payload: Mapping[str, object], context: RequestContext) -> str:
		reference = self._required_text(payload, "reference", context)
		if reference != "$first":
			return reference
		state = self.application.query(CurrentStateQuery(), context).recruiting
		if state is None or not state.openings:
			raise self._invalid(context, "$first requires at least one visible recruiting opening")
		return state.openings[0].reference

	def _prospect_reference(self, payload: Mapping[str, object], context: RequestContext) -> str:
		reference = self._required_text(payload, "reference", context)
		if reference != "$first":
			return reference
		state = self.application.query(CurrentStateQuery(), context).recruiting
		if state is None or not state.applicants:
			raise self._invalid(context, "$first requires at least one visible Inbound Applicant")
		return state.applicants[0].reference

	def _intent_id(self, payload: Mapping[str, object], context: RequestContext) -> str:
		intent_id = self._required_text(payload, "intent_id", context)
		if intent_id != "$pending":
			return intent_id
		intent = self.application.query(CurrentStateQuery(), context).pending_write_intent
		if intent is None:
			raise self._invalid(context, "$pending requires an active write intent")
		return intent.intent_id

	def _validate_action(self, payload: Mapping[str, object], context: RequestContext) -> str:
		action_value = payload.get("action", "state")
		if not isinstance(action_value, str) or action_value not in RECRUITING_ACTIONS:
			raise self._invalid(context, "action is not supported")
		unexpected = set(payload) - _ACTION_FIELDS[action_value]
		if unexpected:
			raise self._invalid(
				context,
				f"unexpected fields for {action_value}: {', '.join(sorted(unexpected))}",
			)
		if action_value in {"select-opening", "inspect"}:
			self._required_text(payload, "reference", context)
		elif action_value == "prepare-reply":
			self._required_text(payload, "reference", context)
			self._required_text(payload, "message", context, max_length=1000)
		elif action_value in {"confirm", "cancel-write"}:
			self._required_text(payload, "intent_id", context)
		elif action_value == "cancel-applicants":
			self._required_text(payload, "run_id", context)
		elif action_value == "applicants":
			timeout_value = payload.get("timeout", 30)
			if not isinstance(timeout_value, (int, float)) or isinstance(timeout_value, bool):
				raise self._invalid(context, "timeout must be a number")
		return action_value

	def invoke(self, payload: Mapping[str, object]) -> dict[str, object]:
		"""Invoke one transport-neutral action and return its canonical result."""

		context = self._context()
		action = self._validate_action(payload, context)
		if action == "run":
			steps = payload.get("steps")
			if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
				raise self._invalid(context, "steps must be a non-empty array")
			for step in steps:
				if not isinstance(step, Mapping) or step.get("action") == "run":
					raise self._invalid(context, "each step must be a non-run action object")
				self._validate_action(step, context)
			results = [self.invoke(step) for step in steps]
			return {
				"action": action,
				"steps": results,
				"snapshot": json_value(self.application.query(CurrentStateQuery(), context)),
			}

		resource_ref: str | None = None
		if action == "state":
			snapshot = self.application.query(CurrentStateQuery(), context)
		elif action == "openings":
			result = self.application.execute(LoadRecruitingOpeningsCommand(), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "select-opening":
			reference = self._opening_reference(payload, context)
			result = self.application.execute(SelectRecruitingOpeningCommand(reference), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "applicants":
			timeout_value = payload.get("timeout", 30)
			if not isinstance(timeout_value, (int, float)) or isinstance(timeout_value, bool):
				raise self._invalid(context, "timeout must be a number")
			result = self.application.execute(StartInboundApplicantsCommand(), context)
			resource_ref = result.resource_ref
			snapshot = self._wait_for_applicants(context, float(timeout_value))
		elif action == "cancel-applicants":
			run_id = self._required_text(payload, "run_id", context)
			result = self.application.execute(CancelRunCommand(run_id=run_id), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "inspect":
			reference = self._prospect_reference(payload, context)
			result = self.application.execute(InspectRecruitingProspectCommand(reference), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "prepare-reply":
			reference = self._prospect_reference(payload, context)
			message = self._required_text(payload, "message", context, max_length=1000)
			result = self.application.execute(PrepareRecruitingReplyCommand(reference, message), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "confirm":
			intent_id = self._intent_id(payload, context)
			result = self.application.execute(ConfirmWriteIntentCommand(intent_id), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		else:
			intent_id = self._intent_id(payload, context)
			result = self.application.execute(CancelWriteIntentCommand(intent_id), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref

		return {"action": action, "snapshot": json_value(snapshot), "resource_ref": resource_ref}


def domain_error_payload(error: DomainError) -> dict[str, object]:
	"""Use one error shape for CLI and MCP transport envelopes."""

	return {
		"code": error.code.value,
		"message": error.message,
		"recoverable": error.recoverable,
		"recovery_action": error.recovery_action,
		"correlation_id": error.correlation_id,
	}


__all__ = ["RECRUITING_ACTIONS", "RecruitingSurface", "domain_error_payload", "json_value"]
