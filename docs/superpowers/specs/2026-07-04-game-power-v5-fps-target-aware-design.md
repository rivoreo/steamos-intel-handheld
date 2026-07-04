# Game Power V5 FPS-Target-Aware Governor Design

## Status

Proposed implementation plan. Requires Plan Review before code changes.

## Goal

Make the Game Power governor avoid unnecessary GPU-priority EPP writes when a
foreground game is already meeting the configured FPS target with stable frame
pacing, while preserving the V4 EPP-only GPU-priority behavior for low-TDP or
below-target scenes.

The immediate proof target is the current Cyberpunk 2077 foreground scene
(`SteamAppId=1091500`) at a manual `40 FPS` target:

- 12W: keep applying EPP-only GPU priority because the scene is below target.
- 22W: stop applying EPP-only GPU priority when live frame telemetry proves the
  scene is above target with stable pacing.

## Evidence

Latest controlled profile artifacts:

- 12W:
  - `.cache/game-power/profiles/20260704T212056-app1091500-12w-off-baseline-before-r1`
  - `.cache/game-power/profiles/20260704T212258-app1091500-12w-gpu-priority-candidate-r1`
  - `.cache/game-power/profiles/20260704T212500-app1091500-12w-off-baseline-after-r1`
- 22W:
  - `.cache/game-power/profiles/20260704T212712-app1091500-22w-off-baseline-before-r1`
  - `.cache/game-power/profiles/20260704T212915-app1091500-22w-gpu-priority-candidate-r1`
  - `.cache/game-power/profiles/20260704T213116-app1091500-22w-off-baseline-after-r1`

Observed policy deltas:

| TDP | Candidate | Avg FPS | 1% low | 0.1% low | Package W | Interpretation |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 12W | EPP-only | +3.03% | +3.35% | +14.72% | unchanged | EPP helps under constrained power |
| 22W | EPP-only | +0.62% | +1.35% | -1.52% | unchanged | near-neutral while already above 40 FPS |

Runtime limitation found during code inspection:

- `FrameTargetTelemetry` currently records only the target (`fps_target`,
  `target_frame_ms`) in runtime evidence.
- `GamePowerSample` does not carry current FPS or frame pacing.
- `GamePowerController._sample_supports_gpu_priority()` only considers AppID,
  RAPL package pressure, and GPU activity.

Therefore V5 must not infer "target met" from the target alone. It needs an
optional live frame-performance telemetry path, and it must retain V4 behavior
when that path is unavailable.

MangoHud controlled capture writes live rows with `fps` and `frametime`
approximately every 100ms. On the latest artifacts, using a 20-row window:

| TDP | Condition | Matching windows |
| --- | --- | ---: |
| 12W | avg FPS >= 42 and p95 frametime <= 28.75ms | 0 / 582 |
| 22W | avg FPS >= 42 and p95 frametime <= 28.75ms | 583 / 583 |

That condition cleanly distinguishes "below-target, keep helping GPU" from
"above-target, stop spending extra CPU policy pressure" for this profile.

## Design

### Telemetry Model

Add a `FramePerformanceTelemetry` dataclass to `game_power.py`:

- `avg_fps: float | None`
- `p95_frame_ms: float | None`
- `sample_count: int`
- `window_s: float | None`
- `source: str | None`
- `confidence: str | None`

Add `frame_performance: FramePerformanceTelemetry | None` to
`GamePowerSample`.

### Live MangoHud Reader

Add a small MangoHud CSV reader used by the standalone profiler/CLI path:

- CLI argument: `--frame-performance-csv PATH`
- CLI argument: `--frame-performance-window-samples`, default `20`
- CLI argument: `--frame-performance-min-samples`, default `12`

The reader parses the current CSV file, keeps the last N valid `fps` and
`frametime` rows, and returns high-confidence telemetry only when it has at
least the configured minimum samples. It reads bounded data using a deque rather
than loading an unbounded long capture into memory.

The long-running packaged daemon will not get this argument by default. Without
live frame telemetry, V5 is intentionally behavior-compatible with V4.

### Decision Gate

Add target-satisfied detection to the controller before normal GPU-priority
activation:

- require `sample.frame_target.fps_target > 0`
- require `sample.frame_performance.confidence == "high"`
- require `sample.frame_performance.sample_count >= min_samples`
- require `avg_fps >= fps_target * 1.05`
- require `p95_frame_ms <= target_frame_ms * 1.15`

If all are true:

- classification primary becomes `fps-target-satisfied`
- evidence records `avg_fps`, `p95_frame_ms`, `fps_target_ratio`,
  `p95_frame_time_ratio`, and `frame_performance_sample_count`
- `_sample_supports_gpu_priority()` returns `False`
- if the controller is already active, existing restore hysteresis performs a
  clean restore after consecutive satisfied samples
- if the controller is inactive, it stays observe-only and performs no write

If telemetry is missing, stale, malformed, or below threshold, the existing V4
positive/negative logic is unchanged.

### Profiler Integration

In controlled MangoHud profile mode:

1. Start MangoHud logging.
2. Create the `mangohud.start` marker after forcing any previous
   `log_session` off and before enabling the new session, so delayed files from
   the previous run cannot satisfy the current run.
3. Wait for a non-summary CSV newer than `mangohud.start` and require at least
   `--frame-performance-min-samples` valid `fps` / `frametime` rows before
   passing it to the governor.
4. If no fresh CSV reaches the minimum valid-row count before timeout, omit the
   frame-performance argument and write a fallback marker into the run directory.
   This is a V4-compatible fallback and must be visible in the artifacts.
5. Pass that live CSV path to `steamos-intel-handheld-game-power` via
   `--frame-performance-csv`.
6. Keep the existing post-run MangoHud summary and runtime contract validation.

This gives the profiler a real target-aware runtime path without requiring the
packaged service to guess a global MangoHud file location.

The profile script must avoid stale-file false positives:

- ignore files named like `*_summary.csv` or `mangohud-summary.csv`
- require mtime newer than `mangohud.start`
- require the selected path to keep existing at governor launch
- record the selected path or fallback reason in `manifest.json` or an adjacent
  run artifact

### Runtime Contract

The runtime telemetry contract must distinguish "V5 active" from "V4 fallback":

- when frame-performance telemetry is expected, JSONL rows must include
  `frame_avg_fps`, `frame_p95_ms`, `frame_performance_sample_count`,
  `frame_performance_source`, and `frame_performance_confidence`
- controlled 22W / 40 FPS target runs must include at least one
  `fps-target-satisfied` classification once the live MangoHud window is ready
- controlled 12W / 40 FPS target runs must keep `gpu-priority-epp` reachable and
  must not classify the below-target scene as `fps-target-satisfied`
- if live CSV discovery times out, the run is not allowed to claim V5
  target-aware behavior; it may only claim V4-compatible fallback

## Out Of Scope

- changing default TDP policy
- re-enabling CPU max-frequency caps by default
- thread affinity or hot-thread pinning
- sched_ext deployment
- Decky UI changes
- claiming FPS-target-aware behavior for the packaged daemon without a live FPS
  source

## Acceptance Criteria

- Unit tests prove target-satisfied samples suppress activation when live frame
  telemetry is above target.
- Unit tests prove below-target samples still activate EPP-only GPU priority.
- Unit tests prove missing frame-performance telemetry preserves V4 behavior.
- Unit tests prove an active controller restores after target-satisfied
  hysteresis.
- JSONL output includes frame-performance telemetry fields when present.
- Runtime classification evidence includes target-satisfied ratios.
- The standalone CLI accepts `--frame-performance-csv` and rejects invalid sample
  count settings.
- The profile script passes a live MangoHud CSV to the governor in controlled
  capture mode when available, and records a fallback reason when not available.
- The runtime contract fails when frame-performance telemetry is expected but no
  frame-performance rows are present.
- The runtime contract fails a 22W target-aware claim when no
  `fps-target-satisfied` classification is present.
- Local required Harness sweep passes.
- Device deployment succeeds and the active service is confirmed current.
- Foreground controlled profile at 12W/22W confirms:
  - 12W still reaches `gpu-priority-epp` for below-target scenes.
  - 22W emits `fps-target-satisfied` / observe-only samples once the live
    MangoHud window is available.
  - restore snapshots are clean.

## Rollback

The behavior is self-disabling when no frame-performance telemetry is present.
If the live MangoHud reader is unstable, disable passing `--frame-performance-csv`
from the profile script and the governor reverts to V4 behavior.
