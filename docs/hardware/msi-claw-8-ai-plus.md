# MSI Claw 8 AI+ A2VM

Initial target for this project.

## Observed device data

- Product family: MSI Claw 8 AI+
- DMI board name: `MS-1T52`
- CPU family: Intel Lunar Lake / 258V class
- SteamOS Manager package observed: `26.2.1-1`
- Tested RAPL domain: `/sys/class/powercap/intel-rapl:0`
- SteamOS UI TDP range used by this project: 8W to 30W
- RAPL short-term hardware ceiling used by this project: 37W

## Verified behavior

On the first test device, SteamOS Manager accepted a remote `TdpLimit1`
provider through `/etc/steamos-manager/remotes.d`. Setting 28W through
`steamosctl set-tdp-limit 28` updated:

- SteamOS Manager central `TdpLimit`
- remote provider `TdpLimit`
- Intel RAPL PL1

The test restored 30W afterward.

When the SteamOS UI TDP-limit toggle was enabled and set to 17W, the same path
reported:

- SteamOS Manager central `TdpLimit`: 17W
- remote provider `TdpLimit`: 17W
- RAPL PL1: 17W
- RAPL PL2: initially 21W in the prototype that used a `1.25x` heuristic

The SteamOS UI controls the single SteamOS Manager `TdpLimit`; this project maps
that to PL1 after clamping it to the 258V handheld sustained range. PL2 follows
the Claw game curve `min(PL1 + 2W, 32W)`: 17W maps to 19W, and 30W maps to 32W.
The 37W Maximum Turbo Power value is kept as the short-term hardware ceiling,
not as the default game PL2. A `--pl2-w` override remains available for device
profiles that need a different burst limit.

## MangoHud sensor note

On SteamOS 3.8.11 build `20260620.1` with kernel
`6.16.12-valve24-1-neptune-616-gc748040e4712`, the SteamOS session runs
`mangoapp` as the `deck` user.

CPU temperature is already available to MangoHud through the native `coretemp`
path. During testing, `mangoapp` held an fd for
`/sys/devices/platform/coretemp.0/hwmon/hwmon5/temp1_input`, whose label was
`Package id 0`.

CPU power needed a permissions fix. MangoHud's Linux RAPL backend reads
`energy_uj` from the `package-0` powercap domain, but the tested SteamOS build
exposed both package counters as root-only:

- `/sys/class/powercap/intel-rapl:0/energy_uj`
- `/sys/class/powercap/intel-rapl-mmio:0/energy_uj`

Temporarily granting read access let the `deck` user sample deltas from both
files, proving this path is sufficient for MangoHud CPU power. The service now
enables and prepares those package RAPL energy counters at startup.

GPU power uses a separate real counter on this platform. The kernel exposes
Intel GPU energy as the RAPL `uncore` domain:

- `/sys/class/powercap/intel-rapl:0:1/name`: `uncore`
- `/sys/class/powercap/intel-rapl:0:1/energy_uj`

That counter matches `perf stat -a -e power/energy-gpu/` and is readable by
`deck` after the service enables the `uncore` powercap domain and prepares
MangoHud sensor access. The MangoHud fork
branch used by this project reads this `uncore` counter for Intel `i915`/`xe`
GPU power.

GPU temperature is still unavailable on the tested SteamOS build. The Intel GPU
uses the `xe` driver and exposes fdinfo and GT frequency data, but it does not
expose `/sys/class/drm/renderD128/device/hwmon`. MangoHud mainline expects that
DRM hwmon directory for Intel GPU temperature, so this project does not fake a
temperature value from unrelated sensors.

## Boot note

After reboot, the service started and SteamOS Manager rediscovered the remote.
The service first read the persisted 30W state, then a SteamOS-side client set
TDP to 37W. The provider now clamps that legacy request to 30W and applies
30W/32W to long-term/short-term RAPL constraints. This should be treated as a
SteamOS policy interaction, not as a state persistence failure.

## Display note

The internal display should be treated as an sRGB-class panel unless EDID or
vendor data proves a wider color gamut. Bit depth is a separate concern from
color gamut: EDID reports 8 bits per primary color channel, while the observed
SteamOS session can still use an `XR30` framebuffer format.

On the first test device, the visible color shift coincided with gamescope
switching the primary DRM framebuffer between `XR30` 1920x1200 and `XB24`
1920x1200 paths when the Steam cursor/overlay disappeared. Launching gamescope
with `--force-composition` was not sufficient on this SteamOS build. Applying
the runtime gamescope convar `composite_force 1` with `gamescopectl` stabilized
the session on the `XR30` 1920x1200 path during testing.

The repository keeps this as an optional workaround in
`scripts/configure-gamescope-display-workaround.sh enable root@host`. The
script installs a user service that waits for gamescope's environment file and
runs `gamescopectl composite_force 1` after each gamescope session start. It
does not use `drm_single_plane_optimizations`; disabling that runtime setting
also stabilized the format during one experiment, but it produced a UI freeze
and is not safe enough for the harness.
