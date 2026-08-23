"""Validate Developer Preview source artifacts and redacted manual evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


_TOP_LEVEL_KEYS = {
	"schema_version",
	"product_version",
	"commit",
	"windows_version",
	"browser",
	"source_build_sha256",
	"checked_at",
	"workspaces",
	"recovery_checks",
	"privacy_canaries",
}
_WORKSPACE_KEYS = {"read_journey", "confirmed_write", "write_category", "result_category"}
_RECOVERY_KEYS = {
	"cancellation",
	"restart",
	"authentication_expiry",
	"rate_limiting",
	"risk_control",
	"uncertain_outcome",
}
_PASS = {"pass"}
_WRITE_CATEGORIES = {
	"job-seeking": {"greeting", "application"},
	"recruiting": {"reply"},
}
_RESULT_CATEGORIES = {"succeeded"}
_ASSET_PATTERN = re.compile(r"(?:src|href)=['\"](?:\./|/)?(assets/index-[A-Za-z0-9_-]+\.(?:js|css))['\"]")
_SEMVER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?")
_WINDOWS_PATTERN = re.compile(r"Windows (?:10|11)(?: [0-9A-Za-z.-]+){0,3}")
_BROWSER_PATTERN = re.compile(r"(?:Chrome|Edge) [0-9]+(?:\.[0-9]+){0,3}")
_TIMESTAMP_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _exact_string(value: object, pattern: re.Pattern[str]) -> bool:
	return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_workspace(value: object, workspace: str) -> bool:
	return (
		isinstance(value, dict)
		and set(value) == _WORKSPACE_KEYS
		and value["read_journey"] in _PASS
		and value["confirmed_write"] in _PASS
		and value["write_category"] in _WRITE_CATEGORIES[workspace]
		and value["result_category"] in _RESULT_CATEGORIES
	)


def valid_manual_evidence(value: object) -> bool:
	if not isinstance(value, dict) or set(value) != _TOP_LEVEL_KEYS:
		return False
	workspaces = value.get("workspaces")
	recovery = value.get("recovery_checks")
	return (
		value.get("schema_version") == "1"
		and _exact_string(value.get("product_version"), _SEMVER_PATTERN)
		and _exact_string(value.get("commit"), re.compile(r"[0-9a-f]{40}"))
		and _exact_string(value.get("windows_version"), _WINDOWS_PATTERN)
		and _exact_string(value.get("browser"), _BROWSER_PATTERN)
		and _exact_string(value.get("source_build_sha256"), re.compile(r"[0-9a-f]{64}"))
		and _exact_string(value.get("checked_at"), _TIMESTAMP_PATTERN)
		and isinstance(workspaces, dict)
		and set(workspaces) == set(_WRITE_CATEGORIES)
		and all(_valid_workspace(workspaces[name], name) for name in _WRITE_CATEGORIES)
		and isinstance(recovery, dict)
		and set(recovery) == _RECOVERY_KEYS
		and all(result in _PASS for result in recovery.values())
		and value.get("privacy_canaries") in _PASS
	)


def _source_checks(repo_root: Path) -> list[dict[str, str]]:
	static_root = repo_root / "src" / "boss_agent_cli" / "web" / "static"
	index = static_root / "index.html"
	assets_ok = False
	if index.is_file():
		asset_references = set(_ASSET_PATTERN.findall(index.read_text(encoding="utf-8")))
		assets_ok = (
			len(asset_references) >= 2
			and all((static_root / reference).is_file() for reference in asset_references)
			and not any(static_root.rglob("*.map"))
		)

	getting_started = (repo_root / "docs" / "getting-started.md").read_text(encoding="utf-8")
	getting_started_en = (repo_root / "docs" / "getting-started.en.md").read_text(encoding="utf-8")
	instructions_ok = all(
		marker in getting_started and marker in getting_started_en
		for marker in ("uv sync --all-extras", "pnpm install --frozen-lockfile", "pnpm run build", "uv run boss-web")
	)

	web_python = "\n".join(
		path.read_text(encoding="utf-8")
		for path in (repo_root / "src" / "boss_agent_cli" / "web").glob("*.py")
	)
	bridge_isolated = "boss_agent_cli.bridge" not in web_python and "/api/v1/bridge" not in web_python

	template = repo_root / "docs" / "developer-preview-evidence.template.json"
	try:
		template_ok = valid_manual_evidence(json.loads(template.read_text(encoding="utf-8")))
	except (OSError, json.JSONDecodeError):
		template_ok = False

	workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
	attributes = (repo_root / ".gitattributes").read_text(encoding="utf-8")
	windows_ci_ok = all(
		line in attributes
		for line in ("web/index.html text eol=lf", "src/boss_agent_cli/web/static/** text eol=lf")
	) and all(
		marker in workflow
		for marker in (
			"developer_preview_windows:",
			"runs-on: windows-latest",
			"pnpm install --frozen-lockfile",
			"uv run patchright install chromium",
			'BOSS_RUN_BROWSER_TESTS: "1"',
			"uv run pytest -q tests/",
		)
	)

	checks = (
		("production-assets", assets_ok),
		("source-start-instructions", instructions_ok),
		("browser-bridge-isolation", bridge_isolated),
		("manual-evidence-template", template_ok),
		("windows-clean-checkout-ci", windows_ci_ok),
	)
	return [{"name": name, "status": "pass" if passed else "fail"} for name, passed in checks]


def _source_build_sha256(repo_root: Path) -> str:
	"""Fingerprint the lockfiles and committed production Web assets."""
	paths = [repo_root / "pyproject.toml", repo_root / "uv.lock", repo_root / "web" / "pnpm-lock.yaml"]
	static_root = repo_root / "src" / "boss_agent_cli" / "web" / "static"
	paths.extend(sorted(path for path in static_root.rglob("*") if path.is_file()))
	digest = hashlib.sha256()
	for path in paths:
		relative = path.relative_to(repo_root).as_posix().encode("utf-8")
		contents = path.read_bytes()
		digest.update(len(relative).to_bytes(4, "big"))
		digest.update(relative)
		digest.update(len(contents).to_bytes(8, "big"))
		digest.update(contents)
	return digest.hexdigest()


def _emit(payload: object) -> None:
	print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _source_command(repo_root: Path) -> int:
	repo_root = repo_root.resolve()
	checks = _source_checks(repo_root)
	ok = all(check["status"] == "pass" for check in checks)
	_emit(
		{
			"schema_version": "1",
			"gate": "developer-preview-source",
			"ok": ok,
			"source_build_sha256": _source_build_sha256(repo_root),
			"checks": checks,
		}
	)
	return 0 if ok else 1


def _validate_evidence_command(path: Path) -> int:
	try:
		value: Any = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		value = None
	if not valid_manual_evidence(value):
		_emit(
			{
				"schema_version": "1",
				"gate": "developer-preview-manual-evidence",
				"ok": False,
				"error": "Evidence does not match the redacted schema",
			}
		)
		return 2
	_emit({"schema_version": "1", "gate": "developer-preview-manual-evidence", "ok": True})
	return 0


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	subparsers = parser.add_subparsers(dest="command", required=True)
	source = subparsers.add_parser("source", help="Validate committed source-delivered production artifacts")
	source.add_argument("--repo-root", type=Path, default=Path.cwd())
	evidence = subparsers.add_parser("validate-evidence", help="Validate a redacted manual real-BOSS record")
	evidence.add_argument("path", type=Path)
	args = parser.parse_args(argv)
	if args.command == "source":
		return _source_command(args.repo_root)
	return _validate_evidence_command(args.path)


if __name__ == "__main__":
	sys.exit(main())
