# Plan Review Report

## Summary

| Reviewer | Role | Score | Verdict |
|----------|------|-------|---------|
| A | Architecture & Feasibility | +1 | APPROVE |
| B | Completeness & Risk | +1 | APPROVE |
| C | Quality & Conventions | +2 | APPROVE |
| D | UX/UI Design | +1 | APPROVE |
| E | Product & Business Value | +1 | APPROVE |

**Overall Result**: APPROVED WITH NOTES

**Review Iterations**: 2 plus held-out sweep

**Harness Status**: passed

**Active Reviewers**: 5/5

## Bounded Surface

Artifact:
- `docs/superpowers/plans/2026-06-26-profile-aware-tdp-policy.md`

Context boundaries:
- Current backend: `src/steamos_intel_handheld/power_control.py`
- Current tests: `tests/test_power_control_backend.py`, `tests/test_power_control_cli.py`
- Current verifier and service: `scripts/verify-on-device.sh`, `data/systemd/steamos-intel-handheld-power-control.service`
- Current docs: `README.md`, `docs/design.md`, `docs/hardware/msi-claw-8-ai-plus.md`

Out of scope:
- New SteamOS UI controls
- CPU/GPU frequency governor changes
- Fan curves
- Battery charge-limit UI
- Per-game profile storage

Attribution evidence:
- Original surface snapshot: `e879e126d86dc7a7bee69ec10633a9d35ab6e950ba4a2643babba1ab0f1fb53c`
- Final surface snapshot: `69fadff9b782dcb3074267e385f56de89c98c8f225ca4cdf3cb8c268d551f4b5`
- Latest revision diff: revisions made in the same plan file before this report was written.

## Key Strengths

- The plan preserves the SteamOS slider contract as PL1 and moves PL2/Tau into backend policy, which matches the single-slider UI constraint.
- The staged MSI EC shift-mode rollout avoids flipping a risky EC behavior before hardware validation.
- RAPL Tau support is designed to be opportunistic: PL1/PL2 writes remain authoritative, while Tau write failures are logged and do not undo power-limit writes.
- The plan keeps `--pl2-w` as an explicit override, preserving an escape hatch for device-specific testing.

## Revision Changelog

### Iteration 1 -> Iteration 2

1. **[MAJOR/A]** Fixed task sequencing for RAPL Tau support.
   - Finding: Task 2 tried to test policy-driven Tau writes before backend policy wiring existed.
   - Change: Task 2 now only adds RAPL time-window data-model and clamp-helper tests. Task 4 owns the integrated PL2/Tau write-path tests.

2. **[MAJOR/A]** Fixed Battery Low Power formula/table inconsistency.
   - Finding: The table said 25W -> 28W and 30W -> 33W, while the formula produced 30W and 35W.
   - Change: Formula now returns `min(28, max(25, PL1 + 3))` for 18W-25W and `min(33, PL1 + 3)` above 25W.

3. **[MAJOR/B]** Added concrete on-device validation thresholds.
   - Finding: The service-default flip depended on "materially above PL1", which was not measurable.
   - Change: Added 10-minute average, burst recovery, cumulative high-power time, and stability thresholds.

4. **[MAJOR/B]** Defined Tau write failure behavior.
   - Finding: The plan did not state whether a `constraint_*_time_window_us` write failure should fail the whole TDP apply.
   - Change: Tau write failures are logged and do not roll back PL1/PL2 writes; PL1/PL2 write failures remain hard failures.

5. **[MAJOR/B]** Fixed AC/DC reapply flag behavior.
   - Finding: `reapply_if_power_source_changed()` wrote RAPL unconditionally, ignoring `apply_rapl=False`.
   - Change: The planned method now respects `apply_rapl` and `apply_msi_claw_ec`.

6. **[MINOR/C]** Removed compatibility hazards and placeholders during self-review before the discovery sweep.
   - Finding: Python 3.10 compatibility would be broken by `StrEnum`, and example device commands used `<device>`.
   - Change: The plan now uses `class X(str, Enum)` and concrete `root@steamdeck-host` examples.

## Final Reviewer Notes

### Reviewer A: Architecture & Feasibility

Score: +1. The revised plan is architecturally feasible and now has a coherent order: pure policy, RAPL metadata support, power-source controls, backend wiring, EC shift policy, reapply loop, then docs and device validation. Remaining note: during implementation, keep `compute_tdp_limits()` legacy behavior clearly documented so future contributors do not confuse it with the new policy path.

### Reviewer B: Completeness & Risk

Score: +1. The revised plan addresses the major risk gaps by adding measurable hardware thresholds and safe Tau write failure behavior. Remaining note: implementation should log enough context on Tau failures, including constraint path and requested Tau, because on-device debugging will otherwise be slow.

### Reviewer C: Quality & Conventions

Score: +2. The plan follows existing repo conventions: pytest-first, isolated backend helpers, no UI scope creep, and explicit commands. The private helper tests are acceptable here because the backend already keeps sysfs handling isolated and test-injected.

### Reviewer D: UX/UI Design

Score: +1. There is no new visual UI, but the plan affects user-visible performance and battery semantics. The plan keeps slider meaning stable as PL1 and documents mode behavior. Remaining note: docs should avoid presenting Battery Low Power and AC Quiet as UI-selectable SteamOS modes until a real profile signal or UI path exists.

### Reviewer E: Product & Business Value

Score: +1. The plan targets a real user-facing gain: better 1% low burst behavior without turning battery mode into 37W behavior. The staged service-default gate is the right product risk control. Remaining note: after implementation, keep a simple before/after table in hardware docs so users can understand why 17W/25W replaced 17W/19W.

## Held-Out Sweep

Held-out sweep result: no evidence-backed critical or major blockers found.

Non-blocking suggestions:
- Add one short README table for the default Auto policy after implementation.
- Include the current power-source value in service logs when policy is applied.
- Consider adding a future, separate plan for SteamOS profile detection if a reliable source is found.

## Harness JSON

```json
{
  "schemaVersion": "lunatalk.review-loop.v1",
  "reviewType": "plan",
  "iteration": 2,
  "harnessStatus": "passed",
  "reason": "No open critical or major blockers remain after revision and held-out sweep.",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C", "D", "E"],
  "surfaceId": "profile-aware-tdp-policy:69fadff9b782",
  "scores": {"A": 1, "B": 1, "C": 2, "D": 1, "E": 1},
  "convergence": {
    "openBlockers": 0,
    "unresolvedDecisionItems": 0,
    "unresolvedEvidenceItems": 0,
    "newBlockers": 0,
    "reopenedBlockers": 0,
    "escapedBlockers": 0,
    "escapedBlockerStreak": 0,
    "latentMissedBlockerStreak": 0,
    "novelIssuesBySource": {
      "revision_introduced": 0,
      "latent_missed": 0,
      "scope_expansion": 0,
      "unsupported": 0
    },
    "maxMaterialRevisionAttempts": 1,
    "heldOutSweepComplete": true,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "docs/superpowers/plans/2026-06-26-profile-aware-tdp-policy.md@sha256:e879e126d86dc7a7bee69ec10633a9d35ab6e950ba4a2643babba1ab0f1fb53c",
    "currentSurfaceSnapshot": "docs/superpowers/plans/2026-06-26-profile-aware-tdp-policy.md@sha256:69fadff9b782dcb3074267e385f56de89c98c8f225ca4cdf3cb8c268d551f4b5",
    "latestRevisionDiff": "same file revisions summarized in Revision Changelog"
  },
  "ledger": [
    {
      "dedupeKey": "task-sequence-tau-before-policy-wiring",
      "severity": "major",
      "status": "resolved",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "escapedBlocker": false,
      "novelIssueSource": "none",
      "evidence": "Task 2 originally tested policy Tau writes before Task 4 backend policy wiring.",
      "finding": "Task sequencing made the plan impossible to execute task-by-task.",
      "recommendation": "Move Tau write-path integration to the backend wiring task.",
      "disposition": "Resolved by limiting Task 2 to RAPL time-window metadata and moving write-path tests to Task 4."
    },
    {
      "dedupeKey": "battery-low-formula-table-mismatch",
      "severity": "major",
      "status": "resolved",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "escapedBlocker": false,
      "novelIssueSource": "none",
      "evidence": "Battery Low Power table and formula produced different 25W and 30W PL2 values.",
      "finding": "The plan contradicted itself.",
      "recommendation": "Make the formula match the table or change the table.",
      "disposition": "Resolved by changing the formula to match the table."
    },
    {
      "dedupeKey": "vague-on-device-thresholds",
      "severity": "major",
      "status": "resolved",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "escapedBlocker": false,
      "novelIssueSource": "none",
      "evidence": "The service-default flip originally depended on a vague sustained-power condition.",
      "finding": "Hardware validation gate was not objectively measurable.",
      "recommendation": "Add concrete pass/fail thresholds.",
      "disposition": "Resolved with 10-minute average, burst recovery, cumulative high-power, and stability thresholds."
    },
    {
      "dedupeKey": "tau-write-failure-policy",
      "severity": "major",
      "status": "resolved",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "escapedBlocker": false,
      "novelIssueSource": "none",
      "evidence": "The plan did not define behavior when time-window write exists but fails.",
      "finding": "A non-critical Tau write could break PL1/PL2 application if not specified.",
      "recommendation": "Specify failure handling.",
      "disposition": "Resolved by logging Tau write failures and keeping PL1/PL2 writes authoritative."
    }
  ],
  "nextAction": {
    "type": "stop",
    "summary": "Plan review passed. Wait for explicit user approval before implementation."
  }
}
```

## Final Recommendation

The plan is ready for user review. It should not be implemented until the user approves the staged behavior, especially the decision to implement but not immediately enable profile-driven MSI EC shift mode in the installed service.
