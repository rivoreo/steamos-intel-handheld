# Game Power V6 Final Plan

## Status

Plan Review converged and implementation started. Focused TDD for the V6 final
safe slice is passing locally; required sweep and device validation are still
required before completion.

## Goal

Finish V6 as one coherent, shippable scheduler/product slice:

- automatic mode remains default and safe,
- FPS target becomes a first-class runtime objective,
- learning is target-aware and understandable,
- Decky exposes product-level controls only,
- profiler evidence is strong enough to reject unsafe default knobs,
- every device-facing claim is backed by Harness evidence.

## Research Inputs

Primary research ledger:
`docs/superpowers/specs/2026-07-05-game-power-v6-final-research-ledger.md`.

Key conclusions:

- FPS target / frame-time budget should drive the scheduler when live target and
  frame telemetry exist.
- Linux `uclamp`, cgroup `cpu.weight`, hard affinity, and sched_ext are real
  tools, but they require stronger restore and cross-game evidence before
  default activation.
- Manual per-game power knobs already exist in PowerTools/SimpleDeckyTDP; this
  project should not expose raw P/E-core frequency, threshold, PL2/Tau, cgroup,
  or affinity controls.
- A manual FPS target is acceptable because it defines the scheduler objective
  rather than changing measured CPU constants.

## Fresh Device Evidence

Device: `root@10.100.0.19`.
Foreground app: Cyberpunk 2077, `SteamAppId=1091500`.

Runtime snapshot before profiling:

- service active,
- mode `automatic`,
- TDP 12W,
- classification `gpu-package-bound-cpu-contention`,
- action `gpu-priority-epp`,
- FPS target `unknown`,
- frame source `missing`.

Fresh profile:
`.cache/game-power/v6-final-baseline/gpu-priority`

| TDP | Candidate | Avg FPS | 1% low | 0.1% low | Package W | Core share | Uncore share | Candidate action | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 12W | off baseline before | 28.3 | 21.52 | 18.59 | 11.97 | 0.465 | 0.238 | observe-only | baseline |
| 12W | gpu-priority | 27.4 | 20.96 | 19.60 | 11.98 | 0.477 | 0.229 | 29 EPP, 1 observe | not an FPS win |
| 12W | off baseline after | 27.7 | 21.19 | 20.16 | 11.96 | 0.476 | 0.231 | observe-only | baseline |
| 30W | off baseline before | 61.3 | 48.39 | 45.37 | 26.51 | 0.365 | 0.375 | observe-only | baseline |
| 30W | gpu-priority | 61.2 | 45.40 | 39.64 | 26.44 | 0.366 | 0.375 | observe-only | not package-bound |
| 30W | off baseline after | 61.5 | 45.83 | 15.21 | 26.58 | 0.368 | 0.374 | observe-only | baseline |

Contract evidence:

- `profile-runtime-telemetry-contract.json`: pass, 6/6 run contracts pass.
- `action-equivalence.json`: pass.
- FPS target remained `unknown`.
- Each run had 30 frame-performance rows in the profiler path, but the packaged
  daemon still has no service-level live frame provider.

CPU-cap A/B:

- Raw artifact: `.cache/game-power/v6-final-baseline/cpu-cap/raw`.
- The script exited non-zero because the 30W candidate did not reach the
  expected `gpu-priority-cpu-cap` action. That failure is useful evidence:
  30W was `not-package-bound`, so CPU-cap correctly did not trigger.

| TDP | Candidate | Avg FPS | 1% low | 0.1% low | Package W | Core share | Uncore share | Candidate action | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 12W | off baseline before | 29.3 | 22.61 | 21.80 | 11.95 | 0.450 | 0.248 | observe-only | baseline |
| 12W | gpu-priority-cpu-cap | 28.8 | 21.42 | 14.26 | 11.94 | 0.459 | 0.242 | 29 CPU-cap, 1 observe | rejected as default |
| 12W | off baseline after | 26.3 | 21.16 | 13.99 | 11.94 | 0.495 | 0.218 | observe-only | drift baseline |
| 30W | off baseline before | 61.7 | 47.97 | 41.79 | 26.73 | 0.369 | 0.373 | observe-only | baseline |
| 30W | gpu-priority-cpu-cap | 60.9 | 41.17 | 13.97 | 26.65 | 0.366 | 0.377 | observe-only | not package-bound |

Interpretation so far:

- 12W in this scene is package-limited and CPU-contention-heavy, but EPP-only
  did not improve FPS.
- 30W is near a 60 FPS cap and not package-bound; gpu-priority correctly does
  not write.
- CPU-cap and EPP-only both lack evidence for a safer/faster universal default
  in this scene.
- The missing target source is now the most important product/control gap.

## Product Contract

Decky may expose:

- Automatic,
- View data only,
- Off,
- refresh / one-shot probe,
- FPS target slider with an "Auto" state and coarse values.

Decky must not expose:

- P-core frequency,
- E-core frequency,
- CPU-cap thresholds,
- `cpu.weight`,
- `cpu.uclamp.*`,
- PL2/Tau,
- thread affinity,
- raw RAPL or sysfs paths.

### FPS Target UI

The FPS target control is a product-level objective:

- default: Auto, meaning "use SteamOS/gamescope target when available",
- manual: slider values in safe coarse steps, proposed range 30-120 FPS with
  5 FPS steps,
- clear/manual off: return to Auto.
- backend and CLI validation must reject non-finite, non-integer, off-step, or
  out-of-range values without mutating the control file.

Runtime display must state the source:

- SteamOS/gamescope,
- manual,
- unknown,
- unlimited,
- unsupported.

If target is unknown, Decky should say automatic is balancing from power signals
and learning cannot be reused as a target-aware hint yet.

## Implementation Progress

Implemented in this slice:

- `game_power_control.py` now supports safe manual FPS target overrides:
  `set-fps-target`, `clear-fps-target`, validation, status JSON, and config
  overlay.
- `power_control.py` wires a per-sample FPS target provider into
  `SystemGamePowerObserver`; the provider reads manual runtime control first
  and then attempts gamescope command-line discovery.
- targetless learning contexts are visible in session-close JSONL but cannot
  promote or reuse hints.
- runtime snapshot now includes a `learning` object with sample counts,
  reuse readiness, and skip reasons.
- Decky backend exposes `set_fps_target`, returns `control` in status, and
  keeps the safe no-restart control path.
- Decky frontend now has product-level copy, a 30-120 FPS / 5 FPS manual target
  slider, Auto/SteamOS target reset, and learning-state display.

Focused local evidence:

```text
.venv/bin/python -m pytest tests/test_game_power_control.py \
  tests/test_power_control_cli.py tests/test_game_power.py \
  tests/test_decky_plugin_backend.py tests/test_decky_plugin_assets.py
119 passed in 2.39s
```

## Validation Evidence

Local required sweep:

```text
scripts/harness.py sweep required --report .cache/harness/required.json
local: pass
```

Device deployment:

```text
scripts/install-on-device.sh root@10.100.0.19
Installed steamos-intel-handheld power control on root@10.100.0.19
Game Power plugin files are installed at
/home/deck/homebrew/plugins/steamos-intel-handheld-game-power.
```

Device verifier:

```text
scripts/verify-on-device.sh root@10.100.0.19
OK: RAPL PL1/PL2 restored
OK: systemd failed-unit list is empty
OK: SteamOS Manager TDP remote works and restored 30W
```

Game Power foreground verifier:

```text
scripts/verify-game-power-on-device.sh root@10.100.0.19
game-power verifier: CPU policy restored
```

Fresh post-implementation profile:
`.cache/game-power/v6-final-after`.

Invocation:

```text
PROFILE_GAME_POWER_APPID=1903340
PROFILE_GAME_POWER_CAPTURE_MODE=controlled
PROFILE_GAME_POWER_TDPS='12 30'
PROFILE_GAME_POWER_POLICIES='off gpu-priority'
PROFILE_GAME_POWER_REPEATS=1
PROFILE_GAME_POWER_DURATION_S=45
PROFILE_GAME_POWER_WARMUP_S=10
PROFILE_GAME_POWER_POLL_S=2
PROFILE_GAME_POWER_FPS_TARGET=45
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

Contract results:

- `action-equivalence.json`: pass, 0 action deltas, 0 reason deltas.
- `profile-runtime-telemetry-contract.json`: pass, 6/6 run contracts, 22 samples
  per run.
- Every profile run reported `fps_target=45.0`,
  `fps_target_source=manual`, and `fps_target_confidence=high`.

Observed profile summary:

| TDP | Policy | Position | Avg FPS | 1% low | 0.1% low | Package W | Core W | Uncore W | Core share | Uncore share | Actions | Classification |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 12W | off | baseline-before | 21.7 | 20.30 | 19.79 | 11.96 | 2.47 | 5.33 | 0.206 | 0.445 | observe-only:22 | observe-only:22 |
| 12W | gpu-priority | candidate | 21.8 | 20.29 | 20.03 | 11.96 | 2.41 | 5.40 | 0.201 | 0.452 | gpu-priority-epp:21, observe-only:1 | gpu-package-bound:22 |
| 12W | off | baseline-after | 21.9 | 20.39 | 20.04 | 11.96 | 2.41 | 5.41 | 0.201 | 0.452 | observe-only:22 | observe-only:22 |
| 30W | off | baseline-before | 31.2 | 29.40 | 29.03 | 27.34 | 10.87 | 9.78 | 0.398 | 0.358 | observe-only:22 | observe-only:22 |
| 30W | gpu-priority | candidate | 31.3 | 29.16 | 27.92 | 27.23 | 10.72 | 9.84 | 0.394 | 0.361 | observe-only:22 | not-package-bound:22 |
| 30W | off | baseline-after | 31.2 | 29.19 | 28.48 | 27.24 | 10.73 | 9.82 | 0.394 | 0.361 | observe-only:22 | observe-only:22 |

Interpretation:

- This foreground scene is effectively a 30 FPS class workload; the 45 FPS
  manual target intentionally exercises the below-target/manual-target telemetry
  path, not an expected 45 FPS outcome.
- At 12W the candidate applies EPP almost every sample, but FPS is effectively
  neutral against paired baselines.
- At 30W the candidate correctly avoids writes because the run is
  `not-package-bound`.
- A short 30 FPS target-satisfied device spot check should be run when SSH is
  reachable again; the local controller tests already cover target-satisfied
  suppression.

## Runtime Design

### Target Discovery Provider

Add a runtime target provider used by the daemon/service:

1. Read manual FPS target override from the runtime control file.
2. If no manual target exists, discover SteamOS/gamescope limit from stable
   sources:
   - gamescope command line `-r`, `--framerate-limit`, `--fps-limit`,
   - future SteamOS setting source when validated.
3. If no source is present, return target unknown.

The current device has no gamescope `-r` argument, and a quick Steam config
search did not expose a stable FPS-limit field. Therefore V6 must not claim
automatic target discovery is available on this device unless the provider
actually finds a source.

The provider must be in the scheduler's sample path, not only in status/config:

- define a `FrameTargetProvider` callable that returns
  `FrameTargetTelemetry | None`,
- pass it to `SystemGamePowerObserver`,
- call it for every foreground-game sample,
- set `sample.frame_target` from the provider result before classification,
- use that same sample target for hint context, runtime snapshot, and
  target-satisfied decisions.

This avoids a false target-aware UI where `game_power_control status` knows the
manual target but `GamePowerController.evaluate()` still receives a targetless
sample.

### Runtime Control File

Extend `game_power_control.py` without adding raw policy knobs:

- preserve `set-mode`, `status`, and `restore-defaults`,
- add `set-fps-target FPS` for manual slider writes,
- add `clear-fps-target` to return to auto target discovery,
- `status --json` includes a structured `fps_target_override`,
- `effective_config_from_runtime_file()` overlays both mode and manual target.

Manual target writes should preserve the existing mode override when present.
Mode writes should preserve the existing target override when present.

Validation contract:

- allowed manual values are integer FPS, 30-120 inclusive, divisible by 5,
- invalid values return an error/non-zero CLI exit and do not rewrite the file,
- `set-mode` must preserve a valid target override,
- `set-fps-target` must preserve a valid mode override,
- `restore-defaults` clears both mode and target overrides.

Status JSON shape:

```json
{
  "mode": "automatic",
  "effective_mode": "gpu-priority",
  "override_active": true,
  "fps_target_override": {
    "status": "manual",
    "fps": 40,
    "source": "decky"
  }
}
```

### Learning Guard

Fix the targetless learning gap:

- contexts with `fps_target="none-configured"` or target status unknown are not
  `complete`,
- incomplete contexts may open a visibility session, but they never receive a
  reusable canonical hint key and cannot promote a reusable hint,
- legacy `none-configured` cache entries remain readable but are advisory-only
  and cannot reduce activation warmup,
- runtime snapshot exposes whether this session can be reused next launch.

Implementation contract:

- split the session identity used for runtime visibility from the canonical
  reusable hint key,
- targetless/incomplete contexts get a stable visibility key such as
  `visible:<appid>:<pl1>:<power_source>`,
- only complete target-known contexts get `canonical_hint_key(context)`,
- normalize legacy loaded contexts whose `fps_target` is `none-configured`,
  `unknown`, or missing to `complete=False`,
- `GamePowerHintStore.get_hint()` must return `None` for incomplete, unknown,
  unlimited, or legacy targetless contexts.

### Runtime Snapshot

Extend `game-power-runtime-snapshot-v1` additively:

- `learning`: session samples, positive samples, required samples/sessions,
  hint key if usable, hint source, hint used, hint disabled, retention days,
  `reusable_next_launch`, and skip reason,
- `fps_target`: existing schema plus manual/autodiscovered source and confidence,
- `frame_source`: unchanged unless live source is present.

Existing Decky/backend code must tolerate missing `learning` for older daemons.

### Decky Backend

Add backend functions:

- one callable: `set_fps_target(fps: number | null)`,
- `null` means Auto/clear,
- `get_status()` returns runtime learning plus authoritative control state from
  `steamos-intel-handheld-game-power-control status --json`.

The backend still shells only through `steamos-intel-handheld-game-power-control`
and does not restart the service or write systemd drop-ins.

Backend must validate the same range as the CLI before spawning the command so
plugin errors are immediate, but the CLI remains authoritative and must reject
invalid calls too.

### Decky Frontend

Update the first panel:

- current mode,
- FPS target source and value,
- learning state from the required copy matrix below,
- service state.

Add an FPS target slider:

- label is always visible,
- value text is explicit, e.g. `Auto`, `Manual: 40 FPS`,
- uses `role=alert` for errors,
- no dangerous tuning constants are shown.

Required mode copy:

- Automatic: `Balances automatically. Uses FPS target when known; otherwise uses
  power signals.`
- View data only: `Shows telemetry only. No power settings are changed.`
- Off: `Stops the scheduler. No sampling or power changes.`
- 自動：`自動平衡；有 FPS 目標時依目標調度，沒有時依功耗訊號。`
- 只看資料：`只顯示遙測，不改動功耗設定。`
- 關閉：`停止調度器；不採樣、不改動功耗。`

Required learning states:

- `Learning this session` / `本次遊戲正在學習`
- `Reusable next launch` / `下次啟動可重用`
- `Not reusable: FPS target unknown` / `不可重用：FPS 目標未知`
- `Not reusable: view-data-only mode` / `不可重用：只看資料模式`
- `Not reusable: scheduler off` / `不可重用：調度器已關閉`
- `Not reusable: not enough samples yet ({samples}/{required})` /
  `不可重用：樣本不足（{samples}/{required}）`
- `Not reusable: current hint contradicted` / `不可重用：目前提示已被反證`
- `Learning state unavailable from this daemon` / `此 daemon 未提供學習狀態`

## TDD Plan

1. `tests/test_game_power_control.py`
   - failing test: `set_fps_target` writes manual target while preserving mode.
   - failing test: `clear_fps_target` removes only target override.
   - failing test: corrupt/invalid target falls back to base config.
   - failing test: invalid non-finite, off-step, and out-of-range FPS values do
     not mutate the control file.

2. `tests/test_power_control_cli.py`
   - failing test: service context is incomplete when FPS target is unknown.
   - failing test: manual runtime target becomes `FrameTargetTelemetry` with
     source `manual` and high confidence.
   - failing test: gamescope cmdline parser maps `-r 40` to source
     `gamescope-cmdline`.
   - failing test: target provider result reaches the actual
     `GamePowerSample.frame_target` used by classification/runtime snapshot.

3. `tests/test_game_power.py`
   - failing test: targetless sessions do not promote hints.
   - failing test: legacy `none-configured` hint entry loads but `get_hint`
     returns `None`.
   - failing test: runtime snapshot includes learning state and target reuse
     skip reason.
   - failing test: incomplete contexts still produce a visibility session but no
     reusable hint key.

4. `tests/test_decky_plugin_backend.py`
   - failing test: backend calls control CLI to set/clear FPS target.
   - failing test: backend rejects invalid slider values before spawning CLI.
   - failing test: missing older runtime snapshot yields safe default learning
     state.

5. `tests/test_decky_plugin_assets.py`
   - failing test: frontend contains FPS slider/manual target copy in en/zh-Hant.
   - failing test: bundled `dist/index.js` includes the same copy.
   - failing test: forbidden raw knobs remain absent.
   - failing test: frontend includes the learning copy matrix.

6. Focused test command:

   ```bash
   .venv/bin/python -m pytest \
     tests/test_game_power_control.py \
     tests/test_power_control_cli.py \
     tests/test_game_power.py \
     tests/test_decky_plugin_backend.py \
     tests/test_decky_plugin_assets.py
   ```

7. Required sweep:

   ```bash
   scripts/harness.py sweep required --report .cache/harness/required.json
   ```

## Implementation Surface

Allowed files:

- `src/steamos_intel_handheld/game_power.py`
- `src/steamos_intel_handheld/game_power_control.py`
- `src/steamos_intel_handheld/power_control.py`
- `src/steamos_intel_handheld/game_power_profile.py` only if target parser is
  deduplicated
- `decky/steamos-intel-handheld-game-power/main.py`
- `decky/steamos-intel-handheld-game-power/src/index.tsx`
- `decky/steamos-intel-handheld-game-power/dist/index.js`
- tests listed above
- V6 final docs / review reports

No default RAPL, cgroup, affinity, sched_ext, or CPU-frequency policy expansion
is allowed. Fresh CPU-cap A/B did not show an improvement and did not justify
default CPU max-frequency writes.

## Guarded Experiments

Keep these profiler-only unless future evidence proves them:

- CPU cap variants,
- background `cpu.weight`,
- background `cpu.uclamp.max`,
- thread affinity advisor,
- sched_ext/scx.

## Verification

Local:

- focused pytest,
- required harness sweep.

Device:

- `scripts/install-on-device.sh root@10.100.0.19`,
- `VERIFY_TDP_POLICY_MODE=ac-performance scripts/verify-on-device.sh root@10.100.0.19`,
- `scripts/verify-game-power-on-device.sh root@10.100.0.19`,
- controlled profile including 12W after deployment.

Manual/device checks:

- Decky loads without syntax error,
- FPS target slider writes manual target,
- runtime snapshot shows manual target source,
- clearing target returns to auto/unknown source,
- service restore leaves CPU policy clean.

## Rollback

- Clearing the runtime control file returns to packaged default mode and target
  auto-discovery.
- If target discovery fails, daemon remains power-signal automatic with target
  unknown and does not learn reusable target-aware hints.
- If Decky fails, CLI control remains usable through
  `steamos-intel-handheld-game-power-control`.
