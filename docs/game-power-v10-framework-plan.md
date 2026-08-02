# Game Power V10 Framework Implementation Plan

Date: 2026-07-06
Status: approved plan. Designer/acceptor: Fable 5. Implementer: Opus 4.8.
Basis: docs/game-power-v10-direction.md (evidence + philosophy). Read it
first; this plan only specifies the build.

Strategy per the operator: build the complete framework now with
conservative default constants; tune constants on device after rollout.
Every tunable is a config field, never a literal. All V9 machinery
(phase machine, coloring, verdict ledger, telemetry contracts, evidence
gates) stays and is extended, not replaced.

## 0. Framework invariants

- Reduction-only under user intent: no actuator may exceed what the user
  set (TDP slider, QAM limiter, refresh rate). Boost = removing our own
  reductions, never overclocking past user state.
- Snapshot/restore/fail-closed for every new writer, same discipline as
  V9 (readback-verified restore included).
- gpu-priority mode behavior stays byte-identical (replay gate).
- target-balance JSONL/snapshot changes are additive (telemetry v3).
- Personas: `battery`, `ac-quiet`, `ac-performance`. Resolved from the
  existing power-source detection + a Decky/runtime-control override.
  Default mapping: battery→battery, AC→ac-performance (conservative:
  AC behavior today is unchanged until the user opts into quiet).

## 1. Contracts (implement exactly; these are the framework)

### 1.1 FrameFeed contract

File: `$XDG_RUNTIME_DIR/steamos-intel-handheld/frame-feed.json` (daemon
reads /run/user/1000/... path configurable `--frame-feed-file`).
Written atomically by the mangoapp fork at ~2 Hz:

```json
{
  "schema": "steamos-intel-handheld-frame-feed-v1",
  "pid": 12345,
  "appid": "3423533071",
  "updated_monotonic_s": 12345.678,
  "window_s": 2.0,
  "frame_count": 119,
  "avg_fps": 59.6,
  "p95_frame_ms": 18.9,
  "last_frame_ms": 16.7,
  "spike": {"count": 2, "worst_ms": 27.3}
}
```

Daemon-side `FrameFeedReader` (new class in game_power.py, injected
paths): stale if `updated_monotonic_s` older than `frame_feed_stale_s`
(default 5.0) against CLOCK_MONOTONIC; stale/missing/corrupt → feed
absent → V9 behavior (MangoHud CSV when configured, else NO_TARGET
degradation). Feed presence upgrades `FramePerformanceTelemetry`
confidence to high with source `mangoapp-feed`.

### 1.2 GPU actuator contract

New module `src/steamos_intel_handheld/game_power_gpu.py`:

- `discover_gpu_gts(sysfs_root)` → list of GT handles from
  `/sys/class/drm/card*/device/tile*/gt*/freq0/` with rp0/rpe/rpn,
  min/max readable+writable detection.
- `GpuFreqActuator` with `snapshot()`, `apply(min_khz=None, max_khz=None)`
  (clamped to [rpn, rp0]; applies to ALL discovered GTs),
  `restore(snapshot) -> list[str]` readback-verified like the CPU one.
- Feature-detect the SLPC power-profile knob (probe for
  `slpc_power_profile`-like files under gt*/; name resolved at runtime,
  store what was found in telemetry; absent → skip silently).
- Fail-closed latch mirrors the CPU actuator pattern.

### 1.3 Soft-PL1 overlay contract (power_control.py TdpBackend)

- New API on the backend: `set_soft_pl1_w(value_w | None)` where None
  clears the overlay. Effective PL1 written to RAPL =
  `min(user_slider_pl1, soft_pl1)` and never below `soft_pl1_floor_w`
  (default 8). PL2/Tau derivation keeps using the USER slider value
  (bursts stay full). EC mirroring follows effective PL1 through the
  existing guarded path.
- Any user slider write, service stop, restore path, or governor
  deactivation clears the overlay and rewrites the slider value.
- State file/telemetry record both `user_pl1_w` and `soft_pl1_w`.
- The game-power governor is the only caller (in-process; no new IPC).

### 1.4 Demand ladders (replace/augment the V9 CPU ladder)

At/above target, per tick, in this order (one step per qualifying hold,
same hold/backoff/fast-release framework as V9's ladder — reuse it,
generalized to a `TrimLadder` abstraction over "rungs"):

- Battery persona rung sequence:
  G1 GPU max_freq -15% of rp0, G2 -30%, G3 -45%,
  P1 soft-PL1 = ceil(observed package median + 1.5 W), P2 -1 W, P3 -2 W
  (P-rungs re-evaluated each qualifying window; floor 8 W),
  C1 ecore EPP balance_power, C2 + pcore EPP balance_power.
  V9's S3/S4 CPU frequency caps are NOT in the battery sequence
  (evidence: S4 p95 regression; caps stay available as verdict-gated
  rungs only).
- ac-quiet: same sequence, guard band wider (see constants).
- ac-performance: ladder disabled beyond C1/C2 (EPP only) — preserves
  current AC behavior with headroom parked above target.
- Fast release (any target miss / p95 breach): release ALL rungs at
  once (GPU caps lifted, soft-PL1 cleared, EPP restored), record
  backoff on the failed rung, re-climb per hold rules.

### 1.5 Fast boost lane

- Sub-tick loop in the governor: `fast_poll_s` default 0.25 (config;
  runs only in target-balance mode with an active game).
- Triggers (any): frame-feed `spike.worst_ms > spike_boost_ratio *
  target_frame_ms` (default 1.5); feed `last_frame_ms` over the same
  bar; foreground CPU PSI avg10 jump > `psi_boost_delta` (default 15)
  between fast samples.
- Action = boost posture, applied within one fast tick: release all
  rungs, soft-PL1 cleared, GPU min_freq floor to `gpu_boost_floor_ratio
  * rpe` (default 1.0 → rpe, i.e. efficient-frequency floor, NOT rp0),
  pcore EPP performance. Boost holds `boost_hold_s` (default 3.0) past
  the last trigger, then the slow lane resumes ownership.
- Boost is unconditional (no verdict gate) because it only removes our
  own reductions. LOADING phase implies boost posture.
- Implementation: keep single-threaded — the main loop sleeps in
  `fast_poll_s` increments, running the cheap fast-lane check each
  increment and full slow-lane work every `poll_s / fast_poll_s`-th
  increment. Cheap = frame-feed file mtime/read + one PSI read; no
  RAPL/fdinfo/schedstat in the fast path.

### 1.6 Frame limiter integration (framework-level, consent-gated)

- New helper CLI subcommand on the game-power control CLI:
  `limiter status|set <fps>|clear` which talks to gamescope via
  `gamescopectl` (user-session env like the display workaround service;
  document that the daemon itself never calls it — the Decky backend or
  the user helper service does).
- Governor reads limiter state as an FPS-target source (extends the V9
  discovery: runtime convar beats argv `-r`).
- Auto-apply is OFF by default (`limiter_auto=false` config +
  runtime-control field); when the user enables it in Decky and persona
  is battery/ac-quiet and no user limit exists, the helper applies the
  detected target. Restore on session end/mode change.

### 1.7 Telemetry v3 (additive)

target-balance JSONL/snapshot gains: `persona`, `soft_pl1_w`,
`gpu_freq_caps` (per-GT min/max applied), `boost_active`,
`boost_reason`, `trim_rungs_active` (list), `frame_feed_status`
(`live|stale|absent`), `limiter_state`. Contract v3 in the profiler
validates presence for v10 runs; v1/v2 artifacts validate unchanged.
Replay equivalence extends to rung/boost sequences.

## 2. Slices

### Slice A (daemon core — game_power.py, game_power_gpu.py, power_control.py)

1.2 GPU actuator + 1.3 soft-PL1 overlay + 1.4 TrimLadder generalization
+ 1.5 fast boost lane + personas + telemetry v3 emission + FrameFeedReader
(1.1 reader side). Use fake sysfs trees for GT freq; existing tests stay
green; gpu-priority replay remains byte-identical.

### Slice B (frame feed producer — external/MangoHud fork)

mangoapp patch writing the 1.1 JSON atomically at 2 Hz (it already
computes fps/frametime for the overlay; add a small exporter guarded by
env `MANGOAPP_FRAME_FEED=1` set via our existing mangoapp drop-in).
C++ must compile: if a local build is not possible in this environment,
deliver the patch + meson hookup and a compile-check via the repo's
documented MangoHud build path; mark build-unverified honestly in the
report if so.

### Slice C (profiler + limiter helper + probes)

game_power_profile.py + shell wrapper: new candidate policies
`v10-battery` (full battery ladder), `v10-gpu-cap` (G rungs only),
`v10-soft-pl1` (P rungs only) — one-candidate-per-run A/B discipline
unchanged; probe capture modes P1-P5 from the direction doc
(`PROFILE_GAME_POWER_PROBE=gpu-cap-sweep|soft-pl1-sweep|pin-baseline`);
telemetry contract v3; export-verdicts mapping for the new rungs;
limiter helper CLI (1.6) + its tests.

### Slice D (Decky + docs + policy)

Persona selector + limiter consent toggle + live power/demand row in the
Decky panel (additive, degrade gracefully); README + docs/design.md V10
section (honest: framework shipped, constants provisional, tuning
pending device probes).

Slices A then (B, C in parallel — disjoint files) then D.

## 3. Acceptance checklist (Fable 5)

1. Contracts 1.1-1.7 implemented exactly; every tunable a config field.
2. Reduction-only invariant provable in tests (never exceeds user
   slider/limits; boost never sets GPU min above rp0 nor touches PL2).
3. All restore paths readback-verified + fail-closed; LOADING/mode
   change/close release everything including soft-PL1 and GPU caps.
4. gpu-priority byte-identical; V9 target-balance tests still green
   (V9 CPU-cap rungs now verdict-gated on battery — existing tests
   updated deliberately, not accidentally).
5. Local checks: full pytest, ruff, bash -n, and replay equivalence
   (v1+v2+v3).
6. Honest reporting of anything unbuildable locally (mangoapp C++).
