# Game Power Governor Design

## Goal

Build an upstreamable game-power governor for Intel SteamOS handhelds that
keeps integrated GPU performance from being starved by CPU boost behavior under
a shared package TDP limit. The governor must be observable, reversible, safe to
ship disabled by default, and validated on a real Cyberpunk 2077 workload before
becoming an installed default.

## Problem

The current project provides a SteamOS Manager TDP provider and maps the SteamOS
slider to Intel RAPL package PL1/PL2/Tau. That controls total package power, but
it does not arbitrate how the package budget is split between CPU cores and the
integrated GPU/uncore during a foreground game.

On the MSI Claw 8 AI+ test machine, Cyberpunk 2077 with FSR frame generation
enabled showed that frame generation itself works. The remaining bottleneck is
shared-power allocation. A read-only sample taken with the game still running at
22W PL1 showed:

```text
package: 21.91 W
core:     8.56 W
uncore:   7.45 W
dram:     0.45 W
psys:    31.05 W
EPP:      balance_performance on all CPU policies
CPU freq: roughly 2.7 GHz to 3.3 GHz during the sample
```

The package is already pinned near the long-term RAPL contract. The existing
service can maintain the total power envelope, but it has no game-aware control
loop to reduce CPU aggressiveness when the GPU side is the frame-rate limiter.

## Research Summary

Linux `intel_pstate` in active HWP mode lets hardware select CPU P-states while
the OS provides hints such as Energy Performance Preference. This is useful for
changing CPU boost behavior without taking over frequency selection completely.

Linux powercap/RAPL exposes package, core, uncore, DRAM, and platform energy
counters where the platform supports them. Package constraints apply to the
whole CPU package, so package PL1 alone cannot express "reserve more power for
iGPU." The governor therefore needs to observe subdomain power and adjust CPU
policy knobs when a game workload is GPU-limited.

GameMode already recognizes game-scoped optimizations such as CPU governor
changes, iGPU power-balance checks, niceness, scheduler policy, and core
pinning. That is a strong precedent for game-scoped host policy, but on the
current SteamOS test state the daemon is installed and active while Cyberpunk is
not in an active GameMode session.

Integrated CPU-GPU research consistently treats shared thermal and power budgets
as a coupled scheduling problem. Good policy is not "maximum CPU boost" or
"minimum CPU power" globally. It is a workload-sensitive allocation problem:
preserve enough CPU performance for the game thread and frame preparation while
preventing unnecessary CPU boost and background CPU activity from shrinking the
GPU's available headroom.

Relevant references:

- Linux `intel_pstate`: https://docs.kernel.org/admin-guide/pm/intel_pstate.html
- Linux powercap/RAPL: https://docs.kernel.org/power/powercap/powercap.html
- GameMode: https://github.com/FeralInteractive/gamemode
- sched_ext: https://docs.kernel.org/scheduler/sched-ext.html
- Integrated CPU-GPU thermal/power management: https://arxiv.org/abs/1808.09651
- CPU-GPU co-scheduling and power capping: https://arxiv.org/abs/2405.03831

## Design Principles

- Treat SteamOS `TdpLimit` as the sustained total-package power contract.
- Do not silently raise PL1 or override the SteamOS slider.
- Prefer reversible CPU policy hints before hard CPU pinning or kernel scheduler
  replacement.
- Keep all runtime writes scoped to foreground-game activation and restore the
  previous state on exit, loss of focus, process disappearance, service stop, or
  error.
- Make policy decisions from measured evidence, not from fixed assumptions that
  every game is GPU-bound.
- Keep the implementation split into observation, decision, and actuation so it
  can later move toward GameMode, sched_ext, SteamOS Manager, or an upstream
  SteamOS service boundary.

## Architecture

Add a new Python module:

```text
src/steamos_intel_handheld/game_power.py
```

The existing `power_control.py` remains responsible for SteamOS Manager D-Bus,
RAPL PL1/PL2/Tau, MSI EC mirroring, and MangoHud sensor preparation. It will
only wire the new governor into the existing service lifecycle when explicitly
enabled through CLI flags.

The governor has three layers.

### Observer

The observer produces a `GamePowerSample` from read-only sources:

- foreground Steam app identity, initially through gamescope stats when
  available and process/cgroup fallback for `app-steam-app<appid>` scopes
- RAPL energy deltas for `package-0`, `core`, `uncore`, `dram`, and `psys`
- current RAPL PL1/PL2 limits and the active SteamOS TDP value
- CPU policy state from `/sys/devices/system/cpu/cpufreq/policy*`
- DRM fdinfo engine busy deltas for the foreground game process
- optional FPS/focus data from gamescope stats when available

Observation failures are non-fatal in `observe` mode. Missing optional sensors
are recorded as unavailable so the decision layer can avoid acting on weak
evidence.

### Decision Engine

The decision engine consumes recent samples and returns one of:

```text
idle
observe-only
gpu-priority-epp
gpu-priority-cpu-cap
restore
```

The first production policy is a conservative GPU-priority controller:

- activate only when a foreground Steam game is detected for at least two
  consecutive samples
- require package power to be close to PL1 before attempting to reduce CPU
  aggressiveness
- require GPU/uncore activity or GPU fdinfo busy to be significant
- avoid CPU caps when CPU load is low enough that EPP alone should be sufficient
- restore immediately when the game disappears, focus is lost, package pressure
  falls, or samples become too stale

Initial thresholds:

```text
sample interval:                 2.0 s
package pressure threshold:       package W >= 0.94 * PL1 W
core share threshold:             core W >= 0.30 * package W
uncore activity threshold:        uncore W >= 0.20 * package W
render busy threshold:            render engine busy >= 0.70 when fdinfo exists
activation hysteresis:            2 positive samples
restore hysteresis:               3 negative or invalid samples
default EPP target:               balance_power
default CPU cap P-core policies:  3200000 kHz
default CPU cap E-core policies:  2800000 kHz
```

These defaults are intentionally starting points. The validation plan must tune
them on the Cyberpunk scene and reject any setting that improves average FPS
while hurting frame pacing or responsiveness.

### Actuator

The actuator applies and restores CPU policy through sysfs:

- snapshot per-policy `energy_performance_preference`
- snapshot per-policy `scaling_max_freq`
- write EPP target for each policy when supported
- optionally write a capped `scaling_max_freq` per policy class
- restore the exact snapshot when leaving active control

Policy-class detection uses `cpu_capacity` when available. The highest capacity
cluster is treated as P-core class, lower-capacity clusters as E-core class. If
capacity is unavailable, the actuator falls back to one all-policy cap and logs
that the topology was unknown.

Actuation is idempotent. Repeated decisions do not rewrite sysfs when the target
state is already applied. Any write failure triggers immediate restore of
previously written policies and downgrades the governor to observe-only for that
activation.

## CLI And Service Integration

Add optional CLI arguments to `steamos-intel-handheld-power-control`:

```text
--game-power-mode off|observe|gpu-priority
--game-power-poll-s 2.0
--game-power-epp balance_power
--game-power-pcore-max-mhz 3200
--game-power-ecore-max-mhz 2800
--game-power-cpu-cap on|off
--game-power-target-appid APPID
```

Installed service default:

```text
--game-power-mode off
```

Development and validation can enable:

```text
--game-power-mode observe
--game-power-mode gpu-priority
```

The default installed service must not change CPU behavior until real-device
validation proves the policy is stable enough for this target. A later package
revision may enable `observe` by default for telemetry-quality logging without
runtime writes.

## Upstream Path

This design keeps policy and mechanism separate so that validated behavior can
be proposed upstream in stages:

1. Publish the measurement model and Cyberpunk/SteamOS evidence as a standalone
   Intel handheld power-governor report.
2. Propose the observation and reversible EPP policy as a GameMode backend or
   SteamOS handheld profile hook.
3. Evaluate `scx_lavd` and sched_ext as an optional CPU scheduler layer for
   frame-pacing and latency, not as the first shared-power fix.
4. If multiple games and handhelds validate the policy, propose a SteamOS
   profile-level "iGPU priority under shared TDP" control that keeps TDP slider
   semantics intact while applying CPU hints around foreground games.

The first repo implementation should be clean enough to be read as a reference
implementation: documented sysfs writes, clear restore behavior, and tests that
prove no writes happen in `off` or `observe` mode.

## Validation Plan

Local validation:

- unit tests for RAPL sample math
- unit tests for CPU topology classification
- unit tests for EPP/frequency snapshot and restore
- unit tests for decision hysteresis
- CLI tests for argument parsing and default-off behavior
- integration asset tests proving the installed systemd unit stays off by
  default
- required harness sweep after code changes

Device validation:

1. Confirm baseline state: current TDP, EPP, CPU max frequencies, RAPL PL1/PL2,
   foreground app identity, and sensor availability.
2. Capture `observe` mode in the same Cyberpunk scene and verify no CPU policy
   files change.
3. Capture `gpu-priority` EPP-only in the same scene:
   - average FPS
   - frame pacing when available
   - package/core/uncore/psys power
   - CPU frequency distribution
   - GPU render/compute busy
   - GPU frequency when available through MangoHud or sysfs
4. Capture `gpu-priority` with CPU cap enabled only if EPP-only does not shift
   enough power or clocks toward the GPU.
5. Restore the machine to its original CPU policy and verify all policy files
   match the pre-test snapshot.

Success criteria for Cyberpunk validation:

- no crashes or service failures
- restore is exact after every test
- package remains inside the SteamOS TDP contract
- GPU-side power or frequency increases when CPU pressure is reduced
- FPS or frame pacing improves in a repeatable scene, or the policy is left
  disabled with the evidence recorded

## Non-Goals

- Do not replace SteamOS Manager's TDP model.
- Do not raise the user's selected TDP automatically.
- Do not enable sched_ext by default.
- Do not park or pin game threads in the first implementation.
- Do not infer game performance from average FPS alone.
- Do not claim upstream readiness until at least one real game and one synthetic
  control workload have been measured.

## Risks

- Some games are CPU-bound. Reducing CPU aggressiveness could hurt frame pacing.
  Hysteresis and sensor checks are required before active control.
- Some sensors may be missing or noisy. The decision engine must degrade to
  observe-only when evidence is incomplete.
- CPU max frequency caps can be too aggressive. Frequency capping must be
  optional and separately validated from EPP control.
- `scx_lavd` may improve latency but does not directly reserve iGPU package
  power. It should be evaluated as a second-stage scheduler experiment.

