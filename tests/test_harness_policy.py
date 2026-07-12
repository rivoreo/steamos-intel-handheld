from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tdd_workflow_is_part_of_ai_harness():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()
    workflow = (ROOT / "docs/tdd-workflow.md").read_text()
    workflow_norm = " ".join(workflow.split())

    assert "## TDD contract" in harness
    assert "RED" in workflow
    assert "GREEN" in workflow
    assert "VERIFY" in workflow
    required_categories = (
        "reusable code",
        "parsers",
        "validators",
        "generators",
        "installers",
        "recovery",
        "reproducible bug fixes",
        "flash",
        "rollback",
        "AVB",
        "partition",
        "userdata",
        "hardware-write",
        "harness",
        "hook",
        "policy",
        "release artifacts",
        "stable contracts",
    )
    for category in required_categories:
        assert category in workflow
    assert (
        "Direct compiler/linker diagnostics and throwaway probes are exempt only when "
        "they do not change maintained artifacts."
        in workflow_norm
    )
    assert (
        "Any maintained source, build, or package change produced by such a loop still "
        "requires RED/GREEN."
        in workflow_norm
    )
    assert (
        "Before editing behavior-changing maintained code, policy, harness, release "
        "artifacts, or stable contracts"
        in workflow_norm
    )
    assert "Make the smallest maintained-behavior change" in workflow
    assert "production code" not in workflow
    assert "production change" not in workflow
    assert "production code" not in harness


def test_agent_facing_tdd_mirrors_point_to_the_canonical_contract():
    mirrors = (
        (ROOT / "AGENTS.md").read_text(),
        (ROOT / "docs/ai-development-harness.md").read_text(),
        (ROOT / ".github/pull_request_template.md").read_text(),
    )

    for text in mirrors:
        assert "docs/tdd-workflow.md" in text
        assert "maintained repeatable behavior" in text
        assert "Verification remains independent and required" in text


def test_pull_request_template_requires_tdd_evidence():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()
    template = (ROOT / ".github/pull_request_template.md").read_text()

    assert "RED evidence" in template
    assert "GREEN evidence" in template
    assert "Verification evidence" in template
    assert "maintained repeatable behavior" in template
    assert "Verification remains independent and required" in template
    assert "RED evidence, or N/A for exempt work" in template
    assert "GREEN evidence, or N/A for exempt work" in template
    assert "RED and GREEN evidence, or mark each N/A for exempt work" in harness


def test_harness_has_a_single_local_verification_command():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()

    assert "scripts/check-local.sh" in harness
    assert "ruff check src tests scripts" in harness
    assert "ruff check ." not in harness
    assert "bash -n scripts/*.sh" in harness
    assert "pytest" in harness
    assert "compileall" in harness


def test_ai_harness_documents_read_only_hook_reminders():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()

    assert "scripts/harness-hook.py" in harness
    assert "read-only" in harness
    assert "does not run checks" in harness
    assert "does not change repository state" in harness
    assert "scripts/harness-hook.py" in agents
    assert "does not change repository state" in agents
    assert "evidence_artifact_results" in agents
    assert "denies `git commit` while required verification is pending" in agents
    assert "validates declared `evidence_artifacts`" in agents


def test_agents_authorize_optional_bounded_subagent_delegation():
    agents = " ".join((ROOT / "AGENTS.md").read_text().split())

    assert "## Subagent Delegation" in agents
    assert "standing authorization" in agents
    assert "original task" in agents
    assert "does not need to request subagents or approve each delegation" in agents
    assert "optional, not required for every task" in agents
    assert "does not expand task scope or authority" in agents
    assert "destructive actions, device access, and external side effects" in agents
    assert "main agent" in agents
    assert "personally verify" in agents
    assert "After deciding to delegate" in agents
    assert "model-tier-prompting" in agents
    assert "not a permission gate" in agents


def test_local_harness_runs_shell_syntax_check_and_ci_summary():
    script = (ROOT / "scripts/check-local.sh").read_text()

    assert "bash -n scripts/*.sh" in script
    assert "GITHUB_STEP_SUMMARY" in script
    assert "AI-first local harness" in script


def test_agent_docs_use_current_handheld_target_and_generic_placeholder():
    harness = (ROOT / "docs/ai-development-harness.md").read_text()
    workflow = (ROOT / "docs/tdd-workflow.md").read_text()
    qemu_docs = (ROOT / "docs/steamos-qemu-build-env.md").read_text()

    assert "root@192.168.128.214" not in harness
    assert "root@192.168.128.214" not in workflow
    assert "root@192.168.128.214" not in qemu_docs
    assert "root@10.100.0.19" in harness
    assert "root@10.100.0.19" in workflow
    assert "root@<host>" in qemu_docs


def test_github_ci_checks_out_mangohud_submodule():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "uses: actions/checkout@v4" in workflow
    assert "submodules: recursive" in workflow
