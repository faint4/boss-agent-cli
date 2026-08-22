# Make the application module authoritative

Web, CLI, and MCP will call one deep Python application module whose interface owns Workspace commands, queries, Recoverable Runs, Write Intents, state snapshots, and domain errors. This replaces four parallel capability contracts with one seam: transport adapters may preserve compatibility, but Click registration, Web routing, schema generation, and MCP exposure must not reimplement workflow or safety decisions.

## Consequences

- The module interface and its fake platform adapters become the highest behavioral test seam.
- Platform clients, persistence, DPAPI, clock, and identifiers remain internal seams injected into the module implementation.
- The Web interface is a versioned transport over the module, while CLI and MCP call it in process.
- Migration uses expand–migrate–contract so existing CLI and MCP behavior remains green while duplicated mappings are removed.
