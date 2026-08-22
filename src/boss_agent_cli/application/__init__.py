"""Public interface for the authoritative application module."""

from boss_agent_cli.application.contracts import (
	ApplicationEvent,
	ApplicationStateSnapshot,
	CommandResult,
	ConnectPlatformSessionCommand,
	CurrentStateQuery,
	DomainError,
	DomainErrorDetails,
	ErrorCode,
	PlatformSessionState,
	RequestContext,
	LogoutPlatformSessionCommand,
	RunSummary,
	RunEventKind,
	SwitchWorkspaceCommand,
	WriteIntentSummary,
	WorkspaceKind,
)
from boss_agent_cli.application.module import Application

__all__ = [
	"Application",
	"ApplicationEvent",
	"ApplicationStateSnapshot",
	"CommandResult",
	"ConnectPlatformSessionCommand",
	"CurrentStateQuery",
	"DomainError",
	"DomainErrorDetails",
	"ErrorCode",
	"PlatformSessionState",
	"RequestContext",
	"LogoutPlatformSessionCommand",
	"RunEventKind",
	"RunSummary",
	"SwitchWorkspaceCommand",
	"WriteIntentSummary",
	"WorkspaceKind",
]
