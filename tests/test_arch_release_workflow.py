from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO_BASE = "https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos"
BOOTSTRAP_INSTALL_COMMAND = (
    "pacman -S --needed rivoreo-keyring rivoreo-steamos-repo "
    "steamos-intel-handheld steamos-intel-handheld-mangoapp"
)
WORKFLOW = ROOT / ".github/workflows/arch-release.yml"
PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
BUILD_SCRIPT = ROOT / "scripts/build-arch-release-repo.sh"
ASSEMBLE_SCRIPT = ROOT / "scripts/assemble-arch-release-pages.sh"
BOOTSTRAP = ROOT / "site/rivoreo-steamos/bootstrap.sh"
PACKAGE_DOCS = ROOT / "docs/package-repository.md"
MAIN_PKGBUILD = ROOT / "packaging/arch/PKGBUILD"
KEYRING_PKGBUILD = ROOT / "packaging/arch/rivoreo-keyring/PKGBUILD"
REPO_PKGBUILD = ROOT / "packaging/arch/rivoreo-steamos-repo/PKGBUILD"
REPO_CONF = ROOT / "packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf"
MANGOAPP_PKGBUILD = ROOT / "packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD"


def _logical_shell_lines(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return lines


def _line_index_containing(text: str, *needles: str) -> int:
    for index, line in enumerate(_logical_shell_lines(text)):
        if all(needle in line for needle in needles):
            return index
    raise AssertionError(f"Could not find line containing: {needles}")


def _line_indices_containing(text: str, *needles: str) -> list[int]:
    return [
        index
        for index, line in enumerate(_logical_shell_lines(text))
        if all(needle in line for needle in needles)
    ]


def test_arch_release_workflow_is_tag_only_and_uses_recursive_checkout() -> None:
    workflow = WORKFLOW.read_text()

    assert "tags:" in workflow
    assert '"v*.*.*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "tag:" in workflow
    assert "branches:" not in workflow
    assert "submodules: recursive" in workflow
    assert "fetch-depth: 0" in workflow


def test_arch_release_workflow_uses_protected_signing_secrets_and_pages_deploy() -> None:
    workflow = WORKFLOW.read_text()

    assert "ARCH_REPO_GPG_PRIVATE_KEY" in workflow
    assert "ARCH_REPO_GPG_PASSPHRASE" in workflow
    assert "ARCH_REPO_GPG_KEY_ID" in workflow
    assert "environment:" in workflow
    assert "github-pages" in workflow
    assert "actions/upload-pages-artifact@v4" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert "needs: [validate, build-repo]" in workflow


def test_arch_release_workflow_builds_mangoapp_before_repository_package_build() -> None:
    workflow = WORKFLOW.read_text()

    download_line = _line_index_containing(workflow, "Download patched mangoapp binary")
    chmod_line = _line_index_containing(
        workflow,
        "chmod 0755",
        ".cache/arch-release/mangoapp/mangoapp",
    )
    build_repo_line = _line_index_containing(workflow, "Build signed pacman repository")

    assert "build-mangoapp:" in workflow
    assert "Build patched mangoapp in SteamOS rootfs chroot" in workflow
    assert "scripts/steamos-qemu-build-env.sh fetch-raw" in workflow
    assert "scripts/steamos-qemu-build-env.sh prepare-rootfs" in workflow
    assert "scripts/steamos-qemu-build-env.sh build-mangoapp-rootfs" in workflow
    assert "scripts/steamos-qemu-build-env.sh provision" not in workflow
    assert "scripts/steamos-qemu-build-env.sh run-build" not in workflow
    assert "ssh_ready" not in workflow
    assert "mangoapp-binary" in workflow
    assert "needs: [validate, build-mangoapp]" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "MANGOAPP_BINARY=" in workflow
    assert download_line < chmod_line < build_repo_line


def test_arch_release_workflow_uses_linux_rootfs_chroot_for_mangoapp_build() -> None:
    workflow = WORKFLOW.read_text()

    build_mangoapp_line = _line_index_containing(workflow, "build-mangoapp:")
    runner_line = next(
        index
        for index in _line_indices_containing(workflow, "runs-on:", "ubuntu-latest")
        if index > build_mangoapp_line
    )

    assert build_mangoapp_line < runner_line
    assert "STEAMOS_ROOTFS_DIR=" in workflow
    assert "macos-15-intel" not in workflow
    assert "runs-on: macos-13" not in workflow
    assert "brew install qemu expect" not in workflow
    assert "qemu-system-x86_64" not in workflow


def test_arch_release_workflow_keeps_prerelease_tags_hidden_from_pages() -> None:
    workflow = WORKFLOW.read_text()

    assert "publish_pages:" in workflow
    assert "^v[0-9]+[.][0-9]+[.][0-9]+$" in workflow
    assert 'echo "publish_pages=true" >> "$GITHUB_OUTPUT"' in workflow
    assert 'echo "publish_pages=false" >> "$GITHUB_OUTPUT"' in workflow
    assert "needs.validate.outputs.publish_pages == 'true'" in workflow
    assert "needs: [validate, build-repo]" in workflow


def test_arch_release_workflow_installs_git_before_container_checkout() -> None:
    workflow = WORKFLOW.read_text()
    build_repo_line = _line_index_containing(workflow, "build-repo:")
    dependency_line = _line_index_containing(workflow, "Install checkout dependencies")
    checkout_line = next(
        index
        for index in _line_indices_containing(workflow, "uses: actions/checkout@v4")
        if index > build_repo_line
    )

    assert "pacman -S --noconfirm git" in workflow
    assert build_repo_line < dependency_line < checkout_line


def test_arch_release_workflow_can_use_ephemeral_candidate_signing_key_without_secrets() -> None:
    workflow = WORKFLOW.read_text()

    assert "PUBLISH_PAGES: ${{ needs.validate.outputs.publish_pages }}" in workflow
    assert 'if [ "$PUBLISH_PAGES" = "true" ]; then' in workflow
    assert "Missing Arch release signing secrets" in workflow
    assert "Rivoreo candidate signing key" in workflow
    assert "quick-generate-key" in workflow
    assert 'ARCH_REPO_GPG_KEY_ID=$imported' in workflow
    assert 'GPGKEY=$imported' in workflow


def test_ordinary_pages_workflow_cannot_overwrite_release_repository() -> None:
    workflow = PAGES_WORKFLOW.read_text()

    assert "deploy-pages" not in workflow
    assert "upload-pages-artifact" not in workflow
    assert "push:" not in workflow


def test_release_build_script_signs_packages_and_regularizes_repo_aliases() -> None:
    script = BUILD_SCRIPT.read_text()

    assert "makepkg --cleanbuild --syncdeps --noconfirm --needed --sign" in script
    assert "repo-add --sign --verify" in script
    assert "rivoreo-steamos.db.tar.zst" in script
    assert 'cp "$repo_db" "$repo_out/rivoreo-steamos.db"' in script
    assert 'cp "$repo_files" "$repo_out/rivoreo-steamos.files"' in script
    assert "gpg --batch --export" in script
    assert "updpkgsums" in script


def test_release_build_script_removes_repo_add_alias_symlinks_before_copying_aliases() -> None:
    script = BUILD_SCRIPT.read_text()
    repo_add_line = _line_index_containing(script, "repo-add --sign --verify")

    aliases = (
        "rivoreo-steamos.db",
        "rivoreo-steamos.db.sig",
        "rivoreo-steamos.files",
        "rivoreo-steamos.files.sig",
    )
    for alias in aliases:
        alias_path = f'"$repo_out/{alias}"'
        remove_line = _line_index_containing(script, "rm -f", alias_path)
        copy_line = _line_index_containing(script, "cp ", alias_path)

        assert repo_add_line < remove_line < copy_line


def test_release_build_script_syncs_main_pkgbuild_version_before_building() -> None:
    script = BUILD_SCRIPT.read_text()
    main_sync_line = _line_index_containing(
        script,
        "sed -i",
        "pkgver=$pkgver",
        "packaging/arch/PKGBUILD",
    )
    mangoapp_sync_line = _line_index_containing(
        script,
        "sed -i",
        "pkgver=$pkgver",
        "packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD",
    )
    build_line = _line_index_containing(script, "build_pkg packaging/arch")

    assert main_sync_line < build_line
    assert mangoapp_sync_line < build_line


def test_release_build_script_builds_all_release_packages_including_mangoapp() -> None:
    script = BUILD_SCRIPT.read_text()

    assert "prepare_mangoapp_package_inputs" in script
    assert "MANGOAPP_BINARY" in script
    assert "external/MangoHud/LICENSE" in script
    assert "10-rivoreo-mangoapp.conf" in script
    assert "build_pkg packaging/arch/steamos-intel-handheld-mangoapp" in script


def test_release_build_script_accepts_candidate_tags_for_pyproject_version() -> None:
    script = BUILD_SCRIPT.read_text()

    assert "release_tag_pattern=" in script
    assert "v${pkgver//./[.]}" in script
    assert "([-.][A-Za-z0-9._]+)?" in script
    assert '[[ ! "$release_tag" =~ $release_tag_pattern ]]' in script
    assert '[ "$release_tag" != "v$pkgver" ]' not in script


def test_release_pages_assembler_renders_fingerprint_and_checks_artifact_shape() -> None:
    script = ASSEMBLE_SCRIPT.read_text()

    assert "__RIVOREO_KEY_FINGERPRINT__" in script
    assert "RIVOREO_KEY_FINGERPRINT" in script
    assert 'test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db"' in script
    assert 'test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db.sig"' in script
    assert 'test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files"' in script
    assert 'test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files.sig"' in script
    assert "fingerprint.txt" in script


def test_release_packages_are_defined_for_keyring_and_repo_config() -> None:
    keyring = KEYRING_PKGBUILD.read_text()
    repo_pkg = REPO_PKGBUILD.read_text()
    repo_conf = REPO_CONF.read_text()
    mangoapp = MANGOAPP_PKGBUILD.read_text()

    assert "pkgname=rivoreo-keyring" in keyring
    assert "rivoreo-trusted" in keyring
    assert "pkgname=rivoreo-steamos-repo" in repo_pkg
    assert 'backup=("etc/pacman.d/rivoreo-steamos.conf")' in repo_pkg
    assert "[rivoreo-steamos]" in repo_conf
    assert "SigLevel = Required TrustedOnly" in repo_conf
    assert f"Server = {PUBLIC_REPO_BASE}/os/$arch" in repo_conf
    assert "pkgname=steamos-intel-handheld-mangoapp" in mangoapp
    assert 'arch=("x86_64")' in mangoapp
    assert 'source=("mangoapp" "10-rivoreo-mangoapp.conf" "MangoHud-LICENSE")' in mangoapp
    assert "/opt/steamos-intel-handheld/bin/mangoapp" in mangoapp
    assert (
        "/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf"
        in mangoapp
    )
    assert "/usr/share/licenses/$pkgname/MangoHud-LICENSE" in mangoapp


def test_release_pkgbuilds_do_not_ship_main_package_with_skip_checksum() -> None:
    pkgbuild = MAIN_PKGBUILD.read_text()

    assert 'sha256sums=("SKIP")' not in pkgbuild
    assert "sha256sums=(" in pkgbuild


def test_main_pkgbuild_declares_python_build_backend_dependency() -> None:
    pkgbuild = MAIN_PKGBUILD.read_text()

    assert '"python-setuptools"' in pkgbuild


def test_active_bootstrap_is_fingerprint_pinned_and_secure() -> None:
    bootstrap = BOOTSTRAP.read_text()

    assert "__RIVOREO_KEY_FINGERPRINT__" in bootstrap
    assert f'REPO_BASE_URL:-{PUBLIC_REPO_BASE}' in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap
    assert "SigLevel = Never" not in bootstrap
    assert "http://" not in bootstrap
    assert "https://holo.libz.so" not in bootstrap
    assert "pacman-key --add" in bootstrap
    assert "pacman-key --lsign-key" in bootstrap
    assert BOOTSTRAP_INSTALL_COMMAND in bootstrap
    assert "steamos-readonly disable" in bootstrap


def test_release_public_urls_are_https_only_project_pages_urls() -> None:
    active_paths = [
        ROOT / "site/index.html",
        ROOT / "site/rivoreo-steamos/bootstrap.sh",
        ROOT / "docs/package-repository.md",
        ROOT / "docs/release-process.md",
        ROOT / "packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf",
    ]

    assert not (ROOT / "site/CNAME").exists()
    for path in active_paths:
        text = path.read_text()
        assert PUBLIC_REPO_BASE in text
        assert "http://" not in text
        assert "https://holo.libz.so" not in text


def test_package_repository_docs_describe_github_actions_release_publisher() -> None:
    docs = PACKAGE_DOCS.read_text()

    assert "GitHub Actions release publisher" in docs
    assert "ARCH_REPO_GPG_PRIVATE_KEY" in docs
    assert "ARCH_REPO_GPG_KEY_ID" in docs
    assert "vX.Y.Z" in docs
    assert "vX.Y.Z-rc.N" in docs
    assert "do not deploy GitHub Pages" in docs
    assert "signed repository artifact" in docs
    assert "short-lived candidate signing key" in docs
    assert "stable releases require the protected signing secrets" in docs
    assert "ordinary pushes" in docs
