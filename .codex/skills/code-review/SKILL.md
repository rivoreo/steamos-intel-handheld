---
name: code-review
description: Use this skill for reviewing code changes, diffs, pull requests, completed implementation tasks, or merge readiness when the review should run as an automatic convergence loop. It replaces one-shot or fixed-round code review with full-diff sweeps, issue-ledger tracking, held-out final review, plateau detection, and a harness-readable status. Trigger for Code Review, PR review, review this diff, merge gate, implementation review, or Goal/Cell review steps over code.
---

# Code Review

Review code until it reaches a real convergence state. This skill is intended
for automated Goal/Cell/harness loops where code review should keep improving
the change while there are useful blockers, then stop without a fixed round cap.

## Core Model

The loop returns one of three statuses:

- `passed`: no blocking code issues remain and a held-out full-diff sweep finds
  no new evidence-backed blocker.
- `continue`: fixable blocking issues remain; patch the code/tests and review
  again.
- `blocked`: the loop has plateaued, oscillated, needs a human/product/security
  decision, lacks evidence, or hit an external Goal/harness budget.

Do not stop because a hard-coded number of review rounds was reached. Do not
continue after remaining issues are non-blocking or unfixable without new input.

## Inputs

Prepare these before starting:

1. What changed: short implementation summary.
2. Requirements or plan: task text, plan file, issue, or acceptance criteria.
3. Git range: base SHA and head SHA, or an explicit diff/file list.
4. Verification evidence: tests/build/smoke commands already run, if any.
5. Review state snapshots for iteration > 1: `originalDiffSnapshot`,
   `currentDiffSnapshot`, and `latestPatchDiff`.
6. Known risk areas or intentional deviations.

## Reviewer Lanes

Use independent reviewers or independent passes. Each lane may be one subagent
or one clearly separated review pass.

| ID | Lane | Focus |
| --- | --- | --- |
| A | Correctness & Plan Alignment | Requirement coverage, behavior, regressions, API contracts |
| B | Risk & Safety | Security, auth, data loss, concurrency, migrations, rollback |
| C | Tests & Verification | TDD evidence, real behavior coverage, missing tests, flaky gates |
| D | Maintainability | Simplicity, local patterns, coupling, readability, abstractions |
| E | UX/Product Impact | User-visible changes, i18n, error states, rollout, docs |

A, B, C, and D are active by default. Activate E when the diff touches UI,
user-facing API contracts, release behavior, documentation that users consume,
or product workflows.

## Convergence Loop

### 1. Bound Review Surface

Freeze a bounded review surface before the first reviewer runs. A review whose
scope can expand forever cannot converge.

The surface must include:

- diff boundaries: base/head range, changed files, generated files, and ignored
  paths
- requirement boundaries: plan, issue, acceptance criteria, and intentional
  deviations
- evidence boundaries: available tests/build/smoke results and known missing
  evidence
- active reviewer lanes and the dimensions each lane owns

Review the entire bounded diff surface, not only the new, changed, latest, or
incremental patch since the last review. The normal surface is the full
base-to-head diff plus directly touched-file context needed to understand that
diff. Do not expand into a whole-repository audit.

Reviewers may mark blockers only inside this surface. Unrelated documents,
files, tests, or harness behavior are outside the blocker surface unless they
are part of the diff, a directly referenced contract, or listed evidence
boundary. If a finding requires external facts, unrelated files, or a broader
product decision, mark it `needs_decision` or `missing_evidence`, not `open`.

Store an attribution evidence pack when the surface is frozen:

```json
{
  "attributionEvidence": {
    "originalDiffSnapshot": "stable path, hash, or artifact id for the first reviewed bounded diff",
    "currentDiffSnapshot": "stable path, hash, or artifact id for the current bounded diff",
    "latestPatchDiff": "diff from the previous reviewed head to the current head"
  }
}
```

The exact storage can be a git range, file path, content hash, or persisted Cell
artifact, but it must let later reviewers compare the original diff, current
diff, and latest patch. A prose fix changelog alone is useful context but is not
enough to classify whether a late blocker was introduced by the latest patch or
was missed from the original diff.

### 2. Discovery Sweep

Start with a fresh full-diff sweep. Reviewers inspect the whole current change,
not only recent patches.

Recommended commands:

```bash
git diff --stat <base>..<head>
git diff <base>..<head>
git diff --check <base>..<head>
```

Also inspect tests and touched files directly when the diff is not enough to
understand surrounding behavior. Findings must cite file:line references,
requirements, or concrete test evidence. Vague advice is a suggestion, not a
blocker. After this sweep, the surface is considered covered once all active
lanes have returned parseable output.

### 3. Issue Ledger

Normalize findings into an Issue Ledger. This ledger is the loop memory; later
reviewers should not receive the full prose from prior review rounds.

Each item uses:

```json
{
  "dedupeKey": "stable lowercase key for this issue",
  "severity": "critical|important|minor|suggestion",
  "status": "open|resolved|wontfix|needs_decision|missing_evidence|stale",
  "lane": "A|B|C|D|E",
  "firstSeenIteration": 1,
  "lastSeenIteration": 1,
  "materialFixAttempts": 0,
  "escapedBlocker": false,
  "novelIssueSource": "none|revision_introduced|latent_missed|scope_expansion|unsupported",
  "file": "path/to/file.ext",
  "line": 42,
  "evidence": "specific code/test/requirement evidence",
  "finding": "what is wrong",
  "recommendation": "specific fix",
  "disposition": "why it is open/resolved/non-blocking"
}
```

Critical and important items block merge readiness unless they are outside scope,
duplicates, or lack concrete evidence. Minor and suggestion items do not block
convergence.

### 4. Patch

Patch open blockers with the smallest code and test changes that satisfy the
requirements. Preserve TDD evidence: for behavior changes, add or update a
failing test first, confirm Red, implement Green, then refactor.

Produce a changelog mapping ledger keys to commits or edits:

```markdown
## Review Fix Changelog (Iteration N -> N+1)

1. **[IMPORTANT/B][missing-auth-check]** Added ownership guard
   - Finding: handler trusted request accountId.
   - Change: handler now derives owner from auth context; added denial test.
```

Increment `materialFixAttempts` only when the patch changed code or tests in a
way that could reasonably resolve that ledger item.

### 5. Verification Sweep

Run the next iteration as a fresh full-diff sweep over the current base-to-head
diff. Reviewers receive:

- current diff or git range
- requirements/plan
- current Issue Ledger with statuses
- fix changelog
- verification evidence
- attributionEvidence containing `originalDiffSnapshot`,
  `currentDiffSnapshot`, and `latestPatchDiff`

Each reviewer must:

1. Recheck open and resolved blockers.
2. Perform a fresh full sweep for missed blockers and regressions.

This prevents both shallow delta-only reviews and anchoring on previous prose.

### 6. Novel Issue Attribution

After the bounded review surface has had a discovery sweep and one verification
sweep, every new critical/important finding must be attributed before it can
affect convergence. A late finding may be a regression introduced by the patch,
or it may be an original in-surface issue that review missed. Treat those cases
differently.

- Is the finding inside the bounded review surface?
- Does it include concrete file:line, requirement, or test evidence?
- Is it materially different from an existing ledger item?
- Was it introduced by the patch, or did it exist from the start?
- Was it missed because the reviewer lane/rubric/surface was unclear?

Make this attribution from `attributionEvidence`, not from reviewer memory or
the prose fix changelog alone. If `originalDiffSnapshot`,
`currentDiffSnapshot`, or `latestPatchDiff` is unavailable, return `blocked`
with `reasonCode: "missing_evidence"` instead of guessing.

Set `novelIssueSource` for each late blocker:

| Source | Meaning | Gate behavior |
| --- | --- | --- |
| `revision_introduced` | The latest patch created a new blocker or regression. | Add it as an open ledger blocker and continue the normal patch loop. Reset `latentMissedBlockerStreak` because this is a code-quality problem, not a review miss. |
| `latent_missed` | The blocker existed inside the original bounded diff surface and should have been caught in discovery or verification. | Mark it as an escaped blocker and increment `escapedBlockerStreak` and `latentMissedBlockerStreak`. |
| `scope_expansion` | The finding requires files, generated output, runtime state, requirements, or product scope outside the frozen surface. | Do not mark it `open` in this loop. Return `blocked` with `needs_decision` or `missing_evidence`, or start a new review with a new surface if the harness explicitly expands scope. |
| `unsupported` | The finding lacks concrete evidence or is only a preference. | Record as minor/suggestion or stale; it must not block convergence. |

If one valid `latent_missed` blocker appears, add it to the Issue Ledger and
continue once with `escapedBlockerStreak: 1` and
`latentMissedBlockerStreak: 1`. If the next full sweep also finds one or more
`latent_missed` blockers, `latentMissedBlockerStreak >= 2` and
`escapedBlockerStreak >= 2` must return `blocked` with
`reasonCode: "review_process_defect"`. That means the code-review skill, rubric,
or surface definition is too open-ended; the harness should stop and fix the
review process rather than patching forever.

If every patch keeps introducing different `revision_introduced` blockers, do
not blame the review skill. Continue while each blocker is concrete and fixable.
If the same area keeps producing new critical/important regressions after
material fixes and no smaller fix is available, return `blocked` as plateau or
artifact instability with the unstable ledger items.

### 7. Held-Out Sweep

When the normal gate has no open blockers, run a held-out sweep before returning
`passed`. Use a new reviewer or lane set that receives only:

- final diff or git range
- requirements/plan
- test/build evidence

Do not provide the Issue Ledger or changelog. If the held-out sweep finds no
evidence-backed critical/important issues, the loop has converged. If it finds a
real blocker, add it to the ledger and continue. If it finds only minor or
suggestion items, record them as non-blocking and pass.

### 8. Plateau And Oscillation Detection

Return `blocked` instead of looping when any of these is true:

- The same blocker remains open after two material fixes and no reviewer can
  provide new, actionable evidence.
- Fixes toggle between incompatible reviewer demands.
- The remaining issue requires a product, security, legal, release, or ownership
  decision not present in the requirements.
- The diff cannot be reviewed because generated code, external dependency state,
  migrations, or test evidence are missing.
- `latent_missed` blockers keep appearing after consecutive full sweeps,
  indicating `review_process_defect`.
- Patches repeatedly introduce new unrelated critical/important blockers in the
  same area, indicating artifact instability rather than review failure.
- The outer Goal/harness budget is exhausted.

Blocked is a valid convergence outcome. It tells the harness to stop automatic
review and surface the unresolved ledger.

## Gate Logic

| Condition | harnessStatus | Next action |
| --- | --- | --- |
| Open critical/important issues exist and are fixable | `continue` | Patch and run another full-diff sweep |
| `unresolvedDecisionItems > 0` or `unresolvedEvidenceItems > 0` | `blocked` | Stop with `needs_decision` or `missing_evidence` |
| New blocker has `novelIssueSource=revision_introduced` | `continue` | Add an open blocker to the ledger and patch again |
| New blocker has `novelIssueSource=scope_expansion` | `blocked` | Stop with `needs_decision` or `missing_evidence`, or restart with a new surface |
| New blocker has `novelIssueSource=unsupported` | no status change | Record as non-blocking or stale |
| `latentMissedBlockerStreak >= 2` and `escapedBlockerStreak >= 2` | `blocked` | Stop with `review_process_defect` |
| No open blockers, held-out sweep not yet run | `continue` | Run held-out sweep |
| No open blockers after held-out sweep | `passed` | Stop |
| Plateau, oscillation, repeated `latent_missed` blockers, missing evidence, or external budget | `blocked` | Stop with unresolved ledger or review process defect |

The Issue Ledger is the convergence state. Reviewer text is supporting evidence.

## Harness Output Contract

Every iteration must end with one JSON block:

```json
{
  "schemaVersion": "lunatalk.review-loop.v1",
  "reviewType": "code",
  "iteration": 4,
  "harnessStatus": "continue|passed|blocked",
  "reason": "short reason for the status",
  "reasonCode": "open_blockers|held_out_required|converged|plateau|oscillation|artifact_instability|missing_evidence|needs_decision|review_process_defect|reviewer_failure|budget_exhausted",
  "base": "<base-sha-or-label>",
  "head": "<head-sha-or-label>",
  "surfaceId": "stable hash or short name for the bounded review surface",
  "activeLanes": ["A", "B", "C", "D"],
  "convergence": {
    "openBlockers": 1,
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
    "maxMaterialFixAttempts": 1,
    "heldOutSweepComplete": false,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "attributionEvidence": {
    "originalDiffSnapshot": "<path-or-hash-or-artifact-id>",
    "currentDiffSnapshot": "<path-or-hash-or-artifact-id>",
    "latestPatchDiff": "<path-or-hash-or-artifact-id>"
  },
  "ledger": [],
  "verification": {
    "commandsRun": [],
    "missingEvidence": []
  },
  "nextAction": {
    "type": "patch|fresh_full_sweep|held_out_sweep|stop",
    "summary": "what the harness should do next"
  }
}
```

The harness must store this JSON as the Cell result and use it to decide whether
to schedule the next loop iteration.

## Failure Handling

- Retry a failed lane once with the same diff, requirements, and lane.
- If a lane still fails, mark that lane as a coverage gap and continue only if
  fewer than half of active lanes failed.
- If half or more active lanes fail, return `blocked` with reasonCode
  `reviewer_failure`; do not spin.
- JSON parse failure is a reviewer failure.

## Review Calibration

- Lead with findings, ordered by severity, with file:line references.
- Do not mark preferences or style nits as blockers.
- Missing tests can be important when behavior changed without evidence.
- If the implementation is correct but the plan is wrong, say so and mark the
  issue as `needs_decision` when the code cannot resolve it alone.
- Do not claim readiness without fresh verification evidence.

## Eval Seeds

Use `evals/evals.json` with Skill Creator when improving this skill. The evals
cover real blocker discovery, anti-anchoring across patches, held-out final
review, and plateau exit behavior.
