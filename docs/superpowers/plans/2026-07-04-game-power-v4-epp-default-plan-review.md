# Plan Review Report: Game Power V4 EPP-Only Default

## Summary

| Reviewer | Role | Score | Verdict |
| --- | --- | ---: | --- |
| A | Architecture & Feasibility | +2 | APPROVE |
| B | Completeness & Risk | +1 | APPROVE |
| C | Quality & Conventions | +1 | APPROVE |
| E | Product & Value | +2 | APPROVE |

**Overall Result**: APPROVED WITH NOTES
**Review Iterations**: 1 discovery + 1 held-out sweep, budget 2/5
**Harness Status**: passed
**Active Reviewers**: A, B, C, E. Reviewer D not active because this plan does
not change Decky UI or user-visible interaction flow.
**Findings**: 1 confirmed non-blocking / 1 refuted / 0 needs-decision
**User Checkpoints**: intake asked no. The user already authorized V4
implementation and asked not to pause for decisions.

## Bounded Surface

Reviewed artifact:

- `docs/superpowers/specs/2026-07-04-game-power-v4-epp-default-design.md`

Context:

- Cyberpunk controlled profile artifacts under `.cache/game-power/profiles/`
- current service default includes `--game-power-cpu-cap on`
- upstream docs for Linux uclamp, EAS, sched_ext, systemd resource control, and
  Feral GameMode

Out of scope:

- new Decky UI
- sched_ext deployment
- automatic hot-thread pinning
- per-AppID winner promotion

## Key Strengths

- The plan is deliberately small and shippable: it changes the unsafe default
  without removing the profiler path needed for later CPU-cap research.
- The acceptance criteria cover CLI, packaged unit, docs, local Harness, and
  real-device validation.
- The plan separates evidence-backed default policy from future research ideas,
  preventing a broader scheduler redesign from blocking this safety fix.

## Issue Ledger

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 1,
  "budget": {"maxSweeps": 5, "sweepsUsed": 2},
  "harnessStatus": "passed",
  "reason": "no verified blocking findings remain after held-out sweep",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C", "E"],
  "surfaceId": "game-power-v4-epp-default-design",
  "scores": {"A": 2, "B": 1, "C": 1, "E": 2},
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 1,
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
    "maxMaterialRevisionAttempts": 0,
    "heldOutSweepsUsed": 1,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "userCheckpoints": {
    "intakeAsked": false,
    "decisionAsked": false,
    "recordedAssumptions": []
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "docs/superpowers/specs/2026-07-04-game-power-v4-epp-default-design.md",
    "currentSurfaceSnapshot": "docs/superpowers/specs/2026-07-04-game-power-v4-epp-default-design.md",
    "latestRevisionDiff": "initial review"
  },
  "ledger": [
    {
      "dedupeKey": "device-validation-must-check-active-execstart",
      "severity": "minor",
      "status": "open",
      "verification": "not_required",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 0,
      "novelIssueSource": "none",
      "evidence": "Acceptance criteria mention device deployment and foreground-game verification.",
      "finding": "The implementation should explicitly check the active service ExecStart after deployment so a stale unit cannot masquerade as deployed.",
      "recommendation": "Run systemctl cat/is-active on the device after install and record the active ExecStart.",
      "disposition": "Non-blocking because the acceptance criteria already require the check; keep as execution note."
    },
    {
      "dedupeKey": "cpu-cap-removal-may-prevent-future-wins",
      "severity": "major",
      "status": "refuted",
      "verification": "refuted",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 0,
      "novelIssueSource": "none",
      "evidence": "Decision items keep CPU-cap tunables and profile scripts.",
      "finding": "Turning CPU cap off by default could remove the path needed to improve CPU/GPU contention.",
      "recommendation": "Keep CPU cap available for explicit profiling.",
      "disposition": "Refuted: the plan explicitly keeps `--game-power-cpu-cap on` and `gpu-priority-cpu-cap` profile policies for evidence-gated future work."
    }
  ],
  "nextAction": {
    "type": "stop",
    "summary": "Proceed to TDD implementation of EPP-only default."
  }
}
```

## Non-Blocking Notes

- During device validation, check both the installed unit text and the active
  running process or `systemctl cat` output.
- The profile wrapper should remain unchanged in this iteration so CPU cap can
  continue to be compared under controlled A/B runs.

## Final Recommendation

Proceed with the implementation exactly as scoped. This is a default-safety
change, not a final answer to scheduler design. The next research iteration
should build an evidence gate that can promote stronger controls only when
they repeatedly win for a stable hardware and game context.
