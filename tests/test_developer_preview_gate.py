from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = REPO_ROOT / "scripts" / "developer_preview_gate.py"


def _run_gate(*args: str) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		[sys.executable, str(GATE_SCRIPT), *args],
		cwd=REPO_ROOT,
		capture_output=True,
		text=True,
		encoding="utf-8",
		check=False,
	)


def _manual_evidence() -> dict[str, object]:
	return {
		"schema_version": "1",
		"product_version": "1.18.0",
		"commit": "a" * 40,
		"windows_version": "Windows 11 24H2",
		"browser": "Chrome 140",
		"source_build_sha256": "b" * 64,
		"checked_at": "2026-08-23T02:00:00Z",
		"workspaces": {
			"job-seeking": {
				"read_journey": "pass",
				"confirmed_write": "pass",
				"write_category": "greeting",
				"result_category": "succeeded",
			},
			"recruiting": {
				"read_journey": "pass",
				"confirmed_write": "pass",
				"write_category": "reply",
				"result_category": "succeeded",
			},
		},
		"recovery_checks": {
			"cancellation": "pass",
			"restart": "pass",
			"authentication_expiry": "pass",
			"rate_limiting": "pass",
			"risk_control": "pass",
			"uncertain_outcome": "pass",
		},
		"privacy_canaries": "pass",
	}


def test_source_gate_accepts_the_committed_production_checkout() -> None:
	result = _run_gate("source", "--repo-root", str(REPO_ROOT))

	assert result.returncode == 0, result.stderr
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["gate"] == "developer-preview-source"
	assert re.fullmatch(r"[0-9a-f]{64}", payload["source_build_sha256"])
	assert {check["name"] for check in payload["checks"]} == {
		"production-assets",
		"source-start-instructions",
		"browser-bridge-isolation",
		"manual-evidence-template",
		"windows-clean-checkout-ci",
	}
	assert all(check["status"] == "pass" for check in payload["checks"])
	attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
	assert "web/index.html text eol=lf" in attributes
	assert "src/boss_agent_cli/web/static/** text eol=lf" in attributes


def test_manual_evidence_validator_accepts_only_redacted_gate_results(tmp_path: Path) -> None:
	evidence = tmp_path / "developer-preview-evidence.json"
	evidence.write_text(json.dumps(_manual_evidence()), encoding="utf-8")

	result = _run_gate("validate-evidence", str(evidence))

	assert result.returncode == 0, result.stderr
	assert json.loads(result.stdout) == {
		"gate": "developer-preview-manual-evidence",
		"ok": True,
		"schema_version": "1",
	}


def test_manual_evidence_validator_rejects_free_text_and_never_echoes_sensitive_values(tmp_path: Path) -> None:
	evidence_payload = _manual_evidence()
	evidence_payload["notes"] = "token=TOKEN_CANARY cookie=COOKIE_CANARY candidate=PERSON_CANARY"
	evidence = tmp_path / "unsafe-evidence.json"
	evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")

	result = _run_gate("validate-evidence", str(evidence))

	assert result.returncode == 2
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"] == "Evidence does not match the redacted schema"
	assert "CANARY" not in result.stdout
	assert result.stderr == ""


def test_manual_evidence_validator_rejects_uncertain_or_rejected_writes(tmp_path: Path) -> None:
	for result_category in ("uncertain", "rejected"):
		evidence_payload = _manual_evidence()
		evidence_payload["workspaces"]["job-seeking"]["result_category"] = result_category  # type: ignore[index]
		evidence = tmp_path / f"{result_category}.json"
		evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")

		result = _run_gate("validate-evidence", str(evidence))

		assert result.returncode == 2
		assert json.loads(result.stdout)["ok"] is False
