# Application module and versioned Web interface

This design resolves GitHub Issue #5. It defines the single seam shared by the Web product, CLI, and MCP while allowing existing surfaces to migrate without a flag day.

## Design objective

The application module must be deep: callers express an operator intent and receive an authoritative result, while the module hides Workspace resolution, Platform Session ownership, validation, persistence, remote platform access, Write Intent rules, Recoverable Run transitions, audit metadata, and recovery behavior.

Deleting this module would force every transport to reproduce those decisions. That is the leverage and locality the current Click, schema, MCP catalog, and MCP argument mapping do not provide.

## External interface

The module exposes three operations:

1. `execute(command, context)` applies one typed command and returns the new state snapshot plus any created resource reference.
2. `query(query, context)` returns a typed read model without changing local or platform state.
3. `events(run_id, after_cursor, context)` streams ordered Recoverable Run events and ends when the run reaches a terminal or waiting state.

`context` identifies the active local session and request correlation only. It never accepts a filesystem path, platform credential, or client-authoritative Workspace root.

Commands use domain names such as switch Workspace, connect Platform Session, start/cancel/resume Recoverable Run, update Job Shortlist, save local recruiting decision, create Write Intent, confirm Write Intent, clear Workspace, and export Workspace. Queries cover application state, Workspace state, jobs, openings, Inbound Applicants, details, run status, storage summary, and safe recovery choices.

The interface returns immutable data transfer values. It does not print, render, open a browser, terminate the process, or return raw platform responses.

## Internal seams and adapters

The module implementation owns these internal seams:

- a BOSS platform port with production and fake adapters;
- Workspace storage with SQLite/file and in-memory adapters;
- Platform Session protection with Windows DPAPI and in-memory adapters;
- clock and identifier providers with production and deterministic fake adapters;
- optional AI assistance with configured and unavailable adapters.

The BOSS platform is a true external dependency, so tests use a behaviorally explicit fake. SQLite and filesystem behavior are local-substitutable and should be exercised with temporary real stores where isolation or migration is the behavior under test.

The legacy Browser Bridge is not an adapter for the Web product. It remains isolated until it independently satisfies the local Web security baseline.

## State snapshot

Every accepted command returns a complete authoritative snapshot sufficient for the active task surface:

- interface schema version;
- active Workspace and Platform Session state;
- active Recoverable Run, step, progress, wait reason, and permitted recovery choices;
- selected job or Recruiting Prospect reference;
- local decision state;
- transient-content presence without leaking Sensitive Recruiting Content into unrelated views;
- pending Write Intent summary and expiry when present;
- last transition and domain error when applicable.

Clients replace their prior snapshot with the returned snapshot. They do not merge workflow state or infer legal transitions.

## Versioned Web transport

The Python process serves the production React assets and `/api/v1` from the same exact Origin. Version `v1` is part of the path; response bodies also carry `schema_version`.

The initial transport surface is deliberately small:

- read the current application or Workspace snapshot;
- submit a typed command with a client-generated request identifier;
- read one Recoverable Run;
- subscribe to run events by cursor using same-origin authenticated SSE;
- render and confirm one server-owned Write Intent;
- retrieve explicit health and recovery status without internal paths or credentials.

Confirmation uses a dedicated route that accepts only the Write Intent identifier and request identifier. It never accepts a replacement target, action, content, Workspace, or payload hash.

Unknown fields are rejected on commands. Read models may gain additive fields within `v1`; removing fields, changing meaning, or changing required inputs requires a new version. The server advertises only versions it fully implements and never silently downgrades a command.

## Command semantics

- Every command has a stable command name, typed input, typed result, declared idempotency behavior, error set, and authorization classification.
- Local decisions may be idempotent by natural key.
- Starting a run returns a stable run identifier; replaying the same request identifier cannot start a second run.
- Creating a Write Intent returns a short-lived single-target resource.
- Confirming an intent is at-most-once. Success, failure, or uncertain outcome consumes it.
- Cancellation is cooperative and observable. A completed remote write cannot be retroactively cancelled.
- GET, HEAD, OPTIONS, queries, and event subscriptions never change local or platform state.

## Error contract

All transports derive from one domain error catalog. An error contains:

- stable code;
- safe human message;
- recoverable flag;
- explicit recovery action when one exists;
- request or run correlation identifier;
- optional field-level validation details that contain no credentials or Sensitive Recruiting Content.

Expected codes include invalid command, invalid transition, Workspace mismatch, authentication required/expired, rate limited, platform risk control, Write Intent missing/expired/consumed, run not found, cancellation requested, uncertain remote outcome, storage unavailable, and unsupported capability.

HTTP status mapping is a Web adapter decision; CLI exit codes and MCP errors map the same domain errors without changing their meaning.

## Capability catalog and migration

The application command/query definitions become the source for transport names, input schemas, descriptions, risk classification, and error metadata. Adapters may add presentation-only flags, but cannot add workflow behavior.

Migration follows expand–migrate–contract:

1. add the application module and migrate one read-only status slice while all old paths remain;
2. migrate the Job-Seeking Core Journey through Web, CLI, schema, and MCP adapters;
3. migrate the Recruiting Core Journey through the same adapters;
4. move shared error and capability metadata to the catalog;
5. remove duplicated core-journey schema definitions and MCP argument construction only after cross-surface contract tests pass.

Non-core legacy commands may remain behind compatibility adapters after the First Product Release. Full CLI/MCP parity is not a release requirement.

## Testing seam

The highest behavioral seam is the application module interface with a fake BOSS adapter, deterministic clock/identifiers, and temporary Workspace stores. Tests issue the same commands and queries as real transports, then assert returned snapshots, emitted events, persisted safe state, fake-platform calls, and absence of calls on rejected transitions.

Additional seams exist only where behavior is transport-specific:

- ASGI contract tests cover `/api/v1` serialization, authentication, Host/Origin/Fetch Metadata, security headers, SSE cursor behavior, and HTTP status mapping.
- React browser tests cover the two task pipelines, Confirmation Gate, cancellation, and recovery using a fake application backend.
- Existing CLI/schema/MCP cross-validation tests cover adapter parity while migration is in progress.
- Windows smoke tests cover the packaged process, exact loopback listener, browser launch, shutdown, upgrade, and uninstall behavior.

Tests assert external behavior rather than internal classes or call order, except where at-most-once platform execution is itself the behavior.
