# Physically isolate role workspaces and platform sessions

The Job-Seeking Workspace and Recruiting Workspace use separate data roots and separate Platform Sessions, even when the same Local Operator uses both on one computer. This duplicates some configuration and prevents seamless cross-role state sharing, but makes role boundaries enforceable at the filesystem, credential, API, and run-lifecycle levels; shared mutable storage or a global BOSS session would make accidental cross-workspace disclosure and writes too easy.

## Consequences

- Workspace selection resolves a server-owned storage root and Platform Session; clients never submit filesystem paths or move a session between roles.
- Switching Workspaces cancels active remote access and clears sensitive in-memory state before activating the other Workspace.
- Only non-sensitive application preferences may live outside the two workspace roots.
- Existing single-directory CLI data is treated as legacy input and is never silently assigned to both Workspaces.
