# Game Power v3 Observer Classifier Design

## Goal

Build the first convergent v3 step toward an FPS-targeted game scheduler:
runtime-visible target/pressure/classification telemetry plus post-run profile
classification, without adding any new production actuator path.

V3 must make future closed-loop policy work testable. It must not let stale
frame metrics, system-wide PSI, or speculative affinity/uclamp advice change
runtime behavior.

## Scope

In scope:

- Add explicit frame target metadata to game-power runtime samples.
- Add pressure telemetry with source, scope, support state, and `None` values
  for unknown metrics.
- Add a deterministic runtime classifier that emits evidence with every
  `GamePowerDecision`.
- Preserve current EPP/CPU-cap action behavior when no fresh live frame source
  exists.
- Add post-run profiler classification from MangoHud summaries and game-power
  JSONL.
- Tighten A/B evidence rules for 12W, 17W, 22W, and 30W profile claims.
- Add focused red/green tests before the required harness sweep.

Out of scope for this v3:

- Runtime restore based on target-sustained FPS.
- Runtime foreground `uclamp.min`, background shaping, cpuset, hard affinity, or
  sched_ext writes.
- Decky controls for thresholds, P-core/E-core frequencies, uclamp, affinity,
  cgroup paths, PL2/Tau, or classifier internals.
- Per-game learned profiles or permanent policy caches.

## Review-Gated Design Decisions

The first review sweep found that the earlier candidate was unsafe as an
actuator. This revision makes the following decisions explicit:

1. `target-sustained` is post-run evidence only in this v3. It is never a
   runtime restore trigger.
2. The standalone game-power CLI may accept `--fps-target` and
   `--fps-target-source`, but it must not accept `--avg-fps` or
   `--p99-frametime-ms` for runtime actuation. Average FPS, p99, and 1% low are
   computed after MangoHud capture in `game_power_profile.py`.
3. Pressure values carry `scope` and `source_path`. Foreground cgroup pressure
   can support foreground advisories; `/proc/pressure/*` is system pressure and
   is advisory only.
4. Unsupported or missing pressure is represented as `supported=false` and
   metric values `null`. It is never treated as zero pressure.
5. Runtime classification is schema and evidence. It does not choose a new
   action in v3; `GamePowerController.evaluate()` keeps the existing hysteresis
   and restore semantics.

## Runtime Data Contract

Add these dataclasses in `src/steamos_intel_handheld/game_power.py`.

```python
@dataclass(frozen=True)
class FrameTargetTelemetry:
    fps_target: float | None = None
    source: str | None = None
    confidence: str | None = None


@dataclass(frozen=True)
class PressureMetric:
    some_avg10: float | None = None
    full_avg10: float | None = None


@dataclass(frozen=True)
class PressureSignal:
    scope: str
    source_path: str | None
    supported: bool
    some_avg10: float | None = None
    full_avg10: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class PressureTelemetry:
    cpu: tuple[PressureSignal, ...] = ()
    memory: tuple[PressureSignal, ...] = ()
    io: tuple[PressureSignal, ...] = ()


@dataclass(frozen=True)
class GamePowerClassification:
    primary: str
    advisories: tuple[str, ...] = ()
    confidence: str = "low"
    evidence: dict[str, object] = field(default_factory=dict)
```

Extend `GamePowerSample` with:

```python
frame_target: FrameTargetTelemetry | None = None
pressure: PressureTelemetry | None = None
```

Extend `GamePowerDecision` with:

```python
classification: GamePowerClassification | None = None
```

Backward compatibility rule: existing tests and constructors must keep working
by using defaults. Older JSONL without classification or pressure remains valid
input for the profiler parser.

Canonical pressure shape: the parent container key is authoritative. A signal
under `PressureTelemetry.cpu` or JSONL `pressure.cpu[]` is CPU pressure; a signal
under `memory` or `io` is that resource. `PressureSignal` and JSONL entries do
not include a duplicate `resource` field. The profiler parser should reject or
ignore any conflicting duplicate resource field rather than treating it as a
second source of truth.

## Frame Telemetry Semantics

Runtime frame target metadata is allowed because the FPS target is known before
or during a run. Runtime frame outcome metrics are not allowed in this v3 unless
a future live source updates them per sample.

The v3 CLI adds:

- `--fps-target FLOAT`
- `--fps-target-source TEXT`
- `--fps-target-confidence TEXT`, defaulting to `manual` source with `medium`
  confidence when `--fps-target` is provided by hand.

The v3 CLI does not add `--avg-fps`, `--p99-frametime-ms`, or
`--one-percent-low-fps`.

Post-run profile classification in `game_power_profile.py` uses MangoHud data:

- `target_frame_ms = 1000 / fps_target`
- `fps_target_met = avg_fps >= 0.98 * fps_target`
- `pacing_proof = true` only when:
  - `p99_frametime_ms <= 1.50 * target_frame_ms`, and
  - `one_percent_low_fps >= 0.80 * fps_target`
- missing p99 or 1% low means `pacing_proof = null`, not true.

Runtime actions ignore all post-run frame outcome fields. Existing restore
still requires the package/GPU pressure signal to go negative for
`restore_samples` consecutive samples.

## Pressure Telemetry Semantics

`SystemGamePowerObserver` should accept an injectable `cgroup_root` argument
with default `/sys/fs/cgroup`. It should collect pressure in this order:

1. If a foreground Steam game process exists, read its cgroup path from
   `/proc/<pid>/cgroup` and resolve it under `cgroup_root`.
2. From that foreground cgroup, try `cpu.pressure`, `memory.pressure`, and
   `io.pressure`. Readable files produce `scope="foreground_cgroup"` signals.
3. If a foreground cgroup pressure file is missing or unreadable, emit a signal
   for that resource with `scope="foreground_cgroup"`, `supported=false`, and
   null metric values.
4. Optionally read `/proc/pressure/cpu`, `/proc/pressure/memory`, and
   `/proc/pressure/io`. These produce `scope="system"` signals only.
5. Parser failures produce `supported=false` with an `error` string and null
   metric values.

Pressure parser rules:

- `some.avg10` maps to `some_avg10`.
- `full.avg10` maps to `full_avg10`.
- A missing `full` line leaves `full_avg10=None`.
- Missing files, disabled PSI, permission errors, and parse errors do not throw
  from the observer sample path.
- Unknown pressure is never converted to `0.0`.

Cgroup resolver rules:

- Use only the cgroup v2 line whose prefix is `0::`.
- The v2 path must be absolute and non-empty.
- Strip the leading `/` before joining to `cgroup_root`.
- Reject empty, `.`, `..`, and path-separator traversal components.
- Resolve the joined path and verify it remains under the resolved
  `cgroup_root`.
- Missing `/proc/<pid>/cgroup`, a process that exits during sampling, missing
  v2 line, invalid path, traversal, or unreadable cgroup directory produces
  foreground-cgroup `supported=false` pressure signals for CPU, memory, and IO.
- The resolver is a pure helper named `resolve_cgroup_v2_path(cgroup_root,
  cgroup_text)` so it can be tested without a live `/proc` or `/sys`.

Runtime pressure helpers are separate from existing profile summarizer helpers:

- `parse_pressure_signal(resource, scope, source_path, text)` lives in
  `game_power.py` and returns a `PressureSignal` with nulls for missing fields.
- `read_pressure_signal(resource, scope, path)` lives in `game_power.py` and
  converts file errors into `supported=false`.
- `game_power_profile.parse_pressure_file()` keeps its current profile-summary
  role unless a later plan migrates profile pressure peaks to the richer
  runtime schema.

## Runtime Classification Contract

Runtime classification emits a primary label plus zero or more advisories.
It does not change `decision.action` in this v3.

Implementation must use a pure helper:

```python
def classify_game_power_sample(
    config: GamePowerConfig,
    sample: GamePowerSample,
) -> GamePowerClassification:
    ...
```

Both `GamePowerController.evaluate()` and the off-mode branch in
`GamePowerGovernor.run_once()` must attach the helper's result to the returned
`GamePowerDecision`. This is required because the current off-mode governor path
constructs a decision without sampling through the controller.

The primary classification uses existing governor thresholds so no measured
constant is exposed to users or changed by this work.

| Priority | Condition | Primary | Confidence |
| --- | --- | --- | --- |
| 1 | `config.mode == off` | `control-disabled` | high |
| 2 | `config.mode == observe` | `observe-only` | high |
| 3 | no foreground AppID | `no-foreground-game` | high |
| 4 | `target_appid` set and foreground AppID differs | `non-target-game` | high |
| 5 | missing RAPL package, PL1, or package power | `insufficient-power-evidence` | low |
| 6 | package >= `package_pressure_ratio * PL1`, GPU activity present, and core share >= `cpu_cap_core_share_threshold` | `gpu-package-bound-cpu-contention` | high |
| 7 | package >= `package_pressure_ratio * PL1` and GPU activity present | `gpu-package-bound` | high |
| 8 | package >= `package_pressure_ratio * PL1` and GPU activity absent | `unknown-package-pressure` | medium |
| 9 | package below pressure threshold | `not-package-bound` | medium |

GPU activity remains the current rule:

- `uncore_share >= uncore_share_threshold`, or
- `render_busy >= render_busy_threshold`.

Advisories are additive:

| Signal | Required scope | Threshold | Advisory |
| --- | --- | --- | --- |
| CPU PSI | `foreground_cgroup` | `some_avg10 >= 2.0` or `full_avg10 >= 0.5` | `foreground-cpu-pressure` |
| memory PSI | `foreground_cgroup` | `some_avg10 >= 1.0` or `full_avg10 >= 0.2` | `foreground-memory-pressure` |
| IO PSI | `foreground_cgroup` | `some_avg10 >= 1.0` or `full_avg10 >= 0.2` | `foreground-io-pressure` |
| CPU/memory/IO PSI | `system` | same resource threshold | `system-pressure-advisory` |

`system-pressure-advisory` cannot become `foreground-cpu-pressure` without
foreground cgroup pressure or foreground thread wait evidence. The v3
implementation may log foreground thread wait evidence from existing profiler
artifacts after the run, but it must not use that post-run evidence as a
runtime action input.

## JSONL Schema

`format_decision_jsonl()` adds fields while keeping old fields unchanged:

```json
{
  "elapsed_s": 2.0,
  "appid": "1091500",
  "action": "gpu-priority-epp",
  "reason": "package limited with GPU activity",
  "package_w": 21.9,
  "core_w": 6.9,
  "uncore_w": 8.8,
  "dram_w": 0.4,
  "psys_w": 31.0,
  "pl1_w": 22,
  "render_busy": 0.82,
  "fps_target": 40.0,
  "fps_target_source": "gamescope-cmdline",
  "fps_target_confidence": "medium",
  "classification": {
    "primary": "gpu-package-bound",
    "advisories": ["foreground-cpu-pressure"],
    "confidence": "high",
    "evidence": {
      "package_pressure_ratio": 0.995,
      "core_share": 0.315,
      "uncore_share": 0.402,
      "render_busy": 0.82,
      "pressure_scopes": ["foreground_cgroup", "system"]
    }
  },
  "pressure": {
    "cpu": [
      {
        "scope": "foreground_cgroup",
        "source_path": "/sys/fs/cgroup/app.slice/app-steam-app1091500.scope/cpu.pressure",
        "supported": true,
        "some_avg10": 2.4,
        "full_avg10": 0.1
      }
    ]
  }
}
```

Missing pressure fields are encoded as `null`. Missing classification in older
JSONL is accepted by the profiler and counted as `unknown`.

Runtime classification count schema:

- `GamePowerLogSummary` adds:
  - `classification_primary: dict[str, int] | None`
  - `classification_advisories: dict[str, int] | None`
- `RunSummary` adds the same two fields.
- `summary.json` emits:

```json
{
  "classification_primary": {
    "gpu-package-bound": 2,
    "unknown": 1
  },
  "classification_advisories": {
    "foreground-cpu-pressure": 1,
    "system-pressure-advisory": 2
  }
}
```

- `PolicyAggregate` adds:
  - `classification_primary_counts: dict[str, int]`
  - `classification_advisory_counts: dict[str, int]`
- Aggregate output sums counts across included runs.
- A legacy row without `classification` increments
  `classification_primary["unknown"]`.
- Advisory counts are counted independently from primary counts. A row with
  primary `gpu-package-bound` and two advisories increments one primary counter
  and both advisory counters.
- A row with `classification.advisories` missing or empty does not increment
  advisory counters.

## Profiler Flow

`scripts/profile-game-power-on-device.sh` should pass only the discovered target
to the game-power runner:

```bash
--fps-target "$FPS_TARGET"
--fps-target-source "$FPS_TARGET_SOURCE"
```

It must not pass average FPS, p99, or low-percentile metrics to the runtime
runner because those are available only after MangoHud capture is collected.

`game_power_profile.py summarize` owns post-run outcome classification:

- parse richer game-power JSONL if present;
- aggregate runtime classification counts;
- retain backward compatibility for legacy rows;
- compute `target_frame_ms`, `fps_target_met`, `pacing_proof`, and
  `post_run_classification`;
- keep existing controlled-capture and restore checks as the authority for A/B
  acceptance.

Imported MangoHud capture remains exploratory. Any improvement claim requires
controlled capture.

The profiler must carry A/B evidence through the same data path used by
comparison and aggregate reporting. Manifest-only evidence is not enough.

`game_power_profile.py summarize` adds CLI inputs:

- `--ab-order-strategy randomized|paired-baseline`
- `--ab-run-order TEXT`, for example `off,gpu-priority,off`
- `--scene-evidence TEXT`, for example `benchmark-loop` or
  `save:dogtown-market-static`
- `--power-source-state ac|battery|unknown`
- `--thermal-evidence TEXT`, for example `cpu_pkg=61.0->63.5` or
  `unavailable`
- `--cooldown-rule TEXT`, for example `fixed-60s` or `return-to-60c`

`RunSummary` and `summary.json` add the same fields:

```json
{
  "ab_order_strategy": "randomized",
  "ab_run_order": "gpu-priority,off",
  "scene_evidence": "save:dogtown-market-static",
  "power_source_state": "ac",
  "thermal_evidence": "cpu_pkg=61.0->63.5",
  "cooldown_rule": "fixed-60s"
}
```

`PolicyAggregate` adds:

- `ab_order_strategy: str | None`
- `scene_evidence: str | None`
- `power_source_state: str | None`
- `thermal_evidence_states: list[str]`
- `cooldown_rule: str | None`
- `ab_evidence_complete: bool`

Aggregate rules:

- `aggregate_run_summaries()` raises or marks the aggregate incomplete if runs
  under the same aggregate have mixed `ab_order_strategy`, `scene_evidence`,
  `power_source_state`, or `cooldown_rule`.
- `thermal_evidence="unavailable"` is accepted only when present explicitly.
  Missing thermal evidence is incomplete.
- Legacy summaries missing any A/B evidence field produce
  `ab_evidence_complete=false`.
- `compare_policy_aggregates()` must not return `BETTER` when either aggregate
  has `ab_evidence_complete=false`, mixed evidence, missing evidence, or
  mismatched scene/power/cooldown/order strategy. It returns an exploratory or
  needs-evidence verdict instead.

The device wrapper supplies these values from environment variables or
auto-collection:

- `PROFILE_GAME_POWER_AB_ORDER_STRATEGY`, default `paired-baseline` for
  controlled capture.
- `PROFILE_GAME_POWER_SCENE_EVIDENCE`, required for non-exploratory claims.
- power source auto-detected when available, otherwise `unknown`.
- thermal evidence auto-collected when available, otherwise `unavailable`.
- `PROFILE_GAME_POWER_COOLDOWN_RULE`, default `fixed-60s` for controlled
  capture.

Post-run classification taxonomy:

| Conditions | `post_run_classification` |
| --- | --- |
| missing or invalid FPS target | `unknown` |
| missing average FPS | `unknown` |
| `fps_target_met` is false | `below-target` |
| `fps_target_met` is true and `pacing_proof` is true | `target-sustained` |
| `fps_target_met` is true and `pacing_proof` is false | `target-average-only` |
| `fps_target_met` is true and `pacing_proof` is null | `target-average-only` |

`target-sustained` remains post-run evidence only. It cannot trigger runtime
restore in v3.

## Evidence Contract For A/B Claims

No V3 result may claim hardware/profile improvement unless all of these are
true:

- `game-power-profile-device` was run on the handheld with a foreground game.
- Capture mode is `controlled`, not imported.
- Each compared policy has at least 3 runs per TDP.
- TDP matrix includes 12W, 17W, 22W, and 30W for the claim being made.
- Baseline and candidate use the same AppID, FPS target, duration, warmup, poll
  interval, EPP, P/E-core caps, and CPU-cap threshold unless the varied tunable
  is the explicit experiment.
- Every run has exact TDP, CPU policy, cgroup/uclamp, and service-mode restore
  evidence.
- Runs are ordered to reduce drift: each TDP uses either a randomized policy
  order per repeat or a paired baseline-candidate-baseline sequence. The order
  must be written to each manifest.
- The manifest records same-scene evidence: either an in-game benchmark loop,
  a named save/scene note, or a caller-provided scene token. Without this, the
  report must call the result exploratory.
- The manifest records AC/battery state before each run. Baseline and candidate
  comparisons require the same power-source state.
- The manifest records initial and final thermal evidence where available
  from hwmon, CPU package temperature, or platform sensors. If thermal sensors
  are unavailable, the manifest must record `thermal_evidence="unavailable"`.
- A cooldown or stabilization rule is recorded: either a fixed cooldown between
  runs or a temperature-return threshold. Without this, the report must call the
  result exploratory.
- The aggregate comparison accepts the candidate at that TDP.
- The required A/B evidence fields are present in `summary.json`,
  `RunSummary`, and `PolicyAggregate`, not only in `manifest.json`.

Local tests and required sweeps validate schemas and logic only. They are not
hardware/profile evidence.

## Focused Red/Green Tests

Before implementation code, add focused failing tests for these contracts:

- `tests/test_game_power.py::test_runtime_fps_target_cli_tags_samples_without_frame_outcome_actuation`
- `tests/test_game_power.py::test_pressure_parser_preserves_missing_full_as_none`
- `tests/test_game_power.py::test_pressure_reader_marks_missing_files_unsupported`
- `tests/test_game_power.py::test_resolve_cgroup_v2_path_requires_absolute_safe_path`
- `tests/test_game_power.py::test_resolve_cgroup_v2_path_rejects_traversal`
- `tests/test_game_power.py::test_classification_table_emits_gpu_package_bound_without_changing_action`
- `tests/test_game_power.py::test_classification_table_covers_observe_no_foreground_and_target_mismatch`
- `tests/test_game_power.py::test_governor_off_mode_jsonl_emits_control_disabled_classification`
- `tests/test_game_power.py::test_system_psi_only_adds_system_advisory_not_foreground_pressure`
- `tests/test_game_power.py::test_no_frame_outcome_telemetry_keeps_existing_restore_hysteresis`
- `tests/test_game_power.py::test_format_decision_jsonl_emits_classification_and_pressure_schema`
- `tests/test_game_power_profile.py::test_parse_legacy_game_power_jsonl_without_classification`
- `tests/test_game_power_profile.py::test_profile_post_run_classifies_target_sustained_with_pacing_proof`
- `tests/test_game_power_profile.py::test_profile_missing_p99_keeps_pacing_proof_unknown`
- `tests/test_game_power_profile.py::test_profile_aggregates_runtime_classification_counts`
- `tests/test_game_power_profile.py::test_aggregate_sums_runtime_classification_counts`
- `tests/test_game_power_profile.py::test_aggregate_marks_missing_ab_evidence_exploratory`
- `tests/test_game_power_profile.py::test_aggregate_rejects_mixed_scene_or_power_source_evidence`
- `tests/test_integration_assets.py::test_profile_wrapper_passes_only_fps_target_to_game_power_runner`
- `tests/test_integration_assets.py::test_profile_wrapper_records_ab_order_scene_power_and_thermal_evidence`

Focused tests should run first with `.venv/bin/python -m pytest <nodes> -q`,
then the required sweep must run:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

## Guarded Device Verification

Only after local tests pass, run guarded device checks when a foreground game is
available:

```bash
scripts/harness.py run game-power-device \
  --allow-requirement root-ssh \
  --allow-requirement handheld \
  --allow-requirement foreground-game
```

For A/B profile evidence:

```bash
PROFILE_GAME_POWER_CAPTURE_MODE=controlled \
PROFILE_GAME_POWER_TDPS="12 17 22 30" \
PROFILE_GAME_POWER_REPEATS=3 \
scripts/harness.py run game-power-profile-device \
  --allow-requirement root-ssh \
  --allow-requirement handheld \
  --allow-requirement foreground-game
```

If either guarded check is not run, the final report must say that hardware or
profile validation was not performed.

## Acceptance Criteria

- Current production action decisions are unchanged when no live frame outcome
  source exists.
- `target-sustained` cannot trigger runtime restore in v3.
- Pressure telemetry distinguishes foreground cgroup pressure, system pressure,
  unsupported pressure, and parse errors.
- Runtime JSONL includes classification and pressure without breaking legacy
  parser behavior.
- Profiler summaries include post-run target/pacing classification and runtime
  classification counts.
- Profile manifests include run order, scene, power-source, thermal, and
  cooldown/stabilization evidence before any A/B improvement claim.
- Decky still exposes only safe public mode intent.
- Required local harness passes after implementation.
- Any hardware/profile claim cites the guarded harness command and artifacts.

## Product And Rollout Metrics

V3 is considered ready to ship when local tests and the required sweep pass, and
the real-device smoke check confirms JSONL schema emission without changing
runtime actions.

For the smoke check, `game-power-device` must produce at least one runtime JSONL
row containing `classification` and `pressure` keys, and action counts must stay
equivalent to the v2 governor for the same no-frame-outcome scenario.

V3 is considered ready to inform a v4 closed-loop actuator only when guarded
profile artifacts show:

- at least 95% of runtime rows have a non-`unknown` primary classification in
  controlled profile runs;
- unsupported foreground pressure is reported explicitly and does not exceed
  25% of foreground-game rows on the target handheld;
- profile summaries include `post_run_classification`, primary counts, and
  advisory counts for every controlled run;
- action counts match the v2 governor baseline for equivalent no-frame-outcome
  runs, proving the observer layer did not change policy;
- at least one 12W/17W/22W/30W controlled matrix completes with restore-clean
  artifacts before any actuator design uses the data.

## Revision Changelog From Plan Review Iteration 1

1. **[MAJOR/A]** Removed unsafe runtime target-sustained restore.
   - Finding: target-sustained FPS could be caused by the active policy.
   - Change: target-sustained is post-run evidence only; runtime restore stays
     tied to existing negative package/GPU hysteresis.

2. **[MAJOR/A,B]** Removed static frame outcomes from runtime actuation.
   - Finding: CLI-injected avg FPS and p99 would be stale across samples.
   - Change: runtime CLI accepts target metadata only; MangoHud outcomes are
     computed after capture in the profiler.

3. **[MAJOR/A,B]** Added pressure scope/support semantics.
   - Finding: global PSI could be mistaken for foreground game pressure and
     missing PSI could be treated as zero.
   - Change: pressure signals now carry scope, source path, support state, null
     metrics, and advisory-only system pressure.

4. **[MAJOR/C]** Added a deterministic classifier table.
   - Finding: labels lacked thresholds and precedence.
   - Change: primary labels reuse existing governor thresholds; pressure
     advisories have explicit thresholds and source requirements.

5. **[MAJOR/C]** Added focused red/green gate list.
   - Finding: "focused TDD" was too vague.
   - Change: named exact test nodes for CLI, pressure, JSONL, classifier,
     restore safety, legacy parsing, post-run pacing proof, and wrapper wiring.

6. **[MAJOR/C]** Tightened A/B evidence rules.
   - Finding: imported MangoHud capture and uncontrolled scenes could
     contaminate claims.
   - Change: improvement claims require controlled capture, 3 repeats, fixed
     run settings, restore-clean artifacts, and aggregate compare at 12W, 17W,
     22W, and 30W.

7. **[MAJOR/C]** Clarified device evidence closure.
   - Finding: local evidence could be confused with hardware/profile evidence.
   - Change: local sweep validates logic only; guarded device/profile commands
     are required before any hardware/profile claim.

## Revision Changelog From Plan Review Iteration 2

1. **[MAJOR/A]** Fixed pressure schema mismatch.
   - Finding: `PressureSignal` required `resource`, but JSONL omitted it.
   - Change: Parent container key is canonical; removed duplicate `resource`
     field from the dataclass contract and specified parser handling.

2. **[MAJOR/B]** Added safe cgroup v2 resolver contract.
   - Finding: foreground cgroup pressure resolution lacked v2-line selection,
     containment, injectable roots, traversal rejection, and process-exit
     semantics.
   - Change: Added resolver rules, helper name, fallback unsupported signals,
     and focused resolver tests.

3. **[MAJOR/B]** Tightened A/B contamination controls.
   - Finding: controlled capture/repeats did not control order, scene, thermal
     state, power source, or cooldown.
   - Change: Added manifest requirements for randomized or paired order,
     same-scene evidence, AC/battery state, thermal evidence, and
     cooldown/stabilization.

4. **[MAJOR/C]** Defined runtime classification count schema.
   - Finding: profiler tests would have to invent field names and shapes.
   - Change: Added `classification_primary` and `classification_advisories`
     fields for `GamePowerLogSummary`, `RunSummary`, summary JSON, and aggregate
     output.

5. **[MAJOR/C]** Covered every-decision classification paths.
   - Finding: the off-mode governor path bypasses controller evaluation.
   - Change: Added `classify_game_power_sample()` helper contract and focused
     tests for off, observe, no foreground, and target mismatch paths.

6. **[MINOR/A,E]** Added post-run taxonomy and rollout metrics.
   - Finding: post-run labels and v3 success KPIs were implied.
   - Change: Added `post_run_classification` taxonomy and concrete criteria for
     shipping v3 and using its data to design v4.

## Revision Changelog From Plan Review Iteration 3

1. **[MAJOR/A,B,C]** Wired A/B evidence into comparison data.
   - Finding: order, scene, power-source, thermal, and cooldown evidence were
     manifest-only, so aggregate comparison could still accept legacy or
     incomplete runs.
   - Change: Added summarize CLI inputs, `RunSummary`, `summary.json`, and
     `PolicyAggregate` fields; required aggregate/compare to mark missing,
     mixed, or mismatched evidence exploratory/needs-evidence instead of
     `BETTER`.

2. **[REFUTED/A]** Kept classifier labels as non-actionable observations.
   - Finding: classifier labels might be mistaken for the current controller's
     activation predicate.
   - Disposition: Refuted by existing plan language that classification does not
     change `decision.action` and the controller keeps existing hysteresis and
     restore semantics.

3. **[MINOR/E]** Clarified smoke-check artifact.
   - Finding: rollout metrics did not name the JSONL completion condition.
   - Change: Required at least one guarded runtime JSONL row with
     `classification` and `pressure`, with action counts equivalent to the v2
     no-frame-outcome baseline.
