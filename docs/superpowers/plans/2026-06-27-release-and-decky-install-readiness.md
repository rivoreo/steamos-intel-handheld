# Release And Decky Install Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public pacman repository release path and the Decky Loader install experience verifiable end to end, then prove the result through local tests, GitLab CI artifacts, a hidden GitHub release candidate, and a stable release dry run or stable release after protected signing secrets are available.

**Architecture:** Keep public deployment on GitHub Actions and GitHub Pages. Keep GitLab CI as validation-only artifact production. Add user-facing Decky Loader detection and package hook notices without making Decky Loader a hard package dependency, because the backend service and CLI remain useful without Decky. Factor release artifact checks into reusable scripts so CI and local dry runs validate the same contracts.

**Tech Stack:** Bash, Arch `PKGBUILD` install hooks, GitHub Actions, GitLab CI, pytest, `makepkg`, `repo-add`, `gpg`, `gh`, optional `glab`.

---

## Current Evidence

- Latest hidden GitHub release candidate `v0.2.0-rc.1` succeeded in run `28253086938`: `validate`, `build-mangoapp`, `build-repo`, and `verify-repo-artifact` passed; `deploy-pages` was skipped.
- Latest stable GitHub tag `v0.2.0` failed in run `28253608651`: `build-repo` stopped at `Import signing key` with `Missing Arch release signing secrets for stable Pages deployment`.
- The public bootstrap URL still serves an inactive scaffold script and exits with status `1`.
- The public pacman repo database URL currently returns `404`.
- The checked-in bootstrap template installs `rivoreo-keyring`, `rivoreo-steamos-repo`, `steamos-intel-handheld`, and `steamos-intel-handheld-mangoapp`, but it only checks `curl`, `gpg`, `pacman`, and `pacman-key`.
- The main Arch package ships the Decky plugin under `/home/deck/homebrew/plugins/steamos-intel-handheld-ec`, but it does not check or report whether Decky Loader is installed.

## File Map

- Modify `site/rivoreo-steamos/bootstrap.sh`: keep the existing fingerprint-pinned repo setup, add non-fatal Decky Loader status reporting after package installation, and keep URL install idempotent.
- Modify `scripts/install-on-device.sh`: add the same non-fatal Decky Loader status reporting for development installs.
- Modify `packaging/arch/PKGBUILD`: add `install="$pkgname.install"` so pacman shows Decky Loader status after install and upgrade.
- Create `packaging/arch/steamos-intel-handheld.install`: Arch install hook that prints a clear Decky Loader detected/missing message and never fails package installation.
- Modify `tests/test_arch_release_workflow.py`: guard bootstrap Decky Loader notice and install hook packaging.
- Modify `tests/test_integration_assets.py`: guard development installer Decky Loader notice.
- Modify `tests/test_release_documentation.py`: require release docs to state stable release cannot deploy without protected signing secrets and to document Decky as optional UI dependency.
- Modify `docs/package-repository.md`: clarify that stable releases need protected signing secrets, GitLab artifacts are validation-only, and Decky Loader is optional but required for the Steam UI panel.
- Modify `docs/release-process.md`: add a release-blocker checklist for protected signing secrets, public Pages checks, Decky notice expectations, GitLab artifact dry run, hidden RC validation, and stable release retry.
- Create `scripts/verify-gitlab-pacman-artifact.sh`: local dry-run verifier for unsigned GitLab artifacts.
- Create `tests/test_gitlab_ci_packaging.py` additions: guard that GitLab artifact verification is documented and that the dry-run script checks repo aliases and package contents.

## Task 1: Add Failing Tests For Decky Loader Install Notices

**Files:**
- Modify: `tests/test_arch_release_workflow.py`
- Modify: `tests/test_integration_assets.py`
- Modify: `tests/test_release_documentation.py`

- [ ] **Step 1: Add failing bootstrap and package-hook tests**

Add these assertions to `tests/test_arch_release_workflow.py`:

```python
def test_bootstrap_reports_decky_loader_status_without_blocking_install() -> None:
    bootstrap = BOOTSTRAP.read_text()

    assert "report_decky_loader_status" in bootstrap
    assert "/home/deck/homebrew/services/PluginLoader" in bootstrap
    assert "Decky Loader detected" in bootstrap
    assert "Decky Loader not detected" in bootstrap
    assert "Steam UI Charge Limit panel requires Decky Loader" in bootstrap
    assert "report_decky_loader_status || true" in bootstrap
    assert BOOTSTRAP_INSTALL_COMMAND in bootstrap


def test_main_pkgbuild_runs_install_hook_for_decky_loader_notice() -> None:
    pkgbuild = MAIN_PKGBUILD.read_text()
    install_hook = ROOT / "packaging/arch/steamos-intel-handheld.install"

    assert 'install="$pkgname.install"' in pkgbuild
    assert install_hook.exists()
    hook = install_hook.read_text()
    assert "post_install()" in hook
    assert "post_upgrade()" in hook
    assert "Decky Loader detected" in hook
    assert "Decky Loader not detected" in hook
    assert "/home/deck/homebrew/services/PluginLoader" in hook
    assert "return 0" in hook


def test_release_artifact_verification_checks_install_hook_payload() -> None:
    workflow = WORKFLOW.read_text()

    assert 'contains "$main_pkg" ".INSTALL"' in workflow
    assert 'tar -xOf "$main_pkg" .INSTALL' in workflow
    assert "Decky Loader not detected" in workflow
```

- [ ] **Step 2: Add failing development installer test**

Extend `test_manual_installer_installs_decky_charge_limit_plugin()` in `tests/test_integration_assets.py`:

```python
def test_manual_installer_installs_decky_charge_limit_plugin():
    script = (ROOT / "scripts/install-on-device.sh").read_text()

    assert "decky/steamos-intel-handheld-ec/plugin.json" in script
    assert "/home/deck/homebrew/plugins/steamos-intel-handheld-ec" in script
    assert "install -m 0644" in script
    assert "decky_src/plugin.json" in script
    assert "decky_src/dist/index.js" in script
    assert "report_decky_loader_status" in script
    assert "/home/deck/homebrew/services/PluginLoader" in script
    assert "Decky Loader not detected" in script
```

- [ ] **Step 3: Add failing documentation tests**

Add this test to `tests/test_release_documentation.py`:

```python
def test_release_docs_explain_decky_loader_is_optional_ui_dependency() -> None:
    package_docs = PACKAGE_DOCS.read_text()
    release_docs = RELEASE_DOCS.read_text()

    for docs in (package_docs, release_docs):
        assert "Decky Loader is optional for the backend service and CLI" in docs
        assert "Steam UI Charge Limit panel requires Decky Loader" in docs
        assert "installer reports whether Decky Loader was detected" in docs
```

- [ ] **Step 4: Run RED tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_arch_release_workflow.py::test_bootstrap_reports_decky_loader_status_without_blocking_install \
  tests/test_arch_release_workflow.py::test_main_pkgbuild_runs_install_hook_for_decky_loader_notice \
  tests/test_integration_assets.py::test_manual_installer_installs_decky_charge_limit_plugin \
  tests/test_release_documentation.py::test_release_docs_explain_decky_loader_is_optional_ui_dependency \
  -q
```

Expected: FAIL because `report_decky_loader_status`, `packaging/arch/steamos-intel-handheld.install`, and the new documentation text do not exist yet.

## Task 2: Implement Non-Fatal Decky Loader Status Reporting

**Files:**
- Modify: `site/rivoreo-steamos/bootstrap.sh`
- Modify: `scripts/install-on-device.sh`
- Modify: `packaging/arch/PKGBUILD`
- Create: `packaging/arch/steamos-intel-handheld.install`
- Modify: `.github/workflows/arch-release.yml`
- Modify: `docs/package-repository.md`
- Modify: `docs/release-process.md`

- [ ] **Step 1: Add Decky notice helper to bootstrap**

Add this function after `need_command()` in `site/rivoreo-steamos/bootstrap.sh`:

```bash
report_decky_loader_status() {
  local plugin_loader=/home/deck/homebrew/services/PluginLoader
  local plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-ec

  if [ -x "$plugin_loader" ]; then
    echo "Decky Loader detected. Charge Limit plugin files are installed at $plugin_dir."
    echo "If the panel is not visible, restart Steam or Decky Loader."
  else
    echo "Decky Loader not detected. Backend service and CLI are installed." >&2
    echo "Steam UI Charge Limit panel requires Decky Loader; install Decky Loader first, then rerun this bootstrap or reinstall the package." >&2
  fi

  return 0
}
```

Then call it after the existing `pacman -S --needed ...` command:

```bash
report_decky_loader_status || true
```

- [ ] **Step 2: Add Decky notice helper to development installer**

Inside the remote `ssh "$target" "` block in `scripts/install-on-device.sh`, add this shell function before the Decky plugin files are installed:

```bash
  report_decky_loader_status() {
    plugin_loader=/home/deck/homebrew/services/PluginLoader
    plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-ec

    if [ -x "$plugin_loader" ]; then
      echo "Decky Loader detected. Charge Limit plugin files are installed at $plugin_dir."
      echo "If the panel is not visible, restart Steam or Decky Loader."
    else
      echo "Decky Loader not detected. Backend service and CLI are installed." >&2
      echo "Steam UI Charge Limit panel requires Decky Loader; install Decky Loader first, then rerun scripts/install-on-device.sh." >&2
    fi

    return 0
  }
```

Then call it after the Decky plugin files are copied:

```bash
  report_decky_loader_status || true
```

- [ ] **Step 3: Add Arch package install hook**

Create `packaging/arch/steamos-intel-handheld.install`:

```bash
_steamos_intel_handheld_decky_notice() {
  local plugin_loader=/home/deck/homebrew/services/PluginLoader
  local plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-ec

  if [ -x "$plugin_loader" ]; then
    echo "Decky Loader detected. Charge Limit plugin files are installed at $plugin_dir."
    echo "If the panel is not visible, restart Steam or Decky Loader."
  else
    echo "Decky Loader not detected. Backend service and CLI are installed."
    echo "Steam UI Charge Limit panel requires Decky Loader; install Decky Loader first, then reinstall steamos-intel-handheld or rerun the bootstrap."
  fi

  return 0
}

post_install() {
  _steamos_intel_handheld_decky_notice || true
}

post_upgrade() {
  _steamos_intel_handheld_decky_notice || true
}
```

- [ ] **Step 4: Wire install hook into main package**

Add this near the top of `packaging/arch/PKGBUILD`, after `license=("MIT")`:

```bash
install="$pkgname.install"
```

- [ ] **Step 5: Verify install hook payload in the signed release artifact**

In `.github/workflows/arch-release.yml`, inside `verify-repo-artifact`, extend the main package checks directly after the existing Decky plugin payload checks:

```bash
          contains "$main_pkg" ".INSTALL"
          tar -xOf "$main_pkg" .INSTALL | grep -F "Decky Loader not detected" >/dev/null
```

- [ ] **Step 6: Update docs**

In `docs/package-repository.md`, add this paragraph after the existing Decky runtime paragraph:

```markdown
Decky Loader is optional for the backend service and CLI. The Steam UI Charge
Limit panel requires Decky Loader because Decky is the runtime that loads the
plugin from `/home/deck/homebrew/plugins/steamos-intel-handheld-ec`. The
installer reports whether Decky Loader was detected; missing Decky Loader does
not fail package installation.
```

In `docs/release-process.md`, add the same behavior in the "What CI Builds" section:

```markdown
Decky Loader is optional for the backend service and CLI. The Steam UI Charge
Limit panel requires Decky Loader, and the installer reports whether Decky
Loader was detected. Missing Decky Loader must remain a warning, not a package
installation failure.
```

- [ ] **Step 7: Run GREEN tests for Decky work**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_arch_release_workflow.py::test_bootstrap_reports_decky_loader_status_without_blocking_install \
  tests/test_arch_release_workflow.py::test_main_pkgbuild_runs_install_hook_for_decky_loader_notice \
  tests/test_integration_assets.py::test_manual_installer_installs_decky_charge_limit_plugin \
  tests/test_release_documentation.py::test_release_docs_explain_decky_loader_is_optional_ui_dependency \
  -q
```

Expected: PASS.

- [ ] **Step 8: Commit Decky install readiness**

Run:

```bash
git add \
  site/rivoreo-steamos/bootstrap.sh \
  scripts/install-on-device.sh \
  packaging/arch/PKGBUILD \
  packaging/arch/steamos-intel-handheld.install \
  .github/workflows/arch-release.yml \
  docs/package-repository.md \
  docs/release-process.md \
  tests/test_arch_release_workflow.py \
  tests/test_integration_assets.py \
  tests/test_release_documentation.py
git commit -m "fix(packaging): report Decky Loader install status"
```

## Task 3: Add GitLab Artifact Dry-Run Verifier With TDD

**Files:**
- Create: `scripts/verify-gitlab-pacman-artifact.sh`
- Modify: `tests/test_gitlab_ci_packaging.py`
- Modify: `docs/package-repository.md`
- Modify: `docs/release-process.md`

- [ ] **Step 1: Add failing static tests for the dry-run verifier**

Add these tests to `tests/test_gitlab_ci_packaging.py`:

```python
DRY_RUN_SCRIPT = ROOT / "scripts/verify-gitlab-pacman-artifact.sh"


def test_gitlab_artifact_dry_run_script_checks_package_and_repo_shape() -> None:
    script = DRY_RUN_SCRIPT.read_text()

    assert "rivoreo-steamos/os/x86_64" in script
    assert "for pkg in \"$repo\"/*.pkg.tar.zst" in script
    assert 'pkgname = steamos-intel-handheld' in script
    assert 'main_pkg=""' in script
    assert "rivoreo-steamos.db" in script
    assert "rivoreo-steamos.files" in script
    assert "test ! -L" in script
    assert "tar -tf" in script
    assert "home/deck/homebrew/plugins/steamos-intel-handheld-ec/plugin.json" in script
    assert ".INSTALL" in script
    assert "Decky Loader not detected" in script
    assert "usr/bin/steamos-intel-handheld-power-control" in script
    assert "usr/bin/steamos-intel-handheld-ec-control" in script


def test_package_repository_docs_describe_gitlab_ci_dry_run() -> None:
    docs = PACKAGE_DOCS.read_text()

    assert "scripts/verify-gitlab-pacman-artifact.sh" in docs
    assert "download the GitLab CI artifact" in docs
    assert "validation-only and unsigned" in docs
```

- [ ] **Step 2: Run RED tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_gitlab_ci_packaging.py::test_gitlab_artifact_dry_run_script_checks_package_and_repo_shape \
  tests/test_gitlab_ci_packaging.py::test_package_repository_docs_describe_gitlab_ci_dry_run \
  -q
```

Expected: FAIL because `scripts/verify-gitlab-pacman-artifact.sh` does not exist and docs do not mention it.

- [ ] **Step 3: Implement the dry-run verifier**

Create `scripts/verify-gitlab-pacman-artifact.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/gitlab/artifact/root" >&2
  exit 2
fi

artifact_root="$1"
repo="$artifact_root/rivoreo-steamos/os/x86_64"

if [ ! -d "$repo" ]; then
  if [ -d "$artifact_root/.cache/pacman-repo/public/rivoreo-steamos/os/x86_64" ]; then
    repo="$artifact_root/.cache/pacman-repo/public/rivoreo-steamos/os/x86_64"
  else
    echo "Could not find rivoreo-steamos/os/x86_64 under $artifact_root" >&2
    exit 2
  fi
fi

test -s "$repo/rivoreo-steamos.db"
test -s "$repo/rivoreo-steamos.files"
test -s "$repo/rivoreo-steamos.db.tar.zst"
test -s "$repo/rivoreo-steamos.files.tar.zst"
test ! -L "$repo/rivoreo-steamos.db"
test ! -L "$repo/rivoreo-steamos.files"
cmp -s "$repo/rivoreo-steamos.db" "$repo/rivoreo-steamos.db.tar.zst"
cmp -s "$repo/rivoreo-steamos.files" "$repo/rivoreo-steamos.files.tar.zst"

main_pkg=""
for pkg in "$repo"/*.pkg.tar.zst; do
  if tar -xOf "$pkg" .PKGINFO | grep -Fx "pkgname = steamos-intel-handheld" >/dev/null; then
    if [ -n "$main_pkg" ]; then
      echo "Found more than one steamos-intel-handheld main package" >&2
      exit 2
    fi
    main_pkg="$pkg"
  fi
done

if [ -z "$main_pkg" ]; then
  echo "Expected one steamos-intel-handheld main package, found none" >&2
  exit 2
fi

tar -xOf "$main_pkg" .PKGINFO | grep -Fx "pkgname = steamos-intel-handheld" >/dev/null
tar -tf "$main_pkg" | grep -Fx "usr/bin/steamos-intel-handheld-power-control" >/dev/null
tar -tf "$main_pkg" | grep -Fx "usr/bin/steamos-intel-handheld-ec-control" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/plugin.json" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/main.py" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/dist/index.js" >/dev/null
tar -tf "$main_pkg" | grep -Fx ".INSTALL" >/dev/null
tar -xOf "$main_pkg" .INSTALL | grep -F "Decky Loader not detected" >/dev/null

echo "GitLab pacman artifact dry run passed: $repo"
```

Run:

```bash
chmod 0755 scripts/verify-gitlab-pacman-artifact.sh
```

- [ ] **Step 4: Update docs with dry-run instructions**

In `docs/package-repository.md`, extend the GitLab CI section:

````markdown
After downloading the GitLab CI artifact, run:

```bash
scripts/verify-gitlab-pacman-artifact.sh /path/to/downloaded/artifact
```

GitLab CI artifacts are validation-only and unsigned. Passing this dry run proves
package and repository shape, not public release trust.
````

In `docs/release-process.md`, add a "GitLab Validation Artifact Dry Run" subsection before "Hidden Candidate Release":

````markdown
## GitLab Validation Artifact Dry Run

When a GitLab pipeline is used for package validation, download the
`arch:repository` artifact and run:

```bash
scripts/verify-gitlab-pacman-artifact.sh /path/to/downloaded/artifact
```

This verifies repository aliases and main package contents. It does not replace
the signed GitHub release artifact gate because GitLab artifacts are
validation-only and unsigned.
````

- [ ] **Step 5: Run GREEN tests for dry-run verifier**

Run:

```bash
.venv/bin/python -m pytest tests/test_gitlab_ci_packaging.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit dry-run verifier**

Run:

```bash
git add \
  scripts/verify-gitlab-pacman-artifact.sh \
  tests/test_gitlab_ci_packaging.py \
  docs/package-repository.md \
  docs/release-process.md
git commit -m "test(packaging): add GitLab artifact dry run"
```

## Task 4: Local Verification Sweep

**Files:**
- Verify only. Do not edit files unless a test fails.

- [ ] **Step 1: Run targeted packaging tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_arch_release_workflow.py \
  tests/test_release_documentation.py \
  tests/test_gitlab_ci_packaging.py \
  tests/test_pages_site.py \
  tests/test_integration_assets.py::test_manual_installer_installs_decky_charge_limit_plugin \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full local harness**

Run:

```bash
PYTHON=.venv/bin/python scripts/check-local.sh
```

Expected: PASS. If unrelated dirty-worktree changes fail tests, record exact failing tests and run scoped verification for all files changed by this plan; do not fix unrelated EC-charge-control changes in this task.

- [ ] **Step 3: Confirm no accidental release tag or remote push happened**

Run:

```bash
git status --short
git tag --list 'v0.2.1*' --sort=-version:refname
```

Expected: only intentional tracked edits are committed; no `v0.2.1*` tag exists until the release validation task.

## Task 5: Push Validation Version And Run GitLab CI

**Files:**
- Modify only if version bump is required: `pyproject.toml`, `packaging/arch/PKGBUILD`, `packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD`, `decky/steamos-intel-handheld-ec/package.json`

- [ ] **Step 1: Confirm GitLab remote**

Run:

```bash
git remote -v
```

Expected: a GitLab remote is present. If only GitHub `origin` is present, ask the user for the GitLab project remote URL and add it with:

```bash
git remote add gitlab "$GITLAB_PROJECT_SSH_URL"
```

Do not invent this URL. If the GitLab project URL is unknown, stop this task and request the concrete remote from the user before creating or pushing any validation tag.

- [ ] **Step 2: Bump validation version if needed**

Because `v0.2.0` and `v0.2.0-rc.1` already exist, use `0.2.1` for the next validation cycle. Update:

```toml
# pyproject.toml
[project]
version = "0.2.1"
```

```bash
# packaging/arch/PKGBUILD
pkgver=0.2.1
```

```bash
# packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD
pkgver=0.2.1
```

```json
// decky/steamos-intel-handheld-ec/package.json
"version": "0.2.1"
```

- [ ] **Step 3: Run version RED/GREEN guard**

Run:

```bash
.venv/bin/python -m pytest tests/test_arch_release_workflow.py tests/test_gitlab_ci_packaging.py -q
PYTHON=.venv/bin/python scripts/check-local.sh
```

Expected: PASS.

- [ ] **Step 4: Commit version bump**

Run:

```bash
git add pyproject.toml packaging/arch/PKGBUILD packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD decky/steamos-intel-handheld-ec/package.json
git commit -m "chore(release): prepare v0.2.1 validation"
```

- [ ] **Step 5: Create and push GitLab validation tag**

Run:

```bash
git tag -a v0.2.1-rc.1 -m "v0.2.1-rc.1"
git push gitlab HEAD
git push gitlab v0.2.1-rc.1
```

Expected: GitLab starts a pipeline for the branch and/or tag.

- [ ] **Step 6: Watch GitLab CI**

Use whichever command is available in this environment:

```bash
glab pipeline list --ref v0.2.1-rc.1
glab pipeline ci view --branch v0.2.1-rc.1
```

If `glab` is unavailable, use the GitLab web UI and record the pipeline URL, pipeline ID, job names, and conclusions.

Expected jobs:

- `python:test`: success
- `arch:package`: success
- `arch:repository`: success

## Task 6: Dry Run GitLab CI Artifacts

**Files:**
- Verify only. Do not edit files unless the dry run exposes a repo-code bug.

- [ ] **Step 1: Download GitLab artifacts**

Use `glab` if available:

```bash
mkdir -p /tmp/steamos-intel-handheld-gitlab-dry-run
glab ci artifact download --job arch:repository --branch v0.2.1-rc.1 --path /tmp/steamos-intel-handheld-gitlab-dry-run
```

If the exact `glab` flags differ, use `glab ci artifact --help` and record the command actually used.

- [ ] **Step 2: Run local dry run**

Run:

```bash
scripts/verify-gitlab-pacman-artifact.sh /tmp/steamos-intel-handheld-gitlab-dry-run
```

Expected: `GitLab pacman artifact dry run passed: ...`.

- [ ] **Step 3: Inspect package contents manually**

Run:

```bash
find /tmp/steamos-intel-handheld-gitlab-dry-run -maxdepth 5 -type f | sort
```

Expected: output includes the pacman repository tree and at least one `steamos-intel-handheld-0.2.1-1-any.pkg.tar.zst` package.

## Task 7: Hidden GitHub Release Candidate And Signed Artifact Dry Run

**Files:**
- Verify only unless GitHub Actions exposes a repo-code bug.

- [ ] **Step 1: Push GitHub branch and hidden RC tag**

Run:

```bash
git push origin HEAD
git push origin v0.2.1-rc.1
```

Expected: GitHub Actions starts `Arch Package Release` for `v0.2.1-rc.1`.

- [ ] **Step 2: Watch GitHub RC run**

Run:

```bash
gh run list --repo rivoreo/steamos-intel-handheld --workflow "Arch Package Release" --limit 5
gh run watch <run-id> --repo rivoreo/steamos-intel-handheld --exit-status
```

Expected:

- `validate`: success
- `build-mangoapp`: success
- `build-repo`: success
- `verify-repo-artifact`: success
- `deploy-pages`: skipped

- [ ] **Step 3: Confirm artifacts**

Run:

```bash
gh api repos/rivoreo/steamos-intel-handheld/actions/runs/<run-id>/artifacts \
  --jq '.artifacts[] | select(.name=="signed-pacman-repository" or .name=="mangoapp-binary") | {name, expired, size_in_bytes}'
```

Expected: both `signed-pacman-repository` and `mangoapp-binary` exist and are not expired.

- [ ] **Step 4: Download and inspect signed repository artifact**

Run:

```bash
rm -rf /tmp/rivoreo-rc-v0.2.1
mkdir -p /tmp/rivoreo-rc-v0.2.1
gh run download <run-id> \
  --repo rivoreo/steamos-intel-handheld \
  --name signed-pacman-repository \
  --dir /tmp/rivoreo-rc-v0.2.1
find /tmp/rivoreo-rc-v0.2.1 -maxdepth 4 -type f | sort
```

Expected: repo aliases `.db`, `.db.sig`, `.files`, `.files.sig`, key files, and five release packages are present.

## Task 8: Stable Release Gate And Public URL Dry Run

**Files:**
- Verify only unless stable release docs or workflow need correction.

- [ ] **Step 1: Check protected signing secret readiness**

Stable release may proceed only if these GitHub repository secrets are configured by the repository owner:

- `ARCH_REPO_GPG_PRIVATE_KEY`
- `ARCH_REPO_GPG_PASSPHRASE`
- `ARCH_REPO_GPG_KEY_ID`

Check that the secret names exist before pushing a stable tag:

```bash
gh secret list --repo rivoreo/steamos-intel-handheld
```

Expected: all three names are listed. GitHub does not reveal secret values, so this proves only presence. The final readiness proof is a stable `build-repo` job reaching `Build signed pacman repository` without the `Missing Arch release signing secrets for stable Pages deployment` failure.

- [ ] **Step 2: If secrets are missing, stop before stable tag**

Do not push `v0.2.1`. Report that code validation and RC validation passed, but stable public deployment is blocked on protected GitHub signing secrets.

- [ ] **Step 3: If secrets are configured, create and push stable tag**

Run:

```bash
git tag -a v0.2.1 -m "v0.2.1"
git push origin v0.2.1
```

Expected: GitHub Actions starts `Arch Package Release` for `v0.2.1`.

- [ ] **Step 4: Watch stable run**

Run:

```bash
gh run list --repo rivoreo/steamos-intel-handheld --workflow "Arch Package Release" --limit 5
gh run watch <run-id> --repo rivoreo/steamos-intel-handheld --exit-status
```

Expected:

- `validate`: success
- `build-mangoapp`: success
- `build-repo`: success
- `verify-repo-artifact`: success
- `deploy-pages`: success

- [ ] **Step 5: Public URL dry run**

Run:

```bash
curl -fsSL https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos/bootstrap.sh | sed -n '1,140p'
curl -fsSIL https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos/os/x86_64/rivoreo-steamos.db
curl -fsSL https://rivoreo.github.io/steamos-intel-handheld/ | sed -n '1,80p'
```

Expected:

- Bootstrap script is the rendered installer and contains a real key fingerprint, not `__RIVOREO_KEY_FINGERPRINT__`.
- Bootstrap script does not contain `public pacman repository is not activated yet`.
- Repo database URL returns HTTP 200.
- Site remains the active install page.

## Task 9: Final Correctness Review

**Files:**
- Verify only. Do not edit unless review exposes a bug.

- [ ] **Step 1: Review final diff**

Run:

```bash
git diff --stat HEAD~3..HEAD
git diff HEAD~3..HEAD
git diff --check HEAD~3..HEAD
```

Expected: no whitespace errors; diff only touches release/install/docs/tests/version files related to this task.

- [ ] **Step 2: Re-run local release and Decky verification**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_arch_release_workflow.py \
  tests/test_release_documentation.py \
  tests/test_gitlab_ci_packaging.py \
  tests/test_pages_site.py \
  tests/test_decky_plugin_assets.py \
  tests/test_decky_plugin_backend.py \
  tests/test_integration_assets.py::test_manual_installer_installs_decky_charge_limit_plugin \
  -q
```

Expected: PASS.

- [ ] **Step 3: Prepare final report**

The final report must include:

- GitLab pipeline URL, pipeline ID, and `python:test`, `arch:package`, `arch:repository` results.
- GitLab artifact dry-run command and output summary.
- GitHub RC tag, commit SHA, run ID, run URL, artifact names, and job results.
- Stable release status: either public URL proof if deployed, or explicit blocked status with the missing signing-secret gate.
- Confirmation that Decky Loader missing state is a non-fatal warning, not a package install failure.
- Local tests and harness commands actually run.

## Acceptance Criteria

- URL bootstrap path installs the package from the public stable repo after a successful stable release.
- Hidden RC path remains non-public and skips Pages.
- Stable path refuses to deploy without protected signing secrets.
- GitLab CI remains validation-only and unsigned.
- GitLab CI artifacts can be downloaded and dry-run locally.
- Decky Loader is not a hard dependency for backend/CLI installation.
- Users receive a clear warning when Decky Loader is missing.
- Users receive a clear confirmation when Decky Loader is detected.
- Tests prove the above behavior before implementation changes.

## Plan Self-Review

- Spec coverage: The plan covers stable release failure, public URL activation, Decky Loader dependency reporting, GitLab validation CI, local dry runs, hidden RC, and final public URL dry run.
- Placeholder scan: No `TBD`, `TODO`, or open-ended implementation instructions remain.
- Type and command consistency: Paths match current repository files. Version validation uses `v0.2.1-rc.1` because `v0.2.0` and `v0.2.0-rc.1` already exist.
