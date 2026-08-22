# Release quality gates

This design resolves GitHub Issue #7. A milestone may be named Developer Preview or First Product Release only when every blocking item for that milestone has evidence in CI, a repeatable Windows smoke procedure, or a recorded manual real-BOSS check where automation would create platform risk.

## Evidence policy

- Automated evidence is required for deterministic local behavior, negative security cases, contracts, persistence, packaging, and browser UI behavior.
- Manual evidence is limited to official BOSS login and one deliberately confirmed real Platform Write in each Core Journey.
- A passing happy path never substitutes for negative tests proving that rejected or uncertain actions have no duplicate side effect.
- Release evidence records product version, commit, Windows version, browser version, installer or source-build hash, workspace used, and redacted result.

## Developer Preview blockers

### Product behavior

- Both Core Journeys run from the production React build served by the Python process.
- Job-Seeking covers goal, search/filter, detail, Job Shortlist, draft, one-item Confirmation Gate, and confirmed greeting/apply.
- Recruiting covers selected opening, Inbound Applicants, resume/chat read, draft, one-item Confirmation Gate, and confirmed reply.
- Core behavior works with no AI provider or API key. Optional AI failure never blocks manual drafting or local decisions.
- Every unavailable transition has a useful reason; no UI-only boolean can authorize a Platform Write.

### Workspace and recovery

- Job-Seeking and Recruiting data roots and Platform Sessions are physically isolated.
- Switching, logout, cancellation, authentication expiry, rate limiting, platform risk controls, and uncertain outcomes invalidate Write Intents and clear Sensitive Recruiting Content.
- Recoverable Runs resume only after explicit operator action and always require a new Write Intent before a Platform Write.
- Workspace clear/export and logout behavior match the documented allowlists.

### Local Web security

- The production server binds only the exact IPv4 loopback address and rejects wildcard/LAN/IPv6-wildcard listening.
- Exact Host, per-start 256-bit authentication, exact Origin, Fetch Metadata, JSON content type, no permissive CORS, and security headers pass positive and negative tests.
- Every Platform Write uses an immutable, expiring, single-use Write Intent with at-most-once execution under replay, double-click, timeout, mutation, and restart tests.
- Platform and AI credentials use current-user DPAPI with fail-closed behavior; no MachineGuid fallback is used for real accounts.
- Credential, token, resume, contact, chat, draft, and personal-data canaries never appear in logs, browser storage, errors, crash output, or default exports.
- The Web runtime does not expose or call the existing unauthenticated Browser Bridge.

### Windows and browser validation

- A documented source checkout procedure builds the React production assets and starts the local product without running Vite against real BOSS data.
- The supported Windows architecture passes clean-machine dependency installation, startup, single-instance, browser-open, shutdown, and orphan-process checks.
- Current supported Chrome and Edge versions complete keyboard-driven task, confirmation, cancellation, and recovery smoke scenarios at normal and narrow window sizes.
- The full Python test suite, lint, type checks for touched modules, Web unit/contract tests, and browser tests pass on Windows CI.

### Real BOSS smoke

- Login begins only after an operator clicks Connect and uses an official BOSS window; the product never collects a password or silently imports daily-browser cookies.
- Each Workspace completes one read journey against a dedicated Platform Session.
- Each Workspace performs exactly one consciously prepared and confirmed low-impact Platform Write, with target/content captured in redacted evidence.
- Authentication expiry, rate limiting, or risk controls abort the smoke rather than triggering retries or bypass behavior.

## First Product Release additional blockers

### Product and identity

- The product has an independent public name, canonical repository URLs, version, release notes, privacy notice, data-retention explanation, and upstream attribution.
- No public text presents the fork as an upstream release or promises full CLI/MCP parity.

### Installer and lifecycle

- A signed per-user Windows installer provides Start Menu and optional desktop shortcuts and needs no command line.
- The launcher starts one local instance, selects an available port safely, opens the canonical loopback URL with a one-time bootstrap flow, and owns the server lifecycle.
- Port occupation, stale process state, startup failure, browser failure, and shutdown are fail-closed and have visible recovery guidance.
- The installed product contains the React production build and Python runtime only; it excludes Vite dev/preview servers, source maps, interactive API docs, debug tracebacks, test credentials, and the unauthenticated Bridge runtime.

### Credentials, privacy, and data lifecycle

- DPAPI credential creation, migration, rotation, corruption, logout, workspace clear, upgrade, and uninstall-preservation behavior have automated tests.
- Sensitive persistent fields, if any, use a random data key protected by current-user DPAPI.
- Storage usage, retention, per-Workspace clear, explicit redacted export, and diagnostic-log clear are visible in the product.
- Telemetry and crash upload are absent or opt-in with an exact field inventory; AI remains opt-in.

### Upgrade, uninstall, and supply chain

- Upgrade from the previous supported release preserves each Workspace independently and never copies a Platform Session across roles.
- Release metadata and installer hashes are published through the canonical release channel; updates never install silently.
- Binaries and installers are code-signed; CI verifies signatures, locked dependencies, vulnerability policy, license policy, SBOM/provenance where configured, and artifact hashes.
- Uninstall removes application binaries and shortcuts without silently deleting Workspace data. The product documents how to clear data before uninstall or remove retained data afterward.

### Final acceptance

- Installation, first launch, both Core Journeys, recovery, upgrade, and uninstall smoke pass on a clean supported Windows machine using both supported browsers.
- All Developer Preview gates remain green against the exact signed release artifact.
- A release is blocked by any duplicate/unconfirmed Platform Write, cross-Workspace leak, credential/plaintext leak, non-loopback listener, unsigned artifact, or irrecoverable upgrade failure.

## Non-blocking follow-ups

- Additional recruitment platforms, cloud/LAN hosting, multi-operator accounts, automatic background updates, offline Web support, full legacy CLI/MCP parity, and unattended Platform Writes are not release gates because they are outside the first product scope.
