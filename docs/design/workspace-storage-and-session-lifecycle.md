# Workspace storage and session lifecycle

This design resolves GitHub Issue #6 for the Windows-first local Web product. It applies the security baseline in [`docs/research/local-web-security-baseline.md`](../research/local-web-security-baseline.md) to the two Core Journeys defined in [`CONTEXT.md`](../../CONTEXT.md).

## Decision summary

1. The Job-Seeking Workspace and Recruiting Workspace have physically separate data roots, databases, credential stores, caches, run records, and Platform Sessions.
2. The application has one active Workspace at a time. Switching is an explicit lifecycle transition, not a UI-only filter.
3. Job-seeking preferences, the Job Shortlist, and an explicitly imported local resume may persist.
4. Sensitive Recruiting Content is fetched on demand, held in memory for the active view or run, and discarded by default.
5. Every Platform Write is represented by a server-owned, short-lived Write Intent and requires its own Confirmation Gate.
6. Authentication failure, rate limiting, risk controls, cancellation, or uncertain remote outcomes stop the run. No stopped run can later execute a Platform Write automatically.

## Ownership boundaries

The application owns three roots under the current Windows user's application-data directory:

```text
BossAgent/
  app/                       non-sensitive application settings only
  workspaces/
    job-seeking/             owned by Job-Seeking Workspace
    recruiting/              owned by Recruiting Workspace
```

The Local Operator cannot configure either workspace root to overlap the other, point inside the source tree, or use an arbitrary client-supplied path. The backend resolves all paths from a fixed workspace identifier.

Each workspace root has its own:

- database and schema version;
- DPAPI-protected Platform Session material;
- cache and temporary-file namespace;
- Recoverable Run records;
- structured audit and diagnostic log;
- retention metadata and clear/export operations.

The `app/` root may contain theme, locale, update channel, last active workspace, and window preferences. It must not contain BOSS credentials, AI credentials, resumes, job selections, Recruiting Prospect identifiers, conversations, or Write Intents.

## Data classification and retention

| Data | Owner | Default storage | Default lifetime | Clear/export behavior |
| --- | --- | --- | --- | --- |
| Search goal and filters | Job-Seeking Workspace | Workspace database | Until edited or workspace cleared | Included in job-seeking export |
| Job Shortlist and local notes | Job-Seeking Workspace | Workspace database | Until removed or workspace cleared | Included in job-seeking export |
| Imported resume | Job-Seeking Workspace | Workspace file store | Until replaced or explicitly cleared | Export only after explicit selection and warning |
| Job search/detail cache | Job-Seeking Workspace | Bounded workspace cache | Short, configurable product default | Cleared independently or with workspace |
| Selected recruiting opening | Recruiting Workspace | Workspace database | Until changed or workspace cleared | Included without applicant content |
| Applicant list references | Recruiting Workspace | Memory first; minimal identifiers may be cached | Active session or short bounded cache | Omitted from default export |
| Resume, contact details, chat text | Recruiting Workspace | Memory only by default | Active view/run | Never included in default export; cleared on switch/exit |
| Local recruiting notes | Recruiting Workspace | Workspace database only after explicit save | Until removed or workspace cleared | Included only in explicit recruiting export |
| Message or greeting draft | Owning Workspace | Memory while editing; Write Intent after preview | Until navigation, cancellation, expiry, or confirmation | Never exported by default |
| Write Intent | Owning Workspace | Server memory or encrypted transient store | Short fixed expiry; consumed once | Never exported; audit keeps metadata only |
| Recoverable Run | Owning Workspace | Workspace database | Until completed, discarded, or retention limit | Export contains status and non-sensitive checkpoints only |
| Platform Session credential | Owning Workspace | Current-user DPAPI protected store | Until logout, invalidation, or workspace clear | Never exported |
| AI API credential | Application credential store, not a role data root | Current-user DPAPI protected store | Until removed | Never exported |
| Audit/diagnostic log | Owning Workspace | Bounded structured log | Short retention with size cap | Export is explicit and redacted |

Recruiting content may be persisted only when a future feature presents the exact content, purpose, retention period, and clear path before the Local Operator opts in. That is outside the first release.

## Platform Session lifecycle

### States

```text
Disconnected -> Connecting -> Connected
      ^             |            |
      |             v            v
      +---------- Recovery <- Stopping
                      |
                      v
                  Disconnected
```

- **Disconnected**: no usable BOSS credential is loaded; remote operations are unavailable.
- **Connecting**: an operator-initiated official BOSS login window is active. The product never asks for a BOSS password.
- **Connected**: the active Workspace can perform allowed reads and create Write Intents.
- **Recovery**: authentication expired, rate limiting, platform risk control, or an uncertain remote result requires explicit human action.
- **Stopping**: new remote work is rejected while active requests, streams, and sensitive in-memory state are cancelled and cleared.

Only the backend changes Platform Session state. The UI observes state and offers valid next actions.

### Explicit workspace switch

Switching from one Workspace to the other performs these steps in order:

1. reject new remote operations in the current Workspace;
2. invalidate all unconfirmed Write Intents;
3. cancel active reads and progress streams;
4. wait for bounded cancellation of in-flight work;
5. mark uncertain remote outcomes for Recovery instead of retrying;
6. clear resumes, conversations, contact details, drafts, and other sensitive memory;
7. release the current Platform Session and workspace database handles;
8. open the target Workspace root and load only its Platform Session;
9. expose the target Workspace state to the UI.

If any cleanup step cannot be proven complete, the application remains in Recovery and does not activate the other Platform Session.

## Recoverable Run model

A Recoverable Run stores only enough state to explain and resume safe work:

- workspace and Core Journey type;
- current read-only step;
- search/filter inputs or selected opening;
- stable references to selected jobs or Recruiting Prospects when permitted;
- completed local decisions such as shortlist membership;
- stop reason, last known outcome category, and timestamp.

It never stores a reusable authorization to write. Drafts and Write Intents expire independently. Resuming a run re-fetches remote state and requires a new Confirmation Gate for every Platform Write.

Run states are `queued`, `running`, `waiting_for_operator`, `stopping`, `stopped`, `completed`, and `recovery_required`. A run cannot move from `stopped` or `recovery_required` directly to a remote write.

## Write Intent lifecycle

1. The UI asks the backend to preview one Platform Write for one target.
2. The backend validates the active Workspace and Platform Session, normalizes the final payload, and creates an immutable intent with a payload hash and expiry.
3. The UI renders the Confirmation Gate from that server-owned intent.
4. Confirmation submits only the intent identifier.
5. The backend consumes the intent at most once and records success, failure, or uncertain outcome without logging content.

Changing the target, message, workspace, account, or action invalidates the intent. Timeout, workspace switch, logout, service restart, cancellation, or Recovery also invalidates it. Retries always create a new intent.

## Clear, logout, and export

### Clear workspace

Clearing a Workspace first stops its runs and releases its Platform Session, then removes that workspace's database, files, caches, logs, credentials, and transient material. It never affects the other Workspace. The UI shows the exact scope and requires confirmation.

### Logout

Logout deletes only the active Workspace's protected BOSS credential, invalidates its Write Intents, stops remote runs, and clears sensitive memory. Local preferences and shortlists remain unless the Local Operator separately clears the Workspace.

### Export

Export is per Workspace, explicit, and creates no archive until the Local Operator chooses a destination. The default export omits credentials, tokens, logs, platform message text, resumes, contact details, and Sensitive Recruiting Content. Optional resume or diagnostic exports require separate warnings and selections.

## Legacy CLI data

The existing CLI's single `data_dir` is not silently reused by the Web product. Developer Preview may offer a read-only inventory that classifies legacy files and lets the Local Operator explicitly import eligible job-seeking preferences or resume files into the Job-Seeking Workspace.

Legacy credentials are not copied into both Workspaces. A Platform Session must be re-established separately for each Workspace unless a later migration explicitly proves the credential's owner and obtains operator confirmation.

## Required acceptance tests

1. Creating data in one Workspace does not create or modify files under the other workspace root.
2. A Platform Session loaded for one Workspace cannot authenticate requests from the other.
3. A client-supplied workspace identifier cannot escape or replace the backend-resolved root.
4. Switching Workspaces invalidates pending Write Intents and clears Sensitive Recruiting Content from memory.
5. Restarting or resuming a Recoverable Run never executes an earlier unconfirmed write.
6. Recruiting resumes, contact details, and chat text do not appear in databases, browser storage, logs, crash output, or default exports.
7. Clearing one Workspace removes its credentials and data without affecting the other Workspace.
8. Logout removes the active Platform Session but preserves explicitly retained local workflow data.
9. Export contains only the documented allowlist and never contains credential or personal-data canaries.
10. Legacy data migration never silently populates both Workspaces and never imports credentials without explicit confirmation.

## Implementation seams

The later application service layer should expose workspace-scoped interfaces rather than passing raw `Path` values through UI/API code:

- `WorkspaceRegistry` resolves the fixed workspace identifier and lifecycle state.
- `WorkspaceStore` owns persistent role data and migrations.
- `PlatformSessionStore` owns DPAPI-protected credentials for exactly one Workspace.
- `RunService` owns observable, cancellable, recoverable reads and local decisions.
- `WriteIntentService` owns preview, confirmation, expiry, consumption, and audit metadata.

Existing `AuthManager(data_dir)` and global CLI `data_dir` remain compatibility adapters until callers move behind these boundaries. The Web API must not instantiate them directly from client input.
