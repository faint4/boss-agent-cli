from pathlib import Path
import subprocess
import sys

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE_SCRIPT = REPOSITORY_ROOT / "scripts" / "fork_governance.py"


def _git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		[
			"git",
			"-c",
			"user.name=Fork Governance Test",
			"-c",
			"user.email=fork-governance@example.invalid",
			*args,
		],
		cwd=repository,
		capture_output=True,
		text=True,
		check=True,
	)


def test_canonical_fork_metadata_passes_the_maintainer_check():
	result = subprocess.run(
		[sys.executable, str(GOVERNANCE_SCRIPT), "verify-metadata"],
		cwd=REPOSITORY_ROOT,
		capture_output=True,
		text=True,
		check=False,
	)

	assert result.returncode == 0, result.stderr
	assert result.stdout.strip() == "canonical fork metadata: ok"


def test_upstream_review_dry_run_records_provenance_impact_and_verification():
	result = subprocess.run(
		[
			sys.executable,
			str(GOVERNANCE_SCRIPT),
			"draft-upstream-review",
			"--source",
			"abc1234",
			"--impact",
			"No product behavior changes; governance rehearsal only.",
			"--tests",
			"python scripts/fork_governance.py verify-metadata",
			"--dry-run",
		],
		cwd=REPOSITORY_ROOT,
		capture_output=True,
		text=True,
		check=False,
	)

	assert result.returncode == 0, result.stderr
	assert "## Upstream source commits" in result.stdout
	assert "can4hou6joeng4/boss-agent-cli@abc1234" in result.stdout
	assert "## Local impact" in result.stdout
	assert "No product behavior changes" in result.stdout
	assert "## Verification" in result.stdout
	assert "fork_governance.py verify-metadata" in result.stdout
	assert "Cherry-pick with `-x`" in result.stdout
	assert "DRY RUN" in result.stdout


def test_pull_request_check_rejects_merge_commits(tmp_path):
	_git(tmp_path, "init", "-b", "master")
	(tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
	_git(tmp_path, "add", "base.txt")
	_git(tmp_path, "commit", "-m", "base")
	base = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
	_git(tmp_path, "switch", "-c", "topic")
	(tmp_path / "topic.txt").write_text("topic\n", encoding="utf-8")
	_git(tmp_path, "add", "topic.txt")
	_git(tmp_path, "commit", "-m", "topic")
	_git(tmp_path, "switch", "master")
	(tmp_path / "master.txt").write_text("master\n", encoding="utf-8")
	_git(tmp_path, "add", "master.txt")
	_git(tmp_path, "commit", "-m", "master")
	_git(tmp_path, "merge", "--no-ff", "topic", "-m", "merge topic")
	head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

	result = subprocess.run(
		[
			sys.executable,
			str(GOVERNANCE_SCRIPT),
			"verify-pr",
			"--repository",
			str(tmp_path),
			"--base",
			base,
			"--head",
			head,
		],
		cwd=REPOSITORY_ROOT,
		capture_output=True,
		text=True,
		check=False,
	)

	assert result.returncode == 1
	assert "merge commits are forbidden" in result.stderr


def test_upstream_sync_issue_form_requires_traceability_fields():
	template_path = REPOSITORY_ROOT / ".github" / "ISSUE_TEMPLATE" / "upstream_sync.yml"
	template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
	fields = {item["id"]: item for item in template["body"] if item.get("type") in {"input", "textarea"}}

	for field_id in ("source_commits", "local_impact", "verification", "attribution"):
		assert fields[field_id]["validations"]["required"] is True


def test_ci_enforces_canonical_metadata_and_linear_pull_requests():
	workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

	assert "python scripts/fork_governance.py verify-metadata" in workflow
	assert "python scripts/fork_governance.py verify-pr" in workflow
	assert "fetch-depth: 0" in workflow
