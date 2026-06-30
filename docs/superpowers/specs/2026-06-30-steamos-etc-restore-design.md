# SteamOS ETC Restore Design

## Goal

Build a small self-healing system service that restores this project's critical
`/etc` artifacts after SteamOS image updates switch the active `/etc` overlay.
The feature should prevent TDP, SteamOS Manager, MangoHud, gamescope, and
WireGuard roaming support from silently disappearing after an OS update.

## Background

On SteamOS 3.8.12 build `20260629.1`, the system booted with a new `/etc`
overlay and preserved the old overlay under `/etc/previous`. Some administrator
state was migrated, including the root system service
`steamos-intel-handheld-power-control.service`, the WireGuard config, and the
`wg-quick@rncn-steamdeck.service` enable symlink. Other project artifacts were
not migrated, including DBus policy, SteamOS Manager remote configuration,
gamescope user service hooks, MangoHud drop-ins, gamescope known-display
profile, and NetworkManager dispatcher files.

The restore design treats `/etc` as the runtime target, not as the source of
truth. The source of truth for managed restore payloads lives under `/opt`,
which survived the observed SteamOS update.

## Architecture

Add a root oneshot service:

```text
steamos-intel-handheld-restore.service
```

The service runs:

```text
/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply
```

The restore CLI reads a manifest and canonical artifacts from:

```text
/opt/steamos-intel-handheld/share/etc-artifacts/
```

It compares the canonical files with the active `/etc` paths, restores missing
or managed-drifted files according to the policy below, reloads service
managers, and restarts only services that are safe to restart without tearing
down the current gamescope session.

`steamos-intel-handheld-power-control.service` depends on the restore service:

```ini
Wants=steamos-intel-handheld-restore.service
After=steamos-intel-handheld-restore.service
```

The restore service is installed as a root system service and enabled for
`multi-user.target`. This makes it a boot-level administrator service, matching
the class of systemd units SteamOS migrated during the observed update.

## Restore Policy

Use a hybrid restore policy.

Managed files are restored when missing or when their checksum differs from the
canonical copy. These files are project-owned and should remain package
authoritative:

- `/etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf`
- `/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml`
- `/etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf`
- `/etc/systemd/user/steamos-intel-handheld-gamescope-display.service`
- `/etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-gamescope-display.service`
- `/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf`
- `/etc/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua`
- `/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg`

Local or secret-bearing files are restored only when missing. If present but
different, the CLI reports drift and leaves the file untouched:

- `/etc/wireguard/rncn-steamdeck.conf`

This preserves WireGuard private keys and local endpoint adjustments while still
allowing the restore service to flag unexpected drift.

## Manifest

The manifest lives in the package source tree at:

```text
data/restore/manifest.toml
```

The package installs it to:

```text
/opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml
```

Each entry records:

- destination path under `/etc`
- source path under `/opt/steamos-intel-handheld/share/etc-artifacts`
- restore policy: `managed` or `missing-only`
- file mode
- owner and group, both `root:root`
- post-restore action tags such as `systemd-system`, `dbus-system`,
  `systemd-user`, `networkmanager-dispatcher`, and `service-restart`

The CLI validates the manifest before applying changes. Invalid absolute source
paths, paths escaping `/etc`, duplicate destinations, unsupported policies, and
unsupported modes are fatal errors.

## Reload And Restart Behavior

After copying any files, the CLI performs only the minimum required reloads.

System-level reloads:

```text
systemctl daemon-reload
busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig
```

Deck user reloads run only when `/run/user/1000/bus` exists:

```text
runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user daemon-reload
```

Safe restarts use `try-restart` so inactive services are not force-started
unnecessarily:

- `steamos-manager.service`
- `gamescope-mangoapp.service`
- `steamos-intel-handheld-gamescope-display.service`

The restore service does not automatically restart:

- `gamescope-session.service`
- `sddm.service`
- Steam itself

Changing the gamescope wrapper path affects the next gamescope session start.
The runtime display workaround can be re-applied immediately through
`steamos-intel-handheld-gamescope-display.service`, but native panel sizing
still waits for a gamescope session restart or reboot.

WireGuard behavior is conservative. The restore service may reload systemd after
restoring dispatcher files, but it does not restart `wg-quick@rncn-steamdeck`
when the tunnel is already active. If the WireGuard service is inactive and the
config exists, the CLI reports the inactive service and leaves network repair to
manual operator action. The command never starts inactive tunnels.

## CLI Modes

The restore CLI supports:

```text
steamos-intel-handheld-restore-etc --check
steamos-intel-handheld-restore-etc --apply
steamos-intel-handheld-restore-etc --json --check
steamos-intel-handheld-restore-etc --json --apply
```

`--check` reports missing files, managed drift, local drift, and planned reloads
without writing. `--apply` restores files and runs reload/restart actions.
`--json` emits a stable machine-readable summary for tests and diagnostic
tooling.

Exit status:

- `0`: no missing managed artifacts remain after the command
- `1`: restore failed or required artifacts remain missing
- `2`: invalid manifest or invalid CLI arguments

Local drift on `missing-only` files is reported but does not make `--check` or
`--apply` fail.

## Packaging

The main `steamos-intel-handheld` package installs:

- the restore CLI wrapper under `/opt/steamos-intel-handheld/bin/`
- the restore systemd unit under `/usr/lib/systemd/system/`
- canonical restore artifacts under `/opt/steamos-intel-handheld/share/etc-artifacts/`
- managed runtime copies under `/etc` through the package payload

The package install hook enables the restore service and the power-control
service. The hook may run the restore CLI once after install or upgrade, but the
boot-time service remains the durable SteamOS-update recovery path.

The development installer mirrors the same layout so live handheld testing and
package installs exercise the same source-of-truth structure.

## Verification

Unit tests cover:

- manifest parsing and path validation
- missing managed files are restored
- managed drift is overwritten
- missing-only files are restored when absent
- missing-only drift is reported without overwrite
- generated action plan includes DBus, systemd system, and systemd user reloads
- JSON output stays stable

Integration asset tests cover:

- package contains restore CLI, system service, manifest, and canonical
  artifacts
- package verification script checks the restore payload
- `steamos-intel-handheld-power-control.service` depends on
  `steamos-intel-handheld-restore.service`

Real-device verification covers:

- simulate a SteamOS update by moving selected current `/etc` artifacts aside
  and running `steamos-intel-handheld-restore-etc --apply`
- verify TDP remote works through `scripts/verify-on-device.sh`
- verify `gamescope-mangoapp.service` points to
  `/opt/steamos-intel-handheld/bin/mangoapp`
- verify WireGuard dispatcher is restored and executable
- verify no failed systemd units remain

The real-device simulation must avoid deleting WireGuard private configuration
without a backup. Tests should move files to a timestamped temporary directory
and restore them on failure.

## Rollout

Implement behind normal package and development installer paths. Deploy to the
handheld with the development installer first, then validate with:

```text
steamos-intel-handheld-restore-etc --check
steamos-intel-handheld-restore-etc --apply
VERIFY_TDP_POLICY_MODE=ac-performance scripts/verify-on-device.sh root@10.100.0.19
```

After local validation, include the restore payload in release artifact
verification. A hidden release candidate should validate the package contents
before any stable release.
