# Independent fork governance

This document resolves GitHub Issue #9. The repository is an independently maintained product fork that selectively absorbs upstream fixes; it is not a downstream mirror and does not inherit upstream product direction or release authority.

## Canonical ownership

- `https://github.com/faint4/boss-agent-cli` is the canonical repository during Developer Preview.
- `origin` points to the canonical fork. `upstream` points to `can4hou6joeng4/boss-agent-cli` and is fetch-only for normal maintenance.
- GitHub Issues, specifications, development tickets, CI, tags, releases, and security guidance in the canonical fork are authoritative for this product.
- Upstream copyright, MIT license, and attribution remain intact. Fork-owned documentation and release notes identify the independent maintainer and never imply upstream endorsement.
- The current repository and package name remain during Developer Preview. An independent product name and canonical metadata are required before the First Product Release.

## Branch and release policy

- `master` is the protected integration and release branch while retaining upstream history.
- Product changes use short-lived branches and reviewable pull requests once CI protection is enabled. Emergency direct changes require the same checks and a linked issue.
- Tags and releases belong to the fork and are never copied merely because upstream published them.
- The first independently branded Web product uses a new major version line. Developer Preview versions are prereleases of that line; the First Product Release removes the prerelease marker only after its quality gates pass.
- Release notes distinguish fork features, selected upstream fixes, migrations, security changes, and known limitations.

## Upstream synchronization

Upstream is checked on a deliberate cadence and before a fork release, but never merged automatically into `master`.

For each sync:

1. create a sync issue identifying the upstream range and candidate commits;
2. classify each commit as security/correctness, dependency/build, documentation, product behavior, or irrelevant;
3. select commits individually and record why they are accepted or rejected;
4. apply them on a `sync/upstream-YYYYMMDD` branch by cherry-pick or a narrowly reviewed equivalent;
5. resolve conflicts in favor of the fork's glossary, ADRs, Confirmation Gate, Workspace isolation, and release scope;
6. run the complete affected contract, security, and Core Journey test suites;
7. merge through a pull request whose notes preserve upstream commit references and attribution.

Wholesale upstream merges, automated merge bots, force-pushing the protected branch, and silent adoption of upstream defaults are prohibited. A security fix may be expedited, but still receives a traceable issue, source reference, focused test, and release note.

## Compatibility policy

- Existing CLI and MCP compatibility is preserved where it does not conflict with the independent product's security and Workspace model.
- The fork may make a breaking change on its new major version when keeping upstream behavior would duplicate contracts, weaken Confirmation Gates, share Platform Sessions, or block the Web product.
- Full command parity and exact upstream release timing are not compatibility promises.
- Deprecations name the replacement application command or adapter and remain for a documented migration window unless a security issue requires immediate removal.

## Issue and label policy

- Product specifications and tracer-bullet implementation work live in GitHub Issues.
- `ready-for-agent` means the ticket has a complete user-visible slice, acceptance criteria, and blocking edges and can be implemented without another product interview.
- The Wayfinder map records planning provenance; a specification issue owns the implementation ticket tree.
- Security-sensitive reports avoid credentials and personal data and use GitHub's private security reporting path when public disclosure would create risk.

## Required repository corrections

Before the First Product Release, package metadata, homepage/repository/issues URLs, badges, README ownership text, release workflows, security policy, installer metadata, and application About screen must point to the independent canonical product. References to upstream remain only for attribution, history, and documented sync provenance.
