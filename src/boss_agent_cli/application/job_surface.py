"""Thin Job-Seeking surface contract shared by CLI and MCP adapters.

The application module owns every workflow and safety decision.  This module only
translates transport-friendly action dictionaries into the authoritative commands
and query already used by the local Web application.
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
	InspectJobCommand,
	JobSearchGoal,
	PrepareJobGreetingCommand,
	RequestContext,
	SetShortlistedCommand,
	StartJobSearchCommand,
	UpdateJobSearchGoalCommand,
)
from boss_agent_cli.application.module import Application


JOB_ACTIONS = (
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
_TERMINAL_RUN_STATES = frozenset({"cancelled", "completed", "recovery"})
_ACTION_FIELDS = {
	"state": frozenset({"action"}),
	"goal": frozenset({"action", "objective", "keyword", "city", "salary", "experience", "education"}),
	"search": frozenset({"action", "timeout"}),
	"cancel-search": frozenset({"action", "run_id"}),
	"inspect": frozenset({"action", "reference"}),
	"shortlist": frozenset({"action", "reference", "shortlisted"}),
	"prepare-greeting": frozenset({"action", "reference", "message"}),
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


class JobSeekingSurface:
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
			recovery_action="Review the Job-Seeking action input",
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
			raise JobSeekingSurface._invalid(context, f"{field} is required")
		return value

	def _wait_for_search(self, context: RequestContext, timeout: float) -> ApplicationStateSnapshot:
		deadline = time.monotonic() + max(0.01, min(timeout, 120.0))
		while time.monotonic() < deadline:
			snapshot = self.application.query(CurrentStateQuery(), context)
			run = snapshot.active_run
			if run is not None and run.state in _TERMINAL_RUN_STATES:
				return snapshot
			time.sleep(0.01)
		raise DomainError(
			code=ErrorCode.INVALID_TRANSITION,
			message="Job search is still running",
			correlation_id=context.correlation_id,
			recoverable=True,
			recovery_action="Query state or cancel the active search",
		)

	def _reference(self, payload: Mapping[str, object], context: RequestContext) -> str:
		reference = self._required_text(payload, "reference", context)
		if reference != "$first":
			return reference
		snapshot = self.application.query(CurrentStateQuery(), context)
		state = snapshot.job_seeking
		if state is None or not state.results:
			raise self._invalid(context, "$first requires at least one visible search result")
		return state.results[0].reference

	def _intent_id(self, payload: Mapping[str, object], context: RequestContext) -> str:
		intent_id = self._required_text(payload, "intent_id", context)
		if intent_id != "$pending":
			return intent_id
		intent = self.application.query(CurrentStateQuery(), context).pending_write_intent
		if intent is None:
			raise self._invalid(context, "$pending requires an active write intent")
		return intent.intent_id

	def invoke(self, payload: Mapping[str, object]) -> dict[str, object]:
		"""Invoke one transport-neutral action and return its canonical result."""

		context = self._context()
		action_value = payload.get("action", "state")
		if not isinstance(action_value, str) or action_value not in JOB_ACTIONS:
			raise self._invalid(context, "action is not supported")
		action = action_value
		unexpected = set(payload) - _ACTION_FIELDS[action]
		if unexpected:
			raise self._invalid(context, f"unexpected fields for {action}: {', '.join(sorted(unexpected))}")

		if action == "run":
			steps = payload.get("steps")
			if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
				raise self._invalid(context, "steps must be a non-empty array")
			results: list[dict[str, object]] = []
			for step in steps:
				if not isinstance(step, Mapping) or step.get("action") == "run":
					raise self._invalid(context, "each step must be a non-run action object")
				results.append(self.invoke(step))
			return {
				"action": action,
				"steps": results,
				"snapshot": json_value(self.application.query(CurrentStateQuery(), context)),
			}

		resource_ref: str | None = None
		if action == "state":
			snapshot = self.application.query(CurrentStateQuery(), context)
		elif action == "goal":
			keyword = self._required_text(payload, "keyword", context, max_length=1000)
			objective_value = payload.get("objective", keyword)
			if not isinstance(objective_value, str) or not objective_value.strip():
				raise self._invalid(context, "objective is required")
			for field in ("city", "salary", "experience", "education"):
				if field in payload and not isinstance(payload[field], str):
					raise self._invalid(context, f"{field} must be a string")
			goal = JobSearchGoal(
				objective=objective_value,
				keyword=keyword,
				city=str(payload.get("city", "")),
				salary=str(payload.get("salary", "")),
				experience=str(payload.get("experience", "")),
				education=str(payload.get("education", "")),
			)
			result = self.application.execute(UpdateJobSearchGoalCommand(goal=goal), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "search":
			timeout_value = payload.get("timeout", 30)
			if not isinstance(timeout_value, (int, float)) or isinstance(timeout_value, bool):
				raise self._invalid(context, "timeout must be a number")
			result = self.application.execute(StartJobSearchCommand(), context)
			resource_ref = result.resource_ref
			snapshot = self._wait_for_search(context, float(timeout_value))
		elif action == "cancel-search":
			run_id = self._required_text(payload, "run_id", context)
			result = self.application.execute(CancelRunCommand(run_id=run_id), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "inspect":
			reference = self._reference(payload, context)
			result = self.application.execute(InspectJobCommand(reference=reference), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "shortlist":
			reference = self._reference(payload, context)
			shortlisted = payload.get("shortlisted", True)
			if not isinstance(shortlisted, bool):
				raise self._invalid(context, "shortlisted must be a boolean")
			result = self.application.execute(
				SetShortlistedCommand(reference=reference, shortlisted=shortlisted), context
			)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "prepare-greeting":
			reference = self._reference(payload, context)
			message = self._required_text(payload, "message", context, max_length=1000)
			result = self.application.execute(PrepareJobGreetingCommand(reference=reference, message=message), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "confirm":
			intent_id = self._intent_id(payload, context)
			result = self.application.execute(ConfirmWriteIntentCommand(intent_id=intent_id), context)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		else:
			intent_id = self._intent_id(payload, context)
			result = self.application.execute(CancelWriteIntentCommand(intent_id=intent_id), context)
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
