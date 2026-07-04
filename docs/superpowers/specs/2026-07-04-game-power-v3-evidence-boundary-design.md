# Game Power v3 Evidence Boundary Design

## Goal

Resolve the four blockers from the prior V3 Plan Review so the first V3
implementation can proceed safely:

- A/B run-order evidence must be carried into aggregate comparison.
- power-source and thermal evidence must be strong enough to prevent
  non-exploratory overclaims.
- cooldown/stabilization must be enforced and proven, not only named.
- Decky `sample_once()` must not expose internal classifier, pressure, cgroup,
  or A/B evidence fields.

This document is a corrective bounded surface. It extends the existing V3
observer/classifier design; it does not add production uclamp, cgroup, affinity,
sched_ext, or FPS-based restore actuators.

## Non-Goals

- No new Decky UI.
- No user-facing classifier display.
- No production scheduling action from frame outcome telemetry.
- No changes to measured P-core/E-core frequency constants.
- No relaxation of restore checks.

## A/B Evidence Data Model

Add a compact evidence model to `game_power_profile.py`.

```python
@dataclass(frozen=True)
class AbEvidence:
    order_strategy: str | None = None
    run_order: str | None = None
    order_valid: bool = False
    candidate_policy: str | None = None
    invocation_id: str | None = None
    pair_id: str | None = None
    pair_position: str | None = None
    scene_evidence: str | None = None
    power_source_state: str | None = None
    power_source_start_state: str | None = None
    power_source_pre_run_state: str | None = None
    power_source_end_state: str | None = None
    power_source_samples: list[str] | None = None
    power_source_stable: bool = False
    thermal_start_c: float | None = None
    thermal_end_c: float | None = None
    thermal_unavailable: bool = False
    thermal_source_kind: str | None = None
    thermal_source_id: str | None = None
    thermal_source_label: str | None = None
    run_started_at_s: float | None = None
    run_ended_at_s: float | None = None
    cooldown_rule: str | None = None
    cooldown_enforced: bool = False
    cooldown_started_at_s: float | None = None
    cooldown_ended_at_s: float | None = None
    cooldown_elapsed_s: float | None = None
```

`RunSummary` and `summary.json` add:

```json
{
  "ab_order_strategy": "paired-baseline",
  "ab_run_order": "off,gpu-priority,off",
  "ab_order_valid": true,
  "ab_candidate_policy": "gpu-priority",
  "ab_invocation_id": "20260704T120000Z-7f3a",
  "ab_pair_id": "20260704T120000Z-7f3a-r1-tdp22-candidate-gpu-priority",
  "ab_pair_position": "candidate",
  "scene_evidence": "save:dogtown-market-static",
  "power_source_state": "ac",
  "power_source_start_state": "ac",
  "power_source_pre_run_state": "ac",
  "power_source_end_state": "ac",
  "power_source_samples": ["ac", "ac", "ac"],
  "power_source_stable": true,
  "thermal_start_c": 61.0,
  "thermal_end_c": 63.5,
  "thermal_unavailable": false,
  "thermal_source_kind": "cpu-package",
  "thermal_source_id": "hwmon:coretemp:Package id 0",
  "thermal_source_label": "Package id 0",
  "run_started_at_s": 12405.3,
  "run_ended_at_s": 12465.3,
  "cooldown_rule": "fixed-60s",
  "cooldown_enforced": true,
  "cooldown_started_at_s": 12345.0,
  "cooldown_ended_at_s": 12405.0,
  "cooldown_elapsed_s": 60.0
}
```

`PolicyAggregate` adds:

```python
ab_order_strategy: str | None
ab_run_orders: list[str]
ab_order_valid_count: int
ab_candidate_policy: str | None
ab_invocation_ids: list[str]
ab_pair_ids: list[str]
ab_pair_position_counts: dict[str, int]
ab_pair_position_counts_by_id: dict[str, dict[str, int]]
scene_evidence: str | None
power_source_state: str | None
power_source_start_state: str | None
power_source_pre_run_state: str | None
power_source_end_state: str | None
power_source_sample_signatures: list[str]
power_source_stable_count: int
thermal_start_c_median: float | None
thermal_end_c_median: float | None
thermal_unavailable_count: int
thermal_source_kind: str | None
thermal_source_id: str | None
thermal_source_label: str | None
thermal_pair_readings_by_id: dict[str, dict[str, dict[str, float | None]]]
thermal_pair_evidence_complete: bool
run_interval_by_pair_id: dict[str, dict[str, dict[str, float | None]]]
cooldown_interval_by_pair_id: dict[str, dict[str, dict[str, float | None]]]
cooldown_interval_evidence_complete: bool
cooldown_rule: str | None
cooldown_enforced_count: int
cooldown_started_at_s_min: float | None
cooldown_ended_at_s_max: float | None
cooldown_elapsed_s_median: float | None
cooldown_run_gap_s_max: float | None
pair_run_order_valid: bool
ab_evidence_complete: bool
```

`PolicyComparison` adds explicit output fields so aggregate JSON has a stable
claim boundary:

```python
thermal_pair_start_delta_max_c: float | None = None
thermal_pair_end_delta_max_c: float | None = None
thermal_pair_mismatch_count: int = 0
cooldown_run_gap_s_max: float | None = None
cooldown_interval_reuse_count: int = 0
claim_scope: dict[str, object] | None = None
human_summary: str | None = None
```

Aggregate JSON writes these fields under the existing
`comparisons[].comparison` object. Exact paths are:

```text
comparisons[].comparison.thermal_pair_start_delta_max_c
comparisons[].comparison.thermal_pair_end_delta_max_c
comparisons[].comparison.thermal_pair_mismatch_count
comparisons[].comparison.cooldown_run_gap_s_max
comparisons[].comparison.cooldown_interval_reuse_count
comparisons[].comparison.claim_scope
comparisons[].comparison.human_summary
```

`claim_scope` must be non-null when and only when
`comparisons[].comparison.verdict == "better"`. For all other verdicts it is
`null`. Focused tests must assert these exact JSON paths.

## A/B Evidence Rules

`aggregate_run_summaries()` computes aggregate-local evidence completeness. It
must mark `ab_evidence_complete=false` when any of these is true:

- any run is missing `ab_order_strategy`;
- `ab_order_strategy` is not `paired-baseline` in the first V3 implementation;
- any run is missing `ab_run_order`;
- any run has `ab_order_valid=false`;
- any run is missing `ab_candidate_policy`, `ab_invocation_id`, `ab_pair_id`, or
  `ab_pair_position` for `paired-baseline`;
- runs in one aggregate have mixed `ab_candidate_policy`;
- any `ab_pair_position` is not one of `baseline-before`, `candidate`, or
  `baseline-after`;
- a candidate-policy aggregate contains any `ab_pair_position` other than
  `candidate`;
- an `off` baseline aggregate contains any `ab_pair_position` other than
  `baseline-before` or `baseline-after`;
- runs in one aggregate have mixed `ab_order_strategy`;
- runs in one aggregate have incompatible `ab_run_order` signatures for the
  selected strategy;
- `scene_evidence` is missing or mixed;
- `power_source_state`, `power_source_start_state`,
  `power_source_pre_run_state`, or `power_source_end_state` is missing, mixed,
  `mixed`, or `unknown`;
- `power_source_samples` is missing, does not contain exactly three ordered
  samples, contains `mixed` or `unknown`, contains more than one distinct known
  state, or does not satisfy
  `samples[0] == power_source_start_state`,
  `samples[1] == power_source_pre_run_state`, and
  `samples[2] == power_source_end_state`;
- `power_source_stable=false` for any run;
- thermal evidence is missing;
- `thermal_unavailable=true` for any controlled run;
- `thermal_unavailable=true` is mixed with real thermal readings in the same
  aggregate;
- `thermal_source_kind`, `thermal_source_id`, or `thermal_source_label` is
  missing for any run with real thermal readings;
- thermal source identity is mixed within one aggregate;
- `thermal_pair_readings_by_id` cannot record a real `thermal_start_c` and
  `thermal_end_c` for every run's `ab_pair_id` and `ab_pair_position`;
- `run_started_at_s` or `run_ended_at_s` is missing for any controlled run;
- `run_ended_at_s <= run_started_at_s`;
- `cooldown_interval_by_pair_id` cannot record `cooldown_started_at_s`,
  `cooldown_ended_at_s`, `cooldown_elapsed_s`, and `cooldown_run_gap_s` for
  every run's `ab_pair_id` and `ab_pair_position`;
- `cooldown_rule` is missing or mixed;
- `cooldown_rule` is not `fixed-60s` in the first V3 implementation;
- `cooldown_enforced=false` for any controlled run;
- `cooldown_started_at_s`, `cooldown_ended_at_s`, or `cooldown_elapsed_s` is
  missing for a fixed-time cooldown rule;
- `cooldown_ended_at_s < cooldown_started_at_s`;
- `cooldown_elapsed_s` does not match the start/end monotonic delta within
  1.0 second;
- `cooldown_elapsed_s < 60.0` for `fixed-60s`;
- `run_started_at_s < cooldown_ended_at_s`;
- `run_started_at_s - cooldown_ended_at_s > 5.0` seconds for `fixed-60s`.

## Pairwise Comparison Evidence Rules

`compare_policy_aggregates()` is the first layer with both baseline and
candidate aggregates, so it owns pairwise evidence checks. Before any metric
improvement branch, it must validate:

- both aggregates have `ab_evidence_complete=true`;
- baseline policy is `off`;
- both aggregates have exactly one unique `ab_run_order` signature;
- baseline and candidate `ab_run_order` signatures match exactly;
- for `paired-baseline`, the shared signature is exactly
  `off,<candidate-policy>,off`, where `<candidate-policy>` matches the
  candidate aggregate policy;
- both aggregates have matching `ab_candidate_policy` equal to the candidate
  aggregate policy;
- candidate and baseline `ab_pair_ids` sets match exactly;
- for `paired-baseline`, baseline `ab_pair_position_counts` contain exactly
  one `baseline-before` and one `baseline-after` for every candidate pair id,
  candidate `ab_pair_position_counts` contain exactly one `candidate` for every
  candidate pair id, using `ab_pair_position_counts_by_id`; baseline sample
  count is exactly twice candidate sample count;
- for every candidate pair id, the combined baseline and candidate aggregate
  `thermal_pair_readings_by_id` maps contain real start and end readings for
  `baseline-before`, `candidate`, and `baseline-after`;
- baseline and candidate thermal source identities match exactly across
  `thermal_source_kind`, `thermal_source_id`, and `thermal_source_label`;
- for every candidate pair id, the candidate `thermal_start_c` differs by no
  more than 5.0 C from both the `baseline-before` start reading and the
  `baseline-after` start reading;
- for every candidate pair id, the candidate `thermal_end_c` differs by no more
  than 5.0 C from both the `baseline-before` end reading and the
  `baseline-after` end reading;
- `thermal_pair_evidence_complete=true` for both aggregates;
- for every candidate pair id, the combined baseline and candidate aggregate
  `run_interval_by_pair_id` maps contain real `run_started_at_s` and
  `run_ended_at_s` values for `baseline-before`, `candidate`, and
  `baseline-after`;
- for every candidate pair id, the combined baseline and candidate aggregate
  `cooldown_interval_by_pair_id` maps contain real `cooldown_started_at_s`,
  `cooldown_ended_at_s`, `cooldown_elapsed_s`, and `cooldown_run_gap_s` values
  for `baseline-before`, `candidate`, and `baseline-after`;
- for every candidate pair id, run intervals are strictly monotonic in the
  paired-baseline order: `baseline-before` ends before `candidate` starts, and
  `candidate` ends before `baseline-after` starts;
- no cooldown interval is reused across two measured runs in the same pair;
- no cooldown interval overlaps its measured run;
- the candidate cooldown starts after the `baseline-before` run ends, and the
  `baseline-after` cooldown starts after the candidate run ends;
- every measured run starts within 5.0 seconds after its own cooldown interval
  ends;
- `cooldown_interval_evidence_complete=true` for both aggregates;
- `ab_order_strategy`, `scene_evidence`, and `power_source_state` match across
  baseline and candidate;
- baseline and candidate thermal start medians differ by no more than 5.0 C;
- baseline and candidate thermal end medians differ by no more than 5.0 C.

The aggregate median thermal checks are a broad sanity check only. They must not
substitute for pair-scoped thermal fairness. A comparison with matching
aggregate medians but any pair where the candidate is outside the 5.0 C
threshold against either bracketing baseline is incomplete and cannot return
`BETTER`.

`compare_policy_aggregates()` computes pair-scoped thermal and cooldown
diagnostics while it has both sides available. The aggregate comparison report
stores `thermal_pair_start_delta_max_c`, `thermal_pair_end_delta_max_c`,
`thermal_pair_mismatch_count`, `cooldown_run_gap_s_max`, and
`cooldown_interval_reuse_count` under `comparisons[].comparison`. These are not
aggregate-local fields because a single aggregate cannot know the bracketing
deltas and reuse checks by itself.

For any `PolicyVerdict.BETTER` result, the aggregate comparison report also
includes a `claim_scope` object. It must contain:

```json
{
  "appid": 1091500,
  "scene_evidence": "save:dogtown-market-static",
  "baseline_policy": "off",
  "candidate_policy": "gpu-priority",
  "tdp_w": 22,
  "duration_s": 60,
  "warmup_s": 10,
  "poll_s": 2,
  "fps_target": 40,
  "fps_target_source": "manual",
  "pair_count": 3,
  "ab_order_strategy": "paired-baseline",
  "ab_run_order": "off,gpu-priority,off",
  "power_source_state": "ac",
  "thermal_source_kind": "cpu-package",
  "thermal_source_id": "hwmon:coretemp:Package id 0",
  "thermal_pair_start_delta_max_c": 2.0,
  "thermal_pair_end_delta_max_c": 2.5,
  "cooldown_rule": "fixed-60s",
  "cooldown_elapsed_s_median": 60.1,
  "evidence_boundary": "scene/profile-specific controlled result; not a general performance claim",
  "hardware_claim_requires": "game-power-profile-device guarded foreground-game artifacts for this captured profile only; not sufficient for hardware-wide, game-wide, release-note, or default-policy performance claims without a separate claim plan"
}
```

README text and profiler output examples must describe `BETTER` as a
scene/profile-specific controlled result bound to the fields in `claim_scope`.
They must not describe it as a general performance win.

Every human-readable CLI, report, README, or generated example surface that
prints or documents a positive `BETTER` verdict must put the boundary text
adjacent to the verdict. The required wording is:

```text
BETTER (scene/profile-specific controlled result; not a general performance claim)
```

The same line or paragraph must also include the guarded-artifact caveat:

```text
guarded foreground-game artifacts are required for this captured profile only
```

`PolicyComparison.human_summary` is the canonical human-readable string for
CLI/report output and must contain both phrases whenever verdict is `BETTER`.
Focused tests must fail if a human-readable `BETTER` output lacks either
adjacent phrase.

If any aggregate-local or pairwise evidence check fails, it returns
`PolicyVerdict.INCONCLUSIVE` with a reason beginning:

```text
A/B evidence incomplete:
```

The reason must include this phrase:

```text
exploratory only; cannot support a BETTER claim
```

It must not return `BETTER` for incomplete aggregate-local or pairwise A/B
evidence.

## Run Order Validation

The wrapper computes `ab_run_order` per `(repeat, tdp, cpu-cap-variant group)`.

For `paired-baseline`, valid order is:

```text
off,<candidate>,off
```

The candidate may be `gpu-priority`, `gpu-priority-cpu-cap`,
`gpu-priority-bg-weight`, or `gpu-priority-bg-uclamp`. The wrapper must run the
sequence as a group instead of iterating all policies linearly. It records the
same `ab_run_order` value in each run's summarize call.

For every `paired-baseline` group, the wrapper records:

- `ab_candidate_policy=<candidate>`;
- one stable `ab_invocation_id` for the whole profile invocation;
- one stable `ab_pair_id` shared by the three measured runs in that group;
- `ab_pair_position=baseline-before` for the first `off` run;
- `ab_pair_position=candidate` for the candidate run;
- `ab_pair_position=baseline-after` for the second `off` run.

The `ab_pair_id` must be unique across repeat/TDP/candidate/variant groups and
stable across the three runs in one group. It must not depend on output
directory names. The wrapper creates one `ab_invocation_id` per profile
invocation and includes it in every pair id, then derives the remaining suffix
from repeat number, TDP, candidate policy, and effective CPU-cap variant label.
This prevents collisions when multiple controlled captures are written to the
same default profile root.

For non-exploratory paired-baseline claims, one profile invocation compares
`off` against exactly one candidate policy. The first V3 implementation rejects
controlled paired-baseline runs when `PROFILE_GAME_POWER_POLICIES` contains
more than one non-`off` candidate. If that candidate is
`gpu-priority-cpu-cap`, the first V3 implementation also rejects more than one
effective CPU-cap variant in the same controlled invocation. Later versions can
add explicit pair grouping instead of this rejection.

For `randomized`, the wrapper would record the actual comma-separated policy
order used for that repeat/TDP group. The first V3 implementation supports only
`paired-baseline`; it must reject
`PROFILE_GAME_POWER_AB_ORDER_STRATEGY=randomized` instead of silently using a
fixed order. Directly summarized or imported `randomized` evidence remains
parseable for forward compatibility, but `aggregate_run_summaries()` must mark
it incomplete until randomized ordering is implemented end to end.

## Aggregate Grouping Contract

`aggregate` must group A/B runs by candidate identity, not only by appid, TDP,
timing, policy, and tunables. The group key for controlled captures must include
`ab_order_strategy`, `ab_candidate_policy`, and the unique `ab_run_order`
signature. This lets multiple one-candidate invocations share the default
profile root without mixing `off` baselines from different candidate policies.

For `paired-baseline`, aggregate comparison must reject a group as incomplete
when any of these is true:

- an `off` baseline group lacks a matching candidate aggregate with the same
  `ab_candidate_policy` and `ab_run_order`;
- a candidate aggregate lacks a matching `off` baseline aggregate with the same
  `ab_candidate_policy` and `ab_run_order`;
- the set of `ab_pair_id` values differs between baseline and candidate after
  accounting for the two baseline positions per pair;
- any `ab_pair_id` has no `baseline-before`, no `candidate`, or no
  `baseline-after` run;
- any `ab_pair_id` has more than one run for the same `ab_pair_position`;
- baseline sample count is not exactly twice candidate sample count;
- any `ab_pair_id` lacks thermal start/end readings for `baseline-before`,
  `candidate`, or `baseline-after`;
- any candidate run's thermal start/end reading differs by more than 5.0 C from
  either its `baseline-before` or `baseline-after` reading for the same
  `ab_pair_id`, even when aggregate thermal medians match;
- any paired run lacks run interval evidence or violates monotonic
  `baseline-before,candidate,baseline-after` interval ordering;
- any paired run lacks cooldown interval evidence, reuses a cooldown interval,
  overlaps cooldown with its measured run, or starts a later pair-position
  cooldown before the previous measured run ended.

The README aggregate examples must show one aggregate command per candidate, or
a single aggregate root only when the aggregate implementation groups by
`ab_candidate_policy` and `ab_run_order` as above. The first V3 documentation
uses one aggregate command per candidate to make the supported workflow obvious.

`aggregate` must not silently omit A/B groups when one side is missing. The JSON
report adds:

```json
{
  "incomplete_groups": [
    {
      "baseline_policy": "off",
      "candidate_policy": "gpu-priority",
      "ab_candidate_policy": "gpu-priority",
      "ab_run_order": "off,gpu-priority,off",
      "missing_side": "baseline",
      "verdict": "inconclusive",
      "reason": "A/B evidence incomplete: missing matching baseline group; exploratory only; cannot support a BETTER claim"
    }
  ]
}
```

It emits an `incomplete_groups` entry when a candidate group lacks a matching
baseline group, or an `off` baseline group lacks a matching candidate group.
The normal `comparisons` array remains reserved for cases where both sides have
aggregate records to compare.

## Power And Thermal Evidence

`power_source_state` values:

- `ac`
- `battery`
- `unknown`
- `mixed`

The wrapper samples power source before cooldown starts, immediately before the
measured run starts, and after the measured run ends. It writes the exact
sequence to `power_source_samples`. `power_source_stable=true` only when every
observed value is known and identical. `power_source_state` is that stable
value; if samples differ, it is `mixed`; if any sample cannot be read, it is
`unknown`.

For controlled captures, `power_source_samples` must contain exactly three
ordered samples:

1. before cooldown starts;
2. immediately before the measured run starts;
3. after the measured run ends.

The first sample must match `power_source_start_state`, the second must match
`power_source_pre_run_state`, and the third must match
`power_source_end_state`. Any missing, unknown, mixed, extra, or misaligned
sample makes the run exploratory-only.

`unknown` and `mixed` are allowed in raw summaries but make
`ab_evidence_complete=false`. They can be used for exploratory reports only.

Thermal evidence rules:

- If a CPU package or platform temperature sensor is available, record
  `thermal_start_c` before the run and `thermal_end_c` after the run.
- For real thermal readings, record `thermal_source_kind`,
  `thermal_source_id`, and `thermal_source_label`. Source kind is
  `cpu-package`, `platform`, or `other`; source id is a stable sensor identity
  such as `hwmon:<driver>:<label>` or a sysfs path when no better identity is
  available; source label is the human-readable sensor label.
- If no thermal source is readable, record `thermal_unavailable=true`.
- Missing thermal evidence is incomplete.
- Mixed unavailable/real readings in one aggregate are incomplete.
- Missing or mixed thermal source identity is incomplete.
- Non-exploratory comparison requires the same thermal source identity within
  each aggregate and across baseline/candidate aggregates.
- For non-exploratory comparison, baseline and candidate aggregate thermal
  medians must differ by no more than 5.0 C for both start and end readings.
- For non-exploratory paired-baseline comparison, each candidate run must also
  be thermally comparable with its own bracketing baselines. For every
  `ab_pair_id`, candidate start/end readings must be within 5.0 C of both the
  `baseline-before` and `baseline-after` readings. This pair-scoped rule is
  stricter than the aggregate median rule and runs before any metric
  improvement branch.
- `thermal_unavailable=true` on any run makes `ab_evidence_complete=false` for
  non-exploratory improvement claims. It is still recorded so reports can
  distinguish explicit sensor absence from missing data.

## Cooldown Enforcement

`cooldown_rule` values:

- `fixed-60s`
- `none`
- `return-to-<temp>C`, reserved for a future implementation

For controlled A/B claims, `none` is incomplete.

The first V3 implementation supports only `fixed-60s` for controlled captures.
It must reject `return-to-<temp>C` until a later plan defines a maximum wait,
timeout evidence, and threshold-met proof. Legacy or imported summaries with
`none` or `return-to-<temp>C` remain parseable but are incomplete for A/B
claims.

The wrapper enforces cooldown before every measured run in a compared group,
including the first baseline run. This makes each run's thermal starting point
explicit rather than relying on implicit warmup state.

For `fixed-60s`:

- the wrapper must sleep at least 60 seconds before each measured run in a
  compared group;
- summarize receives `--run-started-at-s` and `--run-ended-at-s` from the same
  monotonic clock as cooldown timestamps;
- summarize receives `--cooldown-enforced true`;
- summarize receives `--cooldown-started-at-s` and
  `--cooldown-ended-at-s` from one monotonic clock;
- summarize receives `--cooldown-elapsed-s` with the measured elapsed wait.

`run_started_at_s` must be greater than or equal to `cooldown_ended_at_s` and no
more than 5.0 seconds after it. `run_ended_at_s` must be greater than
`run_started_at_s`. For each `ab_pair_id`, the run intervals must preserve the
monotonic pair order `baseline-before`, `candidate`, `baseline-after`, and each
position must have a distinct cooldown interval.

The aggregate stores each cooldown interval in `cooldown_interval_by_pair_id`
using the same pair id and position keys as `run_interval_by_pair_id`. Each
entry records:

```json
{
  "cooldown_started_at_s": 12345.0,
  "cooldown_ended_at_s": 12405.0,
  "cooldown_elapsed_s": 60.0,
  "cooldown_run_gap_s": 0.3
}
```

For `candidate` and `baseline-after`, the cooldown interval must start after
the previous measured run ended. Reusing the same cooldown interval for two
positions, overlapping cooldown with a measured run, or missing any pair
position makes the comparison exploratory-only.

## Summarize CLI Contract

`steamos-intel-handheld-game-power-profile summarize` adds:

```text
--ab-order-strategy paired-baseline|randomized
--ab-run-order TEXT
--ab-order-valid true|false
--ab-candidate-policy TEXT
--ab-invocation-id TEXT
--ab-pair-id TEXT
--ab-pair-position baseline-before|candidate|baseline-after
--scene-evidence TEXT
--power-source-state ac|battery|mixed|unknown
--power-source-start-state ac|battery|unknown
--power-source-pre-run-state ac|battery|unknown
--power-source-end-state ac|battery|unknown
--power-source-samples CSV
--power-source-stable true|false
--thermal-start-c FLOAT
--thermal-end-c FLOAT
--thermal-unavailable true|false
--thermal-source-kind cpu-package|platform|other|unknown
--thermal-source-id TEXT
--thermal-source-label TEXT
--run-started-at-s FLOAT
--run-ended-at-s FLOAT
--cooldown-rule TEXT
--cooldown-enforced true|false
--cooldown-started-at-s FLOAT
--cooldown-ended-at-s FLOAT
--cooldown-elapsed-s FLOAT
```

All new summarize arguments are optional and default to the `AbEvidence`
defaults. These fields are written to both `manifest.json` and `summary.json`.
Legacy summaries without these fields remain parseable, but their aggregate
`ab_evidence_complete` is false.

## Wrapper Contract

`scripts/profile-game-power-on-device.sh` adds:

- `PROFILE_GAME_POWER_AB_ORDER_STRATEGY`, default `paired-baseline`;
- `PROFILE_GAME_POWER_SCENE_EVIDENCE`, default empty;
- `PROFILE_GAME_POWER_COOLDOWN_RULE`, default `fixed-60s`.

For controlled captures:

- empty `PROFILE_GAME_POWER_SCENE_EVIDENCE` is allowed only for exploratory
  output and makes `ab_evidence_complete=false`;
- `paired-baseline` changes the policy execution group to
  `off,<candidate>,off`;
- every measured run receives `--ab-candidate-policy`, `--ab-invocation-id`,
  `--ab-pair-id`, and `--ab-pair-position`;
- every measured run receives `--ab-run-order` and `--ab-order-valid true`;
- `randomized` must be rejected until implemented;
- more than one non-`off` candidate policy in one controlled paired-baseline
  invocation must be rejected until pair grouping is implemented;
- more than one effective CPU-cap variant in one controlled paired-baseline
  invocation must be rejected until pair grouping is implemented;
- `return-to-<temp>C` cooldown must be rejected until timeout and
  threshold-proof semantics are implemented;
- power-source start, pre-run, and end states are auto-collected when possible,
  otherwise `unknown`;
- every measured run receives `--power-source-start-state`,
  `--power-source-pre-run-state`, `--power-source-end-state`,
  `--power-source-samples`, and `--power-source-stable`;
- thermal readings are auto-collected when possible, otherwise
  `thermal_unavailable=true`;
- thermal source identity is auto-collected with thermal readings by choosing a
  deterministic source: prefer `cpu-package`, then `platform`, then `other`,
  and tie-break by stable source id; otherwise `thermal_source_kind=unknown`;
- measured run start/end monotonic timestamps are recorded around the measured
  interval;
- cooldown is enforced between runs in the same compared group.
- when thermal readings are unavailable, controlled profile output remains
  valid for schema/smoke evidence but cannot support a `BETTER` A/B claim.

For imported captures, the wrapper may record the fields, but aggregate
comparison still returns `NEEDS_CONTROLLED_CAPTURE` before considering A/B
evidence completeness.

Wrapper rejection messages must name the supported MVP shape:

```text
paired-baseline supports exactly one non-off candidate, one effective CPU-cap
variant, and fixed-60s cooldown in the first V3 implementation
```

## Single-Run Compare Contract

`steamos-intel-handheld-game-power-profile compare` and
`compare_run_summaries()` remain useful diagnostics for two summary files, but
they are not sufficient evidence for a non-exploratory V3 A/B improvement
claim. In the first V3 implementation, this path must never return
`PolicyVerdict.BETTER`.

If the old single-run thresholds would have returned `BETTER`,
`compare_run_summaries()` instead returns `PolicyVerdict.INCONCLUSIVE` with a
reason beginning:

```text
A/B evidence incomplete:
```

and containing:

```text
single-run compare is exploratory only; cannot support a BETTER claim
```

Non-exploratory `BETTER` claims must use `aggregate` /
`compare_policy_aggregates()` with complete aggregate-local and pairwise A/B
evidence.

## Documentation And Asset Test Migration

The README controlled-capture examples must stop showing one invocation with
multiple candidates. Replace the existing multi-candidate example with separate
one-candidate examples:

```bash
PROFILE_GAME_POWER_CAPTURE_MODE=controlled \
PROFILE_GAME_POWER_REPEATS=3 \
PROFILE_GAME_POWER_FPS_TARGET=40 \
PROFILE_GAME_POWER_SCENE_EVIDENCE="save:<stable-scene>" \
PROFILE_GAME_POWER_POLICIES="off gpu-priority" \
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

For the CPU-cap candidate, use a separate invocation with exactly one effective
variant:

```bash
PROFILE_GAME_POWER_CAPTURE_MODE=controlled \
PROFILE_GAME_POWER_REPEATS=3 \
PROFILE_GAME_POWER_FPS_TARGET=40 \
PROFILE_GAME_POWER_SCENE_EVIDENCE="save:<stable-scene>" \
PROFILE_GAME_POWER_POLICIES="off gpu-priority-cpu-cap" \
PROFILE_GAME_POWER_CPU_CAP_VARIANTS="balanced:3000:2400:0.30" \
PROFILE_GAME_POWER_PCORE_MAX_MHZ=3000 \
PROFILE_GAME_POWER_ECORE_MAX_MHZ=2400 \
PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD=0.30 \
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

Update `tests/test_integration_assets.py` so it asserts the split examples and
asserts the old
`PROFILE_GAME_POWER_POLICIES="off gpu-priority gpu-priority-cpu-cap"` controlled
example is absent. The aggregate examples must either use separate roots per
candidate or run separate aggregate commands with one `--candidate-policy` each.
The first V3 README migration uses separate aggregate commands:

```bash
steamos-intel-handheld-game-power-profile aggregate \
  --root .cache/game-power/profiles \
  --baseline-policy off \
  --candidate-policy gpu-priority \
  --appid 1091500 \
  --tdp-w 22 \
  --duration-s 60 \
  --warmup-s 10 \
  --poll-s 2 \
  --fps-target 40 \
  --fps-target-source manual \
  --min-runs 3
```

README text for aggregate output must state that a `BETTER` result is scoped to
the reported `claim_scope`: appid, scene evidence, candidate policy, TDP,
timing, FPS target, pair count, run order, power source, thermal source and
pair deltas, and cooldown evidence. It must also state that hardware/profile
improvement claims require the guarded foreground-game profile artifacts from
`game-power-profile-device`.

README and generated report examples must render any positive result as:

```text
BETTER (scene/profile-specific controlled result; not a general performance claim)
```

The adjacent sentence must say guarded foreground-game artifacts are required
for this captured profile only and are not sufficient for hardware-wide,
game-wide, release-note, or default-policy performance claims without a
separate claim plan.

```bash
steamos-intel-handheld-game-power-profile aggregate \
  --root .cache/game-power/profiles \
  --baseline-policy off \
  --candidate-policy gpu-priority-cpu-cap \
  --appid 1091500 \
  --tdp-w 22 \
  --duration-s 60 \
  --warmup-s 10 \
  --poll-s 2 \
  --fps-target 40 \
  --fps-target-source manual \
  --min-runs 3
```

## Decky Sample Boundary

The V3 runtime JSONL schema may include internal fields:

- `classification`
- `classification.evidence`
- `pressure`
- `pressure.*[].source_path`
- A/B or profiler-only evidence fields

Decky must not expose these fields through `sample_once()`.

Add a sanitizing helper in `decky/steamos-intel-handheld-game-power/main.py`:

```python
def _public_sample(row: dict) -> dict:
    return {
        "appid": row.get("appid"),
        "action": row.get("action"),
        "reason": row.get("reason"),
        "package_w": row.get("package_w"),
        "core_w": row.get("core_w"),
        "uncore_w": row.get("uncore_w"),
        "pl1_w": row.get("pl1_w"),
        "render_busy": row.get("render_busy"),
    }
```

`_sample_once()` returns `_public_sample(json.loads(line))`. The fallback sample
uses the same public shape. The callable never returns `classification`,
`pressure`, `source_path`, `ab_*`, `thermal_*`, or `cooldown_*`.

This is a backend boundary, not a UI feature. Any future user-facing classifier
display needs a separate plan.

## Focused Tests

Add these tests before implementation:

- `tests/test_game_power_profile.py::test_summary_records_ab_evidence_fields`
- `tests/test_game_power_profile.py::test_aggregate_carries_ab_run_order_evidence`
- `tests/test_game_power_profile.py::test_aggregate_marks_unknown_power_source_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_marks_power_source_change_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_marks_power_source_sample_count_or_alignment_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_thermal_mismatch_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_marks_missing_or_mixed_thermal_source_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_thermal_source_mismatch_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_carries_pair_scoped_thermal_readings`
- `tests/test_game_power_profile.py::test_compare_marks_pair_scoped_thermal_mismatch_incomplete_even_when_aggregate_medians_match`
- `tests/test_game_power_profile.py::test_aggregate_carries_run_interval_evidence`
- `tests/test_game_power_profile.py::test_aggregate_carries_pair_scoped_cooldown_intervals`
- `tests/test_game_power_profile.py::test_aggregate_marks_cooldown_not_adjacent_to_run_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_non_monotonic_pair_run_order_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_reused_or_overlapping_pair_cooldown_interval_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_ab_run_order_mismatch_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_non_off_baseline_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_wrong_paired_baseline_candidate_signature_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_marks_multiple_ab_run_order_signatures_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_scene_power_or_strategy_mismatch_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_groups_split_profile_root_by_ab_candidate_policy`
- `tests/test_game_power_profile.py::test_aggregate_reports_candidate_without_matching_baseline_as_incomplete_group`
- `tests/test_game_power_profile.py::test_aggregate_reports_baseline_without_matching_candidate_as_incomplete_group`
- `tests/test_game_power_profile.py::test_aggregate_marks_randomized_order_incomplete_until_supported`
- `tests/test_game_power_profile.py::test_compare_marks_missing_trailing_baseline_pair_incomplete`
- `tests/test_game_power_profile.py::test_compare_marks_duplicate_pair_position_incomplete`
- `tests/test_game_power_profile.py::test_aggregate_marks_missing_cooldown_enforcement_incomplete`
- `tests/test_game_power_profile.py::test_compare_policy_aggregates_never_returns_better_without_complete_ab_evidence`
- `tests/test_game_power_profile.py::test_compare_policy_aggregates_better_includes_claim_scope_at_comparison_json_path`
- `tests/test_game_power_profile.py::test_compare_policy_aggregates_non_better_has_null_claim_scope`
- `tests/test_game_power_profile.py::test_compare_policy_aggregates_better_human_summary_is_self_scoping`
- `tests/test_game_power_profile.py::test_compare_run_summaries_is_exploratory_and_never_better`
- `tests/test_game_power_profile.py::test_profile_cli_compare_reports_exploratory_without_ab_evidence`
- `tests/test_integration_assets.py::test_profile_wrapper_uses_paired_baseline_order_for_controlled_ab`
- `tests/test_integration_assets.py::test_profile_wrapper_passes_full_ab_identity_tuple_to_summarize`
- `tests/test_integration_assets.py::test_profile_wrapper_records_cooldown_enforcement_evidence`
- `tests/test_integration_assets.py::test_profile_wrapper_records_distinct_pair_cooldown_intervals`
- `tests/test_integration_assets.py::test_profile_wrapper_passes_power_source_samples_to_summarize`
- `tests/test_integration_assets.py::test_profile_wrapper_records_run_interval_and_source_identity_evidence`
- `tests/test_integration_assets.py::test_profile_wrapper_rejects_randomized_order_until_supported`
- `tests/test_integration_assets.py::test_profile_wrapper_rejects_return_to_temp_until_supported`
- `tests/test_integration_assets.py::test_profile_wrapper_rejects_multiple_candidates_until_pair_grouping_supported`
- `tests/test_integration_assets.py::test_profile_wrapper_rejects_multiple_cpu_cap_variants_until_pair_grouping_supported`
- `tests/test_integration_assets.py::test_docs_split_controlled_profile_examples_by_candidate`
- `tests/test_integration_assets.py::test_docs_controlled_profile_examples_include_scene_evidence`
- `tests/test_integration_assets.py::test_docs_scope_better_claims_to_reported_claim_scope`
- `tests/test_integration_assets.py::test_docs_render_better_with_adjacent_claim_boundary`
- `tests/test_game_power_profile.py::test_aggregate_marks_short_or_inconsistent_cooldown_incomplete`
- `tests/test_game_power_profile.py::test_summarize_legacy_invocation_defaults_to_incomplete_ab_evidence`
- `tests/test_decky_plugin_assets.py::test_game_power_decky_backend_sanitizes_v3_internal_sample_fields`
- `tests/test_decky_plugin_backend.py::test_game_power_backend_sample_once_returns_public_subset`
- `tests/test_decky_plugin_backend.py::test_game_power_backend_sample_once_fallback_uses_public_subset`

The wrapper full-identity test must inspect the generated summarize invocations
and assert `--ab-run-order`, `--ab-order-valid`, `--ab-candidate-policy`,
`--ab-invocation-id`, `--ab-pair-id`, and `--ab-pair-position` are passed for
all three measured runs, with positions assigned as
`baseline-before,candidate,baseline-after`.

The wrapper power-source sample test must inspect all three generated summarize
invocations and assert `--power-source-start-state`,
`--power-source-pre-run-state`, `--power-source-end-state`,
`--power-source-samples`, and `--power-source-stable` are passed for every
paired-baseline position, with the sample CSV ordered as start, pre-run, end.

The Decky backend tests must instantiate the backend callable path, not only
scan source text. They feed a JSONL row containing `classification`,
`classification.evidence`, `pressure`, nested `source_path`, `ab_*`,
`thermal_*`, `cooldown_*`, and unknown extra keys, then assert
`Plugin().sample_once()` returns exactly the public key set. The fallback test
must cover no-output behavior and assert the same public shape.

Update these existing tests instead of leaving contradictory expectations:

- `tests/test_game_power_profile.py::test_compare_run_summaries_accepts_better_one_percent_low_without_avg_regression`
- `tests/test_game_power_profile.py::test_compare_run_summaries_accepts_power_saving_when_target_is_sustained`
- `tests/test_game_power_profile.py::test_profile_cli_compare_reads_two_summary_files`
- `tests/test_game_power_profile.py::test_compare_policy_aggregates_accepts_median_low_improvement`
- `tests/test_game_power_profile.py::test_compare_policy_aggregates_accepts_median_power_saving_at_target`
- `tests/test_game_power_profile.py::test_profile_cli_aggregate_scans_profile_root_and_compares_repeated_runs`
- `tests/test_game_power_profile.py::test_profile_cli_aggregate_builds_guarded_affinity_experiment_plan`
- `tests/test_game_power_profile.py::test_profile_cli_aggregate_builds_background_shaping_experiment_plan`

Focused tests run first with:

```bash
.venv/bin/python -m pytest <nodes> -q
```

Then run the required sweep:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

## Acceptance Criteria

- A controlled comparison cannot return `BETTER` unless aggregate-local and
  pairwise A/B evidence are complete.
- Actual run order is present in `RunSummary`, `summary.json`, and
  `PolicyAggregate`.
- Paired-baseline evidence includes candidate policy, pair id, and pair
  position; pair id includes an invocation id to avoid collisions across
  repeated captures in a shared profile root; aggregate comparison rejects mixed
  candidate groups, missing before/candidate/after positions, duplicate positions, and invalid
  baseline:candidate cardinality.
- Aggregate output reports missing baseline/candidate groups in
  `incomplete_groups` instead of silently omitting them.
- `randomized` A/B evidence is parseable but incomplete until randomized order
  support is implemented end to end.
- Unknown or changed power source, missing thermal evidence, pairwise thermal
  mismatch, missing cooldown enforcement, short cooldown, inconsistent cooldown
  timestamps, and legacy summaries all downgrade to exploratory or inconclusive
  comparison.
- Missing or misaligned power sample phases, missing or mixed thermal source
  identity, cooldown intervals not adjacent to the measured run, and
  non-monotonic pair run intervals all downgrade to exploratory or inconclusive
  comparison.
- Missing, reused, overlapping, or non-monotonic pair-scoped cooldown intervals
  all downgrade to exploratory or inconclusive comparison.
- Pair-scoped thermal mismatch downgrades to inconclusive even when aggregate
  thermal medians remain within threshold.
- A `BETTER` aggregate result includes `claim_scope` and README text describes
  it as a scene/profile-specific controlled result, not a general performance
  claim.
- `comparisons[].comparison.claim_scope` is non-null only for `BETTER`, and
  `comparisons[].comparison.human_summary` renders adjacent claim-boundary and
  guarded-artifact wording for every human-readable `BETTER` output.
- Incomplete evidence reasons include `exploratory only; cannot support a
  BETTER claim`.
- The single-run compare CLI is exploratory and cannot return `BETTER`; only
  aggregate comparison can support non-exploratory improvement claims.
- README controlled-capture examples and their asset tests use one candidate
  per invocation, aggregate examples do not mix candidates in one comparison,
  include scene evidence for non-exploratory captures, and no stale
  multi-candidate controlled example remains.
- Existing aggregate positive-path tests either provide complete paired-baseline
  A/B evidence for `BETTER` expectations or assert exploratory/inconclusive
  behavior when evidence is absent.
- Decky `sample_once()` exposes only the public diagnostic subset, proven by a
  runtime backend callable test and not only by static asset/source checks.
- Required local harness passes after implementation.
- Hardware/profile improvement claims still require the guarded foreground-game
  profile check and artifacts.
