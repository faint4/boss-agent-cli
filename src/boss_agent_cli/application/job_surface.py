"""Thin Job-Seeking surface contract shared by CLI and MCP adapters.

The application module owns every workflow and safety decision.  This module only
translates transport-friendly action dictionaries into the authoritative commands
and query already used by the local Web application.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence

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
from boss_agent_cli.application.core_journey_contract import (
	JOB_SEEKING_CONTRACT,
	ContractValidationError,
	domain_error_payload,
	json_value,
)
from boss_agent_cli.application.module import Application


JOB_ACTIONS = JOB_SEEKING_CONTRACT.action_names
_TERMINAL_RUN_STATES = frozenset({"cancelled", "completed", "recovery"})


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
	def _with_legacy_defaults(payload: Mapping[str, object]) -> dict[str, object]:
		"""Keep pre-catalog CLI/MCP conveniences at the adapter seam."""

		normalized = dict(payload)
		action = normalized.setdefault("action", "state")
		if action == "goal":
			if "keyword" in normalized:
				normalized.setdefault("objective", normalized["keyword"])
		elif action == "shortlist":
			normalized.setdefault("shortlisted", True)
		steps = normalized.get("steps")
		if action == "run" and isinstance(steps, Sequence) and not isinstance(steps, (str, bytes)):
			normalized["steps"] = [
				JobSeekingSurface._with_legacy_defaults(step) if isinstance(step, Mapping) else step
				for step in steps
			]
		return normalized

	def _validate_action(self, payload: Mapping[str, object], context: RequestContext) -> str:
		try:
			return JOB_SEEKING_CONTRACT.validate_action(payload)
		except ContractValidationError as exc:
			raise self._invalid(context, str(exc)) from exc

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
		reference = str(payload["reference"])
		if reference != "$first":
			return reference
		snapshot = self.application.query(CurrentStateQuery(), context)
		state = snapshot.job_seeking
		if state is None or not state.results:
			raise self._invalid(context, "$first requires at least one visible search result")
		return state.results[0].reference

	def _intent_id(self, payload: Mapping[str, object], context: RequestContext) -> str:
		intent_id = str(payload["intent_id"])
		if intent_id != "$pending":
			return intent_id
		intent = self.application.query(CurrentStateQuery(), context).pending_write_intent
		if intent is None:
			raise self._invalid(context, "$pending requires an active write intent")
		return intent.intent_id

	def invoke(self, payload: Mapping[str, object]) -> dict[str, object]:
		"""Invoke one transport-neutral action and return its canonical result."""

		context = self._context()
		payload = self._with_legacy_defaults(payload)
		action = self._validate_action(payload, context)

		if action == "run":
			steps = payload["steps"]
			assert isinstance(steps, Sequence) and not isinstance(steps, (str, bytes))
			results: list[dict[str, object]] = []
			for step in steps:
				assert isinstance(step, Mapping)
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
			goal = JobSearchGoal(
				objective=str(payload["objective"]),
				keyword=str(payload["keyword"]),
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
			assert isinstance(timeout_value, (int, float)) and not isinstance(timeout_value, bool)
			result = self.application.execute(StartJobSearchCommand(), context)
			resource_ref = result.resource_ref
			snapshot = self._wait_for_search(context, float(timeout_value))
		elif action == "cancel-search":
			run_id = str(payload["run_id"])
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
			shortlisted = payload["shortlisted"]
			assert isinstance(shortlisted, bool)
			result = self.application.execute(
				SetShortlistedCommand(reference=reference, shortlisted=shortlisted), context
			)
			snapshot = result.snapshot
			resource_ref = result.resource_ref
		elif action == "prepare-greeting":
			reference = self._reference(payload, context)
			message = str(payload["message"])
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


__all__ = ["JOB_ACTIONS", "JobSeekingSurface", "domain_error_payload", "json_value"]
