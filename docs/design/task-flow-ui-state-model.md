# Task flow UI state model

This document captures the decision validated by the Issue #3 logic prototype. The prototype source remains on the throwaway branch `prototype/task-flow-state-model`; production code should implement this contract rather than reuse the HTML shell.

## Verdict

Both Core Journeys use one predictable task path:

```text
Goal or opening -> Fetch results -> Select one object -> Prepare content -> Confirm one write -> Observe result
```

The interface always distinguishes local decisions, remote reads, and Platform Writes. It displays the active Workspace, Platform Session, Recoverable Run, selected object, pending Write Intent, last transition, and recovery requirement. An unavailable action is rejected with a plain-language reason instead of being silently ignored.

## Primary interface structure

The first product release uses the Task Pipeline prototype as its primary structure, with two supporting patterns:

- the object list and quick selection behavior from the Navigation Cockpit prototype;
- the isolated safe-action area, Confirmation Gate, pause, and recovery status from the Operation Queue prototype.

The pipeline has six visible stages:

1. **Goal**: define a job-search goal or choose a recruiting opening.
2. **Results**: fetch and filter jobs or Inbound Applicants.
3. **Object**: inspect exactly one job or Recruiting Prospect.
4. **Content**: make a local decision and prepare any greeting or reply draft.
5. **Confirmation**: inspect a server-owned Write Intent for one target and one action.
6. **Result**: observe success, failure, cancellation, or Recovery.

## Visible state contract

The application chrome always shows:

- active Job-Seeking Workspace or Recruiting Workspace;
- Platform Session state: disconnected, connecting, connected, or recovery;
- current Recoverable Run state and step;
- whether the UI is waiting for the platform, the Local Operator, or recovery.

The task surface shows:

- selected job or Recruiting Prospect;
- whether Sensitive Recruiting Content is currently loaded in memory;
- local shortlist or later-processing decision;
- current draft;
- pending Write Intent, including target, action, final content, expiry, and pending/consumed state;
- the last accepted or rejected transition in domain language.

## Action categories

### Local decisions

Adding a job to the Job Shortlist, saving a local note, or marking a Recruiting Prospect for later processing changes only local workspace state. The UI labels these actions as local and does not show a Confirmation Gate.

### Remote reads

Search, filtering, job detail, applicant list, resume, and conversation reads show observable progress and can be cancelled. Recruiting resumes, contact details, and conversations display an “in memory” indicator and are cleared on cancellation, workspace switch, recovery, or exit.

### Platform Writes

Greeting, applying, replying, requesting a resume, and changing publication state always use this sequence:

1. prepare or edit content;
2. request a server-owned Write Intent;
3. render the Confirmation Gate from that intent;
4. submit only its identifier after explicit confirmation;
5. show the exact remote result.

The first release never confirms several targets or actions together.

## Confirmation Gate

The gate displays, without truncating the meaning:

- active Workspace and Platform Session;
- one target;
- one action;
- final message or exact remote effect;
- a warning that confirmation performs a Platform Write;
- expiry or invalidation status;
- Cancel and Confirm controls.

Changing the Workspace, target, action, or content invalidates the visible confirmation immediately. The UI returns to the content stage and explains that a new confirmation is required.

The Confirm control is unavailable when the Platform Session is not connected, the intent expired or was consumed, the Workspace changed, or the server no longer recognizes the intent. The backend enforces all of these conditions independently of the UI.

## Recoverable Run presentation

Authentication expiry, rate limiting, platform risk controls, cancellation, and uncertain remote outcomes stop forward progress. The task surface then replaces ordinary actions with:

- a concise stop reason;
- what was safely preserved;
- what was cleared;
- whether a remote write may already have occurred;
- the exact human recovery action;
- Resume, Discard, or Open official BOSS controls when applicable.

Recovery never continues automatically. Reconnecting a Platform Session only restores connectivity; the Local Operator must explicitly resume the Recoverable Run. Resumption re-fetches required remote state and creates a new Write Intent before any Platform Write.

For an uncertain remote outcome, the UI must not offer a one-click retry. It asks the Local Operator to verify the result in the official BOSS interface, then record the observed outcome or prepare a new action.

## Transition rules validated by the prototype

| Situation | Required result |
| --- | --- |
| Confirm without a valid Write Intent | Reject and explain that no valid single-item confirmation exists |
| Edit draft after preview | Invalidate the old intent and return to content preparation |
| Login expires before confirmation | Stop the run, clear the intent, preserve a safe checkpoint |
| Login is restored | Remain stopped until the Local Operator chooses Resume |
| Resume after recovery | Re-fetch remote state; require a new intent and confirmation |
| Rate limit or platform risk control | Stop; do not loop or bypass; show manual recovery guidance |
| Remote result is uncertain | Consume the intent, enter Recovery, prohibit automatic retry |
| Cancel an active run | Invalidate intent, clear Sensitive Recruiting Content, retain safe local decisions |
| Switch Workspace | Stop current work, invalidate intent, clear sensitive memory, load the other independent Platform Session |
| Complete a write successfully | Show the exact result and clear transient sensitive state |

## Implementation boundary

The production state model belongs in the shared Python application service layer. React renders server-owned state and sends typed commands; it does not infer legal transitions or reconstruct Write Intents from stale client data.

The versioned Web API must return a stable state snapshot after each command and progress event. CLI and MCP adapters may use the same application services, but only the Web UI presents this task pipeline.

## Acceptance criteria

1. Both Workspaces complete their Core Journey through the same six-stage task path.
2. Every screen identifies the active Workspace, Platform Session, and Recoverable Run state.
3. Local actions, remote reads, and Platform Writes are visually and behaviorally distinct.
4. Every Platform Write has an inspectable one-target Write Intent and individual Confirmation Gate.
5. All invalid transitions return a useful reason and cause no remote side effect.
6. Draft, target, action, Workspace, expiry, or recovery changes invalidate old confirmation.
7. Authentication expiry, rate limiting, risk controls, cancellation, and uncertain outcomes have explicit non-automatic recovery paths.
8. Sensitive Recruiting Content is visibly transient and cleared on every defined lifecycle boundary.
9. Narrow Windows layouts stack the task pipeline without hiding status, confirmation, or recovery information.
10. The application service layer, not React, is authoritative for state transitions.
