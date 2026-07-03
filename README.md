# SteamOS Intel Handheld

SteamOS support layer for Intel handheld PCs, starting with the MSI Claw 8 AI+
A2VM. The first production feature is a system D-Bus TDP provider backed by
Intel RAPL powercap controls.

The project is intentionally structured so it can grow from a field-tested
overlay into an Arch/SteamOS package, and so pieces can be proposed upstream
when the interfaces settle.

## Current scope

- Own the system bus name `org.rivoreo.SteamOSManager.PowerControl`, expose
  `com.steampowered.SteamOSManager1.TdpLimit1` there, and bridge that provider
  into SteamOS Manager through a `remotes.d` fragment so Steam's performance UI
  can drive it.
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
the workaround instead of leaving the previous helper process active. It sends the
runtime convar repeatedly for a short startup window because Steam and
gamescope WSI can rebuild the game surface after the user service first starts.
This is intentionally optional because forcing gamescope composition can cost
some latency or power. It should remain a workaround until the Intel/SteamOS
display path can keep a consistent color pipeline by default.

## Local verification

```bash
scripts/check-local.sh
```

## TDP integration

The TDP backend is registered on the system bus:

```bash
busctl --system set-property \
  org.rivoreo.SteamOSManager.PowerControl \
  /org/rivoreo/SteamOSManager/PowerControl \
  com.steampowered.SteamOSManager1.TdpLimit1 \
  TdpLimit u 17
```

The package restores the SteamOS Manager remote shim into
`/etc/steamos-manager/remotes.d` from the canonical payload under
`/opt/steamos-intel-handheld/share/etc-artifacts/steamos-manager/remotes.d/`.
On the first MSI Claw 8 AI+ test device, SteamOS `3.8.11 (20260620.1)` and
`3.8.12 (20260629.1)` both contain bit-identical `steamos-manager 26.2.1-1`
binaries and device metadata. The fragile part is startup ordering: the user
`steamos-manager.service` can time out if the remote provider already owns its
system bus name while SteamOS Manager is discovering the static remote. The
installed unit therefore uses `wait-and-serve`: the remote file is restored
first, SteamOS Manager starts without the provider present, and the provider
only claims `org.rivoreo.SteamOSManager.PowerControl` after the user service is
active. The verifier checks both the project-owned system-bus provider and the
SteamOS Manager `steamosctl` path.

This project maps the provider's `TdpLimit` value to RAPL as:

- Requested TDP is clamped to the 258V handheld sustained range: 8W to 30W.
- PL1: the clamped `TdpLimit` value, preserving a single sustained power
  contract.
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

## Game power governor

The optional game power governor helps Intel integrated graphics keep package
headroom when CPU boost competes with the iGPU under the same SteamOS TDP. It
is installed default-on with the reversible EPP-only policy:

```bash
--game-power-mode gpu-priority
```

Use the standalone validation CLI when checking a specific game scene:

```bash
steamos-intel-handheld-game-power --mode observe --duration-s 30
steamos-intel-handheld-game-power --mode gpu-priority --duration-s 30 --target-appid 1091500
VERIFY_GAME_POWER_APPID=1091500 scripts/verify-game-power-on-device.sh root@10.100.0.19
```

`observe` only reads sensors. `gpu-priority` snapshots CPUFreq policy state,
applies reversible EPP hints, and only applies max-frequency caps when
`--cpu-cap` is requested. The governor restores the previous CPU EPP and frequency limits when the active policy deactivates, the command exits, the service stops, or a write fails. It does not raise the SteamOS TDP, does not raise PL1, and does not replace SteamOS Manager's TDP slider.

### Game-power profiling

The game-power profiler compares policy runs using MangoHud FPS data and
machine-readable game-power samples:

```bash
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

By default the guarded wrapper runs an imported-log capture at 22W for:

- `off`
- `gpu-priority`

To include the stronger CPU max-frequency cap candidate, opt into it in the
profile matrix instead of changing the installed default:

```bash
PROFILE_GAME_POWER_CAPTURE_MODE=controlled \
PROFILE_GAME_POWER_REPEATS=3 \
PROFILE_GAME_POWER_POLICIES="off gpu-priority gpu-priority-cpu-cap" \
PROFILE_GAME_POWER_CPU_CAP_VARIANTS="loose:3400:2600:0.35 balanced:3200:2400:0.35 conservative:3000:2200:0.30" \
PROFILE_GAME_POWER_PCORE_MAX_MHZ=3000 \
PROFILE_GAME_POWER_ECORE_MAX_MHZ=2200 \
PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD=0.30 \
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

The wrapper temporarily forces the installed service governor to `off`, so the
standalone profiler controls the test policy and the `off` run is a real
baseline. Results are copied into `.cache/game-power/profiles/`. Each run
directory contains `manifest.json`, `summary.json`, `game-power.jsonl`,
`cgroup-pressure.jsonl`, CPU policy snapshots, TDP snapshots, and the MangoHud
CSV/summary used for FPS analysis. Controlled capture temporarily installs a
runtime user-service drop-in for `gamescope-mangoapp.service`, restarts
`mangoapp`, and uses `mangohudctl` to start and stop one logging session per
policy run. The drop-in is removed and `mangoapp` is restarted again during
restore.

Imported captures are useful for parser and comparison development, but they do
not prove an automated A/B result. A policy recommendation requires controlled
capture, exact restore, and repeated runs that meet the comparison thresholds.
After collecting repeated controlled runs, aggregate the profile root instead of
trusting a single short sample:

```bash
steamos-intel-handheld-game-power-profile aggregate \
  --root .cache/game-power/profiles \
  --baseline-policy off \
  --candidate-policy gpu-priority \
  --candidate-policy gpu-priority-cpu-cap \
  --appid 1091500 \
  --tdp-w 22 \
  --duration-s 60 \
  --warmup-s 10 \
  --poll-s 2 \
  --min-runs 3
```

`aggregate` scans `summary.json` files, groups runs by AppID, TDP, and policy,
effective CPU-cap tunables, and capture timing. It uses median FPS/power
metrics, and only recommends a candidate when every included run is controlled,
every restore check passed, and both baseline and candidate have enough
repeated samples. AppID is an experiment grouping key; production game-power
policy should remain a generic telemetry-driven governor rather than a
per-game table.

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
