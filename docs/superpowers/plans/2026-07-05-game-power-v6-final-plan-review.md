# Plan Review Report: Game Power V6 Final

## Summary

| Reviewer | Role | Sweep 1 | Sweep 2 | Verdict |
| --- | --- | --- | --- | --- |
| A | Scheduler safety / shared-power policy | REVISE | APPROVE | APPROVE |
| B | Decky product / UI / backend API | REVISE | APPROVE | APPROVE |
| C | Harness / TDD / verification, local main-agent review | REVIEW | APPROVE | APPROVE |

**Overall Result**: APPROVED AFTER REVISION

Reviewed artifact:
`docs/superpowers/plans/2026-07-05-game-power-v6-final.md`

Research ledger:
`docs/superpowers/specs/2026-07-05-game-power-v6-final-research-ledger.md`

## Bounded Surface

Approved implementation surface:

- `src/steamos_intel_handheld/game_power.py`
- `src/steamos_intel_handheld/game_power_control.py`
- `src/steamos_intel_handheld/power_control.py`
- `src/steamos_intel_handheld/game_power_profile.py` only if target parsing is
  deduplicated
- `decky/steamos-intel-handheld-game-power/main.py`
- `decky/steamos-intel-handheld-game-power/src/index.tsx`
- `decky/steamos-intel-handheld-game-power/dist/index.js`
- focused tests named in the plan
- V6 final docs and review reports

Explicitly out of scope:

- default CPU max-frequency caps,
- default cgroup `cpu.weight` or `cpu.uclamp.*` writes,
- hard thread affinity,
- sched_ext/scx enablement,
- exposing raw P/E-core, PL2/Tau, threshold, cgroup, RAPL, sysfs, or affinity
  controls in Decky.

## Confirmed Blockers And Resolution

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "surfaceId": "game-power-v6-final",
  "iteration": 2,
  "budget": {"maxSweeps": 5, "sweepsUsed": 2},
  "harnessStatus": "plan-only-stale-required-sweep",
  "reason": "all verified blockers resolved; no new blockers found in held-out sweep",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C"],
  "scores": {
    "A": 2,
    "B": 2,
    "C": 2
  },
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 4,
    "refutedFindings": 3,
    "unresolvedDecisionItems": 0,
    "unresolvedEvidenceItems": 0,
    "newBlockers": 0,
    "reopenedBlockers": 0,
    "heldOutSweepsUsed": 1
  },
  "userCheckpoints": {
    "intakeAsked": false,
    "decisionAsked": false,
    "recordedAssumptions": [
      "The user authorized autonomous implementation after Plan Review.",
      "Manual FPS target is a product-level scheduler objective, not a dangerous low-level tuning knob."
    ]
  },
  "ledger": [
    {
      "dedupeKey": "fps-target-not-in-sample-path",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "finding": "Manual/autodetected FPS target could update status/config while GamePowerController still evaluates targetless samples.",
      "resolution": "Plan now requires FrameTargetProvider to be called by SystemGamePowerObserver for every foreground-game sample before classification, hint context, and runtime snapshot."
    },
    {
      "dedupeKey": "targetless-learning-identity-conflict",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "finding": "Incomplete targetless contexts could fail to open visibility sessions and legacy none-configured cache entries could still be reusable.",
      "resolution": "Plan now splits visibility session identity from reusable canonical hint key and normalizes legacy none-configured/unknown/missing target contexts as non-reusable."
    },
    {
      "dedupeKey": "fps-target-api-validation-missing",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "finding": "FPS slider range existed in UI plan but CLI/backend validation and status shape were underspecified.",
      "resolution": "Plan now requires integer 30-120 FPS, 5-FPS steps, no mutation on invalid input, structured fps_target_override status, and one Decky callable with CLI as authority."
    },
    {
      "dedupeKey": "learning-copy-matrix-missing",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "finding": "Learning-state copy could mislead users because only three states were named.",
      "resolution": "Plan now includes en/zh-Hant copy matrix for target unknown, observe mode, off mode, insufficient samples, contradicted hints, and older daemons without learning state."
    }
  ]
}
```

## Harness Notes

Plan review used read-only commands only. `scripts/harness.py status --json`
reported the last required local sweep as stale because the workspace changed.
The implementation phase must run focused pytest first and then:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

Device-facing claims still require:

```bash
scripts/install-on-device.sh root@10.100.0.19
VERIFY_TDP_POLICY_MODE=ac-performance scripts/verify-on-device.sh root@10.100.0.19
scripts/verify-game-power-on-device.sh root@10.100.0.19
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

## Next Action

Proceed to TDD implementation using the approved bounded surface.
