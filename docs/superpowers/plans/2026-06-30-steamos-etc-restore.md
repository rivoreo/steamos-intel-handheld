# SteamOS ETC Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a boot-time self-heal service that restores this project's managed `/etc` artifacts from canonical `/opt/steamos-intel-handheld/share/etc-artifacts` payloads after SteamOS updates rotate the active `/etc` overlay.

**Architecture:** Implement a Python restore CLI with manifest parsing, file and symlink artifact handling, health-check reporting, action planning, JSON output, and safe reload/restart behavior. Package the CLI, restore unit, durable `/etc/systemd/system` anchors, manifests, canonical artifact payloads, and package verification gates so development installs and release packages exercise the same layout.

**Tech Stack:** Python 3.10+, `tomllib`, pytest, Bash installers, Arch `PKGBUILD`, systemd oneshot units, GitHub Actions release artifact checks.

---

## File Structure

- Create `src/steamos_intel_handheld/restore_etc.py`: manifest loader, validator, restore planner, applier, action runner, CLI entrypoint.
- Create `tests/test_restore_etc.py`: parser, validation, file/symlink restore, health-check, action planning, and action failure behavior.
- Create `tests/test_restore_etc_cli.py`: CLI `--check`, `--apply`, `--json`, module entrypoint, and exit code behavior.
- Modify `pyproject.toml`: add `steamos-intel-handheld-restore-etc` console script.
- Create `data/systemd/steamos-intel-handheld-restore.service`: root oneshot restore unit.
- Modify `data/systemd/steamos-intel-handheld-power-control.service`: add `Wants=` and `After=` dependency on the restore unit.
- Create `data/restore/manifest.toml`: main package artifact manifest.
- Create `data/restore/manifest.d/10-mangoapp.toml`: source fragment copied into the mangoapp package input directory.
- Create `data/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg`: dispatcher payload for the known `rncn-steamdeck` tunnel.
- Modify `scripts/install-on-device.sh`: install restore wrapper, manifests, canonical artifacts, durable units, dispatcher, enable restore service, and run restore once.
- Modify `scripts/configure-gamescope-display-workaround.sh`: install canonical artifacts into `/opt/steamos-intel-handheld/share/etc-artifacts` when enabling.
- Modify `scripts/configure-mangoapp-dropin.sh`: install mangoapp manifest fragment and canonical drop-in when enabling.
- Modify `packaging/arch/PKGBUILD`: install restore CLI payload, durable `/etc` units, manifests, canonical main artifacts, and dispatcher.
- Modify `packaging/arch/steamos-intel-handheld.install`: enable restore service, enable power-control service, run restore hook when available.
- Modify `packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD`: install mangoapp manifest fragment and canonical drop-in.
- Modify `scripts/build-arch-release-repo.sh`: copy mangoapp manifest fragment into the package input directory.
- Modify `.github/workflows/arch-release.yml`: verify restore service, manifests, canonical artifacts, durable anchors, dispatcher, and mangoapp fragment in release packages.
- Modify `scripts/verify-gitlab-pacman-artifact.sh` and `tests/test_gitlab_ci_packaging.py`: verify restore payload in dry-run package artifacts.
- Modify `tests/test_integration_assets.py` and `tests/test_arch_release_workflow.py`: assert package and workflow contain the restore payload.
- Modify docs (`README.md`, `docs/package-repository.md`, `docs/release-process.md`): describe self-heal behavior and release verification.

### Task 1: Restore Core Tests

**Files:**
- Create: `tests/test_restore_etc.py`
- Create later: `src/steamos_intel_handheld/restore_etc.py`

- [ ] **Step 1: Write failing parser and planner tests**

```python
from pathlib import Path

import pytest

from steamos_intel_handheld import restore_etc


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def test_load_manifest_merges_fragments_and_rejects_duplicate_destinations(tmp_path):
    root = tmp_path / "opt" / "share" / "etc-artifacts"
    write_file(root / "payload.conf", "canonical\n")
    write_file(root / "manifest.toml", """
[[artifact]]
destination = "/etc/example.conf"
source = "payload.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["systemd-system"]
""")
    write_file(root / "manifest.d" / "10-dup.toml", """
[[artifact]]
destination = "/etc/example.conf"
source = "other.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = []
""")

    with pytest.raises(restore_etc.ManifestError, match="duplicate destination"):
        restore_etc.load_manifest(root)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_restore_etc.py::test_load_manifest_merges_fragments_and_rejects_duplicate_destinations -q`
Expected: FAIL with `ImportError` or missing `restore_etc`.

- [ ] **Step 3: Add file, symlink, health-check, and action tests**

Add concrete pytest functions with these exact names:

- `test_check_reports_missing_managed_file_and_planned_actions`
- `test_apply_restores_missing_file_with_mode`
- `test_apply_replaces_managed_drift`
- `test_apply_repairs_symlink_artifact`
- `test_health_check_reports_wireguard_config_without_restoring`
- `test_user_bus_failures_are_warnings_but_system_reload_failure_is_fatal`

Each test uses a temporary `etc_root`, a temporary canonical root, and the `restore_etc.restore` API:

```python
result = restore_etc.restore(
    etc_root=etc_root,
    artifact_root=artifact_root,
    apply=True,
    runner=restore_etc.RecordingRunner(user_bus_exists=True),
)
```

Expected result objects expose `changed`, `failures`, `warnings`, `artifacts`, and `commands`.

- [ ] **Step 4: Run all restore core tests red**

Run: `.venv/bin/pytest tests/test_restore_etc.py -q`
Expected: FAIL because the restore API is not implemented.

### Task 2: Restore Core Implementation

**Files:**
- Create: `src/steamos_intel_handheld/restore_etc.py`
- Test: `tests/test_restore_etc.py`

- [ ] **Step 1: Implement manifest models and validation**

Implement these public names exactly:

```python
DEFAULT_ARTIFACT_ROOT = Path("/opt/steamos-intel-handheld/share/etc-artifacts")
DEFAULT_ETC_ROOT = Path("/etc")

class ManifestError(ValueError):
    pass


class RestoreFailure(RuntimeError):
    pass

@dataclass(frozen=True)
class Artifact:
    destination: str
    artifact_type: str
    policy: str
    source: str | None
    symlink_target: str | None
    mode: int | None
    owner: str
    group: str
    actions: Sequence[str]
    service_restarts: Sequence[str]
```

`load_manifest(artifact_root: Path) -> list[Artifact]` must read `manifest.toml` then lexical `manifest.d/*.toml`, reject duplicate destinations, reject destinations outside `/etc`, reject absolute sources, reject source paths escaping `artifact_root`, reject unsupported `type`, reject unsupported `policy`, and reject symlink targets that are absolute or contain parent-directory escapes.

- [ ] **Step 2: Implement restore planning and applying**

Implement:

```python
def restore(
    *,
    etc_root: Path = DEFAULT_ETC_ROOT,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    apply: bool,
    runner: CommandRunner | None = None,
) -> RestoreResult:
    raise RestoreFailure("restore implementation is not yet wired")
```

Behavior:
- `file` + `managed`: compare SHA-256 of canonical source and destination; copy when missing or drifted; set mode; chown root/root when running as root.
- `symlink` + `managed`: compare `os.readlink`; unlink wrong file/symlink and create the declared relative symlink.
- `health-check`: report presence and optional service status; never create or overwrite.
- Record action tags from changed artifacts only.
- Run system actions after successful copies when `apply=True`.

- [ ] **Step 3: Implement command runner**

Implement `CommandRunner`, `SubprocessRunner`, and `RecordingRunner`. `SubprocessRunner` runs:

```text
systemctl daemon-reload
busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig
runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user daemon-reload
runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user try-restart <service>
```

System reload and DBus reload failures are fatal when their action tag is required. User-bus reload/restart failures are warnings.

- [ ] **Step 4: Run restore core tests green**

Run: `.venv/bin/pytest tests/test_restore_etc.py -q`
Expected: all tests pass.

### Task 3: Restore CLI Tests And Entrypoint

**Files:**
- Create: `tests/test_restore_etc_cli.py`
- Modify: `src/steamos_intel_handheld/restore_etc.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing CLI tests**

Create concrete pytest functions with these exact names:

- `test_json_check_reports_missing_file_and_exit_one_for_missing_managed`
- `test_json_apply_restores_file_and_returns_zero`
- `test_invalid_manifest_returns_exit_code_two`
- `test_module_entrypoint_accepts_json_check`
- `test_console_script_is_declared_in_pyproject`

Use CLI arguments:

```text
--artifact-root <tmp>/opt/share/etc-artifacts
--etc-root <tmp>/etc
--json --check
--json --apply
```

- [ ] **Step 2: Run CLI tests red**

Run: `.venv/bin/pytest tests/test_restore_etc_cli.py -q`
Expected: FAIL because CLI flags and console script are missing.

- [ ] **Step 3: Implement CLI**

`build_parser()` must define mutually exclusive `--check` and `--apply`, optional `--json`, `--artifact-root`, and `--etc-root`. `main(argv: list[str] | None = None) -> int` must return:
- `0` when no managed artifacts remain missing after the command
- `1` when apply failed or required managed artifacts remain missing after apply
- `2` for manifest and argument errors

JSON output must include keys:

```json
{
  "changed": false,
  "failures": [],
  "warnings": [],
  "actions": [],
  "artifacts": []
}
```

- [ ] **Step 4: Run CLI tests green**

Run: `.venv/bin/pytest tests/test_restore_etc_cli.py -q`
Expected: all tests pass.

### Task 4: Systemd Units, Manifests, And Canonical Assets

**Files:**
- Create: `data/systemd/steamos-intel-handheld-restore.service`
- Modify: `data/systemd/steamos-intel-handheld-power-control.service`
- Create: `data/restore/manifest.toml`
- Create: `data/restore/manifest.d/10-mangoapp.toml`
- Create: `data/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg`
- Modify: `tests/test_integration_assets.py`

- [ ] **Step 1: Write failing asset tests**

Add concrete pytest functions with these exact names:

- `test_restore_service_unit_runs_restore_cli_before_power_control`
- `test_restore_manifest_lists_main_package_artifacts_without_mangoapp_dropin`
- `test_mangoapp_restore_fragment_owns_only_mangoapp_dropin`
- `test_networkmanager_dispatcher_is_packaged_as_executable_source`

Expected strings:
- `ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply`
- `WantedBy=multi-user.target`
- `Wants=steamos-intel-handheld-restore.service`
- `After=steamos-intel-handheld-restore.service`
- `/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg`
- `/etc/wireguard/rncn-steamdeck.conf` with `policy = "health-check"`

- [ ] **Step 2: Run asset tests red**

Run: `.venv/bin/pytest tests/test_integration_assets.py -q`
Expected: FAIL on missing restore unit and manifests.

- [ ] **Step 3: Add units and manifests**

`steamos-intel-handheld-restore.service`:

```ini
[Unit]
Description=Restore steamos-intel-handheld /etc integration files
DefaultDependencies=no
After=local-fs.target
Before=multi-user.target

[Service]
Type=oneshot
ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply

[Install]
WantedBy=multi-user.target
```

Main manifest must contain all main-package managed entries and the WireGuard health check. Mangoapp drop-in must only appear in `data/restore/manifest.d/10-mangoapp.toml`.

- [ ] **Step 4: Run asset tests green**

Run: `.venv/bin/pytest tests/test_integration_assets.py -q`
Expected: all tests pass.

### Task 5: Installer And Packaging Integration

**Files:**
- Modify: `scripts/install-on-device.sh`
- Modify: `scripts/configure-gamescope-display-workaround.sh`
- Modify: `scripts/configure-mangoapp-dropin.sh`
- Modify: `packaging/arch/PKGBUILD`
- Modify: `packaging/arch/steamos-intel-handheld.install`
- Modify: `packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD`
- Modify: `scripts/build-arch-release-repo.sh`
- Modify: `tests/test_integration_assets.py`

- [ ] **Step 1: Write failing packaging tests**

Add tests asserting:
- installer creates `/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc`
- installer installs `/opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml`
- installer copies canonical DBus, SteamOS Manager, systemd, gamescope, and dispatcher artifacts under `/opt/steamos-intel-handheld/share/etc-artifacts`
- package installs restore and power-control units to both `/usr/lib/systemd/system` and `/etc/systemd/system`
- install hook runs `systemctl enable steamos-intel-handheld-restore.service`
- mangoapp package installs `manifest.d/10-mangoapp.toml`

- [ ] **Step 2: Run packaging tests red**

Run: `.venv/bin/pytest tests/test_integration_assets.py -q`
Expected: FAIL on missing installer and package lines.

- [ ] **Step 3: Implement installer and packaging changes**

Install canonical artifacts by mirroring existing repository data payloads into:

```text
/opt/steamos-intel-handheld/share/etc-artifacts/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf
/opt/steamos-intel-handheld/share/etc-artifacts/steamos-manager/remotes.d/99-rivoreo-power-control.toml
/opt/steamos-intel-handheld/share/etc-artifacts/systemd/system/steamos-intel-handheld-power-control.service
/opt/steamos-intel-handheld/share/etc-artifacts/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf
/opt/steamos-intel-handheld/share/etc-artifacts/systemd/user/steamos-intel-handheld-gamescope-display.service
/opt/steamos-intel-handheld/share/etc-artifacts/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua
/opt/steamos-intel-handheld/share/etc-artifacts/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg
```

The symlink artifact is represented only in the manifest with `target = "../steamos-intel-handheld-gamescope-display.service"`.

- [ ] **Step 4: Run packaging tests green**

Run: `.venv/bin/pytest tests/test_integration_assets.py -q`
Expected: all tests pass.

### Task 6: Release Artifact Gates And Documentation

**Files:**
- Modify: `.github/workflows/arch-release.yml`
- Modify: `scripts/verify-gitlab-pacman-artifact.sh`
- Modify: `tests/test_arch_release_workflow.py`
- Modify: `tests/test_gitlab_ci_packaging.py`
- Modify: `README.md`
- Modify: `docs/package-repository.md`
- Modify: `docs/release-process.md`

- [ ] **Step 1: Write failing release gate tests**

Update tests to require:
- `usr/bin/steamos-intel-handheld-restore-etc`
- `usr/lib/systemd/system/steamos-intel-handheld-restore.service`
- `etc/systemd/system/steamos-intel-handheld-restore.service`
- `etc/systemd/system/steamos-intel-handheld-power-control.service`
- `opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml`
- `opt/steamos-intel-handheld/share/etc-artifacts/manifest.d/10-mangoapp.toml`
- `opt/steamos-intel-handheld/share/etc-artifacts/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg`

- [ ] **Step 2: Run release gate tests red**

Run: `.venv/bin/pytest tests/test_arch_release_workflow.py tests/test_gitlab_ci_packaging.py -q`
Expected: FAIL on missing artifact checks.

- [ ] **Step 3: Implement workflow and dry-run checks**

Add `contains "$main_pkg"` checks in `.github/workflows/arch-release.yml` and exact `tar -tf "$main_pkg" | grep -Fx "<package-path>"` checks in `scripts/verify-gitlab-pacman-artifact.sh`. Keep release docs aligned with `docs/release-process.md` candidate-vs-stable rules.

- [ ] **Step 4: Run release gate tests green**

Run: `.venv/bin/pytest tests/test_arch_release_workflow.py tests/test_gitlab_ci_packaging.py -q`
Expected: all tests pass.

### Task 7: Local, Device, Commit, And Release Verification

**Files:**
- No new source files.

- [ ] **Step 1: Run focused local tests**

Run:

```bash
.venv/bin/pytest \
  tests/test_restore_etc.py \
  tests/test_restore_etc_cli.py \
  tests/test_integration_assets.py \
  tests/test_arch_release_workflow.py \
  tests/test_gitlab_ci_packaging.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full local harness**

Run:

```bash
PYTHON=.venv/bin/python scripts/check-local.sh
```

Expected: local harness exits 0.

- [ ] **Step 3: Deploy development layout to device**

Run:

```bash
scripts/install-on-device.sh root@10.100.0.19
scripts/configure-gamescope-display-workaround.sh enable root@10.100.0.19
scripts/configure-mangoapp-dropin.sh enable root@10.100.0.19 .cache/steamos-qemu/mangoapp
```

Expected: restore CLI, durable units, manifests, canonical artifacts, gamescope payload, mangoapp payload, and dispatcher exist on device.

- [ ] **Step 4: Run live restore simulation**

On the device, move only non-secret managed files to a timestamped backup directory, run:

```bash
/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --json --apply
```

Verify:
- selected managed files return
- `/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg` returns with executable mode
- `/etc/wireguard/rncn-steamdeck.conf` was not modified
- `systemctl --failed --no-legend` reports no failures

- [ ] **Step 5: Run device verifier**

Run:

```bash
VERIFY_TDP_POLICY_MODE=ac-performance scripts/verify-on-device.sh root@10.100.0.19
```

Expected: verifier exits 0 and restores the configured TDP policy.

- [ ] **Step 6: Commit scoped changes**

Run:

```bash
git status --short
git add <scoped files from this plan and approved pre-existing related package files>
git commit -m "feat: restore SteamOS etc artifacts after updates"
```

Expected: commit contains restore implementation, tests, packaging, docs, and related package state needed by the feature.

- [ ] **Step 7: Publish release channel**

If no protected stable release confirmation exists, publish hidden RC for `pyproject.toml` version `0.2.1`:

```bash
git tag -a v0.2.1-rc.2 -m "v0.2.1-rc.2"
git push origin codex/arch-package-ci-publisher
git push origin v0.2.1-rc.2
gh run list --repo rivoreo/steamos-intel-handheld --workflow "Arch Package Release" --limit 5
gh run watch <run-id> --repo rivoreo/steamos-intel-handheld --exit-status
```

Expected candidate result:
- `validate`: success
- `build-mangoapp`: success
- `build-repo`: success
- `verify-repo-artifact`: success
- `deploy-pages`: skipped
- artifact `signed-pacman-repository` exists

## Self-Review

Spec coverage:
- Restore service, durable anchors, power-control dependency, managed artifacts, health-check WireGuard, manifest fragments, action behavior, CLI modes, packaging, device verification, and hidden RC validation each have a task above.
- Symlink artifacts are explicitly covered because the gamescope wants entry is a relative symlink rather than a file.

Placeholder scan:
- The plan contains concrete file paths, commands, expected results, and public API names for the restore module.

Type consistency:
- Later tasks use the same `restore_etc.restore`, `RestoreResult`, `ManifestError`, and `RecordingRunner` names introduced in Task 1 and Task 2.
