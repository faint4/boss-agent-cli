"""Executable checks and review artifacts for the independent fork."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


CANONICAL_REPOSITORY = "https://github.com/faint4/boss-agent-cli"
UPSTREAM_REPOSITORY = "https://github.com/can4hou6joeng4/boss-agent-cli"

_PUBLIC_PATHS = (
	Path("pyproject.toml"),
	Path("README.md"),
	Path("README.en.md"),
	Path("CONTRIBUTING.md"),
	Path("CONTRIBUTING.en.md"),
	Path("SECURITY.md"),
	Path("Dockerfile"),
	Path(".github"),
	Path("demo"),
	Path("docs/getting-started.md"),
	Path("docs/getting-started.en.md"),
	Path("docs/marketing"),
	Path("docs/maintainer"),
	Path("src/boss_agent_cli/digest.py"),
	Path("src/boss_agent_cli/commands/stats.py"),
)
_UPSTREAM_REFERENCE_PATHS = {Path(".github/ISSUE_TEMPLATE/upstream_sync.yml")}


def _iter_public_files(root: Path):
	for relative in _PUBLIC_PATHS:
		path = root / relative
		if path.is_file():
			yield path
		elif path.is_dir():
			yield from (
				item
				for item in path.rglob("*")
				if item.is_file() and item.suffix.lower() in {".html", ".md", ".txt", ".toml", ".yaml", ".yml"}
			)


def verify_metadata(root: Path) -> list[str]:
	errors: list[str] = []
	pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
	for expected in (
		f'Homepage = "{CANONICAL_REPOSITORY}"',
		f'Repository = "{CANONICAL_REPOSITORY}"',
		f'Issues = "{CANONICAL_REPOSITORY}/issues"',
	):
		if expected not in pyproject:
			errors.append(f"pyproject.toml is missing {expected}")
	for path in _iter_public_files(root):
		try:
			body = path.read_text(encoding="utf-8")
		except UnicodeDecodeError:
			continue
		if UPSTREAM_REPOSITORY in body and path.relative_to(root) not in _UPSTREAM_REFERENCE_PATHS:
			errors.append(f"stale canonical link: {path.relative_to(root)}")
	return errors


def _root_from_script() -> Path:
	return Path(__file__).resolve().parents[1]


def build_upstream_review(
	*,
	sources: list[str],
	impact: str,
	tests: str,
	dry_run: bool,
) -> str:
	lines = []
	if dry_run:
		lines.extend(("> **DRY RUN** — review workflow rehearsal; do not apply commits.", ""))
	lines.extend(("## Upstream source commits", ""))
	for source in sources:
		lines.append(f"- [can4hou6joeng4/boss-agent-cli@{source}]({UPSTREAM_REPOSITORY}/commit/{source})")
	lines.extend(
		(
			"",
			"## Local impact",
			"",
			impact,
			"",
			"## Verification",
			"",
			f"- `{tests}`",
			"",
			"## Attribution and application",
			"",
			"- Cherry-pick with `-x` so the upstream source remains in commit history.",
			"- Preserve upstream copyright and license notices.",
			"",
			"## Decision",
			"",
			"- [ ] Accept each listed commit individually",
			"- [ ] Reject with a recorded reason",
		)
	)
	return "\n".join(lines)


def verify_pull_request(repository: Path, *, base: str, head: str) -> list[str]:
	ancestor = subprocess.run(
		["git", "merge-base", "--is-ancestor", base, head],
		cwd=repository,
		capture_output=True,
		text=True,
		check=False,
	)
	if ancestor.returncode != 0:
		return ["pull request head must descend from the declared base"]
	merges = subprocess.run(
		["git", "rev-list", "--merges", f"{base}..{head}"],
		cwd=repository,
		capture_output=True,
		text=True,
		check=True,
	).stdout.splitlines()
	if merges:
		return [f"merge commits are forbidden in contribution branches: {', '.join(merges)}"]
	return []


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	subparsers = parser.add_subparsers(dest="command", required=True)
	subparsers.add_parser("verify-metadata", help="check canonical fork links and package metadata")
	draft = subparsers.add_parser("draft-upstream-review", help="print a traceable sync issue body")
	draft.add_argument("--source", action="append", required=True, help="upstream commit SHA")
	draft.add_argument("--impact", required=True, help="expected local product impact")
	draft.add_argument("--tests", required=True, help="verification command or evidence")
	draft.add_argument("--dry-run", action="store_true", help="mark output as a non-applying rehearsal")
	verify_pr = subparsers.add_parser("verify-pr", help="reject non-linear pull request history")
	verify_pr.add_argument("--repository", type=Path, default=_root_from_script())
	verify_pr.add_argument("--base", required=True, help="base commit SHA")
	verify_pr.add_argument("--head", required=True, help="head commit SHA")
	args = parser.parse_args(argv)
	if args.command == "verify-metadata":
		errors = verify_metadata(_root_from_script())
		if errors:
			for error in errors:
				print(error, file=sys.stderr)
			return 1
		print("canonical fork metadata: ok")
	elif args.command == "draft-upstream-review":
		print(
			build_upstream_review(
				sources=args.source,
				impact=args.impact,
				tests=args.tests,
				dry_run=args.dry_run,
			)
		)
	elif args.command == "verify-pr":
		errors = verify_pull_request(args.repository, base=args.base, head=args.head)
		if errors:
			for error in errors:
				print(error, file=sys.stderr)
			return 1
		print("pull request history: linear")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
