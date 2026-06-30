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
`multi-user.target`. The package may carry the unit under `/usr/lib/systemd`,
but the installer and package hook must also place a durable copy in
`/etc/systemd/system/steamos-intel-handheld-restore.service`, because the
observed SteamOS update migrated full system units from `/etc/systemd/system`
while switching the system image under `/usr`. This `/etc` unit is the durable
anchor; `/usr/lib/systemd` is package metadata, not the recovery anchor.

## Restore Policy

Use a hybrid restore policy.

Managed files are restored when missing or when their checksum differs from the
canonical copy. These files are project-owned and should remain package
authoritative when their owning package has installed a manifest entry:

- `/etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf`
- `/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml`
- `/etc/systemd/system/steamos-intel-handheld-power-control.service`
- `/etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf`
- `/etc/systemd/user/steamos-intel-handheld-gamescope-display.service`
- `/etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-gamescope-display.service`
- `/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf`
- `/etc/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua`
- `/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg`

Local or secret-bearing files are not restored from package canonical payloads.
The CLI reports whether they are present and whether dependent services are
active, but it does not create or overwrite them:

- `/etc/wireguard/rncn-steamdeck.conf`

This avoids packaging or duplicating WireGuard private keys. If this file is
missing, the restore report instructs the operator to re-enroll the tunnel
instead of attempting to synthesize a config.

## Manifest

The primary manifest lives in the package source tree at:

```text
data/restore/manifest.toml
```

The main package installs it to:

```text
/opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml
```

Additional packages may install manifest fragments under:

```text
/opt/steamos-intel-handheld/share/etc-artifacts/manifest.d/*.toml
```

The restore CLI merges the primary manifest and all fragment manifests in
lexical order. Duplicate destinations are fatal. This keeps the main package
from owning `steamos-intel-handheld-mangoapp` artifacts while still letting the
single restore service repair files installed by companion packages.

Each entry records:

- artifact type: `file` or `symlink`
- destination path under `/etc`
- source path under `/opt/steamos-intel-handheld/share/etc-artifacts`
  for file artifacts
- symlink target for symlink artifacts
- restore policy: `managed` or `health-check`
- file mode
- owner and group, both `root:root`
- post-restore action tags such as `systemd-system`, `dbus-system`,
  `systemd-user`, `networkmanager-dispatcher`, and `service-restart`
- optional health-check metadata for local files that must be present but are
  not restored, such as WireGuard config paths

The CLI validates the manifest before applying changes. Invalid absolute source
paths, paths escaping `/etc`, duplicate destinations, unsupported artifact
types, unsupported policies, unsupported modes, and symlink targets that are
absolute or escape the destination directory are fatal errors.

## Package Boundaries

The main `steamos-intel-handheld` package owns and restores:

- DBus policy
- SteamOS Manager remote configuration
- power-control system service anchor under
  `/etc/systemd/system/steamos-intel-handheld-power-control.service`
- gamescope native-panel wrapper, known-display profile, and display workaround
  user service hooks
- NetworkManager dispatcher for the known `rncn-steamdeck` tunnel, when that
  dispatcher is installed through this package

The `steamos-intel-handheld-mangoapp` package owns and restores:

- `/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf`
- `/opt/steamos-intel-handheld/bin/mangoapp`

The restore service exists in the main package but reads manifest fragments
from all installed companion packages. If the MangoHud package is absent, no
mangoapp drop-in is restored and no mangoapp restart is attempted.

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

Action failure handling:

- copy failures for managed files are fatal
- manifest validation failures are fatal
- `systemctl daemon-reload` is fatal when any systemd unit or drop-in changed
- DBus `ReloadConfig` is fatal when DBus policy changed
- deck user `daemon-reload` and user service `try-restart` failures are
  reported as warnings because the deck user bus may be absent during early boot
- warning-level action failures appear in JSON output and journal logs

The restore service does not automatically restart:

- `gamescope-session.service`
- `sddm.service`
- Steam itself

Changing the gamescope wrapper path affects the next gamescope session start.
The runtime display workaround can be re-applied immediately through
`steamos-intel-handheld-gamescope-display.service`, but native panel sizing
still waits for a gamescope session restart or reboot.

WireGuard behavior is conservative. The restore service may reload systemd after
restoring dispatcher files, but it never starts or restarts
`wg-quick@rncn-steamdeck`. If the WireGuard service is inactive, or
`/etc/wireguard/rncn-steamdeck.conf` is missing, the CLI reports that state and
leaves network repair to manual operator action.

## CLI Modes

The restore CLI supports:

```text
steamos-intel-handheld-restore-etc --check
steamos-intel-handheld-restore-etc --apply
steamos-intel-handheld-restore-etc --json --check
steamos-intel-handheld-restore-etc --json --apply
```

`--check` reports missing files, managed drift, health-check warnings, and
planned reloads without writing. `--apply` restores files and runs
reload/restart actions. `--json` emits a stable machine-readable summary for
tests and diagnostic tooling.

Exit status:

- `0`: no missing managed artifacts remain after the command
- `1`: restore failed or required artifacts remain missing
- `2`: invalid manifest or invalid CLI arguments

Missing `health-check` files, absent deck user bus, inactive WireGuard, and
failed optional user-service restarts are reported but do not make `--check` or
`--apply` fail.

## Packaging

The main `steamos-intel-handheld` package installs:

- the restore CLI wrapper under `/opt/steamos-intel-handheld/bin/`
- the restore systemd unit under `/usr/lib/systemd/system/` and a durable copy
  under `/etc/systemd/system/`
- the power-control systemd unit under `/usr/lib/systemd/system/` and a durable
  copy under `/etc/systemd/system/`
- canonical restore artifacts owned by the main package under
  `/opt/steamos-intel-handheld/share/etc-artifacts/`
- managed runtime copies under `/etc` through the package payload

The `steamos-intel-handheld-mangoapp` package installs its canonical artifacts
and manifest fragment under the same `/opt/steamos-intel-handheld/share`
namespace, without adding its files to the main package manifest.

The package install hook enables the restore service and the power-control
service. The hook may run the restore CLI once after install or upgrade, but the
boot-time service remains the durable SteamOS-update recovery path.

The development installer mirrors the same layout so live handheld testing and
package installs exercise the same source-of-truth structure.

## Verification

Unit tests cover:

- manifest parsing and path validation
- manifest fragment merge behavior and duplicate destination rejection
- missing managed files are restored
- managed drift is overwritten
- local health-check files are reported without restore
- absent companion package fragments are ignored without restoring companion
  package files
- generated action plan includes DBus, systemd system, and systemd user reloads
- user-bus action failures are warnings while system reload failures are fatal
- JSON output stays stable

Integration asset tests cover:

- package contains restore CLI, system service, manifest, and canonical
  artifacts
- package installs a durable restore service unit under `/etc/systemd/system`
- package verification script checks the restore payload
- `steamos-intel-handheld-power-control.service` depends on
  `steamos-intel-handheld-restore.service`
- `steamos-intel-handheld-mangoapp` contributes its own manifest fragment
  instead of being listed in the main package manifest

Real-device verification covers:

- simulate a SteamOS update by moving selected current `/etc` artifacts aside
  and running `steamos-intel-handheld-restore-etc --apply`
- verify TDP remote works through `scripts/verify-on-device.sh`
- verify `gamescope-mangoapp.service` points to
  `/opt/steamos-intel-handheld/bin/mangoapp`
- verify WireGuard dispatcher is restored and executable
- verify WireGuard config presence is reported but never overwritten
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
