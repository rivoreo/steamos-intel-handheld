# Game Power v3 Plan Review Report

## Summary

Plan Review did not approve the current v3 design surface.

The reviewed artifact is:

- `docs/superpowers/specs/2026-07-04-game-power-v3-observer-classifier-design.md`
- latest reviewed hash: `5a030ff1ee2647be7470ff93569b0d219711da0551fbf5e01cc43073f806c2d9`

Review status: **blocked**

Reason: the same A/B evidence-gating concern remained open after repeated
material revisions. A new Decky callable-boundary blocker was also confirmed.
Implementation must not start from this plan until the unresolved ledger below
is addressed and a new bounded review surface passes.

## Review Surface

In scope:

- runtime frame-target, pressure, and classification telemetry;
- post-run profiler classification;
- profile A/B evidence contract;
- JSONL, summary, and aggregate schemas;
- Decky safety boundaries affected by the new JSONL fields;
- local and guarded harness evidence boundaries.

Out of scope:

- production uclamp, cgroup, affinity, sched_ext, or FPS-based restore
  actuators;
- new Decky UI for classifier internals;
- per-game learned scheduler policy.

## Iteration Results

| Iteration | Result | Notes |
| --- | --- | --- |
| 1 | continue | Initial plan was unsafe as an actuator. Review confirmed stale frame telemetry, global PSI scope, missing thresholds, weak tests, and ambiguous device evidence. |
| 2 | continue | Runtime safety improved, but schema/testability gaps remained: cgroup resolver safety, classification counts, off-mode classification, and A/B controls. |
| 3 | continue | Most schema gaps closed. A/B evidence still was not wired into summary/aggregate gates. Classifier/action predicate concern was verified as refuted. |
| 4 | blocked | A/B evidence gate still did not carry actual order/power/thermal/cooldown proof end to end. Decky callable pass-through of internal JSONL was confirmed. |

## Resolved Findings

- `target-sustained-runtime-restore-causal-safety`: resolved by making
  `target-sustained` post-run evidence only.
- `static-frame-telemetry-freshness`: resolved by allowing runtime FPS target
  metadata only, not avg FPS or p99 frame outcomes.
- `psi-scope-unsupported-semantics`: resolved by adding pressure scope, source,
  support state, and null unknown metrics.
- `classifier-contract-thresholds`: resolved by adding deterministic primary
  and advisory classification tables.
- `red-green-test-list`: resolved by naming focused failing tests before the
  required sweep.
- `device-evidence-closure`: resolved by separating local logic evidence from
  guarded hardware/profile evidence.
- `pressure-resource-schema-mismatch`: resolved by making parent pressure
  container keys canonical.
- `foreground-cgroup-resolver-safety`: resolved by adding safe cgroup v2
  resolver rules and tests.
- `classification-count-schema-underspecified`: resolved by defining
  classification count fields in log, run, summary, and aggregate outputs.
- `every-decision-off-mode-coverage`: resolved by requiring a shared classifier
  helper and off-mode governor coverage.
- `classification-label-vs-controller-activation-predicate`: refuted because
  classification is explicitly non-actionable telemetry and does not change
  `decision.action`.

## Open Blockers

### 1. A/B run order is not carried into aggregate comparison

Verified blocker: `ab-run-order-not-in-aggregate`

The current draft adds `--ab-run-order` and `ab_run_order` to
`RunSummary`/`summary.json`, but `PolicyAggregate` and the aggregate comparison
rules still do not carry or validate actual run-order evidence. The gate can
therefore reason about `ab_order_strategy` without proving the observed run
sequence matched the strategy.

Required fix:

- add actual run-order state to `PolicyAggregate`;
- define how the wrapper computes or supplies `--ab-run-order`;
- mark aggregate evidence incomplete when actual order is missing, mixed, or
  incompatible with `randomized` or `paired-baseline`;
- add focused tests proving `compare_policy_aggregates()` cannot return
  `BETTER` without valid actual order evidence.

### 2. Power and thermal evidence completeness is too weak

Verified blocker: `ab-power-thermal-weak-completeness`

The draft allows `power_source_state="unknown"` and permits thermal evidence as
present or explicitly unavailable, but does not make unknown power source or
mixed/mismatched thermal evidence incomplete for non-exploratory A/B claims.

Required fix:

- treat `power_source_state="unknown"` as incomplete for non-exploratory A/B
  claims;
- structure thermal evidence enough to compare initial/final/unavailable states;
- mark missing, mixed, mismatched, or unjustified unavailable thermal evidence
  as incomplete before `BETTER` can be returned;
- add tests for unknown power source and thermal mismatch downgrades.

### 3. Cooldown is recorded but not enforced

Verified blocker: `ab-cooldown-not-enforced`

The draft records `cooldown_rule`, but does not require the wrapper to enforce
the fixed wait or temperature-return threshold, nor emit proof that the cooldown
happened.

Required fix:

- enforce cooldown between controlled compared runs;
- record cooldown start/end timestamps, elapsed seconds, and thermal readings
  or temperature-return proof;
- propagate cooldown evidence through `summary.json`, `RunSummary`, and
  `PolicyAggregate`;
- mark aggregates incomplete when enforcement evidence is missing.

### 4. Decky sample can leak internal JSONL fields

Verified blocker: `decky-sample-leaks-internal-jsonl`

The draft adds internal JSONL fields such as `classification`, `pressure`,
`classification.evidence`, and `pressure.source_path`. The existing Decky
backend may return a parsed JSONL row unchanged from `sample_once()`, which can
expose internal labels and cgroup paths through the callable surface even if the
frontend does not render them.

Required fix:

- define a v3 Decky sample boundary;
- make `sample_once()` return only the safe public diagnostic subset:
  `appid`, `action`, `reason`, watts, PL1, and render busy as applicable;
- omit `classification`, `pressure`, `source_path`, post-run/A-B fields, and
  classifier internals from Decky callables;
- add a focused Decky backend/assets test proving the new internal fields are
  not exposed.

## Harness JSON

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 4,
  "budget": {
    "maxSweeps": 5,
    "sweepsUsed": 4
  },
  "harnessStatus": "blocked",
  "reason": "The A/B evidence-gate blocker remained open after repeated material revisions, and a Decky callable-boundary blocker was confirmed.",
  "reasonCode": "plateau",
  "activeReviewers": ["A", "B", "C", "D", "E"],
  "surfaceId": "game-power-v3-observer-classifier:5a030ff1ee26",
  "scores": {
    "A": -1,
    "B": -1,
    "C": -1,
    "D": -1,
    "E": 1
  },
  "convergence": {
    "openBlockers": 4,
    "confirmedFindings": 4,
    "refutedFindings": 1,
    "unresolvedDecisionItems": 0,
    "unresolvedEvidenceItems": 0,
    "newBlockers": 1,
    "reopenedBlockers": 0,
    "latentMissedStreak": 1,
    "novelIssuesBySource": {
      "revision_introduced": 0,
      "latent_missed": 1,
      "scope_expansion": 0,
      "unsupported": 0
    },
    "maxMaterialRevisionAttempts": 3,
    "heldOutSweepsUsed": 0,
    "plateauDetected": true,
    "reviewProcessDefect": false
  },
  "nextAction": {
    "type": "stop",
    "summary": "Do not implement from this surface. Address open blockers in a new bounded plan review surface."
  }
}
```
