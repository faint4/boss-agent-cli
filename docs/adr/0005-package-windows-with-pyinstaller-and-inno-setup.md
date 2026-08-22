# Package Windows with PyInstaller onedir and Inno Setup

Developer Preview runs from source, packaging rehearsal produces a PyInstaller `onedir` ZIP, and the First Product Release wraps that same directory in a per-user Inno Setup installer distributed through immutable GitHub Releases. This keeps the existing Python/Hatchling core and makes runtime contents inspectable, while avoiding `onefile` extraction, MSI/MSIX identity complexity, Windows services, silent updates, and a second application framework before the local Web product is stable.

## Consequences

- The installed launcher is a windowless single process that binds `127.0.0.1:0`, owns its child processes, opens the browser only after readiness, and never runs Vite in production.
- Full installer updates require explicit operator confirmation plus GitHub digest and Authenticode publisher verification; no delta or background updater exists in the first release.
- First Product Release artifacts use a stable Inno `AppId`, stable RSA publisher identity, RFC 3161 timestamping, and signed loaded PE files, installer, and uninstaller.
- Uninstall removes binaries, runtime secrets, and Platform Session credentials while preserving user-created Workspace data by default unless the Local Operator explicitly chooses the scoped data-removal option.
