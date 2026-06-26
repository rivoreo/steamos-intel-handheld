from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO_BASE = "https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos"
README = ROOT / "README.md"
PACKAGE_DOCS = ROOT / "docs/package-repository.md"
RELEASE_DOCS = ROOT / "docs/release-process.md"
RELEASE_SKILL = ROOT / ".codex/skills/arch-release-publisher/SKILL.md"
RELEASE_SKILL_METADATA = (
    ROOT / ".codex/skills/arch-release-publisher/agents/openai.yaml"
)


def test_release_process_runbook_is_discoverable() -> None:
    docs = RELEASE_DOCS.read_text()

    assert "# Release Process" in docs
    assert "docs/release-process.md" in README.read_text()
    assert "docs/release-process.md" in PACKAGE_DOCS.read_text()
    assert ".codex/skills/arch-release-publisher" in docs


def test_release_process_runbook_documents_release_channels_and_secrets() -> None:
    docs = RELEASE_DOCS.read_text()

    assert "Stable tags: `vX.Y.Z`" in docs
    assert "Candidate tags: `vX.Y.Z-rc.N`" in docs
    assert "deploys GitHub Pages" in docs
    assert "skips `deploy-pages`" in docs
    assert "signed-pacman-repository" in docs
    assert "short-lived candidate signing key" in docs
    assert "stable releases require the protected signing secrets" in docs
    assert "ARCH_REPO_GPG_PRIVATE_KEY" in docs
    assert "ARCH_REPO_GPG_PASSPHRASE" in docs
    assert "ARCH_REPO_GPG_KEY_ID" in docs


def test_release_process_runbook_includes_operator_commands() -> None:
    docs = RELEASE_DOCS.read_text()

    assert 'git tag -a v0.1.0-rc.5 -m "v0.1.0-rc.5"' in docs
    assert "git push origin v0.1.0-rc.5" in docs
    assert "gh run list --repo rivoreo/steamos-intel-handheld" in docs
    assert "gh run watch <run-id>" in docs
    assert "gh run view <run-id> --log-failed" in docs
    assert "gh run download <run-id>" in docs
    assert "gh api repos/rivoreo/steamos-intel-handheld/actions/runs/<run-id>/artifacts" in docs


def test_release_process_runbook_includes_build_scope_and_install_path() -> None:
    docs = RELEASE_DOCS.read_text()
    normalized_docs = " ".join(docs.split())

    assert "steamos-intel-handheld" in docs
    assert "rivoreo-keyring" in docs
    assert "rivoreo-steamos-repo" in docs
    assert "steamos-intel-handheld-mangoapp" in docs
    assert "package versions derive from `pyproject.toml`" in docs
    assert (
        "repo aliases `.db`, `.files`, and `.sig` are regular files"
        in normalized_docs
    )
    assert (
        f"curl -fsSL {PUBLIC_REPO_BASE}/bootstrap.sh | sudo bash"
        in docs
    )
    assert "https://holo.libz.so" not in docs
    assert "http://" not in docs
    assert "Users should not install from hidden release-candidate artifacts" in docs


def test_release_process_runbook_captures_first_rc_failure_modes() -> None:
    docs = RELEASE_DOCS.read_text()

    assert "Install checkout dependencies" in docs
    assert "missing signing secrets" in docs
    assert "candidate signing fallback" in docs
    assert "python-setuptools" in docs
    assert "setuptools.build_meta" in docs


def test_arch_release_publisher_skill_exists_and_points_to_runbook() -> None:
    skill = RELEASE_SKILL.read_text()

    assert "name: arch-release-publisher" in skill
    assert "docs/release-process.md" in skill
    assert "Read `docs/release-process.md` before changing release behavior" in skill
    assert "stable release" in skill
    assert "hidden release candidate" in skill
    assert "`vX.Y.Z`" in skill
    assert "`vX.Y.Z-rc.N`" in skill
    assert "signed-pacman-repository" in skill
    assert "deploy-pages" in skill
    assert "ARCH_REPO_GPG_PRIVATE_KEY" in skill


def test_arch_release_publisher_skill_metadata_is_discoverable() -> None:
    metadata = RELEASE_SKILL_METADATA.read_text()

    assert 'display_name: "Arch Release Publisher"' in metadata
    assert 'short_description: "Arch package release runbook"' in metadata
    assert "$arch-release-publisher" in metadata
