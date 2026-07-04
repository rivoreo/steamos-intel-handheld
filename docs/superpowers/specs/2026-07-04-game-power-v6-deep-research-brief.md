# Game Power V6 Deep Research Brief

Date: 2026-07-04

Status: revised implementation-ready V6a plan draft. This document preserves
the deep-research evidence and narrows the next implementation to a bounded,
testable slice. It must pass Plan Review before development.

## Research Goal

Move Game Power from a mostly power-signal governor toward a generic
game-oriented controller that can reason about:

- live FPS and frame pacing,
- FPS target satisfaction,
- shared CPU/iGPU package power,
- P-core/E-core topology,
- per-game and per-target learned context,
- foreground thread pressure,
- background/helper process interference,
- user-facing Decky state that explains what is being controlled.

The next implementation should be a small convergent slice, not an unbounded
rewrite.

## Sources Reviewed

- Linux utilization clamping:
  https://docs.kernel.org/scheduler/sched-util-clamp.html
- Linux cgroup v2 CPU controller, pressure, and uclamp interfaces:
  https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux sched_ext:
  https://docs.kernel.org/scheduler/sched-ext.html
- scx_lavd README:
  https://github.com/sched-ext/scx/blob/main/scheds/rust/scx_lavd/README.md
- Android Dynamic Performance Framework:
  https://developer.android.com/games/optimize/adpf
- Android PerformanceHintManager.Session:
  https://developer.android.com/reference/android/os/PerformanceHintManager.Session
- Feral GameMode:
  https://github.com/FeralInteractive/gamemode
- Valve gamescope:
  https://github.com/ValveSoftware/gamescope
- Local MangoHud/mangoapp sources under `external/MangoHud/`.

## Device Evidence

Target: `root@10.100.0.19`

Deployment and platform:

- Kernel: `6.16.12-valve24.4-1-neptune-616-gfe145653a794`.
- Installed packages relevant to V6:
  - `gamescope 3.16.23.2-1`
  - `mangohud 0.8.3.rc1.r24.g33c2c7dd-3`
  - `gamemode 1.8.2-1`
  - `scx-scheds 1.1.1.linux.steamos-1`
  - `kcgroups 0.0.dmemcg.experimental.3-1`
  - `plasma-foreground-booster 0.0.dmemcg.experimental.3-1`
- `cat /sys/kernel/sched_ext/state` returned `disabled`.
- `/opt/steamos-intel-handheld/src/steamos_intel_handheld/*.py` checksums
  match the local repo files.
- The service is running the repo default:
  `--game-power-mode gpu-priority --game-power-cpu-cap off`.
- Runtime override is inactive:
  `{"mode": "default", "override_active": false}`.

Live observe-only sample:

- Foreground AppID: `3423533071`.
- PL1: `30 W`.
- Package power: about `24-25 W`.
- Core power: about `7.8-8.3 W`.
- Uncore power: about `10 W`.
- Foreground cgroup CPU pressure: `some_avg10` about `7.8%`,
  `full_avg10` about `0.8%`.
- `fps_target`, live frame average, frame p95, and render busy were all null in
  daemon observe-only mode.

MangoHud IPC check:

- `io.mangohud.socket` was not present on the deck user bus during the tested
  game session.
- Local MangoHud source shows promising `mangohud-next` frame sample IPC, but
  the installed target path cannot be assumed to expose it.

Controlled profile rerun:

- Command:
  `PROFILE_GAME_POWER_APPID=3423533071 PROFILE_GAME_POWER_CAPTURE_MODE=controlled PROFILE_GAME_POWER_TDPS='12 30' PROFILE_GAME_POWER_POLICIES='off gpu-priority' PROFILE_GAME_POWER_REPEATS=1 PROFILE_GAME_POWER_DURATION_S=20 PROFILE_GAME_POWER_WARMUP_S=5 PROFILE_GAME_POWER_POLL_S=1 PROFILE_GAME_POWER_OUTPUT_ROOT=.cache/game-power/v6-profiles-rerun PROFILE_GAME_POWER_SCENE_EVIDENCE=v6-deep-research-current-scene-rerun-2026-07-04 scripts/profile-game-power-on-device.sh root@10.100.0.19`
- Output root: `.cache/game-power/v6-profiles-rerun`.
- Runtime telemetry contract: 6/6 pass, 20 samples per run.
- Power source was stable `ac` in all rerun summaries.
- The earlier interrupted profile was discarded because the user changed power
  state during the run.
- After the rerun, service restored to default `gpu-priority`, and runtime
  override remained inactive.

Summary table from rerun:

| TDP | Policy | Position | Avg FPS | 1% Low | 0.1% Low | Avg ms | Package W | Core W | Core Share | CPU some peak | Classification |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 12 | off | baseline-before | 42.1 | 34.1085 | 31.72 | 23.7 | 11.949 | 3.562 | 0.298 | 8.64 | observe-only |
| 12 | gpu-priority | candidate | 42.2 | 35.5691 | 34.2911 | 23.7 | 11.957 | 3.486 | 0.292 | 9.13 | gpu-package-bound |
| 12 | off | baseline-after | 41.6 | 34.7255 | 34.3588 | 24.1 | 11.951 | 3.507 | 0.293 | 10.2 | observe-only |
| 30 | off | baseline-before | 60.3 | 51.0245 | 39.9127 | 16.6 | 24.411 | 7.975 | 0.327 | 7.53 | observe-only |
| 30 | gpu-priority | candidate | 59.9 | 44.6013 | 44.3062 | 16.7 | 24.501 | 8.243 | 0.336 | 8.09 | not-package-bound |
| 30 | off | baseline-after | 59.7 | 46.6815 | 44.8624 | 16.7 | 24.456 | 8.216 | 0.336 | 7.02 | observe-only |

Aggregate verdicts:

- `12 W`: inconclusive. Average FPS is effectively unchanged. 1% low improves
  versus the baseline median, but evidence is still one paired run.
- `30 W`: rejected. Average FPS is effectively unchanged, and median 1% low
  worsens by the profiler's acceptance criteria.

Thread and background observations:

- Hot foreground threads include `Endfield.exe`, `UnityGfxDeviceW`, and
  `Job.Worker` threads.
- Foreground hot thread migration deltas were zero in the sample windows, so
  hard thread affinity is not justified from this evidence.
- Some worker threads showed high runqueue wait ratios, but the current advisor
  classifies the roles as `latency-light`; it does not pass guarded affinity
  gates.
- `plugin_loader.service` was a major background CPU-time candidate in
  background-shaping advice, e.g. `20.52 s` CPU over a 20 s 12 W candidate
  window with commands `Decky Loader`, `Game Power`, `Charge Limit`, and
  `SteamGridDB`.

## Research Matrix

### 1. Daemon-level live FPS telemetry

Research:

- Linux uclamp documentation explicitly describes a game using FPS feedback to
  adjust performance constraints.
- Android PerformanceHintManager models the same control principle as
  target-duration plus actual-duration feedback for a thread group.
- gamescope can own frame pacing and frame-rate limit, but the current daemon
  path has no direct frame telemetry source.
- Local MangoHud `mangohud-next` source has frame samples, but the deployed
  target did not expose `io.mangohud.socket`.

Device data:

- Controlled profiler can get frame rows via MangoHud CSV.
- Daemon observe-only samples still have null FPS/frame fields.

Risk:

- Without live frame data, any FPS-target controller becomes a power heuristic,
  not a frame controller.

V6a hypothesis:

- First slice should expose a daemon-safe frame telemetry provider with strict
  fallback semantics:
  1. use an explicit live source only when present,
  2. mark unknown when absent,
  3. never actuate FPS-target retreat from target metadata alone.

V6a contract:

- Add a daemon-owned `GamePowerRuntimeSnapshot` contract. It is the
  authoritative source for Decky and diagnostics.
- The snapshot is produced by the running governor, not by a separate Decky
  observe probe.
- The snapshot may report `frame_source.status=missing`. V6a does not require
  a non-null frame row in automatic mode because no daemon-safe live provider is
  currently verified.
- Frame providers are pluggable and explicit. The initial provider may be
  `none`/`missing`; MangoHud CSV remains a profiler source, not a daemon source.
- A future provider must expose freshness, sample count, malformed/error state,
  and source identity before any FPS-target retreat can actuate.

Snapshot fields:

- `schema_version`: `game-power-runtime-snapshot-v1`
- `timestamp_monotonic_s`
- `source`: `daemon`
- `mode`: `automatic|observe|off`
- `control_active`: boolean
- `sample_source`: `governor|probe`
- `appid`
- `last_action`
- `last_reason`
- `classification_primary`
- `classification_confidence`
- `fps_target`: object using the target-state schema below
- `frame_source`: object using the frame-source schema below
- `package_w`, `core_w`, `uncore_w`, `pl1_w`, `render_busy`
- `stale`: boolean
- `error`: optional string

Frame-source schema:

- `status`: `live|missing|stale|malformed|unsupported`
- `source`: `none|mangohud-csv|mangohud-ipc|gamescope|manual`
- `confidence`: `high|medium|low`
- `avg_fps`, `p95_ms`, `p99_ms`, `sample_count`, `window_s`, nullable when
  status is not `live`.

Validation:

- Unit test missing, stale, malformed, and unsupported frame-source states.
- Runtime contract must require explicit `frame_source.status`, not non-null
  frame rows, unless a live provider is configured.
- Device evidence for V6a must prove the running daemon snapshot reports
  `source=daemon` and truthful `missing`/`unknown` telemetry states.

### 2. FPS target discovery

Research:

- Android ADPF and PerformanceHintManager are built around target work duration.
- Linux uclamp documentation's game example also assumes perceived FPS is
  known.
- gamescope has frame-rate limit arguments, but in the tested session discovery
  returned unknown.

Device data:

- All six controlled rerun summaries had `fps_target_source=unknown` and
  `fps_target_confidence=low`.
- Frame rows existed, but target satisfaction could not be evaluated because
  the target was missing.

Risk:

- Caching or learning without FPS target folds together 40 FPS, 60 FPS, and
  uncapped behavior, which makes the learned policy noisy.

V6a target-state schema:

- `status`: `known|unknown|unlimited|unsupported`
- `source`: `manual|gamescope-cmdline|steam-runtime|none`
- `confidence`: `high|medium|low`
- `fps`: nullable number; required only when `status=known`
- `raw`: optional bounded string for diagnostics

Rules:

- `source` and `confidence` are legal even when `fps` is null.
- Existing `fps_target: none-configured` hint-cache entries remain readable but
  are treated as `status=unknown, source=none, confidence=low`.
- Targetless hints cannot promote to automatic action unless the hint explicitly
  declares `target_independent=true` and the action does not depend on frame
  satisfaction.
- `unlimited` is distinct from `unknown`: unlimited means a source explicitly
  reported no cap; unknown means no reliable target source exists.

Validation:

- Synthetic parser tests for gamescope command lines.
- Device probe against active gamescope command line and Steam session data.
- Decky copy must explain when Game Power is balancing without a known target.
- Cache compatibility tests for old `none-configured` entries.

### 3. Frame pacing controller

Research:

- Frame-time tails matter more than average FPS for smoothness.
- Linux uclamp can mitigate PELT ramp-up latency that causes frame drops.
- Android's target/actual work duration API adjusts core placement and
  frequency to bring actual duration close to target duration.

Device data:

- Average FPS at 30 W is already around 60, but 1% low changed materially.
- Current summaries have `p95_frametime_ms=null` and `p99_frametime_ms=null`
  even though low-percentile FPS fields exist. The V6 data model should prefer
  explicit frame-time percentiles when available.

Risk:

- Average-FPS-only control will overfit to 60 FPS and miss stutter.

V6a hypothesis:

- Use target frame time plus p95/p99 or 1%/0.1% low gates. A control is allowed
  only if it improves low-percentile behavior without reducing average FPS or
  increasing package power beyond configured thresholds.

Validation:

- Add acceptance contracts for p95/p99 presence when supported.
- Compare candidate vs paired baselines using low-percentile and power gates.

### 4. Per-game/per-target learning

Research:

- Existing mobile and Linux mechanisms use feedback loops and context, not
  static per-game tables.
- The current hint cache keys include AppID, topology, kernel/driver, PL1,
  power source, FPS target, and policy version, which is the right shape.

Device data:

- Existing hint cache has useful AppID/TDP/topology buckets, but the observed
  keys are `fps_target: none-configured` and `runtime_signature:
  unavailable`.

Risk:

- Learning from targetless data can make future decisions less stable.

V6a hypothesis:

- Keep AppID as a grouping/cache key, but do not promote hints to automatic
  action unless FPS target and runtime signature are known or the action is
  explicitly target-independent.

Validation:

- Cache policy tests for targetless hints.
- Profile replay tests proving targetless hints remain advisory.

### 5. Background/helper shaping

Research:

- cgroup v2 exposes `cpu.weight`, `cpu.max`, `cpu.pressure`,
  `cpu.uclamp.min`, and `cpu.uclamp.max`.
- Linux uclamp documentation uses background caps as a documented example for
  reserving resources for foreground work.
- GameMode proves host-level game optimizers are common, but it is request
  based and not FPS-target closed-loop.

Device data:

- Candidate runs showed `foreground-cpu-pressure` and
  `system-pressure-advisory`.
- `plugin_loader.service` consumed about one full CPU in the 12 W candidate
  window and remained the top background-shaping candidate.

Risk:

- Bluntly throttling Steam, gamescope, MangoHud, audio, or input helpers can
  break UX.
- Decky/plugin-loader activity may be caused by the test UI being open; this
  needs a repeat with Decky closed.

V6a hypothesis:

- The highest-value future writer candidate is not foreground CPU caps. V6a
  remains observe/dry-run only:
  - observe and rank helper cgroups,
  - exclude gamescope, audio, input, foreground app, and MangoHud by default,
  - reuse or extend the existing `background_shaping_experiment_plan` artifact,
  - emit proposed `cpu.weight` and `cpu.uclamp.max` variants as disabled
    proposals,
  - do not apply any background cgroup writes in V6a, including profile-triggered
    writes.
- Existing guarded profiler writer variants such as `gpu-priority-bg-weight`
  and `gpu-priority-bg-uclamp` remain pre-existing experimental surfaces. V6a
  must not extend them, call them from new code, make them default, or use them
  as V6a validation evidence.

Validation:

- Unit tests for allow/deny lists and restore snapshots.
- Device A/B with Decky closed and then Decky open.
- Tests must assert `write_policy=disabled` for V6a dry-run artifacts.
- Tests must assert V6a Decky-open/closed profiling metadata and dry-run
  proposal generation cannot invoke `apply-background-shaping` and cannot select
  `gpu-priority-bg-weight` or `gpu-priority-bg-uclamp`.
- Any actual write requires a separate future Plan Review.

### 6. Thread-affinity advisor

Research:

- sched_ext/scx_lavd is explicitly gaming motivated and targets tail latency,
  but sched_ext is disabled on this target.
- Android PerformanceHintManager's `setThreads` and target-duration feedback
  show that thread grouping matters, but Android constrains it to foreground
  app-owned threads.
- Prior research points to soft, demand-sized placement rather than hard
  affinity.

Device data:

- Hot game threads already have observed CPU sets. `Endfield.exe` and
  `UnityGfxDeviceW` are constrained to `0-3`.
- Migration deltas were zero in the sampled windows.
- Worker threads show runqueue wait, but current evidence does not justify
  hard pinning.

Risk:

- Hard affinity can reduce scheduler flexibility and worsen low FPS.

V6a hypothesis:

- Keep affinity as observe-only advisor in V6 unless repeated profiles show a
  latency-hot role with migration or runqueue-wait correlation to frame-time
  spikes.

Validation:

- Require correlation between role pressure and frame-time tails.
- Any writer must be a separate Plan Review item.

### 7. Decky product state

Research:

- The product needs to explain whether Game Power is controlling,
  monitoring-only, missing telemetry, or waiting for confidence.
- ADPF exposes thermal/game-mode concepts to developers; our Decky UI should
  expose user-safe state, not raw frequency knobs.

Device data:

- Current UI state can say balanced/automatic while the daemon has no FPS
  target and no live frame telemetry.
- Users can misread `observe` versus `off` unless copy clearly says whether
  sampling continues and whether power settings are changed.

Risk:

- If the UI says the scheduler is target-aware while target/frame telemetry is
  unknown, it creates false confidence.

V6a UX contract:

- Decky consumes the daemon-owned `GamePowerRuntimeSnapshot` for live status.
- If Decky still offers an ad-hoc refresh/probe, that result must be labelled
  `sample_source=probe` and cannot be rendered as the running controller's last
  decision.
- The first panel must show the control truth and telemetry truth together. Do
  not hide missing FPS target or missing frame data in a detail-only section.
- Raw scientific knobs remain hidden: no P-core/E-core frequencies, thresholds,
  uclamp, CPUWeight, PL2, Tau, or affinity controls.

Screen/state matrix:

| State | First panel status | Detail section | Actions |
| --- | --- | --- | --- |
| Initial load | `Reading game-power status...` / `正在讀取遊戲電力狀態...` | Empty skeleton-compatible rows | Disable mode buttons |
| Service unavailable | `Game Power service unavailable` / `遊戲電力服務無法使用` | Error reason when available | Refresh only |
| No foreground game | `No foreground game` / `沒有前景遊戲` | Service mode and last stale snapshot if any | Mode buttons enabled |
| Automatic, target unknown, frame missing | `Balancing from power signals - FPS target unknown` / `依功耗訊號平衡 - FPS 目標未知` | Frame source missing, last action/reason, power metrics | Mode buttons enabled |
| Automatic, target known, frame live | `Target-aware balancing` / `依 FPS 目標平衡` | Target FPS, frame status, p95/p99 when available | Mode buttons enabled |
| Automatic waiting for confidence | `Collecting data before changing power` / `正在累積資料，暫不改動功耗` | Confidence, sample count, missing fields | Mode buttons enabled |
| Observe | `View data only - no power changes` / `只看數據 - 不改動功耗` | Sampling continues, last classification visible | Mode buttons enabled |
| Off | `Scheduler off - no sampling or power changes` / `已完全停用 - 不採樣、不改動功耗` | Service state only | Mode buttons enabled |
| Stale snapshot | `Status may be stale` / `狀態可能已過期` | Last timestamp/source | Refresh and mode buttons |
| Backend error | `Game-power status is unavailable` / `無法讀取遊戲電力狀態` | Error text | Refresh only |
| Applying mode | `Applying...` / `正在套用...` | Preserve previous stable snapshot | Disable mode buttons |
| Restoring default | `Using the service default` / `已切回服務預設` | Updated service mode after refresh | Disable until complete |

Localized copy requirements:

- Replace generic automatic copy with power-signal truth when FPS telemetry is
  unknown.
- English:
  - `Balancing from power signals - FPS target unknown`
  - `Target-aware balancing`
  - `Collecting data before changing power`
  - `View data only - no power changes`
  - `Scheduler off - no sampling or power changes`
  - `Frame data missing`
  - `Frame data live`
  - `FPS target unknown`
  - `FPS target known`
  - `FPS cap unlimited`
- zh-Hant:
  - `依功耗訊號平衡 - FPS 目標未知`
  - `依 FPS 目標平衡`
  - `正在累積資料，暫不改動功耗`
  - `只看數據 - 不改動功耗`
  - `已完全停用 - 不採樣、不改動功耗`
  - `缺少影格資料`
  - `影格資料即時可用`
  - `FPS 目標未知`
  - `FPS 目標已知`
  - `FPS 未限制`

Validation:

- UI asset tests for copy.
- Backend schema tests for telemetry status.
- Manual screenshot verification after deployment.

## Recommended V6a Slice

The most defensible V6a slice is:

1. Add daemon-owned runtime snapshot status, not a new power writer.
2. Make FPS target and frame source state explicit in daemon JSONL, a service
   snapshot file/API, Decky backend, and UI.
3. Convert target/frame missing states into truthful product copy.
4. Add profile/research support to repeat background-helper observations with
   Decky open and Decky closed.
5. Reuse or extend the existing dry-run background-shaping proposal artifact so
   it ranks helper cgroups and prints exact proposed writes with
   `write_policy=disabled`.

Why this slice:

- It closes the biggest correctness gap first: the daemon and Decky share an
  authoritative truth contract even when target/frame telemetry is missing.
- It explains to users when Game Power is acting from power heuristics rather
  than frame-target proof.
- It turns the observed `plugin_loader.service` interference into a controlled
  dry-run hypothesis without risking foreground/game/session services.
- It keeps hard affinity and sched_ext out of production until there is stronger
  evidence.

## Implementation Surface

Allowed files for V6a:

- `src/steamos_intel_handheld/game_power.py`
- `src/steamos_intel_handheld/power_control.py`
- `src/steamos_intel_handheld/game_power_control.py`
- `src/steamos_intel_handheld/game_power_profile.py`
- `decky/steamos-intel-handheld-game-power/main.py`
- `decky/steamos-intel-handheld-game-power/src/index.tsx`
- `decky/steamos-intel-handheld-game-power/dist/index.js`
- `tests/test_game_power.py`
- `tests/test_power_control_cli.py`
- `tests/test_game_power_profile.py`
- `tests/test_decky_plugin_assets.py`
- `tests/test_integration_assets.py`
- `scripts/profile-game-power-on-device.sh`

Write-surface limits inside allowed files:

- `game_power_profile.py` may only be changed for snapshot parsing, dry-run
  proposal reporting, and Decky-open/closed grouping. V6a must not change
  `apply-background-shaping` or `restore-background-shaping` semantics.
- `scripts/profile-game-power-on-device.sh` may only add metadata flags and
  dry-run-only grouping for V6a. It must not route V6a through
  `gpu-priority-bg-weight`, `gpu-priority-bg-uclamp`, or
  `apply_background_shaping_variant`.
- Existing guarded background-shaping writer tests may remain, but they are not
  V6a acceptance evidence.

Out of scope:

- New automatic cgroup writes.
- Hard thread affinity.
- sched_ext/scx_lavd enablement.
- Any default CPU max-frequency cap.
- Claiming target-aware control while `fps_target.status` is `unknown` or
  `frame_source.status` is not `live`.

## TDD Plan

First failing tests:

1. `tests/test_game_power.py`
   - snapshot formatting emits `game-power-runtime-snapshot-v1`
   - target state can represent `unknown`, `unlimited`, and known numeric FPS
   - frame source can represent missing/stale/malformed/live
2. `tests/test_power_control_cli.py`
   - service config creates a daemon snapshot path/API by default
   - service does not require a live frame provider to start
3. `tests/test_decky_plugin_assets.py`
   - backend exposes authoritative status fields
   - frontend includes required English and zh-Hant telemetry copy
   - frontend does not render probe samples as daemon decisions
4. `tests/test_game_power_profile.py`
   - dry-run background-shaping plan keeps `write_policy=disabled`
   - Decky-open/closed metadata groups observations without applying writes
   - V6a profile metadata path does not call `apply-background-shaping`
   - V6a does not select `gpu-priority-bg-weight` or `gpu-priority-bg-uclamp`

## Verification And Harness Gates

Local required gate after any implementation change:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

Focused tests must run before the required sweep, but do not replace it.

Device evidence rules:

- Do not claim daemon runtime-snapshot behavior is device-verified until
  `scripts/verify-game-power-on-device.sh root@10.100.0.19` or a V6a-specific
  guarded check captures the daemon snapshot from the running service.
- Do not claim profile-level FPS or background-shaping findings until
  `scripts/profile-game-power-on-device.sh root@10.100.0.19` or equivalent
  guarded profile artifacts pass.
- Screenshot/manual UI evidence is required before claiming Decky UI deployment
  is visually verified.

V6a completion criteria:

- Required sweep passes.
- Runtime snapshot schema is covered by unit tests.
- Decky copy/state tests pass for English and zh-Hant.
- No test or profile path applies new background cgroup writes by default.
- V6a-specific tests prove the new dry-run/profile metadata path cannot trigger
  existing background-shaping writer variants.
- Device validation either passes or is explicitly reported as not run; no
  hardware claim is made without it.

## Resolved Plan Review Decisions

- Telemetry/status first is intentional and required before more actuation.
- The dry-run shaping artifact should include both `cpu.weight` and
  `cpu.uclamp.max` proposals, clearly disabled.
- Decky must expose telemetry status in the first panel, not only in details.
- Future background-helper writes require a separate Plan Review and repeated
  paired A/B evidence showing FPS/low-percentile improvement or no regression,
  stable power source, stable thermal envelope, clean restore, and no input,
  audio, Steam, gamescope, MangoHud, or foreground-game breakage.

## Not Yet Approved

- No automatic background cgroup writes.
- No hard foreground thread affinity.
- No sched_ext/scx_lavd enablement.
- No claim that default service is FPS-target-aware until daemon live target and
  frame telemetry are available.
