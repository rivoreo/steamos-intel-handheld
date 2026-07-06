# Design

## Goal

Provide a small SteamOS support layer for Intel handhelds without patching
SteamOS Manager in place. The first feature is TDP control for MSI Claw 8 AI+
through SteamOS Manager's remote interface support.

## Architecture

`steamos-intel-handheld-power-control` runs as root on the system bus and owns
`org.rivoreo.SteamOSManager.PowerControl`. It exports the SteamOS Manager
`TdpLimit1` interface at both `/org/rivoreo/SteamOSManager/PowerControl` and
SteamOS Manager's canonical `/com/steampowered/SteamOSManager1` object path.

SteamOS Manager discovers third-party providers through static files in
`/etc/steamos-manager/remotes.d`. On the first MSI Claw 8 AI+ test device,
SteamOS `3.8.11 (20260620.1)` and `3.8.12 (20260629.1)` both shipped the same
`steamos-manager 26.2.1-1` binary and MSI Claw device metadata. The regression
surface is the startup ordering around the static remote: SteamOS Manager can
time out during user-service startup if the remote provider already owns its
system bus name while the user service is discovering `TdpLimit1`.

The installed service avoids that boot race by using `wait-and-serve --user
deck`. The restore service puts the remote shim in `/etc` early, SteamOS
Manager starts while the provider is still absent, and the provider only claims
`org.rivoreo.SteamOSManager.PowerControl` after the user service is active.
Package install and the development installer use the same ordering when they
need the Steam UI TDP slider to appear immediately after deployment.

The backend writes Intel RAPL:

- PL1: requested TDP clamped to the handheld sustained range, 8W to 30W
- PL2: profile-aware backend policy derived from PL1, current power source,
  and selected TDP policy mode
- Tau: short-term RAPL time window from policy when the kernel exposes writable
  `constraint_X_time_window_us`

SteamOS Manager currently exposes one `TdpLimit` value, so PL2 is backend
policy. For the Core Ultra 7 258V profile, Intel's published package-power
boundaries are 8W minimum guaranteed power, 17W base power, 30W maximum
guaranteed sustained power, and 37W maximum turbo power. This project treats
37W as the AC Performance ceiling, not as the default battery PL2.

The default `--tdp-policy auto` resolves to Battery Max-Q on battery, AC
Performance on AC power, and Battery Max-Q when the power source is unknown.
Battery Max-Q uses ceiling-rounded ratio steps at low and mid slider values,
maps 17W and 18W PL1 to 25W PL2 with a short 5s Tau, and keeps 30W PL1 at 35W
PL2 with an 8s Tau. AC Performance maps 9W through 16W PL1 to 25W PL2 with a
10s Tau, then maps PL1 values of 17W and higher to 37W PL2 with a 28s Tau.
Battery Low Power and AC Quiet exist as explicit backend policy modes, but the
current SteamOS UI does not expose a separate profile signal for them.

Linux RAPL exposes power controls as named long-term and short-term
constraints, so the backend resolves `constraint_X_name` instead of assuming
fixed constraint indices. The tested kernel reports `long_term` `max_power_uw`
as 17W even though 30W writes are accepted, so PL1 follows the validated 8W to
30W handheld range. For PL2, a non-zero short-term `max_power_uw` is still
treated as a burst ceiling. If a short-term time-window file is missing or not
writable, Tau is skipped and the PL1/PL2 power-limit write remains the
authoritative operation. The PL2 ceiling is a hard cap: when PL1 itself reaches
that ceiling, the backend keeps PL2 at the ceiling instead of trying to maintain
the normal `PL2 >= PL1 + 1W` headroom.

The persisted state is advisory. The service does not force a boot-time TDP by
default because SteamOS or the Steam client may apply its own policy after login.
The optional `--restore-on-start` flag exists for experiments and device
profiles that explicitly want that behavior. On service startup without
`--restore-on-start`, the backend may still reapply the current PL2/Tau envelope
when the persisted state already matches the current long-term RAPL PL1; this
converges policy after service restarts without forcing an old PL1 back onto the
system.

## Game power governor

The Game power governor is a separate control loop for foreground Steam games
on Intel integrated graphics. It is enabled by default with the reversible
GPU-priority EPP policy. CPU max-frequency caps remain available as explicit
profile/debug candidates using the measured P-core 3000MHz, E-core 2400MHz,
and 0.30 core-share entry threshold, but they are not part of the daemon
default. It does not replace SteamOS Manager's TDP slider and does not raise
PL1 automatically. The TDP backend continues to own the total package-power
contract.

The governor observes Steam game cgroups, RAPL package/core/uncore power, and
DRM fdinfo engine activity. In default `gpu-priority` mode it uses reversible
CPU EPP hints to reduce CPU package pressure when the iGPU is active and
package power is already near PL1. Explicit CPU-cap runs add max-frequency caps
with separate entry and sustain hysteresis so a successful cap lowering core
share does not immediately self-cancel.

Every active write starts from a CPUFreq snapshot. The service restores the
previous EPP and `scaling_max_freq` values when the game disappears, the samples
stop matching the GPU-priority policy, the service stops, or any write fails.
The guarded device verifier runs `observe` and `gpu-priority` through
`scripts/verify-game-power-on-device.sh` and fails if the final CPU policy
snapshot differs from the pre-test snapshot.

The game-power profiler is the measurement layer for future scheduler policy
work. It temporarily disables the service governor during an A/B profiling
session so the standalone profiler can compare `off`, `gpu-priority`, and
candidate CPU-cap/background-helper policies without background policy
interference. Profile artifacts include MangoHud FPS
CSV data, game-power JSONL decisions, package/core/uncore power summaries,
render-busy samples, cgroup CPU pressure peaks, TDP snapshots, and CPU policy
restore diffs.

Imported MangoHud captures are marked as imported and cannot produce a positive
policy recommendation. Controlled capture plus exact restore is required before
the profiler can classify a policy as better, rejected, or inconclusive for a
specific TDP and game scene.

### Target-balance mode (V9)

The V9 `target-balance` policy makes the FPS target the control contract instead
of maximizing raw frames. Full design lives in
`docs/game-power-v9-ultimate-design.md`. What ships in the daemon and the Decky
panel: a phase state machine, a convergence trim ladder that returns surplus CPU
turbo to package/iGPU headroom once the target is sustained, a thread color
ledger that maps roles to a least-invasive actuator, and verdict-gated write
lanes (foreground `cpu.uclamp.min` floor and background cgroup shaping). All of
these are additive to the existing telemetry; the public modes (`automatic`,
`observe`, `off`) and the manual FPS target contract are unchanged, and the
`gpu-priority` decision path is byte-identical.

Fail-closed is the default posture. The daemon loads the verdict ledger
(`/var/lib/steamos-intel-handheld/game-power-verdicts.json`, `/run` fallback)
read-only; a missing or corrupt ledger disables every gated lane and each lane
reports a machine-readable why-not reason code. A lane unlocks only on an exact
context match (AppID, FPS target, topology fingerprint, policy version, TDP
bucket) against a `BETTER` verdict exported from controlled profiler evidence.
The installed service default stays `gpu-priority` until controlled device
evidence accepts `target-balance`; the GPU min-frequency floor, `scx-lavd`, and
compact foreground affinity stay profiler-only lanes with no daemon integration.
P-core classification tolerates within-class capacity spread (a policy is PCORE
at >= 85% of the max capacity, so Lunar Lake's 1005-capacity cpu0/1 classify
with the 1024-capacity cpu2/3); the V6 measured cpu-cap evidence predates this
classification fix (policy0/1 previously took the E-core cap on this device),
so `gpu-priority-cpu-cap` A/B claims should be re-validated before new claims.

The Decky backend consumes only the safe control CLI and the daemon runtime
snapshot. It exposes the additive V9 fields (phase, ladder step, a compact
per-color actuator summary, verdict-ledger health, and gated-lane states with
reason codes) as read-only diagnostics that degrade to `None`/absent for the
`gpu-priority` default, stale snapshots, or `off`/`observe`. The panel frontend
(`decky/steamos-intel-handheld-game-power/src/index.tsx`) renders these in the
runtime telemetry section and is rebuilt into `dist/index.js`.

### Demand-shaped power (V10 framework)

The V10 framework (`docs/game-power-v10-direction.md`,
`docs/game-power-v10-framework-plan.md`) adds the demand-shaping actuators V9
never touched: a GPU frequency-envelope cap module, a soft-PL1 overlay under the
TDP backend, a fast boost lane driven by a live mangoapp frame feed, personas
(`battery`/`ac-quiet`/`ac-performance`), and a consent-gated frame limiter. The
framework ships with probe-sized defaults for the GPU cap and soft-PL1 rungs
(every tunable is a config field, not a literal): the 17 W / 60 fps device
probes measured a GPU-cap pacing plateau down to rp0x0.69 (~1350 MHz) and a
soft-PL1 knee at slider-2 W, so the battery G-rungs are -12%/-22%/-30% (the
-45% depth is the verdict-gated `G4CAP` rung, unlocked by a `gpu-cap` BETTER
verdict) and the P1 rung anchors at least 1 W below the user slider
(`soft_pl1_p1_slider_margin_w`) so a PL1-pinned scene cannot clamp it into a
no-op. Remaining constants stay provisional and are tuned via probes P1-P5. All new actuators are reduction-only under user intent and the
installed service default stays `gpu-priority`; `gpu-priority` decisions remain
byte-identical because the telemetry v3 fields are emitted only on the
target-balance path.

A G-rung cap is a *ratio* of `rp0`, applied PER GT from each GT's own `rp0`
(the render GT tops out at 1950 MHz, the media GT at 1200 MHz — they do not
share bounds), so a `-12%` rung trims the render GT to 1716 MHz and the media
GT to 1056 MHz rather than collapsing both to the smaller GT's cap. Telemetry
`gpu_freq_caps` reports the render-GT values in its flat `min_mhz`/`max_mhz`
keys plus a `per_gt` breakdown of the values actually written to each GT.

The framework's device-facing surfaces owned by this slice:

- The `gamescope-mangoapp` drop-in
  (`data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf`)
  sets `MANGOAPP_FRAME_FEED=1` so the patched mangoapp exports the FrameFeed
  contract (`$XDG_RUNTIME_DIR/steamos-intel-handheld/frame-feed.json`, ~2 Hz,
  atomic). The drop-in stays owned by the `10-mangoapp.toml` restore fragment,
  not the main manifest.
- The Decky backend adds `set_persona`/`clear_persona` (validated against the
  supported personas, fail-closed before spawning) and `limiter_status` /
  `set_limiter` / `clear_limiter`. The limiter helper must run in the gamescope
  session bus, so the root Decky backend hops to the session user with
  `runuser -u deck -- env XDG_RUNTIME_DIR=... DBUS_SESSION_BUS_ADDRESS=...`, the
  same shape the on-device scripts and display-workaround service use; the
  daemon itself never calls it. The limiter is device-unverified and reports
  `unsupported`/`unknown` honestly.
- The runtime snapshot gains the additive v3 fields (`persona`, `soft_pl1_w`,
  `gpu_freq_caps`, `boost_active`, `boost_reason`, `trim_rungs_active`,
  `frame_feed_status`, `limiter_state`), gated exactly like the V9 fields: they
  blank out when the snapshot is stale/invalid or the mode is the `gpu-priority`
  default / `off` / `observe`. The frontend renders a persona selector
  (intent-framed labels, no raw knob vocabulary), a consent frame-limit helper,
  a live package-vs-soft-budget row with a boost indicator, and a frame-feed
  status chip, degrading gracefully when the fields are absent.

The V10 probe capture modes (`PROFILE_GAME_POWER_PROBE=pin-baseline` /
`gpu-cap-sweep` / `soft-pl1-sweep`) and candidate policies (`v10-battery`,
`v10-gpu-cap`, `v10-soft-pl1`) reuse the existing `game-power-profile-device`
harness check, which already runs `scripts/profile-game-power-on-device.sh`
(the probe modes are env-selected inputs to that same command), so no new
guarded check is added.

## Boundaries

- Hardware access is isolated in `TdpBackend`.
- D-Bus code is loaded only inside `serve()` so unit tests do not need D-Bus.
- Device install and verification are shell harnesses under `scripts/`.
- Packaging files are drafts until the service layout is validated on more
  devices.

## Known first-device facts

- Device: MSI Claw 8 AI+ A2VM
- Board: MS-1T52
- SteamOS: 3.8.11 generation
- Kernel family: Valve Neptune 6.16
- SteamOS UI TDP range used by this project: 8W to 30W
- RAPL short-term hardware ceiling used by this project: 37W
