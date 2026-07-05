# Game Power V8 Affinity Coordinator Sub-Agent Plan Review

## Review Surface

- `docs/superpowers/specs/2026-07-05-game-power-v8-affinity-coordinator-design.md`
- `docs/superpowers/plans/2026-07-05-game-power-v8-affinity-coordinator.md`

Context: V8 may add guarded profile-stage affinity automation. It must not add
daemon default affinity writes, Decky apply controls, or unverified performance
claims. Reviewers A, B, and C were run as sub-agents.

## Sub-Agent Results

- Reviewer A / Architecture / sub-agent `019f30d9-d02a-78d2-87fc-30120789dbfa`
  returned `REVISE`.
- Reviewer B / Risk / sub-agent `019f30d9-f46b-72e1-ac99-6eb90b76a753`
  returned `REVISE`.
- Reviewer C / Quality / sub-agent `019f30da-1d79-72a0-9d41-44c3973c2776`
  returned `REVISE`.

## Confirmed Blockers And Fixes

1. **Foreground affinity evidence was not part of summary/aggregate.**
   - Reviewers: A, C.
   - Finding: A `gpu-priority-affinity` run could be compared even when no
     affinity was actually applied or when `taskset` failed.
   - Fix: Plan now adds foreground-affinity evidence fields to `RunSummary`,
     summary loading, and aggregate/comparison validity. Candidate evidence is
     valid only when writes succeeded and restore returned clean.

2. **Zero-match/no-op affinity runs were not rejected.**
   - Reviewers: B, C.
   - Finding: A resolved plan with zero matching current threads could still
     become a labeled affinity run.
   - Fix: Plan now requires `matched_thread_count`, `written_count`, and
     fail-closed CLI behavior before sampling if no writes succeeded.

3. **Partial apply failure and missing `taskset` were under-specified.**
   - Reviewer: B.
   - Finding: One successful write followed by a failure could leave modified
     threads if shell `set -e` exited early.
   - Fix: Plan now requires `check=False`, explicit returncode/status mapping,
     always-written diagnostic reports, immediate restore on apply failure, and
     wrapper guarded apply-then-restore-on-failure semantics.

4. **Restore mismatch behavior was under-specified.**
   - Reviewers: B, C.
   - Finding: Restore tests and plan covered success only.
   - Fix: Plan now requires restore statuses for nonzero return, output mismatch,
     missing original mask, skipped entries, and `restored=false` propagation to
     the wrapper failure path.

5. **Stale TID protection needed current `/proc` revalidation.**
   - Reviewer: A.
   - Finding: Current plan recomputed role key from snapshot values only.
   - Fix: Plan now requires re-reading current `/proc/<pid>/task/<tid>` identity,
     cgroup, and affinity before each write and restore, recording stale or
     mismatched tasks instead of writing them.

6. **Hard-mask sizing needed explicit safety logic.**
   - Reviewer: A.
   - Finding: Directly using `preferred_cpu_overlap` can over-constrain
     multi-thread roles.
   - Fix: Plan now adds a compact-mask planner that rejects empty masks and
     rejects single-CPU masks for multi-thread roles.

7. **Remote SSH env quoting risk from debug overrides.**
   - Reviewer: B.
   - Finding: Passing free-form role/cpu values through the wrapper's inline SSH
     env would be fragile.
   - Fix: Plan now removes wrapper debug envs. The wrapper accepts only
     `PROFILE_GAME_POWER_AFFINITY_PLAN_JSON`, copies it to the target, and
     resolves the candidate remotely. Manual role/cpu control remains only on
     the Python CLI subcommand.

8. **Plan quality and harness preflight gaps.**
   - Reviewer: C.
   - Finding: The plan originally had placeholders and omitted
     `scripts/harness.py list --json`.
   - Fix: Placeholders were removed and harness list was added to verification.

## Harness Result

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 2,
  "budget": {"maxSweeps": 5, "sweepsUsed": 3},
  "harnessStatus": "passed",
  "reason": "Sub-agent confirmed blockers were incorporated into the plan surface.",
  "reasonCode": "converged",
  "activeReviewers": ["A", "B", "C"],
  "surfaceId": "game-power-v8-affinity-coordinator-subagent-reviewed",
  "scores": {"A": -1, "B": -1, "C": -1},
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 8,
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
    "heldOutSweepsUsed": 0,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "userCheckpoints": {
    "intakeAsked": false,
    "decisionAsked": false,
    "recordedAssumptions": [
      "V8 scope is profile-stage automation only.",
      "Real performance claims require guarded device profiling.",
      "Wrapper automation consumes plan JSON, not free-form role/cpu env values."
    ]
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "Initial V8 plan before sub-agent review.",
    "currentSurfaceSnapshot": "Plan/spec after sub-agent blocker fixes.",
    "latestRevisionDiff": "Added foreground-affinity evidence model, no-op rejection, partial failure rollback, stale task revalidation, compact-mask planner, and plan-json-only wrapper input."
  },
  "ledger": [],
  "nextAction": {
    "type": "stop",
    "summary": "Proceed to implementation."
  }
}
```
