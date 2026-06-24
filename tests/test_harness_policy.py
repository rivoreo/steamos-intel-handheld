from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tdd_workflow_is_part_of_ai_harness():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()
    workflow = (ROOT / "docs/tdd-workflow.md").read_text()

    assert "## TDD contract" in harness
    assert "RED" in workflow
    assert "GREEN" in workflow
    assert "VERIFY" in workflow
    assert "No production behavior change may be merged without RED evidence" in workflow


def test_pull_request_template_requires_tdd_evidence():
    template = (ROOT / ".github/pull_request_template.md").read_text()

    assert "RED evidence" in template
    assert "GREEN evidence" in template
    assert "Verification evidence" in template
    assert "No production behavior change without a failing test first" in template


def test_harness_has_a_single_local_verification_command():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()

    assert "scripts/check-local.sh" in harness
    assert "ruff check" in harness
    assert "pytest" in harness
    assert "compileall" in harness


def test_github_ci_checks_out_mangohud_submodule():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "uses: actions/checkout@v4" in workflow
    assert "submodules: recursive" in workflow
