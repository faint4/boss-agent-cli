# Developer Preview acceptance

Developer Preview is the source-delivered Windows milestone. It uses the React production build and one Python local service; it is neither a Vite development server nor the First Product Release installer.

## Clean checkout and startup

Run the following in a new directory on Windows 11 x64:

```powershell
git clone https://github.com/faint4/boss-agent-cli.git
Set-Location boss-agent-cli
uv sync --all-extras
corepack enable
Set-Location web
pnpm install --frozen-lockfile
pnpm test
pnpm run typecheck
pnpm run build
Set-Location ..
uv run patchright install chromium
uv run python scripts/developer_preview_gate.py source --repo-root .
uv run boss-web
```

The final command opens the default browser only after binding `127.0.0.1:0`. Its bootstrap value is in a one-time URL fragment that disappears after authentication. Press `Ctrl+C` to stop the source preview explicitly. Never connect a real account to a Vite `dev` or `preview` server.

## Automated gate

Run before submitting a change:

```powershell
uv run pytest tests/ -q
uv run ruff check src/ tests/ mcp-server/
uv run mypy src/boss_agent_cli
Set-Location web
pnpm test
pnpm run typecheck
pnpm run build
Set-Location ..
uv run python scripts/developer_preview_gate.py source --repo-root .
```

Retain the command's `source_build_sha256` output. It is a deterministic fingerprint of the Python/Web lockfiles and the production assets actually served by Python; copy that exact value into the manual evidence field with the same name.

GitHub's `Developer Preview (Windows)` gate reinstalls locked dependencies on Windows, rebuilds production assets, installs the pinned Patchright Chromium, and runs application-contract, local-Web, negative-security, recovery, privacy, and real-browser tests. The browser test uses the fake BOSS boundary: it never contacts a real account, but it completes both Core Journeys from the production Python service, crosses each individual Confirmation Gate, checks a narrow layout, and proves browser storage remains empty.

Automation covers exact IPv4 loopback/Host/startup authentication/Origin/Fetch Metadata/JSON/CORS/header controls; Workspace path, database, Platform Session, Run, clear, and export isolation; Write Intent mutation, expiry, replay, double-click, restart, and uncertainty; cancellation and recovery stops; privacy canaries; and Browser Bridge exclusion.

## Manual real-BOSS gate

Manual platform testing is limited to four actions:

1. Click Connect in Job-Seeking, establish its dedicated Platform Session through the official BOSS window, and complete one read-only Core Journey.
2. Prepare one low-impact greeting or application, inspect its target and final content, and confirm it once at the Confirmation Gate.
3. Switch to Recruiting, establish a separate Platform Session through the official window, and complete one Inbound Applicants read journey.
4. Prepare one low-impact reply, inspect its target and final content, and confirm it once at the Confirmation Gate.

Stop immediately on authentication expiry, throttling, risk control, or an uncertain result. Do not retry or bypass it, and do not record that item as `pass`. Evidence must never contain cookies, tokens, `security_id`, names, phone numbers, contact handles, company-private information, resumes, chats, or message bodies.

Copy the [redacted template](developer-preview-evidence.template.json) to a controlled location outside the repository, fill only version/commit/Windows/browser, the `source_build_sha256` emitted above, and enumerated outcomes, then validate it:

```powershell
uv run python scripts/developer_preview_gate.py validate-evidence C:\path\to\developer-preview-evidence.json
```

The validator rejects extra fields and free text without echoing submitted values. Do not commit the evidence file to the public repository; the release owner retains the validated record as restricted release evidence.

## Completion rule

Issue #25 closes only after all Windows, Python, Web, browser, and security automation passes and valid redacted evidence exists for both real read journeys and one consciously confirmed low-impact Platform Write in each Workspace. Any unconfirmed or duplicate write, cross-Workspace leak, credential or personal-data leak, non-loopback listener, or automatic retry after risk control or uncertainty blocks Developer Preview.
