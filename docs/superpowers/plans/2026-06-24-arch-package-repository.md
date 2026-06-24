# Arch/SteamOS Package Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current Arch packaging draft into a signed pacman package and self-hosted Rivoreo repository that SteamOS users can bootstrap, install, upgrade, repair, and verify.

**Architecture:** Keep application packaging, repository publication, and SteamOS bootstrap as separate units. Build packages in a clean Arch environment, publish a static pacman repository with signed package/database artifacts, and use an idempotent SteamOS-aware bootstrap script to import trust, add the repo, install packages, and start services.

**Tech Stack:** Arch `PKGBUILD`, `makepkg`, `repo-add`, `pacman-key`, Bash, GitLab CI, GitHub Pages, pytest, SteamOS QEMU build environment for the temporary `mangoapp` binary.

---

### Task 1: Packaging Policy Tests

**Files:**
- Create: `tests/test_arch_packaging.py`
- Modify: `scripts/check-local.sh`

- [ ] **Step 1: Add tests for release-ready PKGBUILD metadata**

Create `tests/test_arch_packaging.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PKGBUILD = ROOT / "packaging/arch/PKGBUILD"


def read_pkgbuild() -> str:
    return PKGBUILD.read_text()


def test_pkgbuild_uses_release_checksum() -> None:
    pkgbuild = read_pkgbuild()
    assert 'sha256sums=("SKIP")' not in pkgbuild
    assert "sha256sums=('SKIP')" not in pkgbuild
    assert "sha256sums=(" in pkgbuild


def test_pkgbuild_declares_install_hook_and_backups() -> None:
    pkgbuild = read_pkgbuild()
    assert "install=$pkgname.install" in pkgbuild
    assert "backup=(" in pkgbuild
    assert "etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf" in pkgbuild
    assert "etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml" in pkgbuild


def test_pkgbuild_runs_project_tests() -> None:
    pkgbuild = read_pkgbuild()
    assert "checkdepends=(" in pkgbuild
    assert "check()" in pkgbuild
    assert "pytest" in pkgbuild


def test_pkgbuild_installs_opt_runtime_contract() -> None:
    pkgbuild = read_pkgbuild()
    assert "/opt/steamos-intel-handheld/bin" in pkgbuild
    assert "/opt/steamos-intel-handheld/src" in pkgbuild
    assert "steamos-intel-handheld-power-control" in pkgbuild
```

- [ ] **Step 2: Include packaging tests in local checks**

Keep `scripts/check-local.sh` as the single local gate. It already runs all
pytest tests, so no code change is needed after adding the new test file.

- [ ] **Step 3: Run the tests and confirm the new policy fails first**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: `tests/test_arch_packaging.py::test_pkgbuild_uses_release_checksum`
fails because the current PKGBUILD uses `SKIP`.

- [ ] **Step 4: Commit the failing policy tests**

Run:

```bash
git add tests/test_arch_packaging.py
git commit -m "test(packaging): define Arch package release policy"
```

### Task 2: Harden The Application PKGBUILD

**Files:**
- Modify: `packaging/arch/PKGBUILD`
- Create: `packaging/arch/steamos-intel-handheld.install`

- [ ] **Step 1: Replace the packaging draft with a release-ready PKGBUILD**

Update `packaging/arch/PKGBUILD` to this shape. Create the `v0.1.0` tag before
publishing, then run `updpkgsums` so the committed `sha256sums` line contains
the real 64-character checksum for the GitHub release tarball:

```bash
# Maintainer: JohnnySun <bmy001@gmail.com>
pkgname=steamos-intel-handheld
pkgver=0.1.0
pkgrel=1
pkgdesc="SteamOS support layer for Intel handheld PCs"
arch=("any")
url="https://github.com/rivoreo/steamos-intel-handheld"
license=("MIT")
depends=("python" "python-dbus-next")
makedepends=("python-build" "python-installer" "python-wheel")
checkdepends=("python-pytest")
install=$pkgname.install
backup=(
  "etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf"
  "etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml"
)
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

check() {
  cd "$pkgname-$pkgver"
  PYTHONPATH=src pytest
}

package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl

  install -Dm0755 data/bin/steamos-intel-handheld-gamescope-display \
    "$pkgdir/opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display"
  install -Dm0755 /dev/stdin \
    "$pkgdir/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/steamos-intel-handheld-power-control "$@"
WRAPPER

  install -Dm0644 data/systemd/steamos-intel-handheld-power-control.service \
    "$pkgdir/usr/lib/systemd/system/steamos-intel-handheld-power-control.service"
  install -Dm0644 data/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf \
    "$pkgdir/etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf"
  install -Dm0644 data/steamos-manager/remotes.d/99-rivoreo-power-control.toml \
    "$pkgdir/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml"
  install -Dm0644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
```

Generate the checksum immediately after the tag exists:

```bash
cd packaging/arch
updpkgsums
grep -E "sha256sums=\\('[0-9a-f]{64}'\\)" PKGBUILD
```

- [ ] **Step 2: Add the package install hook**

Create `packaging/arch/steamos-intel-handheld.install`:

```bash
post_install() {
  systemctl daemon-reload || true
  busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig 2>/dev/null || true
  cat <<'MSG'
Enable the SteamOS Intel handheld provider with:
  sudo systemctl enable --now steamos-intel-handheld-power-control.service
MSG
}

post_upgrade() {
  post_install
  systemctl try-restart steamos-intel-handheld-power-control.service || true
}

pre_remove() {
  systemctl disable --now steamos-intel-handheld-power-control.service 2>/dev/null || true
}

post_remove() {
  systemctl daemon-reload || true
  busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig 2>/dev/null || true
}
```

- [ ] **Step 3: Build in an Arch environment**

Run from an Arch container, chroot, or SteamOS QEMU build environment:

```bash
cd packaging/arch
makepkg --cleanbuild --syncdeps --needed
```

Expected: one `steamos-intel-handheld-0.1.0-1-any.pkg.tar.zst` artifact.

- [ ] **Step 4: Run local checks**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: all tests pass.

- [ ] **Step 5: Commit the hardened package**

Run:

```bash
git add packaging/arch/PKGBUILD packaging/arch/steamos-intel-handheld.install tests/test_arch_packaging.py
git commit -m "feat(packaging): harden Arch package metadata"
```

### Task 3: Add Keyring And Repo Config Packages

**Files:**
- Create: `packaging/arch/rivoreo-keyring/PKGBUILD`
- Create: `packaging/arch/rivoreo-keyring/rivoreo.gpg`
- Create: `packaging/arch/rivoreo-keyring/rivoreo-trusted`
- Create: `packaging/arch/rivoreo-keyring/rivoreo-revoked`
- Create: `packaging/arch/rivoreo-steamos-repo/PKGBUILD`
- Create: `packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf`
- Create: `tests/test_arch_repo_packages.py`

- [ ] **Step 1: Add tests for repo support packages**

Create `tests/test_arch_repo_packages.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_keyring_package_installs_pacman_keyring_files() -> None:
    pkgbuild = (ROOT / "packaging/arch/rivoreo-keyring/PKGBUILD").read_text()
    assert "pkgname=rivoreo-keyring" in pkgbuild
    assert "/usr/share/pacman/keyrings/rivoreo.gpg" in pkgbuild
    assert "/usr/share/pacman/keyrings/rivoreo-trusted" in pkgbuild
    assert "/usr/share/pacman/keyrings/rivoreo-revoked" in pkgbuild


def test_repo_config_uses_required_signatures() -> None:
    conf = (ROOT / "packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf").read_text()
    assert "[rivoreo-steamos]" in conf
    assert "SigLevel = Required TrustedOnly" in conf
    assert "Server = https://holo.libz.so/$repo/os/$arch" in conf


def test_repo_config_package_marks_config_as_backup() -> None:
    pkgbuild = (ROOT / "packaging/arch/rivoreo-steamos-repo/PKGBUILD").read_text()
    assert "pkgname=rivoreo-steamos-repo" in pkgbuild
    assert 'backup=("etc/pacman.d/rivoreo-steamos.conf")' in pkgbuild
```

- [ ] **Step 2: Add the keyring PKGBUILD**

Create `packaging/arch/rivoreo-keyring/PKGBUILD`:

```bash
# Maintainer: JohnnySun <bmy001@gmail.com>
pkgname=rivoreo-keyring
pkgver=2026.06.24
pkgrel=1
pkgdesc="Rivoreo package signing keyring"
arch=("any")
url="https://github.com/rivoreo/steamos-intel-handheld"
license=("MIT")
source=("rivoreo.gpg" "rivoreo-trusted" "rivoreo-revoked")
sha256sums=("SKIP" "SKIP" "SKIP")

package() {
  install -Dm0644 rivoreo.gpg "$pkgdir/usr/share/pacman/keyrings/rivoreo.gpg"
  install -Dm0644 rivoreo-trusted "$pkgdir/usr/share/pacman/keyrings/rivoreo-trusted"
  install -Dm0644 rivoreo-revoked "$pkgdir/usr/share/pacman/keyrings/rivoreo-revoked"
}
```

Generate `rivoreo.gpg`, `rivoreo-trusted`, and `rivoreo-revoked` from the
release signing key. The `SKIP` values are acceptable here only if the files are
stored in the same package directory and reviewed in git; replace them with
real hashes before publishing to AUR.

- [ ] **Step 3: Add the repo config package**

Create `packaging/arch/rivoreo-steamos-repo/rivoreo-steamos.conf`:

```ini
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = https://holo.libz.so/$repo/os/$arch
```

Create `packaging/arch/rivoreo-steamos-repo/PKGBUILD`:

```bash
# Maintainer: JohnnySun <bmy001@gmail.com>
pkgname=rivoreo-steamos-repo
pkgver=2026.06.24
pkgrel=1
pkgdesc="Pacman repository configuration for Rivoreo SteamOS packages"
arch=("any")
url="https://github.com/rivoreo/steamos-intel-handheld"
license=("MIT")
depends=("rivoreo-keyring")
backup=("etc/pacman.d/rivoreo-steamos.conf")
source=("rivoreo-steamos.conf")
sha256sums=("SKIP")

package() {
  install -Dm0644 rivoreo-steamos.conf "$pkgdir/etc/pacman.d/rivoreo-steamos.conf"
}
```

- [ ] **Step 4: Run local checks**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: all tests pass.

- [ ] **Step 5: Commit support packages**

Run:

```bash
git add packaging/arch/rivoreo-keyring packaging/arch/rivoreo-steamos-repo tests/test_arch_repo_packages.py
git commit -m "feat(packaging): add Rivoreo pacman repo packages"
```

### Task 4: Build And Publish Repository Artifacts

**Files:**
- Create: `scripts/build-arch-packages.sh`
- Create: `scripts/publish-pacman-repo.sh`
- Create: `tests/test_repo_scripts.py`

- [ ] **Step 1: Add script tests**

Create `tests/test_repo_scripts.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_build_script_builds_all_arch_package_dirs() -> None:
    script = (ROOT / "scripts/build-arch-packages.sh").read_text()
    assert "packaging/arch" in script
    assert "rivoreo-keyring" in script
    assert "rivoreo-steamos-repo" in script
    assert "makepkg" in script
    assert "--cleanbuild" in script


def test_publish_script_generates_signed_repo_database() -> None:
    script = (ROOT / "scripts/publish-pacman-repo.sh").read_text()
    assert "repo-add" in script
    assert "--sign" in script
    assert "rivoreo-steamos.db.tar.zst" in script
    assert "rivoreo-steamos.db" in script
    assert "rivoreo-steamos.files" in script
    assert "ln -sf" not in script
```

- [ ] **Step 2: Add package build script**

Create `scripts/build-arch-packages.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pkgdest="${PKGDEST:-$repo_root/.cache/arch-packages}"
mkdir -p "$pkgdest"

build_pkg() {
  local dir="$1"
  (
    cd "$repo_root/$dir"
    PKGDEST="$pkgdest" makepkg --cleanbuild --syncdeps --needed --sign
  )
}

build_pkg packaging/arch/rivoreo-keyring
build_pkg packaging/arch/rivoreo-steamos-repo
build_pkg packaging/arch
```

- [ ] **Step 3: Add repo publication script**

Create `scripts/publish-pacman-repo.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pkgsrc="${PKGSRC:-$repo_root/.cache/arch-packages}"
out="${REPO_OUT:-$repo_root/.cache/pacman-repo/public/rivoreo-steamos/os/x86_64}"
db="$out/rivoreo-steamos.db.tar.zst"

mkdir -p "$out"
find "$out" -maxdepth 1 -type f -name '*.pkg.tar.zst' -delete
find "$out" -maxdepth 1 -type f -name '*.pkg.tar.zst.sig' -delete
cp "$pkgsrc"/*.pkg.tar.zst "$out"/
cp "$pkgsrc"/*.pkg.tar.zst.sig "$out"/

rm -f "$out"/rivoreo-steamos.db* "$out"/rivoreo-steamos.files*
repo-add --sign "$db" "$out"/*.pkg.tar.zst

cp "$out/rivoreo-steamos.db.tar.zst" "$out/rivoreo-steamos.db"
cp "$out/rivoreo-steamos.db.tar.zst.sig" "$out/rivoreo-steamos.db.sig"
cp "$out/rivoreo-steamos.files.tar.zst" "$out/rivoreo-steamos.files"
cp "$out/rivoreo-steamos.files.tar.zst.sig" "$out/rivoreo-steamos.files.sig"
```

- [ ] **Step 4: Make scripts executable and run local checks**

Run:

```bash
chmod +x scripts/build-arch-packages.sh scripts/publish-pacman-repo.sh
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: all tests pass.

- [ ] **Step 5: Commit repository scripts**

Run:

```bash
git add scripts/build-arch-packages.sh scripts/publish-pacman-repo.sh tests/test_repo_scripts.py
git commit -m "feat(packaging): add pacman repository publishing scripts"
```

### Task 5: Add SteamOS Bootstrap

**Files:**
- Create: `scripts/bootstrap-steamos-repo.sh`
- Create: `tests/test_bootstrap_steamos_repo.py`
- Create: `docs/package-repository.md`

- [ ] **Step 1: Add bootstrap tests**

Create `tests/test_bootstrap_steamos_repo.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bootstrap-steamos-repo.sh"


def test_bootstrap_requires_signed_repo_and_key_fingerprint() -> None:
    script = SCRIPT.read_text()
    assert "EXPECTED_FINGERPRINT=" in script
    assert "pacman-key --add" in script
    assert "pacman-key --lsign-key" in script
    assert "SigLevel = Required TrustedOnly" in script
    assert "SigLevel = Never" not in script


def test_bootstrap_handles_steamos_readonly_and_include_idempotently() -> None:
    script = SCRIPT.read_text()
    assert "steamos-readonly disable" in script
    assert "Include = /etc/pacman.d/rivoreo-steamos.conf" in script
    assert "grep -Fxq" in script


def test_bootstrap_installs_and_starts_provider() -> None:
    script = SCRIPT.read_text()
    assert "pacman -Syu --needed" in script
    assert "rivoreo-keyring" in script
    assert "rivoreo-steamos-repo" in script
    assert "steamos-intel-handheld" in script
    assert "systemctl enable --now steamos-intel-handheld-power-control.service" in script
```

- [ ] **Step 2: Add the bootstrap script**

Create `scripts/bootstrap-steamos-repo.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://holo.libz.so/rivoreo-steamos}"
EXPECTED_FINGERPRINT="${EXPECTED_FINGERPRINT:?set EXPECTED_FINGERPRINT to the Rivoreo release signing key fingerprint}"
repo_conf="/etc/pacman.d/rivoreo-steamos.conf"
include_line="Include = /etc/pacman.d/rivoreo-steamos.conf"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: curl -fsSL $REPO_URL/bootstrap.sh | sudo bash" >&2
  exit 1
fi

if command -v steamos-readonly >/dev/null 2>&1; then
  steamos-readonly disable
fi

curl -fsSL "$REPO_URL/key/rivoreo.gpg" -o "$tmpdir/rivoreo.gpg"
actual_fingerprint="$(gpg --show-keys --with-colons "$tmpdir/rivoreo.gpg" | awk -F: '$1 == "fpr" {print $10; exit}')"
if [ "$actual_fingerprint" != "$EXPECTED_FINGERPRINT" ]; then
  echo "Rivoreo key fingerprint mismatch: $actual_fingerprint" >&2
  exit 1
fi

pacman-key --init
pacman-key --add "$tmpdir/rivoreo.gpg"
pacman-key --lsign-key "$EXPECTED_FINGERPRINT"

cat >"$repo_conf" <<'CONF'
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = https://holo.libz.so/$repo/os/$arch
CONF

if ! grep -Fxq "$include_line" /etc/pacman.conf; then
  printf '\n%s\n' "$include_line" >>/etc/pacman.conf
fi

pacman -Syu --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld
systemctl enable --now steamos-intel-handheld-power-control.service

if [ -S /run/user/1000/bus ]; then
  runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    systemctl --user restart steamos-manager || true
fi

echo "Rivoreo SteamOS repository and steamos-intel-handheld are installed."
```

The published `bootstrap.sh` must embed the real fingerprint by rendering this
script with `EXPECTED_FINGERPRINT=<fingerprint>` during the release publication
step. Local development runs may pass the variable explicitly.

- [ ] **Step 3: Document installation and recovery**

Create `docs/package-repository.md` with:

````markdown
# Package Repository

Install on SteamOS:

```bash
curl -fsSL https://holo.libz.so/rivoreo-steamos/bootstrap.sh | sudo bash
```

The bootstrap imports the Rivoreo package signing key, configures the
`rivoreo-steamos` pacman repository with `SigLevel = Required TrustedOnly`,
installs `steamos-intel-handheld`, and starts
`steamos-intel-handheld-power-control.service`.

SteamOS uses a read-only rootfs model. The bootstrap disables read-only mode
when `steamos-readonly` exists. After a SteamOS OTA, rerun the bootstrap if the
pacman repo configuration or package files disappear.

Remove:

```bash
sudo pacman -Rns steamos-intel-handheld steamos-intel-handheld-mangoapp
```
````

- [ ] **Step 4: Run local checks**

Run:

```bash
chmod +x scripts/bootstrap-steamos-repo.sh
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: all tests pass.

- [ ] **Step 5: Commit bootstrap docs and script**

Run:

```bash
git add scripts/bootstrap-steamos-repo.sh tests/test_bootstrap_steamos_repo.py docs/package-repository.md
git commit -m "feat(packaging): add SteamOS pacman repo bootstrap"
```

### Task 6: Add Release CI

**Files:**
- Create: `.gitlab-ci.yml`
- Modify: `docs/package-repository.md`
- Create: `tests/test_gitlab_ci_packaging.py`

- [x] **Step 1: Add GitLab CI policy tests**

Create `tests/test_gitlab_ci_packaging.py` to assert that GitLab CI:

- runs package builds in `archlinux:base-devel`;
- uses a non-root `makepkg` builder;
- builds a current-commit source snapshot;
- emits `.pkg.tar.zst` artifacts;
- uses `repo-add` to produce a static pacman repository tree.

- [x] **Step 2: Add package and repository artifact jobs**

Create `.gitlab-ci.yml`:

```yaml
arch:package:
  image: archlinux:base-devel
  script:
    - makepkg --cleanbuild --nodeps --noconfirm
  artifacts:
    paths:
      - .cache/arch-packages/*.pkg.tar.zst

arch:repository:
  image: archlinux:base-devel
  script:
    - repo-add rivoreo-steamos.db.tar.zst ./*.pkg.tar.zst
    - rm -f rivoreo-steamos.db rivoreo-steamos.files
    - cp rivoreo-steamos.db.tar.zst rivoreo-steamos.db
    - cp rivoreo-steamos.files.tar.zst rivoreo-steamos.files
  artifacts:
    paths:
      - .cache/pacman-repo/public
```

The repository job produces validation artifacts. Public activation still needs
signing and a promotion step into the GitHub Pages artifact.

- [x] **Step 3: Run local checks**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: all tests pass.

- [x] **Step 4: Commit CI**

Run:

```bash
git add .gitlab-ci.yml tests/test_gitlab_ci_packaging.py docs/package-repository.md docs/superpowers/plans/2026-06-24-arch-package-repository.md
git commit -m "ci(packaging): add GitLab package artifact pipeline"
```

### Task 7: Hardware Validation And Goal Closure

**Files:**
- Modify: `docs/package-repository.md`
- Modify: `docs/hardware/msi-claw-8-ai-plus.md`

- [ ] **Step 1: Install from the published repository on the handheld**

Run on the target device:

```bash
curl -fsSL https://holo.libz.so/rivoreo-steamos/bootstrap.sh | sudo bash
```

Expected: pacman installs `rivoreo-keyring`, `rivoreo-steamos-repo`, and
`steamos-intel-handheld`; the service is active.

- [ ] **Step 2: Verify device behavior**

Run from the development host:

```bash
scripts/verify-on-device.sh root@<handheld-host>
```

Expected: CPU power reads from RAPL `package-0`, GPU power reads from RAPL
`uncore`, SteamOS Manager remote TDP works, and no failed systemd units remain.

- [ ] **Step 3: Reboot and rerun verification**

Run:

```bash
ssh root@<handheld-host> reboot
scripts/verify-on-device.sh root@<handheld-host>
```

Expected: same success criteria as Step 2.

- [ ] **Step 4: Validate SteamOS OTA repair**

After a SteamOS update, rerun:

```bash
curl -fsSL https://holo.libz.so/rivoreo-steamos/bootstrap.sh | sudo bash
scripts/verify-on-device.sh root@<handheld-host>
```

Expected: bootstrap is safe to rerun and restores repo/package/service state.

- [ ] **Step 5: Record validation results**

Append exact SteamOS version, kernel, package versions, and verification result
to `docs/hardware/msi-claw-8-ai-plus.md`.

- [ ] **Step 6: Commit validation docs**

Run:

```bash
git add docs/package-repository.md docs/hardware/msi-claw-8-ai-plus.md
git commit -m "docs(packaging): record pacman repository validation"
```
