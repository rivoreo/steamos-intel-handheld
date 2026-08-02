# Game Power V9 "Ultimate" Design: Target-Balance + Thread Coloring

Date: 2026-07-05
Status: approved design for implementation. Designer/acceptor: Fable 5.
Implementer: Opus 4.8 slices with focused verification.

## 1. Objective

V9 is the convergence release for the game power governor. It unifies the two
research tracks that V1..V8 built separately:

1. Target-aware power balance (V6 seed): the governor's objective is the
   user's target frame time and pacing, never maximum raw FPS. Once the target
   is sustained, surplus CPU performance is converted into package power
   savings and iGPU headroom instead of unused frames.
2. Thread coloring (V7/V8 research): observed threads and cgroups are
   classified into stable runtime roles ("colors"), each color maps to a
   least-invasive actuator ladder, and hard actuators unlock per-context only
   through the repo's existing evidence gates.

The "ultimate" property is architectural completeness plus self-learning, not
day-one maximal aggression. Every control lane exists in V9; the aggressive
lanes (foreground uclamp floors, background shaping, compact affinity, deeper
frequency steps) activate automatically per (AppID, TDP, FPS-target, topology,
kernel) context once the verdict ledger holds repeated controlled evidence.
This is a deliberate design answer to the V7/V8 device results: all hard
foreground-affinity runs on the MSI Claw 8 AI+ were `inconclusive`, so a
hardcoded "ultimate" pinning policy would ship a measured non-win.

### Design principles (formalized from product intent)

- P1. Loading stutter and low-percentile frames dominate perceived quality.
  The governor must never trade 1% low / p99 for average FPS or watts, and it
  must detect loading phases and release all constraints during them.
- P2. Target FPS is the contract. Heavy scenes may target 30, light scenes
  60/90/120. The target comes from the player (Decky override), gamescope,
  or a confidence-tagged discovery; it is part of every policy decision and
  every cached verdict key.
- P3. At-target means "stop being greedy". Frames above target are wasted
  power and worse thermal stability. Above target the governor trims CPU
  turbo/EPP and lets the shared RAPL budget flow to the iGPU; below target it
  identifies the bound resource (CPU vs GPU) and helps that side.
- P4. Per-core optimality is topology-driven. On Lunar Lake (4 P-cores
  cpu0-3, capacity ~1024, 4.7-4.8 GHz; 4 LP E-cores cpu4-7, capacity 676,
  3.7 GHz; no SMT; per-CPU cpufreq policies), P-core and E-core domains get
  separate EPP and frequency policy. Colors map to topology-derived sets,
  never fixed CPU IDs.
- P5. Every write is snapshotted, restored, and evidenced. V9 adds no
  actuator without the snapshot/restore/telemetry-contract pattern that
  V3..V8 established.

## 2. Device facts feeding this design (verified 2026-07-05)

- MSI Claw 8 AI+ A2VM, kernel `6.16.12-valve24.4-1-neptune-616`.
- Topology: cpu0-1 capacity 1005 max 4700 MHz, cpu2-3 capacity 1024 max
  4800 MHz, cpu4-7 capacity 676 max 3700 MHz; one package, no SMT; one
  cpufreq policy per CPU; `intel_pstate` active mode, per-policy EPP
  writable, `no_turbo=0`, `hwp_dynamic_boost=0`.
- No `/sys/devices/system/cpu/intel_hfi`; no `sched_itmt_enabled` sysctl.
  HFI/ITMT stay out of scope for V9 signal collection.
- uclamp sysctls present; cgroup v2 controllers include `cpuset cpu io
  memory`; foreground game cgroup pattern `app-steam-app<appid>-*.scope`
  confirmed live (AppID 1903340 running during probe).
- sched_ext present, `/sys/kernel/sched_ext/state = disabled`, and SteamOS
  ships `scx-scheds 1.1.1` with `/usr/bin/scx_lavd` installed. A guarded
  sched_ext lane is testable on this exact target today.
- Intel `xe` exposes writable GPU frequency controls per GT:
  `/sys/class/drm/card0/device/tile0/gt{0,1}/freq0/{min_freq,max_freq}`
  with rp0 1950 MHz, rpe 800 MHz, rpn 100 MHz.
- gamescope runs with `-T stats.pipe`; the FIFO is consumed by our packaged
  `mangoapp` fork, so the daemon must not tap the pipe directly.

## 3. Architecture overview

```text
                       +--------------------------------------+
 sensors (existing)    |  GamePowerObserver.sample()          |
 RAPL, fdinfo, PSI,    |  + NEW: foreground schedstat deltas  |
 cgroups, frame feed --+  + NEW: phase classification         |
                       +-------------------+------------------+
                                           |
                    +----------------------v---------------------+
                    | Phase machine (NEW)                        |
                    | loading | below-cpu | below-gpu |          |
                    | at-target | above-target | no-target |     |
                    | unknown                                    |
                    +----------------------+---------------------+
                                           |
        +----------------------------------v-----------------------------------+
        | Actuation planner                                                    |
        |  - per-class EPP (P vs E)            (NEW, always available)         |
        |  - convergence ladder at/above target (NEW, always available)        |
        |  - loading release/boost              (NEW, always available)        |
        |  - background cgroup shaping          (verdict-ledger gated)         |
        |  - foreground cgroup uclamp.min       (verdict-ledger gated)         |
        |  - compact foreground affinity        (profiler-only, V8 machinery)  |
        |  - GPU min-freq floor                 (profiler-only lane)           |
        |  - scx_lavd                           (profiler-only lane)           |
        +----------------------------------+-----------------------------------+
                                           |
                       +-------------------v------------------+
                       | CpuPolicyActuator (extended) +       |
                       | cgroup writers + snapshot/restore    |
                       +--------------------------------------+

  Color ledger (NEW): colors threads/cgroups A-E each colorize interval,
  feeds the planner's role targeting and the runtime snapshot/Decky UI.

  Verdict ledger (NEW): read-only-for-daemon JSON produced from profiler
  aggregates; unlocks gated lanes per context key.
```

Integration points (from the current tree):

- State machine: extend `GamePowerController.evaluate` and
  `classify_game_power_sample` (`game_power.py:1407`, `:1187`).
- Per-class EPP: extend `CpuPolicyActuator` (`game_power.py:497`); the
  per-policy loop and PCORE/ECORE classification (`:410`) already exist.
- Target satisfaction: reuse `_sample_fps_target_satisfied`
  (`game_power.py:1287`) and its config ratios.
- Context cache template: `GamePowerHintStore` (`game_power.py:838`).
- Guarded writers template: `apply_background_shaping_writes`
  (`game_power_profile.py:3852`) and `apply_foreground_affinity_writes`
  (`:3481`).
- Color source signals: profiler role pipeline `_ranked_affinity_thread`
  (`game_power_profile.py:4785`) and `_classify_background_cgroup` (`:4998`).

## 4. Phase state machine

New enum `GamePowerPhase`: `NO_GAME`, `LOADING`, `BELOW_TARGET_CPU_BOUND`,
`BELOW_TARGET_GPU_BOUND`, `AT_TARGET`, `ABOVE_TARGET`, `NO_TARGET`,
`UNKNOWN`. The phase is computed every tick inside classification, recorded
in the decision JSONL and runtime snapshot (additive fields; the telemetry
contract gains `phase` plus `phase_reason_codes`).

Inputs per tick (all existing except the schedstat aggregate):

- `avg_fps`, `p95_frame_ms`, sample counts, confidence (frame feed),
- `fps_target`, `target_frame_ms`, source, confidence,
- `package_w`, `pl1_w`, `core_share`, `uncore_share`, render fdinfo busy,
- foreground cgroup CPU PSI,
- NEW `foreground_runqueue_wait_ms_per_s`: sum of schedstat runqueue-wait
  deltas across the foreground app's top-N threads (N=16 by CPU-time delta),
  sampled at colorize cadence (see section 6) and carried forward between
  colorize ticks.

Classification rules (config-tunable constants; defaults below):

- `NO_GAME`: no foreground AppID -> full restore (existing behavior).
- `NO_TARGET`: game present, target unknown/unlimited -> fall back to the
  V7 `gpu-priority` predicate (`_sample_supports_gpu_priority`). V9 never
  degrades below shipped V7 behavior.
- `LOADING` (highest priority when a target exists): any of
  - foreground process age < 30 s (launch grace),
  - frame feed present but stalled (no new rows for >= 2 s) while foreground
    CPU PSI avg10 > 40 or `core_share > 0.50`,
  - `avg_fps < 0.5 * fps_target` while render busy < 0.30 and
    `core_share > 0.50` (asset/shader burst signature).
  Exit when a stable frame cadence at >= 0.7 * target holds for
  `loading_exit_samples` (default 5) consecutive samples. Per-episode boost
  budget `loading_boost_max_s` (default 180 s); after budget expiry the
  phase may remain LOADING but actuation returns to neutral.
- `AT_TARGET`: `_sample_fps_target_satisfied` true (avg >= 1.05x target,
  p95 <= 1.15x target frame ms, >= 12 high-confidence samples).
- `ABOVE_TARGET`: satisfied and `avg_fps >= 1.25 * fps_target`.
- `BELOW_TARGET_GPU_BOUND`: not satisfied and (render busy >= 0.70 or
  (`uncore_share >= 0.20` and `package_w >= 0.94 * pl1_w`)).
- `BELOW_TARGET_CPU_BOUND`: not satisfied and (`core_share >= 0.35` and
  `foreground_runqueue_wait_ms_per_s >= 50`) or (render busy < 0.60 and
  `core_share >= 0.45`).
- `UNKNOWN`: everything else -> observe, no new writes, existing writes
  keep their sustain rules.

Phase transitions must use the existing hysteresis idiom: a phase change is
committed only after `phase_stable_samples` (default 3) consecutive ticks
classify the same new phase, except entry to `LOADING` and exit from
`AT_TARGET`/`ABOVE_TARGET` on a target miss, which commit after 1 tick
(pacing protection is asymmetric by design: fast to give power back, slow to
take it away).

## 5. Actuation per phase

Action vocabulary grows (additive) with `TARGET_BALANCE_TRIM`,
`TARGET_BALANCE_RELEASE`, `LOADING_BOOST`. The existing actions and the V7
EPP/CPU-cap path remain untouched for the `gpu-priority` mode.

New public mode value stays `automatic`; internally the daemon gains mode
`TARGET_BALANCE` (CLI `--game-power-mode target-balance`). Rollout: the
installed service default remains `gpu-priority` until V9's controlled
device evidence passes; the profiler exercises `target-balance` as a
candidate policy. The design intends `target-balance` to become the new
default after acceptance.

Per phase:

- `NO_GAME`: restore everything (unchanged).
- `NO_TARGET`: V7 behavior (EPP gpu-priority when package-pressured).
- `LOADING`: release all V9 constraints (caps, trims, shaping) and apply
  `LOADING_BOOST`: P-core EPP `performance`, E-core EPP
  `balance_performance`, no frequency caps. Bounded by the episode budget;
  restore to neutral on exit. Rationale: loading is transient, bounded
  boost is cheap, and P1 makes loading stutter a first-class enemy.
- `BELOW_TARGET_CPU_BOUND`: P-core EPP `performance`, E-core EPP
  `balance_power`, no CPU caps, GPU-priority EPP logic suspended.
  Verdict-gated extras: foreground cgroup `cpu.uclamp.min` floor (default
  probe value 25), background shaping (weight-80 / uclamp-max-85 on the V8
  allowlist).
- `BELOW_TARGET_GPU_BOUND`: existing GPU-priority EPP treatment (both
  classes `balance_power`), plus the existing explicit CPU-cap lane when
  enabled. Verdict-gated extras: background shaping; deeper P-core cap
  steps. (GPU min-freq floor exists only as a profiler lane in V9.)
- `AT_TARGET` / `ABOVE_TARGET`: run the convergence ladder (section 7).
- `UNKNOWN`: hold current step, no new writes.

All CPU writes flow through the extended `CpuPolicyActuator` so one snapshot
and one restore path covers V7 and V9. Cgroup writes reuse the V8 guarded
writer functions relocated into a shared module so daemon and profiler use
literally the same apply/restore code.

## 6. Thread color ledger

New module `src/steamos_intel_handheld/game_power_coloring.py`, shared by
daemon (cheap runtime cadence) and profiler (full-artifact cadence).

Colors follow `docs/game-power-thread-coloring-research.md`:

- A `foreground-latency-hot`: foreground cgroup, stable role signature,
  runqueue-wait delta >= 25 ms per colorize window or wait-per-slice above
  threshold, high CPU-time delta.
- B `foreground-throughput-wide`: foreground, many sibling threads with the
  same normalized comm, high CPU time, low runqueue wait, CPUs seen across
  the package.
- C `compositor-overlay-sensitive`: gamescope, mangoapp, pipewire,
  wireplumber, inputplumber, Decky overlay cgroups. Never shaped in V9.
- D `background-helper-shapable`: the V8 background allowlist classes.
- E `unknown-unstable`: everything else, single-appearance roles, roles
  whose cgroup lacks restore-snapshot coverage.

Runtime cadence: colorize every `colorize_interval_s` (default 10 s, i.e.
every 5th governor tick at the 2 s poll), reading `/proc/<pid>/task/<tid>/
schedstat`, `comm`, and `cgroup` for the foreground app plus the allowlist
cgroups. Budget cap: at most 128 TIDs sampled; beyond that, keep the top
128 by previous CPU-time delta and mark the ledger `truncated=true`.

Ledger entry contract (runtime snapshot, decision JSONL, and profiler
artifacts share it):

```json
{
  "role_key": "foreground-game:worker-thread",
  "color": "A",
  "tid_count": 2,
  "cpu_time_ms_per_s": 640.0,
  "runqueue_wait_ms_per_s": 31.5,
  "cpus_seen": [0, 1, 2, 3],
  "actuator": "observe-only|uclamp-min|bg-weight|bg-uclamp|compact-affinity",
  "actuator_state": "active|advisory|blocked",
  "blocking_reason_codes": ["no-verdict-for-context"]
}
```

The daemon never acts on a color without a verdict-ledger entry; colors A/B
map to topology sets (`latency_compact_set` = highest-capacity P-cores,
`efficiency_set` = E-cores) resolved at startup from `cpu_capacity`, never
hardcoded CPU IDs.

## 7. Convergence ladder (at/above target)

Ordered trim steps, each a strict superset of the previous:

- S0 neutral: no writes.
- S1: E-core EPP `balance_power`.
- S2: + P-core EPP `balance_power`.
- S3: + P-core `scaling_max_freq` 4000 MHz (soft turbo trim; E-core uncapped).
- S4: + P-core 3000 MHz, E-core 2400 MHz (the V6-validated balanced caps).
- S5+: deeper steps only when the verdict ledger holds a BETTER verdict for
  this context (created from profiler runs, e.g. P-core 2600/E-core 2000).

Control rules:

- Step up (more trim) only after `ladder_hold_samples` (default 15 ticks =
  30 s at 2 s poll) of continuous `AT_TARGET`/`ABOVE_TARGET` with p95 frame
  time <= 1.10x target frame ms. `ABOVE_TARGET` halves the hold requirement.
- Step down on the first tick that misses the target or breaches the p95
  guard: drop two steps (fast release), then re-enter normal control.
- After stepping down from step k, do not re-enter k for
  `ladder_backoff_s` (default 300 s) in this session (anti-oscillation,
  same philosophy as the V6 cap sustain hysteresis).
- Ladder state, current step, hold/backoff counters, and per-step reason
  codes are in the runtime snapshot and JSONL (additive telemetry).
- Any write failure -> full restore + `_write_failed` latch (existing
  fail-closed behavior).

Package power freed by trimming is not "assigned" to the GPU by writes: the
RAPL budget is shared, so lowering CPU demand is the assignment mechanism.
This is P3 implemented honestly on this hardware.

## 8. Verdict ledger

File: `/var/lib/steamos-intel-handheld/game-power-verdicts.json` (packaged
path; `/run` fallback accepted at runtime), written by operators/CI from
profiler aggregates via a new CLI:

```bash
steamos-intel-handheld-game-power-profile export-verdicts \
  --root .cache/game-power/profiles --out game-power-verdicts.json
```

Only aggregates whose verdict is BETTER (which already implies controlled
capture, >= 3 pairs, exact restore, pairwise gate) are exported. Entry key:

```json
{
  "appid": 1903340,
  "tdp_w": 17,
  "fps_target": 30,
  "topology_fingerprint": "lnl-4p4e-nosmt-<hash>",
  "kernel": "6.16.12-valve24.4",
  "policy_version": "game-power-target-balance-v9",
  "actuator": "bg-weight|bg-uclamp|uclamp-min|ladder-step-5|compact-affinity",
  "verdict": "BETTER",
  "claim_scope": { "...": "copied from aggregate" }
}
```

Daemon behavior: `GamePowerVerdictLedger` loads the file read-only at start
and on mtime change; lookup requires exact match on appid, fps_target,
topology fingerprint, policy version, and TDP bucket (nearest of 12/17/22/
30 W within +-2 W). A missing or corrupt ledger disables all gated lanes
(fail-closed) and reports `verdict_ledger_health` in the snapshot. The
existing `GamePowerHintStore` remains what it is (activation warmup only);
the verdict ledger is the authoritative unlock for write lanes.

## 9. Profiler additions

New candidate policies (shell + python, one candidate per controlled run,
existing off/candidate/off pairing):

1. `target-balance`: the full V9 mode. Primary acceptance vehicle.
2. `target-balance-gpufloor`: target-balance plus a GPU min-freq floor
   (`min_freq` on gt0+gt1) applied only while the run's phase is
   `BELOW_TARGET_GPU_BOUND`; floor value from
   `PROFILE_GAME_POWER_GPU_FLOOR_MHZ` (first probe: 1600). Snapshot both
   GTs' min/max before the run into `gpu-freq-restore.json`, restore and
   verify after; mismatch invalidates the run.
3. `scx-lavd`: guarded sched_ext lane. Pre-check `/sys/kernel/sched_ext/
   state == disabled`; start `/usr/bin/scx_lavd` for the run window; verify
   state `enabled` and record `root_ops`/stats into `sched-ext-state.json`
   (before/during/after); stop and verify `disabled`. Any mismatch or
   scx_lavd crash -> invalid run. No daemon integration in V9.

Summarize/aggregate additions:

- `color-ledger.json` per run (from existing thread-schedstat/affinity/
  process-cgroups artifacts, via the shared coloring module) and a
  `color_ledger` section in aggregates: per-color role stability, median
  wait/cpu-time, actuator recommendation, blocking reason codes.
- Per-phase metrics in `summary.json`: seconds per phase, loading episode
  count/total duration, per-phase p99 frame time, ladder step histogram.
- `export-verdicts` subcommand (section 8).
- Telemetry contract v2 (additive): `phase`, `ladder_step`, `color_ledger`
  presence, verdict-ledger health; `replay-action-equivalence` extended to
  replay phase and ladder decisions with zero-delta requirement.

## 10. Decky surface (additive)

- Show phase, target source/value, ladder step, and a compact color ledger
  (color, role, actuator state, why-not reason).
- No new public modes; `automatic` now means "best validated mode"
  (gpu-priority until acceptance, then target-balance).
- Manual FPS target keeps the existing 30-120 step-5 contract.

## 11. Non-goals and rejected alternatives

- No hard per-thread pinning by default (V8 evidence: inconclusive at 12/17/
  30 W). Compact affinity stays a profiler lane unlocked per-context only.
- No PL1/TDP raising; SteamOS Manager owns the package contract.
- No per-game hardcoded policy tables; AppID is a cache key, not a rule.
- No daemon tap of gamescope stats.pipe (single-reader FIFO owned by
  mangoapp). Frame feed tiers: (1) MangoHud CSV when a logging session is
  active (exists), (2) future mangoapp fork status export (stretch, out of
  V9 acceptance), (3) none -> `NO_TARGET`/V7 degradation.
- No cpuset partitioning, no HFI/ITMT dependence, no sched_ext daemon
  integration in V9.

## 12. Implementation slices (Opus 4.8)

Every slice uses focused pytest while iterating. Run
`PYTHON=.venv/bin/python scripts/check-local.sh` for integration closure.

- S1 Phase machine + per-class EPP + `target-balance` mode + `LOADING`
  handling + telemetry additions. Acceptance: all existing tests green;
  new tests cover every phase transition, asymmetric hysteresis, loading
  budget, per-class EPP writes and restore, no behavior change for
  `gpu-priority` mode.
- S2 Convergence ladder. Acceptance: tests for hold/step/backoff, fast
  release on target miss, write-failure fail-closed, snapshot/restore
  equivalence, JSONL replay equivalence.
- S3 Coloring module + runtime ledger + budget caps; shared with profiler
  summarize (emit `color-ledger.json`). Acceptance: deterministic coloring
  from fixture schedstat/cgroup trees; truncation marking; C-colored roles
  never receive actuators.
- S4 Verdict ledger + gated lanes (bg shaping + uclamp.min via shared
  guarded writers). Acceptance: fail-closed on missing/corrupt ledger;
  exact-match keying; gated writes only in allowed phases; restore parity.
- S5 Profiler: `target-balance`, `target-balance-gpufloor`, `scx-lavd`
  policies, per-phase metrics, aggregate color ledger, `export-verdicts`,
  telemetry contract v2. Acceptance: parser/aggregate fixtures, gate tests,
  contract replay tests.
- S6 Decky additive fields + README/design doc updates + engineering-policy
  compliance (guarded checks for new device lanes, `safe_for_agents=false`).
- S7 Device evidence (guarded, root@10.100.0.19, game must be running):
  `scripts/verify-game-power-on-device.sh --allow-device`, then controlled A/B
  `off target-balance off`, 3 repeats, manual FPS target 30, at 17 W
  (primary) and 12 W (secondary), aggregate + verdicts. Success criteria:
  restore exact in every run; telemetry contract v2 valid; ladder engages
  at-target without pacing regression (p99/1% low within thresholds);
  verdict BETTER on target-sustained power saving, or an honest
  inconclusive with reason codes (design accepts either; a fabricated win
  is a failure).

## 13. Final acceptance checklist (Fable 5)

1. Design conformance: phase machine, ladder, coloring, verdict gating
   match sections 4-8; no actuator bypasses snapshot/restore.
2. Safety: fail-closed paths (control file, ledger, write failure) tested;
   `gpu-priority` default behavior byte-identical in decision replay.
3. Evidence: required sweep green; device evidence artifacts present and
   internally consistent; no claim beyond `claim_scope`.
4. Honesty: verdicts reported as measured, including inconclusive.
