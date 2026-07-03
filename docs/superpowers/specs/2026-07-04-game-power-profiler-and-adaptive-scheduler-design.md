# Game Power Profiler And Adaptive Scheduler Design

## Goal

Build the second stage of the game-power work: a repeatable A/B profiling and
adaptive scheduling framework that can compare game-power policies across TDP
levels, optimize for average FPS and low-percentile frame pacing, and keep
SteamOS handheld power behavior reversible and balanced.

The first production governor is already installed default-on with an EPP-only
GPU-priority policy. This design does not replace that baseline. It adds the
measurement, analysis, and controlled experiment layer needed to decide what the
next policy should be.

This second-stage design supersedes the first-stage document's pre-validation
default-off assumption. The current baseline for all comparisons is the
validated default-on `gpu-priority` EPP policy from commit `7d66e09`.

## Current Evidence

The MSI Claw 8 AI+ test device currently runs:

```text
service: steamos-intel-handheld-power-control.service active
mode:    --game-power-mode gpu-priority
TDP:     22W during the Cyberpunk 2077 validation scene
```

The existing EPP-only policy shifted shared-package power in the desired
direction under Cyberpunk 2077:

```text
observe:      core often 8-10W, uncore roughly 5-8W
gpu-priority: core often 6-7W,  uncore roughly 8.5-9.8W
```

That proves the first policy can reduce CPU-side pressure and free iGPU headroom
inside the same SteamOS TDP contract. It does not yet prove that every game,
every TDP, or every frame-time percentile improves. The next stage must measure
that directly.

Device capability checks from the current machine:

```text
sched_ext state: disabled
GameMode: inactive for Cyberpunk 2077
cgroup v2: present
cpu.uclamp.min/max: present in cgroup hierarchy
MangoHud/mangoapp: active
MangoHud CSV summary: available, includes 0.1% Min FPS, 1% Min FPS, Average FPS
gamescope stats.pipe: present as a FIFO under /run/user/1000/gamescope-*/stats.pipe
Steam app identity: available through app-steam-app1091500 cgroup scopes
```

## Research Summary

The next useful control surfaces are:

- MangoHud FPS logging. MangoHud can write CSV logs and summary CSV files with
  low-percentile FPS and frame-time data. That gives us the user-visible metric
  that RAPL alone cannot provide.
- Linux utilization clamping. `uclamp` lets userspace express scheduler
  performance hints and caps through scheduler/cgroup interfaces. The kernel
  documentation explicitly frames it as a mechanism that games can drive from
  perceived FPS feedback.
- Linux PSI. Pressure Stall Information quantifies CPU, memory, and I/O
  contention that causes latency spikes. This is directly relevant to 1% low
  and frame pacing.
- cgroup v2. The Steam app process tree is already represented in the unified
  hierarchy. cgroup v2 gives us resource accounting and reversible per-cgroup
  controls such as `cpu.uclamp.*`, `cpu.weight`, and pressure files.
- sched_ext. BPF schedulers are dynamically loadable and the kernel reverts to
  the default scheduler when errors or stalls are detected. This is attractive
  for a future scheduler experiment, but it is not the right first step because
  the current device has sched_ext disabled and userspace/cgroup controls are
  already available.

Relevant references:

- MangoHud FPS logging: https://github.com/flightlessmango/MangoHud
- Linux utilization clamping: https://docs.kernel.org/scheduler/sched-util-clamp.html
- Linux PSI: https://docs.kernel.org/accounting/psi.html
- Linux cgroup v2: https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux sched_ext: https://docs.kernel.org/scheduler/sched-ext.html
- Linux intel_pstate/EPP: https://docs.kernel.org/admin-guide/pm/intel_pstate.html

## Design Principles

- Treat average FPS as insufficient. Optimize only when average FPS, 1% low, or
  frame-time percentile evidence improves without introducing visible
  regressions.
- Keep SteamOS `TdpLimit` as the sustained package-power contract. Do not raise
  PL1 automatically.
- Keep the current EPP-only governor as the stable baseline. Every new policy is
  an experiment until measured against that baseline.
- Snapshot and restore every mutable control: TDP, CPU EPP, CPU max frequency,
  cgroup/uclamp files, and service overrides.
- Prefer userspace/cgroup controls before sched_ext. sched_ext remains a future
  opt-in experiment with separate packaging and rollback.
- Produce artifacts that can be compared after the run instead of relying on
  live overlay observation.

## Architecture

Add a profiler layer around the existing `game_power.py` governor.

```text
scripts/profile-game-power-on-device.sh
    SSH wrapper for real-device benchmark runs.

src/steamos_intel_handheld/game_power_profile.py
    Local Python module for parsing MangoHud CSV, game-power samples,
    cgroup/PSI samples, and producing JSON summaries.

tests/test_game_power_profile.py
    Hardware-free parser and policy-comparison tests.

harness.toml
    Guarded `game-power-profile-device` check.
```

The profiler must not depend on a specific game. Cyberpunk 2077 AppID `1091500`
is the first validation workload, not a hard-coded policy.

## Profiling Data Model

Each benchmark run produces one directory:

```text
.cache/game-power/profiles/<timestamp>-<appid>-<tdp>w-<policy>/
```

The directory contains:

```text
manifest.json
mangohud.csv
mangohud-summary.csv
game-power.jsonl
cgroup-pressure.jsonl
cpu-policy.before
cpu-policy.after
tdp.before
tdp.after
summary.json
```

`manifest.json` records the test setup:

```json
{
  "appid": "1091500",
  "game": "Cyberpunk 2077",
  "tdp_w": 22,
  "policy": "gpu-priority",
  "duration_s": 60,
  "warmup_s": 10,
  "started_at": "2026-07-04T02:30:00+08:00",
  "device": "MSI Claw 8 AI+ A2VM",
  "notes": "repeatable in-game static scene"
}
```

`game-power.jsonl` uses one line per control-loop sample:

```json
{
  "elapsed_s": 12.0,
  "appid": "1091500",
  "action": "gpu-priority-epp",
  "package_w": 21.9,
  "core_w": 6.9,
  "uncore_w": 8.8,
  "pl1_w": 22,
  "render_busy": 0.82,
  "epp": "balance_power"
}
```

`summary.json` is the comparison payload:

```json
{
  "appid": "1091500",
  "tdp_w": 22,
  "policy": "gpu-priority",
  "avg_fps": 42.3,
  "one_percent_low_fps": 35.8,
  "point_one_percent_low_fps": 30.2,
  "avg_frametime_ms": 23.6,
  "p95_frametime_ms": 31.0,
  "p99_frametime_ms": 37.5,
  "avg_package_w": 21.8,
  "avg_core_w": 7.1,
  "avg_uncore_w": 8.9,
  "avg_core_share": 0.33,
  "avg_uncore_share": 0.41,
  "cpu_pressure_some_avg10_peak": 2.1,
  "restored": true
}
```

## Benchmark Flow

The device profiler runs a matrix of TDP and policy variants:

```text
TDP levels: 12, 15, 17, 20, 22, 25, 30
Policies:   off, gpu-priority, gpu-priority-cpu-cap, uclamp-background
```

The first implementation should default to a small matrix:

```text
TDP levels: 17, 22, 30
Policies:   off, gpu-priority
duration:   60s per run
warmup:     10s per run
```

The profiler performs these steps for each run:

1. Snapshot TDP, CPU policy, and relevant cgroup CPU control files.
2. Set the requested SteamOS TDP through the existing provider.
3. Temporarily force the installed power-control service's own game-power mode
   to `off` so the profiler's standalone policy runner is the only CPU-policy
   writer during A/B runs.
4. Start or reset MangoHud logging for the target window.
5. Run the selected policy through the standalone game-power CLI.
6. Sample RAPL, fdinfo, CPU policy, cgroup pressure, and policy decisions.
7. Stop or collect MangoHud logging.
8. Restore TDP, CPU policy, cgroup control files, and the original service
   game-power configuration.
9. Diff snapshots and fail if restore is not exact.
10. Parse CSV and JSONL artifacts into `summary.json`.

If MangoHud logging cannot be controlled programmatically in the current SteamOS
session, the first implementation may use preexisting MangoHud CSV files as an
import path. The verifier must then mark `capture_mode` as `imported` rather
than `controlled`, so the result is not mistaken for an automated A/B run.

## Policy Candidates

### Baseline: `off`

Disable the game-power governor and record the natural behavior at the selected
TDP. This is the required control sample.

### Current Default: `gpu-priority`

Use the current reversible EPP-only policy. This remains the default installed
behavior unless profiling proves a regression.

### Optional Hard Cap: `gpu-priority-cpu-cap`

Enable the existing CPU max-frequency cap path. Use this only when EPP-only
does not reduce core share enough and low-percentile FPS is still poor.

### cgroup/uClamp Background Control

Experiment with lowering background CPU pressure by applying reversible cgroup
controls outside the foreground Steam app scope:

```text
foreground Steam app: avoid capping initially
background app scopes: lower cpu.uclamp.max or cpu.weight
system services: observe only in first pass
```

This candidate is aimed at 1% low and frame pacing. It should not be enabled
globally until the profiler proves background pressure is responsible for
stutters and restore behavior is exact.

### Foreground uClamp Prime

Use `cpu.uclamp.min` for the Steam app scope only when PSI and MangoHud show
that the game thread is CPU-starved and the workload is not iGPU-bound. This is
the opposite of the GPU-priority case and is needed to avoid hurting CPU-bound
games.

### sched_ext Experiment

Keep sched_ext out of the first implementation. A separate spec can evaluate
`scx_lavd` or a custom partial scheduler if:

- the target kernel exposes sched_ext as enabled and usable,
- packaging can load and unload the scheduler safely,
- the profiler can prove frame-time improvement beyond cgroup/uclamp controls,
- failures revert to the normal scheduler and the service can detect that.

## Adaptive Policy Direction

The first adaptive controller should be conservative and rule-based:

```text
if no foreground Steam app:
    restore
elif package near PL1 and uncore/render busy high and core share high:
    gpu-priority EPP
elif package near PL1 and uncore/render busy high and core share remains high:
    optional CPU cap only in experiment mode
elif CPU PSI high and uncore/render busy low:
    avoid GPU-priority; consider foreground uClamp prime in experiment mode
elif memory or IO PSI spikes:
    do not change CPU policy; record probable stutter attribution
else:
    restore or observe
```

The controller should not auto-select a new permanent per-game profile in the
first implementation. It should emit recommendations from repeated profiler
runs:

```json
{
  "appid": "1091500",
  "tdp_w": 22,
  "recommended_policy": "gpu-priority",
  "confidence": "medium",
  "reason": "1% low improved by 8.4% and avg FPS improved by 3.1% across 3 runs"
}
```

## Comparison Rules

A policy is considered better only if at least one of these is true:

- 1% low FPS improves by at least 5% with average FPS not worse by more than 2%.
- p99 frametime improves by at least 5% with average FPS not worse by more than
  2%.
- average FPS improves by at least 5% with 1% low not worse by more than 2%.
- power efficiency improves by at least 5% at the same FPS band.

A policy is rejected if:

- 1% low worsens by more than 3%.
- p99 frametime worsens by more than 3%.
- CPU policy, cgroup controls, or TDP are not restored exactly.
- service crashes or the foreground game disappears during the run.
- the result depends on imported logs rather than controlled capture and lacks a
  repeatable test window.

## Safety And Restore

The profiler must snapshot and restore:

- SteamOS `TdpLimit`
- RAPL PL1/PL2/Tau through the existing TDP verifier path
- CPU EPP and `scaling_max_freq`
- cgroup `cpu.uclamp.min`, `cpu.uclamp.max`, and `cpu.weight` for touched scopes
- temporary systemd drop-ins, including the profiler drop-in that disables the
  installed service's default-on game-power governor during each A/B run

The profiler must fail closed when any restore diff is non-empty. Active service
policy remains reversible even when the profiler crashes because the existing
governor restores CPU policy in its service shutdown path.

## Validation Plan

Local validation:

- Parser tests for MangoHud CSV and summary CSV.
- Parser tests for game-power JSONL.
- Comparison tests for average FPS, 1% low, 0.1% low, p95, and p99 frame time.
- Snapshot/restore tests for cgroup CPU control files using temporary fake
  cgroup trees.
- CLI tests proving the default matrix is small and guarded.
- Integration asset tests proving `game-power-profile-device` is guarded in
  `harness.toml`.
- Required harness sweep after each code change.

Device validation:

1. Verify current default-on `gpu-priority` service is active.
2. Run a short imported-log parser test against existing MangoHud CSV files.
3. Run a controlled Cyberpunk 2077 A/B at 22W:
   - `off`
   - `gpu-priority`
4. Run the default matrix at 17W, 22W, and 30W after the capture path is stable.
5. Add `gpu-priority-cpu-cap` only if EPP-only fails to improve low-percentile
   frame pacing at a low TDP.
6. Add cgroup/uClamp experiments only after the profiler can attribute stutters
   to CPU pressure or background contention.

## Non-Goals

- Do not auto-raise TDP or PL1.
- Do not enable sched_ext by default.
- Do not ship automatic per-game profile persistence in the first profiler
  implementation.
- Do not tune from a single run.
- Do not claim FPS improvement from RAPL-only evidence.
- Do not treat imported MangoHud logs as equivalent to controlled A/B captures.

## Open Issues

The only unresolved implementation detail is how to programmatically start and
stop MangoHud logging inside the SteamOS gamescope session without requiring
manual key chords. The first implementation should support both:

- controlled capture when a reliable trigger is available,
- imported CSV parsing when controlled capture is not yet reliable.

The summary must mark which capture mode was used.
