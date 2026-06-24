# SteamOS Intel Handheld

SteamOS support layer for Intel handheld PCs, starting with the MSI Claw 8 AI+
A2VM. The first production feature is a SteamOS Manager remote TDP provider
backed by Intel RAPL powercap controls.

The project is intentionally structured so it can grow from a field-tested
overlay into an Arch/SteamOS package, and so pieces can be proposed upstream
when the interfaces settle.

## Current scope

- Expose `com.steampowered.SteamOSManager1.TdpLimit1` through the SteamOS
  Manager remote interface mechanism.
- Own the system bus name `org.rivoreo.SteamOSManager.PowerControl`.
- Apply TDP requests to Intel RAPL PL1 and PL2 limits.
- Prepare package and uncore RAPL `energy_uj` access so MangoHud can report
  CPU power and Intel integrated GPU power from real kernel counters.
- Provide install and verification harnesses for real SteamOS devices.
- Provide an optional gamescope display workaround for color pipeline
  instability on Intel handhelds.
- Keep unit tests independent from D-Bus and physical hardware by using a fake
  sysfs powercap tree.

## Supported hardware

Known target:

- MSI Claw 8 AI+ A2VM / Intel Lunar Lake

Planned target family:

- MSI Claw 8 AI+ EX and other Intel handhelds with compatible RAPL controls.

## Quick development install

The harness expects root SSH access to the target SteamOS machine.

```bash
scripts/install-on-device.sh root@192.168.128.214
scripts/verify-on-device.sh root@192.168.128.214
```

The verifier temporarily sets TDP to 28W, confirms SteamOS Manager, the remote
service, and RAPL agree, then restores 30W by default.

The installer keeps this project's executable payload under
`/opt/steamos-intel-handheld`. System configuration remains in the conventional
locations under `/etc`, including systemd units, D-Bus policy, and SteamOS
Manager remote definitions.

## Optional display workaround

On the MSI Claw 8 AI+ A2VM test device, gamescope can switch the primary DRM
framebuffer between `XR30` and `XB24` paths when the Steam cursor/overlay
disappears. That can look like a subtle color or gamma shift in games.

The workaround uses gamescope's runtime control channel after the session starts:

```bash
scripts/configure-gamescope-display-workaround.sh enable root@192.168.128.214
scripts/configure-gamescope-display-workaround.sh disable root@192.168.128.214
```

The enabled user service waits for SteamOS to write
`/run/user/1000/gamescope-environment`, then runs
`gamescopectl composite_force 1`. This is intentionally optional because
forcing gamescope composition can cost some latency or power. It should remain
a workaround until the Intel/SteamOS display path can keep a consistent color
pipeline by default.

## Local verification

```bash
scripts/check-local.sh
```

## SteamOS Manager integration

The remote is registered through:

```toml
[TdpLimit1]
bus_name = "org.rivoreo.SteamOSManager.PowerControl"
object_path = "/org/rivoreo/SteamOSManager/PowerControl"
```

The service waits until the deck user's `steamos-manager` user service is
active before owning the D-Bus name. That preserves SteamOS Manager startup
ordering and avoids the startup deadlock seen when a remote is already present
while the user manager is still registering its own services.

SteamOS exposes one `TdpLimit` value through this interface. This project maps
that value to RAPL as:

- Requested TDP is clamped to the 258V handheld sustained range: 8W to 30W.
- PL1: the clamped SteamOS `TdpLimit` value.
- PL2: `min(PL1 + 2W, 32W, short_limit_max_w)`.

For the Core Ultra 7 258V profile, the UI range is 8W to 30W and the short-term
hardware ceiling remains 37W. Intel-published Claw 8 AI+ game test points use
17W/19W and 30W/32W, so the default policy follows that fixed +2W curve instead
of a generic notebook PL2 multiplier. Future device profiles can override PL2
with `--pl2-w` when platform thermals require a different burst limit.

The same root service also prepares MangoHud sensor paths. On the tested
SteamOS 3.8.11 Claw 8 AI+ system, MangoHud runs as `deck` and needs read access
to `/sys/class/powercap/*/energy_uj` for the `package-0` CPU domain and the
`uncore` Intel GPU domain. The service grants read access to those real kernel
energy counters at startup and leaves unrelated RAPL domains private.

MangoHud upstream already recognizes the Intel `xe` driver for fdinfo load, GT
frequency, and throttling, but on this system the driver does not expose
`/sys/class/drm/renderD128/device/hwmon`. The MangoHud submodule tracks the
`JohnnySun/MangoHud:intel-rapl-gpu-power` fork branch, which reads Intel
`i915`/`xe` GPU power from the RAPL `uncore` energy counter when present. GPU
temperature is still not faked; it remains 0 until the kernel exposes a real
Intel GPU temperature sensor.

## Repository layout

- `src/steamos_intel_handheld/` - Python service code.
- `data/` - systemd, D-Bus, SteamOS Manager, and optional gamescope integration
  files.
- `external/MangoHud/` - MangoHud fork branch used for the Intel RAPL GPU
  power patch; keep `upstream` pointed at flightlessmango for mainline merges.
- `scripts/` - real-device install, verification, and inventory harness.
- `tests/` - hardware-free unit tests.
- `docs/` - design notes, hardware notes, upstreaming plan, and AI harness
  guidance.
- `packaging/arch/` - Arch/SteamOS package draft.

See `docs/references.md` for the power-management references behind the default
PL1/PL2 mapping.

## Upstream posture

This repo keeps local policy small. The remote provider implements a SteamOS
Manager interface instead of patching SteamOS Manager directly. Once the
hardware support matrix and failure modes are clearer, the relevant pieces can
be split into upstreamable changes:

- device metadata for SteamOS Manager
- a generic Intel RAPL TDP backend
- packaging and service ordering guidance
