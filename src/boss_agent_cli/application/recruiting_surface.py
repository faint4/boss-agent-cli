"""Thin Recruiting surface contract shared by CLI and MCP adapters.

The application module owns workflow, privacy, and write-confirmation policy. This
module only validates transport-friendly actions and invokes the same commands and
query used by the local Web surface.
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
	InspectRecruitingProspectCommand,
	LoadRecruitingOpeningsCommand,
	PrepareRecruitingReplyCommand,
	RequestContext,
	SelectRecruitingOpeningCommand,
	StartInboundApplicantsCommand,
)
from boss_agent_cli.application.core_journey_contract import (
	RECRUITING_CONTRACT,
	ContractValidationError,
	domain_error_payload,
	json_value,
)
from boss_agent_cli.application.module import Application


RECRUITING_ACTIONS = RECRUITING_CONTRACT.action_names
_TERMINAL_RUN_STATES = frozenset({"cancelled", "completed", "recovery"})


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
	def _with_legacy_defaults(payload: Mapping[str, object]) -> dict[str, object]:
		"""Keep pre-catalog CLI/MCP conveniences at the adapter seam."""

		normalized = dict(payload)
		normalized.setdefault("action", "state")
		steps = normalized.get("steps")
		if normalized["action"] == "run" and isinstance(steps, Sequence) and not isinstance(steps, (str, bytes)):
			normalized["steps"] = [
				RecruitingSurface._with_legacy_defaults(step) if isinstance(step, Mapping) else step
				for step in steps
			]
		return normalized

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
		reference = str(payload["reference"])
		if reference != "$first":
			return reference
		state = self.application.query(CurrentStateQuery(), context).recruiting
		if state is None or not state.openings:
			raise self._invalid(context, "$first requires at least one visible recruiting opening")
		return state.openings[0].reference

	def _prospect_reference(self, payload: Mapping[str, object], context: RequestContext) -> str:
		reference = str(payload["reference"])
		if reference != "$first":
			return reference
		state = self.application.query(CurrentStateQuery(), context).recruiting
		if state is None or not state.applicants:
			raise self._invalid(context, "$first requires at least one visible Inbound Applicant")
		return state.applicants[0].reference

	def _intent_id(self, payload: Mapping[str, object], context: RequestContext) -> str:
		intent_id = str(payload["intent_id"])
		if intent_id != "$pending":
			return intent_id
		intent = self.application.query(CurrentStateQuery(), context).pending_write_intent
		if intent is None:
			raise self._invalid(context, "$pending requires an active write intent")
		return intent.intent_id

	def _validate_action(self, payload: Mapping[str, object], context: RequestContext) -> str:
		try:
			return RECRUITING_CONTRACT.validate_action(payload)
		except ContractValidationError as exc:
			raise self._invalid(context, str(exc)) from exc

	def invoke(self, payload: Mapping[str, object]) -> dict[str, object]:
		"""Invoke one transport-neutral action and return its canonical result."""

		context = self._context()
		payload = self._with_legacy_defaults(payload)
		action = self._validate_action(payload, context)
		if action == "run":
			steps = payload["steps"]
			assert isinstance(steps, Sequence) and not isinstance(steps, (str, bytes))
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
			assert isinstance(timeout_value, (int, float)) and not isinstance(timeout_value, bool)
			result = self.application.execute(StartInboundApplicantsCommand(), context)
			resource_ref = result.resource_ref
			snapshot = self._wait_for_applicants(context, float(timeout_value))
		elif action == "cancel-applicants":
			run_id = str(payload["run_id"])
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
			message = str(payload["message"])
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
__all__ = ["RECRUITING_ACTIONS", "RecruitingSurface", "domain_error_payload", "json_value"]
