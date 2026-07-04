# Game Power v3 Evidence Boundary Plan Review Report

## Summary

Review surface:

- `docs/superpowers/specs/2026-07-04-game-power-v3-evidence-boundary-design.md`
- final passed surface hash:
  `e846340cccf380a1f473e5701db97df9bedec833accdaa64e6b3afb71eb029c5`

Overall result: **passed**

Reason: the revised Game Power v3 evidence-boundary surface passed a fresh
verification sweep and a held-out sweep. No verifier-confirmed critical or major
blockers remain.

Implementation may proceed from this bounded surface. This approval is for the
evidence-boundary implementation only; it does not approve production cgroup,
uclamp, affinity, sched_ext, FPS actuators, new Decky UI, P/E-core constant
changes, restore relaxation, or per-game learned scheduling policy.

## Review Surface

In scope:

- A/B evidence data model for controlled game-power profiler output.
- Run-order, invocation, pair identity, power-source, thermal-source, run
  interval, cooldown interval, scene, and claim-scope evidence.
- Aggregate comparison gates that prevent false `BETTER` claims.
- Single-run compare downgrade to exploratory output.
- Human-readable `BETTER` claim-boundary output.
- README and asset/runtime-test migration for the first V3 workflow.
- Decky `sample_once()` callable boundary.

Out of scope:

- Production uclamp, cgroup, affinity, sched_ext, or FPS-based actuators.
- New Decky UI or user-facing classifier display.
- Changes to measured P-core/E-core frequency constants.
- Restore-check relaxation.
- Per-game learned scheduling policy.

## Iteration Results

| Iteration | Result | Notes |
| --- | --- | --- |
| Prior loop 1-5 | blocked | Original corrective surface exhausted the default 5-sweep budget before held-out review. |
| Prior held-out 1 | continue | Confirmed `pair-scoped-thermal-mismatch-not-enforceable`; revised surface added pair-scoped thermal readings and pair-level thermal checks. |
| Prior held-out 2 | blocked | Second held-out found six confirmed major blockers: thermal source identity, cooldown/run interval adjacency, power sample alignment, wrapper identity tests, Decky runtime tests, and positive claim boundary. |
| Fresh loop 1 | continue | Four confirmed major blockers remained: comparison output schema, pair-scoped cooldown intervals, wrapper power-source sample propagation tests, and human-readable `BETTER` self-scoping. |
| Fresh loop 2 | held-out required | A/B/C/D/E all approved the revised `e846340c...` surface with no critical or major findings. |
| Fresh loop 3 held-out | passed | A/B/C/D/E held-out reviewers approved. One minor architecture note was recorded; no critical or major findings remained. |

## Final Scores

| Reviewer | Role | Score | Verdict |
| --- | --- | --- | --- |
| A-heldout | Architecture & Feasibility | +1 | APPROVE with minor note |
| B-heldout | Completeness & Risk | +2 | APPROVE |
| C-heldout | Quality & Conventions | +2 | APPROVE |
| D-heldout | Decky/User-visible Boundary | +2 | APPROVE |
| E-heldout | Product & Rollout Value | +2 | APPROVE |

## Resolved Findings

- `pairwise-thermal-mismatch-layer`: moved baseline-vs-candidate thermal checks
  to `compare_policy_aggregates()`.
- `power-source-within-run-stability`: added start/end/sample sequence and
  `power_source_stable` evidence.
- `return-to-temp-timeout-or-deferral`: first V3 rejects `return-to-<temp>C`.
- `return-to-temp-tdd-gap`: replaced temperature-return behavior with rejection
  tests for first V3.
- `pairwise-ab-run-order-signature-missing`: pairwise comparison now validates
  unique matching run-order signatures.
- `single-run-compare-bypasses-ab-evidence`: single-run compare is
  exploratory-only and cannot return `BETTER`.
- `controlled-example-doc-test-migration-missing`: README examples are split by
  candidate and stale multi-candidate examples are prohibited.
- `split-capture-aggregation-pair-boundary-missing`: added candidate-aware
  grouping and pair metadata.
- `paired-baseline-pair-identity-cardinality-missing`: added `ab_pair_id`,
  `ab_pair_position`, and per-pair cardinality checks.
- `aggregate-unmatched-group-output-shape-missing`: added `incomplete_groups`
  report shape.
- `randomized-summary-unsupported-path`: randomized evidence is parseable but
  incomplete until implemented end to end.
- `readme-controlled-examples-missing-scene-evidence`: README examples include
  `PROFILE_GAME_POWER_SCENE_EVIDENCE`.
- `pair-scoped-thermal-mismatch-not-enforceable`: added
  `thermal_pair_readings_by_id`, pair-scoped thermal threshold checks, and a
  regression where aggregate medians match but one pair is thermally mismatched.
- `thermal-source-identity-missing`: added thermal source kind/id/label fields,
  deterministic source selection, and source matching rules.
- `cooldown-not-tied-to-run-interval`: added measured run interval evidence and
  cooldown-to-run adjacency rules.
- `power-samples-cardinality-alignment-missing`: required exactly three ordered
  power-source samples aligned with start/pre-run/end fields.
- `wrapper-ab-identity-test-missing`: required wrapper tests for the full A/B
  summarize identity tuple.
- `decky-runtime-callable-test-missing`: required runtime
  `Plugin().sample_once()` tests, including fallback shape.
- `better-positive-claim-boundary-missing`: added `claim_scope`,
  `human_summary`, and adjacent human-readable `BETTER` boundary/caveat wording.
- `comparison-output-schema-missing`: added explicit `PolicyComparison` output
  fields and exact `comparisons[].comparison.*` JSON paths.
- `pair-scoped-cooldown-intervals-missing`: added
  `cooldown_interval_by_pair_id`, cooldown interval completeness, and pairwise
  reuse/overlap/order checks.
- `wrapper-power-source-samples-test-missing`: required wrapper tests for
  power-source sample flags on all paired-baseline positions.
- `human-readable-better-self-scope-missing`: required every human-readable
  `BETTER` output to carry adjacent claim-boundary and guarded-artifact wording.

## Non-Blocking Notes

- Multiple scene-specific captures for the same appid/TDP/candidate in one
  profile root are conservatively collapsed into an incomplete aggregate instead
  of separate scene-scoped comparisons. This is safe against false `BETTER`
  claims; a later iteration can add scene-evidence filtering/grouping to reduce
  avoidable inconclusive results.

## Harness JSON

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 3,
  "budget": {
    "maxSweeps": 5,
    "sweepsUsed": 3
  },
  "harnessStatus": "passed",
  "reason": "Fresh verification sweep resolved all open blockers, and held-out reviewers found no confirmed critical or major blockers.",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C", "D", "E"],
  "surfaceId": "game-power-v3-evidence-boundary:e846340cccf3",
  "scores": {
    "A": 1,
    "B": 2,
    "C": 2,
    "D": 2,
    "E": 2
  },
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 29,
    "refutedFindings": 1,
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
    "heldOutSweepsUsed": 1,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "docs/superpowers/specs/2026-07-04-game-power-v3-evidence-boundary-design.md@5a10e081e0d498422d7cbb15a68d8aeb09acca29221483d38bd9e1a9a0807c53",
    "currentSurfaceSnapshot": "docs/superpowers/specs/2026-07-04-game-power-v3-evidence-boundary-design.md@e846340cccf380a1f473e5701db97df9bedec833accdaa64e6b3afb71eb029c5",
    "latestRevisionDiff": "Added PolicyComparison output schema, pair-scoped cooldown intervals, wrapper power-source sample tests, and human-readable BETTER self-scoping."
  },
  "nextAction": {
    "type": "stop",
    "summary": "Plan Review passed. Proceed to TDD implementation from the bounded evidence-boundary surface."
  }
}
```
