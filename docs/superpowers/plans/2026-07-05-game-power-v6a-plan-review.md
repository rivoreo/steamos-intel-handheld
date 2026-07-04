# Plan Review Report: Game Power V6a Runtime Truth Layer

## Summary

| Reviewer | Role | Score | Verdict |
| --- | --- | ---: | --- |
| A | Architecture & Feasibility | -1 | REVISE |
| B | Completeness & Risk | -1 | REVISE |
| C | Quality & Conventions | -1 | REVISE |
| D | Decky UX/UI | -1 | REVISE |
| final-verification | Fresh verification sweep | +2 | APPROVE |

**Overall Result**: APPROVED AFTER SUB-AGENT BLOCKER FIXES
**Review Iterations**: 3 sweeps, budget 3/5
**Harness Status**: passed
**Reviewed Artifact**:
`docs/superpowers/specs/2026-07-04-game-power-v6-deep-research-brief.md`
**Final Surface Hash**:
`013b748287356dcd0f0f8ca7bf6170fd0528064cf9dea06e5e6600488bc7ec22`

## Bounded Surface

Implementation surface allowed by the approved V6a plan:

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

Out of scope:

- New automatic cgroup writes.
- Hard thread affinity.
- sched_ext/scx_lavd enablement.
- Default CPU max-frequency caps.
- Claiming target-aware control while `fps_target.status` is `unknown` or
  `frame_source.status` is not `live`.

## Confirmed Blockers And Resolution

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 3,
  "budget": {"maxSweeps": 5, "sweepsUsed": 3},
  "harnessStatus": "passed",
  "reason": "all verified blockers were resolved and final verification found no remaining critical or major blocker",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C", "D", "final-verification"],
  "surfaceId": "game-power-v6a-runtime-truth-layer",
  "scores": {
    "A": -1,
    "B": -1,
    "C": -1,
    "D": -1,
    "final-verification": 2
  },
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 7,
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
    "maxMaterialRevisionAttempts": 2,
    "heldOutSweepsUsed": 1,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "userCheckpoints": {
    "intakeAsked": false,
    "decisionAsked": false,
    "recordedAssumptions": [
      "The user authorized the agent to make product decisions without stopping for more questions.",
      "V6a should be a convergent implementation slice, not an unbounded scheduler rewrite."
    ]
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "a9ed42be4594fef48488d1065dac124182f709ef0f70c92c22e387dbae47aecf",
    "currentSurfaceSnapshot": "013b748287356dcd0f0f8ca7bf6170fd0528064cf9dea06e5e6600488bc7ec22",
    "latestRevisionDiff": "docs/superpowers/specs/2026-07-04-game-power-v6-deep-research-brief.md"
  },
  "ledger": [
    {
      "dedupeKey": "daemon-telemetry-contract-missing",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "A/B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "The original plan asked for daemon/product telemetry but did not define the daemon-owned provider/snapshot contract.",
      "finding": "Decky and daemon telemetry could diverge or rely on standalone observe probes.",
      "recommendation": "Define daemon-owned snapshot, source enum, lifecycle, fallback, JSONL and Decky contract.",
      "disposition": "Resolved by GamePowerRuntimeSnapshot and frame-source schema."
    },
    {
      "dedupeKey": "target-state-schema-incompatible",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "The original plan wanted unknown/unlimited target states, while current CLI/cache shape collapses missing target.",
      "finding": "Target state could not represent source/confidence without a numeric FPS target.",
      "recommendation": "Define target-state schema and old hint-cache compatibility.",
      "disposition": "Resolved by V6a target-state schema."
    },
    {
      "dedupeKey": "decky-authoritative-snapshot-missing",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "Current Decky backend reads service mode and then launches a separate observe-only CLI probe.",
      "finding": "Decky could show probe data as if it were the running daemon decision.",
      "recommendation": "Define authoritative daemon/service snapshot and probe labelling.",
      "disposition": "Resolved by Decky consuming daemon-owned snapshot and labelling probe samples."
    },
    {
      "dedupeKey": "decky-state-matrix-missing",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "D",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "The original Decky section listed fields but not UI states.",
      "finding": "Loading/error/no-game/service-down/stale/unknown/waiting behavior was unspecified.",
      "recommendation": "Add handheld screen/state matrix.",
      "disposition": "Resolved by the V6a Decky matrix."
    },
    {
      "dedupeKey": "decky-copy-and-ia-unresolved",
      "severity": "major",
      "status": "resolved",
      "verification": "needs_decision",
      "reviewer": "D",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "The original plan left top-panel versus detail placement open.",
      "finding": "Automatic copy could still overclaim when target/frame telemetry is unknown.",
      "recommendation": "Decide first-panel IA and localized copy.",
      "disposition": "Resolved by choosing first-panel telemetry truth and fixed English/zh-Hant copy."
    },
    {
      "dedupeKey": "v6-not-implementation-plan",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "C",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "The original artifact stated it was research-only and had open questions.",
      "finding": "It was not TDD-ready.",
      "recommendation": "Add implementation surface, first failing tests, schemas, and resolved decisions.",
      "disposition": "Resolved by V6a implementation surface and TDD plan."
    },
    {
      "dedupeKey": "harness-acceptance-not-closed",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "C",
      "firstSeenIteration": 1,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "The original validation bullets did not map to harness gates.",
      "finding": "Local tests could be mistaken for hardware/profile proof.",
      "recommendation": "Separate focused tests, required sweep, guarded device checks, and screenshot evidence.",
      "disposition": "Resolved by Verification And Harness Gates."
    },
    {
      "dedupeKey": "background-shaping-boundary-inconsistent",
      "severity": "major",
      "status": "resolved",
      "verification": "confirmed",
      "reviewer": "C/final-verification",
      "firstSeenIteration": 1,
      "lastSeenIteration": 3,
      "materialRevisionAttempts": 2,
      "novelIssueSource": "none",
      "evidence": "Existing profiler writer variants could conflict with a dry-run-only V6a boundary.",
      "finding": "The plan did not account for pre-existing profile-triggered writer surfaces.",
      "recommendation": "State V6a does not extend, call, default, or use existing writer variants as evidence, and require tests proving the new path cannot invoke them.",
      "disposition": "Resolved by explicit write-surface limits and final verification."
    }
  ],
  "nextAction": {
    "type": "stop",
    "summary": "Plan Review passed; proceed to V6a implementation using the approved bounded surface."
  }
}
```

## Verification Commands

- `scripts/harness.py sweep required --report .cache/harness/required.json`
  - Passed after the research brief was added.
  - Passed again after the V6a plan revision.
  - Passed again after final background-shaping boundary clarification.

Device/profile evidence was gathered before plan review and recorded in the
reviewed artifact. No new device validation was required for the plan-only
review.
