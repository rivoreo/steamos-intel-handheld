# Game Power v3 Runtime Telemetry And Classifier Design

## Goal

Implement the next bounded Game Power v3 slice: runtime-visible FPS target,
target-frametime, pressure, and classification telemetry, plus profiler-side
runtime classification aggregation, without adding new production cgroup,
uclamp, cpuset, affinity, sched_ext, or FPS-outcome actuators.

This slice moves the project toward the full FPS-targeted closed-loop scheduler
by making the controller's state observable and auditable. It deliberately does
not let new telemetry change runtime actions yet.

## Current State

The current `src/steamos_intel_handheld/game_power.py` runtime controller has:

- foreground Steam AppID detection from cgroup paths;
- RAPL package/core/uncore power windows;
- DRM fdinfo render busy;
- hysteresis for `gpu-priority-epp`, `gpu-priority-cpu-cap`, and `restore`;
- exact CPU EPP / max-frequency restore through `CpuPolicyActuator`;
- JSONL output with appid, action, reason, RAPL watts, PL1, and render busy.

The current profiler has:

- post-run FPS target fields from MangoHud summaries;
- CPU pressure peak summaries from wrapper-generated `cgroup-pressure.jsonl`;
- thread affinity, schedstat, process cgroup, background shaping advice;
- V3 A/B evidence-boundary support and scoped `BETTER` claim output.

Missing pieces for a closed-loop scheduler:

- runtime FPS target metadata is not visible in game-power JSONL;
- target frametime is not emitted by the runtime;
- runtime pressure lacks foreground-cgroup/system scope and unsupported-state
  semantics;
- controller decisions do not carry a deterministic classification or evidence
  payload;
- profiler summaries do not count runtime classification output;
- wrapper does not pass discovered FPS target metadata to the runtime runner.

## Non-Goals

- No runtime restore based on `target-sustained`.
- No runtime actual-FPS, p99, 1% low, or average frametime actuation from CLI
  arguments.
- No production foreground `uclamp.min`, background `uclamp.max`, `cpu.weight`,
  `cpu.max`, cpuset, hard affinity, sched_ext, or learned per-game cache.
- No Decky UI changes and no user-facing control for measured P/E-core
  constants or internal thresholds.
- No Decky/public API exposure of pressure source paths, classifier evidence,
  internal thresholds, measured constants, or new write controls.
- No FPS limiter control. `--fps-target` tags telemetry only; it does not set
  Steam/gamescope FPS, raise FPS, or change runtime policy.
- No relaxation of CPU policy, cgroup, service, or TDP restore semantics.

## Design Principles

- **Telemetry before control.** New target, pressure, and classification fields
  are observable evidence only in this slice.
- **No stale frame outcomes.** Runtime may know a target FPS and target frame
  budget, but actual FPS and frame-time percentiles remain post-run profiler
  evidence until a live source is implemented.
- **Scoped pressure.** Foreground cgroup pressure and system pressure are
  different signals. Missing or unreadable pressure is explicit unsupported
  evidence, never zero pressure.
- **Action compatibility.** With no live frame outcome source, existing
  `GamePowerController.evaluate()` action results must stay unchanged for the
  same RAPL/fdinfo samples.
- **Exact restore.** Existing CPUFreq writes and restore paths remain the only
  production actuator in this slice.

## Runtime Data Model

Add these dataclasses to `game_power.py`:

```python
@dataclass(frozen=True)
class FrameTargetTelemetry:
    fps_target: float | None = None
    source: str | None = None
    confidence: str | None = None

    @property
    def target_frame_ms(self) -> float | None: ...


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

Extend `GamePowerSample`:

```python
frame_target: FrameTargetTelemetry | None = None
pressure: PressureTelemetry | None = None
```

Extend `GamePowerDecision`:

```python
classification: GamePowerClassification | None = None
```

All new dataclass fields must have defaults so existing constructors and tests
remain source-compatible.

`FrameTargetTelemetry.target_frame_ms` returns `round(1000.0 / fps_target, 3)`
for finite positive targets and `None` otherwise, matching the existing
profiler `_target_frame_ms()` helper.

## CLI Contract

`steamos-intel-handheld-game-power` adds:

- `--fps-target FLOAT`
- `--fps-target-source TEXT`
- `--fps-target-confidence TEXT`

If `--fps-target` is provided and source/confidence are omitted, source defaults
to `manual` and confidence defaults to `medium`.

Invalid FPS targets are rejected by argument validation:

- `0`;
- negative values;
- `NaN`;
- positive or negative infinity.

`--fps-target-source` and `--fps-target-confidence` without `--fps-target` are
also rejected so JSONL never implies an active target that the runtime does not
know.

The CLI must not accept runtime frame outcome fields in this slice:

- no `--avg-fps`
- no `--p99-frametime-ms`
- no `--one-percent-low-fps`
- no `--actual-frametime-ms`

## Pressure Semantics

`SystemGamePowerObserver` accepts `cgroup_root`, defaulting to
`/sys/fs/cgroup`.

For each foreground-game sample:

1. Resolve the Steam game process cgroup v2 path under `cgroup_root`.
2. Read foreground `cpu.pressure`, `memory.pressure`, and `io.pressure` when
   available.
3. Emit unsupported foreground signals for missing, unreadable, invalid, or
   unsafe cgroup pressure paths.
4. Read optional system PSI from
   `SystemGamePowerObserver.proc_root / "pressure" / {cpu,memory,io}` and emit
   `scope="system"` signals. Never hard-code host `/proc` in helper code or
   tests.

Rows with no foreground game emit system pressure when available and emit no
foreground-cgroup pressure entries. They are excluded from foreground pressure
coverage ratios.

`GameProcess` adds `cgroup_text: str = ""` and `find_steam_game_processes()`
fills it with the raw `cgroup` file text. The default preserves existing
constructors while letting observer-level tests verify cgroup pressure wiring
without reparsing `/proc/<pid>/cgroup` outside `find_steam_game_processes()`.

Helper contracts:

```python
def parse_pressure_signal(
    resource: str,
    scope: str,
    source_path: str | None,
    text: str,
) -> PressureSignal
def read_pressure_signal(resource: str, scope: str, path: Path) -> PressureSignal
def resolve_cgroup_v2_path(cgroup_root: Path, cgroup_text: str) -> Path | None
```

Parser rules:

- `some avg10=2.50` maps to `some_avg10=2.5`.
- `full avg10=0.20` maps to `full_avg10=0.2`.
- Missing `full` keeps `full_avg10=None`.
- Parse errors return `supported=false`, null metrics, and a short error.
- Unknown pressure is never represented as `0.0`.

Cgroup resolver rules:

- Use only the line whose prefix is `0::`.
- The v2 cgroup path must be absolute and non-empty.
- Reject empty components, `.`, `..`, and traversal.
- Strip the leading `/`, join the remaining path components under
  `cgroup_root`, resolve, and require the result to remain inside the resolved
  `cgroup_root`. Do not join an absolute child path directly because pathlib
  would discard the intended root.
- Missing cgroup text, no v2 line, process exit, invalid cgroup path, or path
  containment failure all produce unsupported foreground pressure signals.

## Classification Contract

Add a pure helper:

```python
def classify_game_power_sample(
    config: GamePowerConfig,
    sample: GamePowerSample,
    *,
    controller_active: bool = False,
) -> GamePowerClassification:
    ...
```

The helper is still pure: it has no side effects and mutates no controller
state. `controller_active` is the controller pre-state for the sample being
evaluated, before hysteresis counters or `_active` are updated.

`GamePowerController.evaluate()` calls the helper with the current pre-state and
attaches the result to every returned decision. `GamePowerGovernor.run_once()`
also attaches classification in the off-mode branch because that path does not
call the controller.

Primary labels use existing controller thresholds:

| Priority | Condition | Primary | Confidence |
| --- | --- | --- | --- |
| 1 | mode is `off` | `control-disabled` | high |
| 2 | mode is `observe` | `observe-only` | high |
| 3 | no foreground AppID | `no-foreground-game` | high |
| 4 | target AppID set and foreground differs | `non-target-game` | high |
| 5 | missing package, PL1, or package watts | `insufficient-power-evidence` | low |
| 6 | package-bound, GPU activity, `controller_active=false`, and core share is missing or below `core_share_threshold` | `insufficient-cpu-contention-evidence` | medium |
| 7 | package-bound, GPU activity, and core share >= CPU-cap threshold | `gpu-package-bound-cpu-contention` | high |
| 8 | package-bound and GPU activity | `gpu-package-bound` | high |
| 9 | package-bound without GPU activity | `unknown-package-pressure` | medium |
| 10 | package below pressure threshold | `not-package-bound` | medium |

GPU activity stays exactly the current predicate:

- `uncore_share >= uncore_share_threshold`, or
- `render_busy >= render_busy_threshold`.

The action predicate must not be replaced by classification labels in this
slice. `classification.primary` must never be the sole source of truth for
actuation; `decision.action` remains authoritative. The test suite must include
a sample that classifies as `insufficient-cpu-contention-evidence` while the
decision remains `observe-only`, proving classification does not overrule the
existing hysteresis/action path.

Advisories:

| Signal | Scope | Threshold | Advisory |
| --- | --- | --- | --- |
| CPU PSI | foreground_cgroup | `some_avg10 >= 2.0` or `full_avg10 >= 0.5` | `foreground-cpu-pressure` |
| memory PSI | foreground_cgroup | `some_avg10 >= 1.0` or `full_avg10 >= 0.2` | `foreground-memory-pressure` |
| IO PSI | foreground_cgroup | `some_avg10 >= 1.0` or `full_avg10 >= 0.2` | `foreground-io-pressure` |
| any PSI | system | same resource threshold | `system-pressure-advisory` |

System pressure cannot become a foreground advisory without foreground cgroup
pressure.

The classification evidence object includes, when available:

- `package_pressure_ratio`
- `package_pressure_threshold`
- `core_share`
- `core_share_threshold`
- `uncore_share`
- `uncore_share_threshold`
- `render_busy`
- `render_busy_threshold`
- `pressure_scopes`
- `fps_target`
- `target_frame_ms`
- `controller_active`

Evidence must not include PIDs, cgroup paths, pressure source paths, measured
P/E-core constants, or write-control details.

## Runtime JSONL

`format_decision_jsonl()` keeps all existing top-level keys and adds:

- `fps_target`
- `fps_target_source`
- `fps_target_confidence`
- `target_frame_ms`
- `classification`
- `pressure`

Canonical pressure JSON shape:

```json
{
  "pressure": {
    "cpu": [
      {
        "scope": "foreground_cgroup",
        "source_path": "/sys/fs/cgroup/.../cpu.pressure",
        "supported": true,
        "some_avg10": 2.4,
        "full_avg10": 0.1,
        "error": null
      }
    ],
    "memory": [],
    "io": []
  }
}
```

The parent key is the resource. Individual pressure entries do not carry a
duplicate `resource` field.

Canonical classification JSON shape:

```json
{
  "classification": {
    "primary": "gpu-package-bound-cpu-contention",
    "advisories": ["foreground-cpu-pressure"],
    "confidence": "high",
    "evidence": {
      "package_pressure_ratio": 0.97,
      "package_pressure_threshold": 0.94,
      "core_share": 0.42,
      "core_share_threshold": 0.3,
      "uncore_share": 0.27,
      "uncore_share_threshold": 0.2,
      "render_busy": 0.86,
      "render_busy_threshold": 0.7,
      "pressure_scopes": ["foreground_cgroup", "system"],
      "fps_target": 45.0,
      "target_frame_ms": 22.222,
      "controller_active": false
    }
  }
}
```

Serializer rules:

- `classification` is always an object when present, never a string.
- `primary` is a non-empty string.
- `advisories` is a list of strings, sorted for stable JSON.
- `confidence` is `low`, `medium`, or `high`.
- `evidence` is a dict whose values are JSON primitives or lists of JSON
  primitives.

Profiler parser rules:

- Legacy rows without `classification` increment
  `classification_primary["unknown"]`.
- Malformed classification values also increment `unknown` and
  `classification_malformed`.
- Empty or malformed advisories do not increment advisory counts.
- Unknown primary strings are counted under their literal string; the parser is
  forward-compatible with future labels.

## Public Decky/API Sample Contract

Decky samples keep the existing public subset only:

```json
{
  "appid": "1091500",
  "action": "observe-only",
  "reason": "sample",
  "package_w": 22.0,
  "core_w": 7.0,
  "uncore_w": 9.0,
  "pl1_w": 22.0,
  "render_busy": 0.86
}
```

The Decky backend must drop all new runtime-private fields, including:

- `classification`;
- `pressure`;
- `fps_target`, `fps_target_source`, `fps_target_confidence`,
  `target_frame_ms`;
- `source_path`;
- classifier evidence;
- AB/profile evidence fields;
- raw measured policy constants;
- any write-control knobs.

The boundary is verified by backend tests using a private row that contains the
new fields and asserting that `_public_sample()` returns only the public keys.

## Profiler Summary Changes

`parse_game_power_jsonl()` counts runtime classification output:

- `classification_primary: dict[str, int] | None`
- `classification_advisories: dict[str, int] | None`

Legacy JSONL rows without `classification` increment
`classification_primary["unknown"]`. Missing or empty advisories do not
increment advisory counts.

`GamePowerLogSummary`, `RunSummary`, and `PolicyAggregate` add:

- primary classification counts;
- advisory classification counts;
- `fps_target_source_counts: dict[str, int] | None`;
- `fps_target_confidence_counts: dict[str, int] | None`;
- `runtime_telemetry_counts: RuntimeTelemetryCounts | None`;
- `classification_unknown_ratio: float | None`;
- `pressure_supported_ratio: float | None`;
- `pressure_unsupported_ratio: float | None`.

Aggregate output sums counts across included runs and recomputes ratios from
summed numerators and denominators, never by averaging per-run ratios.

Persisted telemetry count contract:

```python
@dataclass(frozen=True)
class RuntimeTelemetryCounts:
    foreground_runtime_rows: int = 0
    unknown_foreground_rows: int = 0
    foreground_pressure_signals: int = 0
    supported_foreground_pressure_signals: int = 0
    unsupported_foreground_pressure_signals: int = 0
```

`GamePowerLogSummary.runtime_telemetry_counts` is produced directly from
runtime JSONL parsing. `RunSummary.runtime_telemetry_counts` persists that value
to `summary.json`. `PolicyAggregate.runtime_telemetry_counts` is the sum of
included run counts. Aggregate ratios are computed only from these summed base
counts; the aggregate path must not reparse raw per-run JSONL and must not
average per-run ratios. The focused persistence test must write `summary.json`
and load it through `_load_run_summary()` or `run_aggregate()`, not only through
in-memory dataclasses.

`RunSummary` also adds:

- `pacing_proof: bool | None`
- `post_run_classification: str | None`
- `classification_malformed: int`

Post-run classification rules:

| Condition | `post_run_classification` |
| --- | --- |
| missing/invalid FPS target | `unknown` |
| missing average FPS | `unknown` |
| `fps_target_met is false` | `below-target` |
| target met and p99 <= 1.50 target frame ms and 1% low >= 0.80 target FPS | `target-sustained` |
| target met but pacing proof fails or is unavailable | `target-average-only` |

`target-sustained` remains post-run evidence only and cannot trigger runtime
restore in this slice.

This slice also tightens the existing profiler comparison helpers so
`target-sustained` has one meaning everywhere:

- `_run_target_sustained(run)` returns true only when
  `run.post_run_classification == "target-sustained"` or when equivalent
  target, p99, and 1% low proof is present on legacy in-memory test objects.
- `PolicyAggregate` adds `target_sustained_count` and
  `target_average_only_count`.
- `_aggregate_target_sustained(aggregate)` returns true only when
  `target_sustained_count == sample_count`.
- Aggregate `classification_malformed` is the sum of included run-level
  malformed counts, and aggregate tests must assert that sum.
- Existing `fps_target_met_count == sample_count` remains
  `target-average-met`, not `target-sustained`.
- `BETTER` target-power-saving claims must use the stricter sustained helper,
  so average FPS alone cannot create a sustained-target claim.

Rollout KPIs emitted by profiler summaries/aggregates:

- `classification_primary` and `classification_advisories`;
- `classification_malformed`;
- `classification_unknown_ratio`;
- `pressure_supported_ratio` and `pressure_unsupported_ratio`;
- `fps_target_source_counts` and `fps_target_confidence_counts`;
- `target_sustained_count` and `target_average_only_count`;
- local replay `action_delta_count` evidence for runtime slices that should not
  alter production actions.

KPI formulas:

- A foreground runtime row is a runtime JSONL row with a non-empty `appid`.
- `classification_unknown_ratio = unknown_foreground_rows /
  foreground_runtime_rows`. `unknown_foreground_rows` counts foreground rows
  whose classification is missing, malformed, or has primary `unknown`.
  `foreground_runtime_rows` counts all foreground runtime rows parsed from
  game-power JSONL. If the denominator is zero, the ratio is `None`.
- `pressure_supported_ratio = supported_foreground_pressure_signals /
  foreground_pressure_signals`.
- `pressure_unsupported_ratio = unsupported_foreground_pressure_signals /
  foreground_pressure_signals`.
- A foreground pressure signal is a `PressureSignal` with
  `scope == "foreground_cgroup"` on a foreground runtime row. System-pressure
  signals and no-foreground rows are excluded from these denominators. If the
  denominator is zero, both ratios are `None`.
- `fps_target_source_counts` and `fps_target_confidence_counts` count runtime
  JSONL rows whose `fps_target` is finite and positive. Missing source or
  confidence on those rows increments `unknown`.
- Local replay `action_delta_count` is the number of mismatched `action` values
  when the post-change `GamePowerController` evaluates fixed golden
  `GamePowerSample` sequences against pre-V3 expected outputs.
  `reason_delta_count` separately counts mismatched `reason` strings. The replay
  command fails when either count is non-zero.
  Golden coverage must include observe mode, activation hysteresis,
  `gpu-priority-epp`, `gpu-priority-cpu-cap`, restore hysteresis, target AppID
  mismatch, and missing power evidence. The accepted value is exactly `0`.
  Device validation does not claim true pre-V3 equivalence; it verifies current
  action-path reachability and restore on real hardware, while local replay
  owns the no-action-delta proof.

Action replay artifact schema:

```json
{
  "schema_version": "game-power-action-equivalence-v1",
  "scenario_count": 7,
  "sample_count": 18,
  "action_delta_count": 0,
  "reason_delta_count": 0,
  "scenarios": [
    {
      "name": "activation-hysteresis",
      "expected": ["observe-only", "gpu-priority-epp"],
      "actual": ["observe-only", "gpu-priority-epp"],
      "deltas": []
    }
  ]
}
```

The local proof is generated by
`steamos-intel-handheld-game-power-profile replay-action-equivalence
--output-json .cache/game-power/action-equivalence-replay.json`. The subcommand
fails if any scenario differs from the stored pre-V3 expected sequence. The
required local sweep must run this subcommand before success is reported, and
the JSON artifact is the `action-equivalence-replay-summary` evidence.

Graduation thresholds before a later actuator slice can use this telemetry:

- `classification_malformed == 0`;
- `classification_unknown_ratio <= 0.05` on foreground rows, unless the profile
  has fewer than 20 foreground rows and is explicitly marked smoke-only;
- `pressure_supported_ratio >= 0.80` on foreground pressure signals for
  hardware-supported cgroup v2 pressure;
- `action_delta_count == 0` in local replay;
- guarded profile artifacts include FPS target source/confidence counts for the
  targeted smoke.

## Wrapper Contract

`scripts/profile-game-power-on-device.sh` must pass discovered FPS target
metadata to the runtime game-power runner:

```bash
--fps-target "$FPS_TARGET"
--fps-target-source "$FPS_TARGET_SOURCE"
--fps-target-confidence "$FPS_TARGET_CONFIDENCE"
```

It must not pass average FPS, p99, 1% low, or post-run frame metrics to the
runtime runner. Those are computed after MangoHud capture.

Wrapper discovery must preserve confidence:

- manual `PROFILE_GAME_POWER_FPS_TARGET` -> `high`;
- gamescope command-line target -> `medium`;
- unknown/unlimited target -> no runtime `--fps-target` args and no misleading
  source/confidence on runtime JSONL.

## Machine-Checkable Validation Contract

Add a `steamos-intel-handheld-game-power-profile validate-runtime-telemetry`
subcommand that returns non-zero on contract failure and prints a compact JSON
verdict on success. Required options:

```bash
steamos-intel-handheld-game-power-profile validate-runtime-telemetry \
  --game-power-jsonl PATH \
  [--summary-json PATH] \
  [--require-classification] \
  [--require-pressure] \
  [--expect-fps-target FLOAT] \
  [--expect-fps-target-source TEXT] \
  [--expect-fps-target-confidence TEXT] \
  [--expect-target-frame-ms FLOAT] \
  [--action-replay-json PATH] \
  [--expect-action-delta-count INT] \
  [--require-classification-counts] \
  [--require-pressure-ratios]
```

Validation rules:

- `--require-classification` requires at least one runtime JSONL row with a
  canonical `classification` object.
- `--require-pressure` requires at least one runtime JSONL row with a canonical
  `pressure` object and at least one pressure entry or explicit unsupported
  foreground signal on a foreground row.
- Expected FPS-target options require every runtime row with a finite positive
  target to match the supplied target/source/confidence/target-frame-ms, and
  require at least one matching row.
- `--summary-json` with `--require-classification-counts` requires non-empty
  `classification_primary` and `classification_advisories` fields when
  advisories were present.
- `--summary-json` with `--require-pressure-ratios` requires
  `pressure_supported_ratio` and `pressure_unsupported_ratio` keys, allowing
  `null` only when there are zero foreground pressure signals.
- `--action-replay-json` requires the action replay artifact schema above.
  `--expect-action-delta-count 0` fails when the replay artifact is missing,
  malformed, or reports any other count.
- The success JSON includes `samples`, `foreground_samples`,
  `classification_samples`, `pressure_samples`,
  `fps_target_source_counts`, `fps_target_confidence_counts`, and
  `action_delta_count` when `--action-replay-json` is supplied.

`scripts/verify-game-power-on-device.sh` must invoke this validator for both
the observe and gpu-priority JSONL captures and fail before restore success is
reported if classification or pressure is missing. It must also run or capture
a CPU-cap variant with `VERIFY_GAME_POWER_CPU_CAP=on` and validate that at least
one JSONL row reaches `action == "gpu-priority-cpu-cap"` when CPU-cap is
enabled. The validator success JSON for this path includes
`cpu_cap_action_reached: true`.

`scripts/profile-game-power-on-device.sh` must invoke this validator after
each run is summarized and again for the aggregate/summary artifact. The
targeted smoke run passes `--expect-fps-target 40`,
`--expect-fps-target-source manual`, `--expect-fps-target-confidence high`, and
`--expect-target-frame-ms 25.0`. Its policy set must include
`gpu-priority-cpu-cap`, so the profile smoke exercises `off`, `gpu-priority`,
and `gpu-priority-cpu-cap`.

`harness.toml` evidence artifacts and markers:

- `local` adds `action-equivalence-replay-summary`. The required local command
  emits a line starting with `action-equivalence-replay-summary: ` followed by
  the replay JSON artifact path, and the harness run report records that marker
  under the local check.
- `game-power-device` adds `runtime-telemetry-contract-json`. The verifier emits
  `runtime-telemetry-contract-json: <path>` for the validator success JSON and
  the guarded harness report records that artifact for this check.
- `game-power-profile-device` adds
  `profile-runtime-telemetry-contract-json`. The profile wrapper emits
  `profile-runtime-telemetry-contract-json: <path>` for the targeted 12W / 40
  FPS validator success JSON and the guarded harness report records that
  artifact for this check.

`scripts/harness.py` must reject a check report for these three new V3 artifact
IDs when the matching marker is missing from command output or the collected run
report. Existing generic evidence artifacts keep their current behavior.

## Tests

Add or update focused tests before implementation:

- `tests/test_game_power.py::test_runtime_fps_target_cli_tags_samples_without_frame_outcome_actuation`
- `tests/test_game_power.py::test_pressure_parser_preserves_missing_full_as_none`
- `tests/test_game_power.py::test_pressure_reader_marks_missing_files_unsupported`
- `tests/test_game_power.py::test_resolve_cgroup_v2_path_requires_absolute_safe_path`
- `tests/test_game_power.py::test_resolve_cgroup_v2_path_strips_leading_slash_under_root`
- `tests/test_game_power.py::test_resolve_cgroup_v2_path_rejects_traversal`
- `tests/test_game_power.py::test_system_observer_attaches_foreground_and_system_pressure`
- `tests/test_game_power.py::test_system_observer_marks_exited_process_pressure_unsupported`
- `tests/test_game_power.py::test_classification_table_emits_gpu_package_bound_without_changing_action`
- `tests/test_game_power.py::test_classification_uses_controller_active_state_for_core_share_gate`
- `tests/test_game_power.py::test_classification_table_covers_observe_no_foreground_and_target_mismatch`
- `tests/test_game_power.py::test_governor_off_mode_jsonl_emits_control_disabled_classification`
- `tests/test_game_power.py::test_system_psi_only_adds_system_advisory_not_foreground_pressure`
- `tests/test_game_power.py::test_no_frame_outcome_telemetry_keeps_existing_restore_hysteresis`
- `tests/test_game_power.py::test_v3_telemetry_replay_preserves_pre_v3_action_sequence`
- `tests/test_game_power.py::test_classification_evidence_excludes_private_identifiers_and_write_knobs`
- `tests/test_game_power.py::test_format_decision_jsonl_emits_classification_and_pressure_schema`
- `tests/test_game_power.py::test_fps_target_rejects_non_positive_nan_and_infinite_values`
- `tests/test_game_power_profile.py::test_parse_legacy_game_power_jsonl_without_classification`
- `tests/test_game_power_profile.py::test_parse_malformed_classification_counts_unknown_and_malformed`
- `tests/test_game_power_profile.py::test_runtime_kpi_ratios_use_foreground_pressure_signal_denominators`
- `tests/test_game_power_profile.py::test_runtime_telemetry_counts_persist_for_weighted_aggregate_ratios`
- `tests/test_game_power_profile.py::test_runtime_fps_target_source_and_confidence_counts_aggregate`
- `tests/test_game_power_profile.py::test_replay_action_equivalence_outputs_zero_delta_artifact`
- `tests/test_game_power_profile.py::test_validate_runtime_telemetry_fails_missing_or_nonzero_action_replay`
- `tests/test_game_power_profile.py::test_profile_post_run_classifies_target_sustained_with_pacing_proof`
- `tests/test_game_power_profile.py::test_profile_missing_p99_keeps_pacing_proof_unknown`
- `tests/test_game_power_profile.py::test_target_average_only_does_not_count_as_target_sustained`
- `tests/test_game_power_profile.py::test_profile_aggregates_runtime_classification_counts`
- `tests/test_game_power_profile.py::test_aggregate_sums_runtime_classification_counts`
- `tests/test_game_power_profile.py::test_validate_runtime_telemetry_requires_classification_pressure_and_target`
- `tests/test_game_power_profile.py::test_validate_runtime_telemetry_requires_cpu_cap_action_when_requested`
- `tests/test_decky_plugin_backend.py::test_game_power_backend_sample_once_returns_public_subset`
- `tests/test_integration_assets.py::test_profile_wrapper_passes_only_fps_target_metadata_to_game_power_runner`
- `tests/test_integration_assets.py::test_verify_game_power_device_validates_runtime_telemetry_contract`
- `tests/test_integration_assets.py::test_profile_wrapper_validates_runtime_telemetry_contract`
- `tests/test_harness_manifest.py::test_v3_evidence_artifacts_are_mapped_to_specific_checks`
- `tests/test_harness_manifest.py::test_harness_rejects_missing_v3_evidence_markers`

Then run:

```bash
.venv/bin/python -m pytest <focused-nodes> -q
scripts/harness.py sweep required --report .cache/harness/required.json
```

## Device Validation

After local verification, install to the handheld and run guarded harness checks:

```bash
scripts/install-on-device.sh root@10.100.0.19
scripts/harness.py run game-power-device \
  --allow-guarded \
  --allow-requirement root-ssh \
  --allow-requirement handheld \
  --allow-requirement foreground-game \
  --report .cache/harness/game-power-device.json
VERIFY_GAME_POWER_CPU_CAP=on scripts/harness.py run game-power-device \
  --allow-guarded \
  --allow-requirement root-ssh \
  --allow-requirement handheld \
  --allow-requirement foreground-game \
  --report .cache/harness/game-power-device-cpu-cap.json
PROFILE_GAME_POWER_TDPS=12 PROFILE_GAME_POWER_FPS_TARGET=40 \
  PROFILE_GAME_POWER_POLICIES="off gpu-priority gpu-priority-cpu-cap" \
  scripts/harness.py run game-power-profile-device \
  --allow-guarded \
  --allow-requirement root-ssh \
  --allow-requirement handheld \
  --allow-requirement foreground-game \
  --report .cache/harness/game-power-profile-device-12w.json
```

For this slice, the device smoke evidence must include:

- at least one `observe` or `gpu-priority` JSONL row with `classification`;
- at least one row with `pressure`;
- FPS-target metadata and target frame ms in the targeted 12W profile smoke
  (`40 FPS`, `manual`, `high`, `25.0 ms`);
- non-empty profiler aggregate classification counts;
- pressure supported/unsupported ratios;
- validator success JSON for runtime and profile telemetry contracts;
- existing action behavior still reaches the same `gpu-priority-epp` /
  `gpu-priority-cpu-cap` / restore paths under the same no-frame-outcome
  evidence model;
- the CPU-cap guarded run reports `cpu_cap_action_reached: true`;
- final CPU policy restore remains clean.

## Acceptance Criteria

- Current production action decisions stay unchanged when no live frame outcome
  source exists.
- Runtime JSONL exposes FPS target, target frame ms, pressure, and
  classification.
- Machine-checkable validators fail if required runtime/profile telemetry
  fields are absent.
- Pressure telemetry distinguishes foreground cgroup, system pressure,
  unsupported pressure, and parse errors.
- System PSI alone cannot emit foreground pressure advisories.
- `target-sustained` is profiler-only and cannot trigger runtime restore.
- `target-sustained` requires FPS target, p99, and 1% low proof; average-FPS
  target success alone is reported as `target-average-only` and cannot support
  a sustained-target `BETTER` claim.
- Profiler summaries and aggregates include runtime classification counts.
- Wrapper passes only FPS target, source, and confidence metadata, not post-run
  frame outcomes, to the runtime runner.
- Decky still receives only its explicit sanitized public sample subset, with
  tests proving new private runtime fields are stripped.
- Local replay action-equivalence proof reports `action_delta_count == 0`.
- Required local harness passes.
- Guarded device `game-power-device` and `game-power-profile-device` harness
  checks pass after install, including a targeted 12W / 40 FPS profile smoke
  run and a CPU-cap action-path smoke.
