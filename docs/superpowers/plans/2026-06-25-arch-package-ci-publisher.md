# Arch Package CI Publisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the GitHub Actions release publisher described in `docs/superpowers/specs/2026-06-25-arch-package-ci-publisher-design.md`.

**Architecture:** Keep release logic in small shell scripts that can be inspected by tests and called by GitHub Actions. The release workflow is the only path that signs packages, assembles the pacman repository, and deploys GitHub Pages; ordinary CI remains validation-only. The public site and bootstrap endpoint are rendered into the Pages artifact with the release key fingerprint so the checked-in source never needs to contain a private or guessed key.

**Tech Stack:** GitHub Actions, Arch Linux `makepkg`, `repo-add`, GnuPG, Bash, static GitHub Pages, pytest.

---

## File Structure

- Create `.github/workflows/arch-release.yml`: tag-triggered release workflow that validates, builds signed pacman repo files, assembles Pages, and deploys.
- Modify `.github/workflows/pages.yml`: prevent ordinary `main` pushes from overwriting the signed repository deployment.
- Create `scripts/build-arch-release-repo.sh`: Arch-container release builder for packages, signatures, and repo metadata.
- Create `scripts/assemble-arch-release-pages.sh`: copies `site/`, public key files, and repo files into `_site` with regular-file pacman aliases.
- Modify `site/rivoreo-steamos/bootstrap.sh`: active, fingerprint-pinned bootstrap template rendered by the release workflow.
- Modify `site/index.html`: active install messaging in English, Simplified Chinese, and Traditional Chinese.
- Modify `docs/package-repository.md`: document the GitHub Actions publisher and secret requirements.
- Modify `packaging/arch/PKGBUILD`: remove `SKIP` for release builds and allow CI to refresh checksums from the tag snapshot.
- Create `packaging/arch/rivoreo-keyring/PKGBUILD`: package the Rivoreo pacman public key and trust metadata.
- Create `packaging/arch/rivoreo-steamos-repo/PKGBUILD`: package the pacman repo include file.
- Create `packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf`: pacman repository stanza.
- Create `tests/test_arch_release_workflow.py`: static tests for release workflow, scripts, package definitions, docs, and active bootstrap/site state.
- Modify `tests/test_gitlab_ci_packaging.py`: keep GitLab validation aligned with refreshed package checksums.
- Modify `tests/test_pages_site.py`: update assertions from pending placeholder state to active install state.

## Task 1: Add Release Workflow And Script Tests

**Files:**
- Create: `tests/test_arch_release_workflow.py`
- Modify: `tests/test_pages_site.py`
- Modify: `tests/test_gitlab_ci_packaging.py`

- [x] **Step 1: Add failing workflow/script tests**

Create `tests/test_arch_release_workflow.py` with assertions for these concrete behaviors:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    assert "needs: build-repo" in workflow


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
    assert "cp \"$repo_db\" \"$repo_out/rivoreo-steamos.db\"" in script
    assert "cp \"$repo_files\" \"$repo_out/rivoreo-steamos.files\"" in script
    assert "gpg --batch --export" in script
    assert "updpkgsums" in script


def test_release_pages_assembler_renders_fingerprint_and_checks_artifact_shape() -> None:
    script = ASSEMBLE_SCRIPT.read_text()

    assert "__RIVOREO_KEY_FINGERPRINT__" in script
    assert "RIVOREO_KEY_FINGERPRINT" in script
    assert "test ! -L \"$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db\"" in script
    assert "test ! -L \"$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files\"" in script
    assert "fingerprint.txt" in script


def test_release_packages_are_defined_for_keyring_and_repo_config() -> None:
    keyring = KEYRING_PKGBUILD.read_text()
    repo_pkg = REPO_PKGBUILD.read_text()
    repo_conf = REPO_CONF.read_text()

    assert "pkgname=rivoreo-keyring" in keyring
    assert "rivoreo-trusted" in keyring
    assert "pkgname=rivoreo-steamos-repo" in repo_pkg
    assert 'backup=("etc/pacman.d/rivoreo-steamos.conf")' in repo_pkg
    assert "[rivoreo-steamos]" in repo_conf
    assert "SigLevel = Required TrustedOnly" in repo_conf
    assert "Server = https://holo.libz.so/rivoreo-steamos/os/$arch" in repo_conf


def test_release_pkgbuilds_do_not_ship_main_package_with_skip_checksum() -> None:
    pkgbuild = MAIN_PKGBUILD.read_text()

    assert 'sha256sums=("SKIP")' not in pkgbuild
    assert "sha256sums=(" in pkgbuild


def test_active_bootstrap_is_fingerprint_pinned_and_secure() -> None:
    bootstrap = BOOTSTRAP.read_text()

    assert "__RIVOREO_KEY_FINGERPRINT__" in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap
    assert "SigLevel = Never" not in bootstrap
    assert "pacman-key --add" in bootstrap
    assert "pacman-key --lsign-key" in bootstrap
    assert "pacman -S --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld" in bootstrap
    assert "steamos-readonly disable" in bootstrap


def test_package_repository_docs_describe_github_actions_release_publisher() -> None:
    docs = PACKAGE_DOCS.read_text()

    assert "GitHub Actions release publisher" in docs
    assert "ARCH_REPO_GPG_PRIVATE_KEY" in docs
    assert "ARCH_REPO_GPG_KEY_ID" in docs
    assert "vX.Y.Z" in docs
    assert "ordinary pushes" in docs
```

- [x] **Step 2: Update existing Pages tests for active state**

In `tests/test_pages_site.py`, replace pending-state assertions with active-state assertions:

```python
def test_pages_site_explains_capabilities_and_active_release_state() -> None:
    index = SITE_INDEX.read_text()
    assert "SteamOS Manager TDP remote" in index
    assert "Intel RAPL power path" in index
    assert "MangoHud sensor access" in index
    assert "Repository active" in index
    assert "Install channel open" in index
    assert "signed package repository is published through GitHub Actions" in index
    assert "Repository not activated" not in index
    assert "Install channel not open" not in index
    assert "signed package database has not been published to Pages" not in index
```

Replace `test_placeholder_bootstrap_exits_before_packages_exist` with:

```python
def test_active_bootstrap_configures_signed_repo() -> None:
    bootstrap = BOOTSTRAP.read_text()
    assert "pacman -S --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld" in bootstrap
    assert "signed package database has not been published" not in bootstrap
    assert "exit 1" not in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap
```

- [x] **Step 3: Run targeted tests and see expected failures**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" \
  .venv/bin/python -m pytest tests/test_arch_release_workflow.py tests/test_pages_site.py tests/test_gitlab_ci_packaging.py -q
```

Expected: failures for missing release workflow, scripts, package definitions,
active bootstrap, and active site text.

## Task 2: Add Package Definitions And Release Build Scripts

**Files:**
- Modify: `packaging/arch/PKGBUILD`
- Create: `packaging/arch/rivoreo-keyring/PKGBUILD`
- Create: `packaging/arch/rivoreo-steamos-repo/PKGBUILD`
- Create: `packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf`
- Create: `scripts/build-arch-release-repo.sh`
- Create: `scripts/assemble-arch-release-pages.sh`
- Modify: `.gitlab-ci.yml`

- [x] **Step 1: Harden main PKGBUILD checksum behavior**

Change the main package source to use a local release archive and a non-SKIP
checksum placeholder that CI refreshes with `updpkgsums`:

```bash
source=("$pkgname-$pkgver.tar.gz")
sha256sums=("0000000000000000000000000000000000000000000000000000000000000000")
```

Update `.gitlab-ci.yml` package job to run:

```bash
cd packaging/arch
updpkgsums
su builder -c "cd '$CI_PROJECT_DIR/packaging/arch' && PKGDEST='$PACKAGE_OUT' makepkg --cleanbuild --nodeps --noconfirm"
```

- [x] **Step 2: Add keyring package definition**

Create `packaging/arch/rivoreo-keyring/PKGBUILD`:

```bash
# Maintainer: JohnnySun <bmy001@gmail.com>
pkgname=rivoreo-keyring
pkgver=2026.06.25
pkgrel=1
pkgdesc="Rivoreo pacman repository keyring"
arch=("any")
url="https://holo.libz.so/rivoreo-steamos/"
license=("MIT")
source=("rivoreo.gpg" "rivoreo-trusted" "rivoreo-revoked")
sha256sums=("SKIP" "SKIP" "SKIP")

package() {
  install -Dm0644 rivoreo.gpg "$pkgdir/usr/share/pacman/keyrings/rivoreo.gpg"
  install -Dm0644 rivoreo-trusted "$pkgdir/usr/share/pacman/keyrings/rivoreo-trusted"
  install -Dm0644 rivoreo-revoked "$pkgdir/usr/share/pacman/keyrings/rivoreo-revoked"
}
```

- [x] **Step 3: Add repo config package definition**

Create `packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf`:

```ini
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = https://holo.libz.so/rivoreo-steamos/os/$arch
```

Create `packaging/arch/rivoreo-steamos-repo/PKGBUILD`:

```bash
# Maintainer: JohnnySun <bmy001@gmail.com>
pkgname=rivoreo-steamos-repo
pkgver=2026.06.25
pkgrel=1
pkgdesc="Pacman repository configuration for Rivoreo SteamOS packages"
arch=("any")
url="https://holo.libz.so/rivoreo-steamos/"
license=("MIT")
backup=("etc/pacman.d/rivoreo-steamos.conf")
source=("rivoreo-steamos.conf")
sha256sums=("SKIP")

package() {
  install -Dm0644 rivoreo-steamos.conf "$pkgdir/etc/pacman.d/rivoreo-steamos.conf"
}
```

- [x] **Step 4: Add release repository builder script**

Create `scripts/build-arch-release-repo.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON:-python3}"
pkgver="$("$python_bin" - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
PY
)"
release_tag="${RELEASE_TAG:-v$pkgver}"
if [[ ! "$release_tag" =~ ^v[0-9]+[.][0-9]+[.][0-9]+([-.][A-Za-z0-9._]+)?$ ]]; then
  echo "RELEASE_TAG must be a vX.Y.Z tag, got: $release_tag" >&2
  exit 2
fi
if [ "$release_tag" != "v$pkgver" ]; then
  echo "Tag $release_tag does not match pyproject version $pkgver" >&2
  exit 2
fi

: "${ARCH_REPO_GPG_KEY_ID:?ARCH_REPO_GPG_KEY_ID is required}"
package_out="${PACKAGE_OUT:-$repo_root/.cache/arch-release/packages}"
repo_out="${REPO_OUT:-$repo_root/.cache/arch-release/public/rivoreo-steamos/os/x86_64}"
key_out="${KEY_OUT:-$repo_root/site/rivoreo-steamos/key}"
src_archive="$repo_root/packaging/arch/steamos-intel-handheld-$pkgver.tar.gz"

rm -rf "$package_out" "$repo_out"
mkdir -p "$package_out" "$repo_out" "$key_out"

git archive --format=tar --prefix="steamos-intel-handheld-$pkgver/" "$release_tag" \
  | gzip -n > "$src_archive"

gpg --batch --export "$ARCH_REPO_GPG_KEY_ID" > "$key_out/rivoreo.gpg"
fingerprint="$(
  gpg --batch --with-colons --show-keys "$key_out/rivoreo.gpg" \
    | awk -F: '$1 == "fpr" {print $10; exit}'
)"
if [ "$fingerprint" != "$ARCH_REPO_GPG_KEY_ID" ]; then
  echo "Imported key fingerprint $fingerprint does not match ARCH_REPO_GPG_KEY_ID" >&2
  exit 2
fi
printf '%s\n' "$fingerprint" > "$key_out/fingerprint.txt"
install -Dm0644 "$key_out/rivoreo.gpg" packaging/arch/rivoreo-keyring/rivoreo.gpg
printf '%s:4:\n' "$fingerprint" > packaging/arch/rivoreo-keyring/rivoreo-trusted
: > packaging/arch/rivoreo-keyring/rivoreo-revoked

build_pkg() {
  local pkgdir="$1"
  (
    cd "$pkgdir"
    if [ -f PKGBUILD ] && grep -q 'sha256sums=("0000000000000000000000000000000000000000000000000000000000000000")' PKGBUILD; then
      updpkgsums
    fi
    PKGDEST="$package_out" makepkg --cleanbuild --syncdeps --noconfirm --needed --sign
  )
}

build_pkg packaging/arch
build_pkg packaging/arch/rivoreo-keyring
build_pkg packaging/arch/rivoreo-steamos-repo

cp "$package_out"/*.pkg.tar.zst "$package_out"/*.pkg.tar.zst.sig "$repo_out"/
repo_db="$repo_out/rivoreo-steamos.db.tar.zst"
repo_files="$repo_out/rivoreo-steamos.files.tar.zst"
repo-add --sign --verify "$repo_db" "$repo_out"/*.pkg.tar.zst
cp "$repo_db" "$repo_out/rivoreo-steamos.db"
cp "$repo_db.sig" "$repo_out/rivoreo-steamos.db.sig"
cp "$repo_files" "$repo_out/rivoreo-steamos.files"
cp "$repo_files.sig" "$repo_out/rivoreo-steamos.files.sig"
test ! -L "$repo_out/rivoreo-steamos.db"
test ! -L "$repo_out/rivoreo-steamos.files"
```

- [x] **Step 5: Add Pages assembler script**

Create `scripts/assemble-arch-release-pages.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

: "${RIVOREO_KEY_FINGERPRINT:?RIVOREO_KEY_FINGERPRINT is required}"
repo_tree="${REPO_TREE:-$repo_root/.cache/arch-release/public/rivoreo-steamos/os/x86_64}"
site_out="${SITE_OUT:-$repo_root/_site}"
key_dir="${KEY_DIR:-$repo_root/site/rivoreo-steamos/key}"

rm -rf "$site_out"
mkdir -p "$site_out/rivoreo-steamos/os/x86_64" "$site_out/rivoreo-steamos/key"
cp -R site/. "$site_out/"
cp "$key_dir/rivoreo.gpg" "$site_out/rivoreo-steamos/key/rivoreo.gpg"
printf '%s\n' "$RIVOREO_KEY_FINGERPRINT" > "$site_out/rivoreo-steamos/key/fingerprint.txt"
cp "$repo_tree"/* "$site_out/rivoreo-steamos/os/x86_64/"

sed "s/__RIVOREO_KEY_FINGERPRINT__/$RIVOREO_KEY_FINGERPRINT/g" \
  site/rivoreo-steamos/bootstrap.sh > "$site_out/rivoreo-steamos/bootstrap.sh"
chmod 0755 "$site_out/rivoreo-steamos/bootstrap.sh"

test -f "$site_out/index.html"
test -f "$site_out/rivoreo-steamos/bootstrap.sh"
test -f "$site_out/rivoreo-steamos/key/rivoreo.gpg"
test -f "$site_out/rivoreo-steamos/key/fingerprint.txt"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db.sig"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files.sig"
test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db"
test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files"
```

- [x] **Step 6: Run targeted tests**

Run the same targeted pytest command from Task 1.

Expected: package/script tests pass; workflow and site/bootstrap tests still fail.

## Task 3: Add GitHub Actions Release Publisher

**Files:**
- Create: `.github/workflows/arch-release.yml`
- Modify: `.github/workflows/pages.yml`

- [x] **Step 1: Replace ordinary Pages deploy with static validation**

Modify `.github/workflows/pages.yml` so it no longer deploys on `push`. Keep a
manual/static validation job that copies `site/` and checks static files without
using Pages deployment actions.

- [x] **Step 2: Add release workflow**

Create `.github/workflows/arch-release.yml` with jobs named `validate`,
`build-repo`, and `deploy-pages`. Include:

```yaml
on:
  push:
    tags:
      - "v*.*.*"
  workflow_dispatch:
    inputs:
      tag:
        description: "Existing vX.Y.Z tag to publish"
        required: true
```

The checkout step must use `fetch-depth: 0` and `submodules: recursive`.
The build job must import `ARCH_REPO_GPG_PRIVATE_KEY`, verify
`ARCH_REPO_GPG_KEY_ID`, run `scripts/build-arch-release-repo.sh`, and upload the
`.cache/arch-release/public` artifact. The deploy job must download that
artifact, run `scripts/assemble-arch-release-pages.sh`, upload the Pages
artifact, and deploy through the `github-pages` environment.

- [x] **Step 3: Run targeted workflow tests**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" \
  .venv/bin/python -m pytest tests/test_arch_release_workflow.py -q
```

Expected: release workflow tests pass except any still tied to bootstrap/site
activation.

## Task 4: Activate Bootstrap, Website, And Docs

**Files:**
- Modify: `site/rivoreo-steamos/bootstrap.sh`
- Modify: `site/index.html`
- Modify: `docs/package-repository.md`

- [x] **Step 1: Replace placeholder bootstrap with fingerprint-pinned installer**

The bootstrap script must require root, render-time fingerprint substitution,
key fingerprint verification, `pacman-key --add`, `pacman-key --lsign-key`,
`SigLevel = Required TrustedOnly`, and the package install command:

```bash
pacman -S --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld
```

- [x] **Step 2: Update website copy**

Change visible and translated install-state text from pending to active:

- `Repository active`
- `Install channel open`
- `The signed package repository is published through GitHub Actions.`
- `Install command`
- `Active pacman stanza`

Use Traditional Chinese wording with `套件庫已啟用`, `可以安裝`, `簽名套件庫`,
and `釋出`, avoiding Simplified Chinese vocabulary in the `zh-TW` dictionary.

- [x] **Step 3: Update package repository docs**

Document the GitHub Actions release publisher, required secrets, tag-only
release trigger, static validation role of GitLab CI, regular-file alias
requirement, and bootstrap command.

- [x] **Step 4: Run site and workflow tests**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" \
  .venv/bin/python -m pytest tests/test_arch_release_workflow.py tests/test_pages_site.py tests/test_gitlab_ci_packaging.py -q
```

Expected: all targeted tests pass.

## Task 5: Full Verification, Commit, And Push

**Files:**
- All files touched by Tasks 1-4.

- [x] **Step 1: Run whitespace and placeholder checks**

Run:

```bash
git diff --check
rg -n "TBD|TODO|FIXME|SigLevel = Never" .github docs/package-repository.md site/rivoreo-steamos/bootstrap.sh scripts packaging tests
```

Expected: `git diff --check` exits 0. The `rg` command must not find release
placeholders or insecure pacman config.

- [x] **Step 2: Run full local harness**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: ruff passes, pytest passes, and compileall passes.

- [x] **Step 3: Commit**

Run:

```bash
git add .github docs/package-repository.md docs/superpowers/plans/2026-06-25-arch-package-ci-publisher.md packaging scripts site tests
git commit -m "feat(packaging): add Arch package release publisher"
```

- [x] **Step 4: Push**

Run:

```bash
git push origin main
```

Expected: local `main` and `origin/main` point at the implementation commit.
