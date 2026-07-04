# Plan Review Report: Game Power Sampling Closure

## Summary

Review surface:

- `docs/superpowers/specs/2026-07-04-game-power-rolling-evidence-design.md`
- final passed surface hash:
  `2301a26ee2505bc52012a3fbd7bcf008e9bff519ed78f212d5f1fc32cd3d86ec`

Overall result: **passed**

Reason: the revised sampling-closure design passed adversarial A/B/C/E review
and a second held-out sweep. No confirmed critical or major blockers remain.

Implementation may proceed from this bounded surface. This approval is for the
sampling-closure implementation only; it does not approve production cgroup,
uclamp, affinity, sched_ext, FPS actuators, Decky UI changes, P/E-core constant
changes, restore relaxation, or FPS/frametime claims without guarded profile
evidence.

## Review Surface

In scope:

- rolling in-memory evidence for Game Power classification stability;
- foreground Steam AppID/context session summaries;
- bounded persistent hint aggregates and promoted hints;
- AppID/TDP/power/FPS/topology/OS/runtime/policy context keys;
- runtime-unaware short-lived warm-up hints;
- context-change reset and restore-outcome ordering;
- cache corruption, locking, pruning, contradiction, repair, and observability;
- focused RED tests plus required local harness sweep.

Out of scope:

- new CPUFreq, RAPL, cgroup, uclamp, cpuset, affinity, sched_ext, or FPS-based
  actuators;
- Decky UI changes;
- user-tunable measured P-core/E-core constants;
- per-game manual profile editing;
- FPS, p99 frametime, or 1% low claims without `game-power-profile-device`
  evidence.

## Iteration Results

| Iteration | Result | Notes |
| --- | --- | --- |
| 1 | continue | Initial surface lacked hint-context provider, durable aggregates, restore/write outcome, cache corruption handling, context reset, defaults, and metrics. |
| 2 | continue | Core blockers were mostly fixed, but runtime signature semantics, cache bounds, zero-window behavior, and context-change restore ordering remained under-specified. |
| 3 | held-out required | A/B/C/E approved after runtime-unaware semantics, cache pruning, zero-window compatibility, and restore ordering were fixed. |
| Held-out 1 | continue | Fresh reviewers found persistent-learning safety gaps: contradicted hinted sessions could still learn/repair, current-session contradiction was nondeterministic, session-close observability was missing, and canonical keys/PL1 buckets were underspecified. |
| Held-out 2 | passed | Final held-out reviewers approved with only minor clarifications. Those notes were folded in: OS-signature changes are safe key mismatches that age out, restore success is tri-state, and persistent contradiction/repair state is observable. |

## Final Scores

| Reviewer | Role | Score | Verdict |
| --- | --- | --- |
| A | Architecture & Feasibility | +2 | APPROVE |
| B | Completeness & Risk | +1 | APPROVE |
| C | Quality & Conventions | +1 | APPROVE |
| E | Product & Rollout Value | +2 | APPROVE |
| Held-out final 1 | Fresh architecture/risk/testability | +1 | APPROVE |
| Held-out final 2 | Fresh implementation/observability | +1 | APPROVE |

## Resolved Findings

- `hint-context-provider-missing`: added `GamePowerHintContext`, completion
  rules, service wiring, standalone fallback, and context-source boundaries.
- `durable-aggregate-missing`: added aggregate records separate from promoted
  hint entries so multi-session thresholds survive launches.
- `context-reset-missing`: added a canonical active context and close/restore
  reset/reload semantics for AppID, PL1, power source, FPS target, topology,
  OS, runtime, and policy changes.
- `cache-corruption-migration-undefined`: added missing-file, oversize,
  invalid-JSON, malformed-record, key/context mismatch, policy-version, and
  lock-failure rules.
- `restore-write-outcome-implicit`: added `GamePowerActuatorOutcome` and made
  restore/write cleanliness a promotion gate.
- `runtime-signature-ambiguous`: made runtime identity an optional stronger
  dimension; unavailable runtime uses a short-lived runtime-unaware bucket and
  cannot claim Proton/runtime-change detection.
- `cache-not-bounded`: added record, byte, age, and runtime-unaware age limits
  plus prune-before-write behavior.
- `rolling-tests-did-not-prove-majority`: replaced vague sequences with cases
  where legacy consecutive hysteresis is satisfied but rolling majority blocks
  activation or restore.
- `zero-window-compatibility-ambiguous`: defined
  `rolling_window_samples <= 1` as legacy hysteresis behavior.
- `contradicted-session-can-learn`: contradicted hinted sessions are ineligible
  for aggregate contribution, promotion, and same-session repair.
- `current-session-contradiction-nondeterministic`: added
  `session_hint_contradiction_samples=2`, immediate disable after
  restore-after-hint, reset semantics, and evidence fields.
- `session-close-observability-missing`: added `game-power-session-close`
  JSONL event with restore, aggregate, promotion, contradiction, repair, and
  cache-write outcomes.
- `hint-key-serialization-unsafe`: added canonical sorted JSON tuple hashed
  under `game-power-context-v1`.
- `pl1-bucket-undefined`: defined PL1 bucket as nearest integer watt after
  microwatt conversion.

## Non-Blocking Notes

- Runtime-unaware hints are intentionally weaker and short-lived. A later
  iteration can add a real Proton/runtime provider and retire the unavailable
  bucket when reliable runtime identity exists.
- Post-rollout dashboards can add operational thresholds for
  `hint_disabled`, `unwanted_flip_count`, `exact_restore_ratio`, and
  `cache_write_result`, but the first implementation only needs to emit the
  evidence.

## Harness JSON

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 5,
  "budget": {
    "maxSweeps": 5,
    "sweepsUsed": 5
  },
  "harnessStatus": "passed",
  "reason": "A/B/C/E approved after iteration 3 and a second held-out sweep found no confirmed critical or major blockers.",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C", "E", "held-out"],
  "surfaceId": "2301a26ee2505bc52012a3fbd7bcf008e9bff519ed78f212d5f1fc32cd3d86ec",
  "scores": {
    "A": 2,
    "B": 1,
    "C": 1,
    "E": 2,
    "held_out_final_1": 1,
    "held_out_final_2": 1
  },
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 0,
    "refutedFindings": 0,
    "unresolvedDecisionItems": 0,
    "unresolvedEvidenceItems": 0,
    "newBlockers": 0,
    "reopenedBlockers": 0,
    "latentMissedStreak": 0,
    "novelIssuesBySource": {
      "revision_introduced": 0,
      "latent_missed": 0,
      "scope_expansion": 0,
      "unsupported": 0
    },
    "maxMaterialRevisionAttempts": 1,
    "heldOutSweepsUsed": 2,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "nextAction": {
    "type": "stop",
    "summary": "Plan Review passed. Proceed to TDD implementation from the bounded sampling-closure surface."
  }
}
```
