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
- Optionally mirror TDP requests to guarded MSI Claw 8 AI+ EC PL1/PL2 bytes.
- Expose the validated MSI Claw 8 AI+ A2VM battery charge-limit EC byte through
  a Decky Loader plugin and a guarded CLI for 60/80/100 percent presets.
- Prepare package and uncore RAPL `energy_uj` access so MangoHud can report
  CPU power and Intel integrated GPU power from real kernel counters.
- Provide install and verification harnesses for real SteamOS devices.
- Provide an optional gamescope display workaround for color pipeline
  instability on Intel handhelds.
- Restore package-owned `/etc` integration files from canonical
  `/opt/steamos-intel-handheld/share/etc-artifacts` payloads after SteamOS
  updates rotate the active `/etc` overlay.
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
scripts/install-on-device.sh root@10.100.0.19
scripts/verify-on-device.sh root@10.100.0.19
```

The development installer does not require Docker on the target device. Docker
or an equivalent container runner is only relevant for local package-build
workflows; the published package repository is built in GitHub Actions, and the
installed SteamOS service does not call Docker.

The verifier temporarily sets TDP to 17W by default, confirms SteamOS Manager,
the remote service, RAPL PL1/PL2, and any exposed short-term Tau agree, then
restores 30W by default. Set `VERIFY_TDP_POLICY_MODE=ac-performance` to verify
the AC performance PL2 policy instead of the default Battery Max-Q policy.

The installer keeps this project's executable payload under
`/opt/steamos-intel-handheld`. System configuration remains in the conventional
locations under `/etc`, including systemd units, D-Bus policy, and SteamOS
Manager remote definitions.

The installer also enables `steamos-intel-handheld-restore.service`. That
oneshot service runs before the TDP service and repairs managed `/etc` files
from `/opt/steamos-intel-handheld/share/etc-artifacts` when SteamOS switches to
a fresh `/etc` overlay after an OS update. It restores project-owned systemd,
D-Bus, SteamOS Manager, gamescope, MangoHud drop-in, and NetworkManager
dispatcher files. It only health-checks `/etc/wireguard/rncn-steamdeck.conf` and
never packages, copies, or regenerates WireGuard private keys.

## Optional display workaround

On the MSI Claw 8 AI+ A2VM test device, SteamOS can start gamescope with the
Steam Deck's `1280x800` game canvas even though the internal panel is
`1920x1200`. The display workaround installs a gamescope wrapper so the session
uses the connected `eDP-1` panel's native mode for `-w` and `-h`, keeping the
gamescope canvas 1:1 with the panel. Game render scale or lower resolutions
should then be chosen inside each game instead of by shrinking gamescope.

The same test device can also switch the primary DRM framebuffer between
`XR30` and `XB24` paths when the Steam cursor/overlay disappears. That can look
like a subtle color or gamma shift in games.

The workaround also installs a gamescope known-display Lua profile for the
MSI Claw 8 AI+ internal `CSW` `PN8007QB1-2` panel. That lets gamescope identify
the panel as a 1920x1200 non-HDR internal display with a 48-120Hz dynamic
refresh range instead of treating it as an unknown display with only the EDID's
60Hz and 120Hz modes.

The workaround uses gamescope's runtime control channel after the session starts:

```bash
scripts/configure-gamescope-display-workaround.sh enable root@10.100.0.19
scripts/configure-gamescope-display-workaround.sh disable root@10.100.0.19
```

The native-panel wrapper takes effect after the next gamescope session restart
or reboot. The enabled user service waits for SteamOS to write
`/run/user/1000/gamescope-environment`, then runs
`gamescopectl composite_force 1`. The service is bound to
`gamescope-session.service`, so a gamescope session restart stops and re-runs
the workaround instead of leaving the previous oneshot active. It sends the
runtime convar repeatedly for a short startup window because Steam and
gamescope WSI can rebuild the game surface after the user service first starts.
This is intentionally optional because forcing gamescope composition can cost
some latency or power. It should remain a workaround until the Intel/SteamOS
display path can keep a consistent color pipeline by default.

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
- PL1: the clamped SteamOS `TdpLimit` value, preserving the SteamOS slider as
  the sustained power contract.
- PL2: a backend policy derived from the current power source and selected TDP
  policy mode.

For the Core Ultra 7 258V profile, the UI range is 8W to 30W and the short-term
hardware ceiling remains 37W. The default `--tdp-policy auto` resolves to
Battery Max-Q when the machine is on battery, AC Performance when it is plugged
in, and Battery Max-Q when the power source is unknown. Battery Max-Q uses
ceiling-rounded 1.25x/1.45x PL2 ratios at low and mid slider values, maps 17W
and 18W PL1 to 25W PL2 with a short 5s Tau, and maps 30W PL1 to 35W PL2 with an
8s Tau. AC Performance maps 9W through 16W PL1 to 25W PL2 with a 10s Tau, then
maps PL1 values of 17W and higher to the 37W PL2 ceiling with a 28s Tau.
`--pl2-w` remains an explicit override for device profiles that need a
different burst limit, and PL2 is still capped by `short_limit_max_w`.
When PL1 itself reaches the short-term ceiling, the ceiling wins over the
usual `PL2 >= PL1 + 1W` headroom preference.
If the kernel does not expose writable `constraint_X_time_window_us` files, Tau
writes are skipped while PL1/PL2 writes still apply.

On the MSI Claw 8 AI+ A2VM, Windows MSI Center M Manual mode was observed to
store Manual PL1/PL2 directly in EC offsets `0x50` and `0x51` as watt values.
The installed service enables `--apply-msi-claw-ec`, which mirrors the same
policy PL1/PL2 to those EC bytes after strict DMI and EC firmware checks. The
installed service keeps the conservative `--msi-claw-ec-shift-policy
tdp-threshold` default for now: 17W stays in comfort (`0xc1`) and values above
17W use turbo (`0xc4`). The staged `profile` shift policy can enable turbo for
Battery Max-Q at 17W, but it should not become the installed default until
on-device sustained-power validation passes. The service debounces EC writes so
Steam slider movement only writes the final settled EC target. It only accepts MSI
`Claw 8 AI+ A2VM`, board `MS-1T52`, and EC firmware strings that start with
`1T52EMS1.109`; other systems fail closed before any EC write.

The battery charge-limit plugin has its own matching guard. It only reads or
writes the validated `0xd7` charge-limit byte when DMI reports MSI
`Claw 8 AI+ A2VM` on board `MS-1T52`. CPU family alone is not treated as
sufficient.

The same root service also prepares MangoHud sensor paths. On the tested
SteamOS 3.8.11 Claw 8 AI+ system, MangoHud runs as `deck` and needs read access
to `/sys/class/powercap/*/energy_uj` for the `package-0` CPU domain and the
`uncore` Intel GPU domain. The service enables those domains, grants read access
to the real kernel energy counters at startup, and leaves unrelated RAPL domains
private.

MangoHud upstream already recognizes the Intel `xe` driver for fdinfo load, GT
frequency, and throttling, but on this system the driver does not expose
`/sys/class/drm/renderD128/device/hwmon`. The MangoHud submodule tracks the
`JohnnySun/MangoHud:intel-rapl-gpu-power` fork branch, which reads Intel
`i915`/`xe` GPU power from the RAPL `uncore` energy counter when present. It
also maps Intel integrated fdinfo shared memory into the SteamOS `vram` overlay
row, using `drm-resident-system0` on `i915` and `drm-resident-gtt` on `xe` when
local or VRAM fdinfo memory is absent. This is still current-process memory,
not a total system VRAM counter. GPU temperature is still not faked or shown
until the `xe` kernel driver exposes a real DRM hwmon temperature input such as
`/sys/class/drm/renderD128/device/hwmon/hwmon*/temp*_input`.

## Repository layout

- `src/steamos_intel_handheld/` - Python service code.
- `data/` - systemd, D-Bus, SteamOS Manager, and optional gamescope integration
  files.
- `decky/` - Decky Loader plugin assets packaged for the charge-limit UI.
- `external/MangoHud/` - MangoHud fork branch used for the Intel RAPL GPU
  power patch; keep `upstream` pointed at flightlessmango for mainline merges.
- `scripts/` - real-device install, verification, and inventory harness.
- `tests/` - hardware-free unit tests.
- `docs/` - design notes, hardware notes, upstreaming plan, and AI harness
  guidance.
- `docs/release-process.md` - operator runbook for hidden Arch release
  candidates and stable repository publication.
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
