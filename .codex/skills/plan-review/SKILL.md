---
name: plan-review
description: Use this skill for plan, architecture, product, technical design, or implementation-plan review when the review should improve the artifact through an automatic convergence loop. It replaces fixed round-count review with issue-ledger tracking, fresh full sweeps, held-out final sweeps, plateau detection, and a harness-readable status. Trigger for /plan-review, design review, plan gate, architecture review, or any LunaTalk workflow phase that needs independent reviewers before implementation.
---

# Plan Review

Review a plan until it reaches a real convergence state. This skill is designed
for Goal/Cell/harness loops: it does not stop because a fixed round limit was
hit, and it does not keep looping after the review has stopped producing useful,
fixable blockers.

## Core Model

The review loop has three possible harness outcomes:

- `passed`: no blocking issues remain and a final held-out sweep finds no new
  evidence-backed blocker.
- `continue`: fixable blocking issues remain; revise the plan and run another
  review iteration.
- `blocked`: the loop has plateaued, oscillated, requires a human/product
  decision, lacks required evidence, or hit an external Goal/harness budget.

Do not use a fixed round cap as the stopping condition. Runtime budgets belong
to the outer harness. This skill defines convergence semantics so the harness can
stop for a reason that is visible and reviewable.

## Inputs

Prepare these before starting:

1. Plan artifact: inline text or file path.
2. Context summary: problem, constraints, scope, acceptance criteria.
3. Relevant code/document references.
4. Review state snapshots for iteration > 1: `originalSurfaceSnapshot`,
   `currentSurfaceSnapshot`, and `latestRevisionDiff`.
5. Optional concern list.

## Reviewer Roles

| ID | Role | Focus |
| --- | --- | --- |
| A | Architecture & Feasibility | Architecture, feasibility, API/data contracts, integration, performance |
| B | Completeness & Risk | Requirements, edge cases, security, failures, migration, rollback |
| C | Quality & Conventions | Project conventions, testability, clarity, reuse, maintainability |
| D | UX/UI Design | User journeys, loading/empty/error states, accessibility, interaction flow |
| E | Product & Business Value | MVP scope, priority, business value, success metrics, rollout |

A, B, and C are always active. Activate D when the plan changes UI, user
journeys, user-visible states, or frontend data shapes. Activate E when the plan
changes product behavior, user-facing APIs, workflow scope, pricing, release
strategy, or measurable business outcomes.

## Convergence Loop

### 1. Bound Review Surface

Freeze a bounded review surface before the first reviewer runs. A review whose
scope can expand forever cannot converge.

The surface must include:

- artifact boundaries: exact plan files or sections under review
- context boundaries: requirements, constraints, code references, and acceptance
  criteria available to reviewers
- active reviewer roles and the dimensions each role owns
- explicit out-of-scope areas

Review the entire bounded artifact, not only the new, changed, latest, or
incremental paragraphs. If the surface is one design document, reviewers inspect
that full document, including older sections that interact with the change. If
the surface is a named section, reviewers inspect that whole section and the
explicitly listed cross-references.

Reviewers may mark blockers only inside this surface. Unrelated documents,
files, tests, or harness behavior are outside the blocker surface unless they
are explicitly listed as context boundaries. If a finding requires external
facts or broader scope, mark it `needs_decision` or `missing_evidence`, not
`open`.

Store an attribution evidence pack when the surface is frozen:

```json
{
  "attributionEvidence": {
    "originalSurfaceSnapshot": "stable path, hash, or stored text for the first reviewed bounded surface",
    "currentSurfaceSnapshot": "stable path, hash, or stored text for the current bounded surface",
    "latestRevisionDiff": "diff or section-level change summary from the previous iteration to the current one"
  }
}
```

The exact storage can be a file path, content hash, or persisted Cell artifact,
but it must let later reviewers compare the original surface, current surface,
and latest revision. A prose changelog alone is useful context but is not enough
to classify whether a late blocker was introduced by the latest revision or was
missed from the original surface.

### 2. Discovery Sweep

Start with a fresh full sweep. Each active reviewer receives only:

- the current plan artifact
- the context summary
- role-specific rubric from `references/reviewer-prompts.md`
- the required JSON output format from `references/scoring-rubric.md`

They must review the whole artifact, not only the parts likely to be wrong. They
must cite evidence: section names, file paths, API contracts, missing acceptance
criteria, or explicit contradictions. Findings without evidence are suggestions,
not blockers. After this sweep, the surface is considered covered once all
active reviewers have returned parseable output.

### 3. Issue Ledger

Normalize all findings into an Issue Ledger. This ledger is the loop memory; do
not feed previous full review prose into later reviewers.

Each ledger item uses:

```json
{
  "dedupeKey": "stable lowercase key for this issue",
  "severity": "critical|major|minor|suggestion",
  "status": "open|resolved|wontfix|needs_decision|missing_evidence|stale",
  "reviewer": "A|B|C|D|E",
  "firstSeenIteration": 1,
  "lastSeenIteration": 1,
  "materialRevisionAttempts": 0,
  "escapedBlocker": false,
  "novelIssueSource": "none|revision_introduced|latent_missed|scope_expansion|unsupported",
  "evidence": "specific section/file/contract evidence",
  "finding": "what is wrong",
  "recommendation": "specific fix",
  "disposition": "why it is open/resolved/non-blocking"
}
```

Critical and major items are blockers unless they are outside the reviewer scope,
duplicate an existing ledger item, or lack concrete evidence. Minor and
suggestion items never block convergence.

### 4. Revise

The main agent revises the plan. Produce a changelog that maps every addressed
blocker to a concrete change:

```markdown
## Revision Changelog (Iteration N -> N+1)

1. **[MAJOR/A][api-contract-auth]** Added auth and ownership rules
   - Finding: API contract omitted ownership checks.
   - Change: Added "Auth & Ownership" section and request/response examples.
```

Do not broaden scope while revising unless the ledger requires it. Increment
`materialRevisionAttempts` only when the plan changed in a way that could
reasonably resolve that ledger item.

### 5. Verification Sweep

Run the next iteration as a fresh full sweep, not a delta-only pass. Reviewers
receive:

- revised plan artifact
- current Issue Ledger with statuses, not the full previous commentary
- revision changelog
- attributionEvidence containing `originalSurfaceSnapshot`,
  `currentSurfaceSnapshot`, and `latestRevisionDiff`
- context summary and role rubric

Each reviewer must do two checks:

1. Verify whether open/resolved blockers were actually handled.
2. Perform a fresh full sweep of the whole artifact for regressions or missed
   blockers.

This avoids both bad extremes: it does not starve reviewers with only a diff,
and it does not anchor them on the previous review transcript.

### 6. Novel Issue Attribution

After the bounded review surface has had a discovery sweep and one verification
sweep, every new critical/major finding must be attributed before it can affect
convergence. A late finding may mean the artifact is still changing badly, or it
may mean the review process is missing in-surface issues. Treat those cases
differently.

- Is the finding inside the bounded review surface?
- Does it include concrete evidence?
- Is it materially different from an existing ledger item?
- Was it introduced by the revision, or did it exist from the start?
- Was it missed because the reviewer role/rubric/surface was unclear?

Make this attribution from `attributionEvidence`, not from reviewer memory or
the prose changelog alone. If `originalSurfaceSnapshot`,
`currentSurfaceSnapshot`, or `latestRevisionDiff` is unavailable, return
`blocked` with `reasonCode: "missing_evidence"` instead of guessing.

Set `novelIssueSource` for each late blocker:

| Source | Meaning | Gate behavior |
| --- | --- | --- |
| `revision_introduced` | The latest plan revision created a new blocker or regression. | Add it as an open ledger blocker and continue the normal revise loop. Reset `latentMissedBlockerStreak` because this is an artifact-quality problem, not a review miss. |
| `latent_missed` | The blocker existed inside the original bounded review surface and should have been caught in discovery or verification. | Mark it as an escaped blocker and increment `escapedBlockerStreak` and `latentMissedBlockerStreak`. |
| `scope_expansion` | The finding requires facts, requirements, files, or product scope outside the frozen surface. | Do not mark it `open` in this loop. Return `blocked` with `needs_decision` or `missing_evidence`, or start a new review with a new surface if the harness explicitly expands scope. |
| `unsupported` | The finding lacks concrete evidence or is only a preference. | Record as minor/suggestion or stale; it must not block convergence. |

If one valid `latent_missed` blocker appears, add it to the Issue Ledger and
continue once with `escapedBlockerStreak: 1` and
`latentMissedBlockerStreak: 1`. If the next full sweep also finds one or more
`latent_missed` blockers, `latentMissedBlockerStreak >= 2` and
`escapedBlockerStreak >= 2` must return `blocked` with
`reasonCode: "review_process_defect"`. That means the review skill, rubric, or
surface definition is too open-ended; the harness should stop and fix the review
process rather than revising the artifact forever.

If every revision keeps introducing different `revision_introduced` blockers,
do not blame the review skill. Continue while each blocker is concrete and
fixable. If the same section keeps producing new critical/major regressions
after material revisions and no smaller fix is available, return `blocked` as
plateau or artifact instability with the unstable ledger items.

### 7. Held-Out Sweep

When the normal gate has no open blockers, run a held-out sweep before returning
`passed`. Use a new reviewer or reviewer set that receives only the final plan,
context summary, and acceptance criteria. Do not provide the Issue Ledger or
changelog.

If the held-out sweep finds no evidence-backed critical/major blockers, the loop
has converged. If it finds a real blocker, add it to the ledger and continue.
If it finds only minor/suggestion items, record them as non-blocking and pass.

### 8. Plateau And Oscillation Detection

Return `blocked` instead of looping when any of these is true:

- The same blocker remains open after two material revisions and the reviewer
  provides no new fixable evidence.
- Reviewers alternate between incompatible requirements and the conflict cannot
  be resolved by technical evidence.
- The remaining blocker requires a product, legal, security, or release decision
  that is not encoded in the plan or context.
- The artifact cannot be reviewed because required code, data, or requirements
  are missing.
- `latent_missed` blockers keep appearing after consecutive full sweeps,
  indicating `review_process_defect`.
- Revisions repeatedly introduce new unrelated critical/major blockers in the
  same section, indicating artifact instability rather than review failure.
- The outer Goal/harness budget is exhausted.

When blocked, include unresolved ledger items, the attempted changes, and the
specific decision or missing evidence needed to resume.

## Gate Logic

Use score and ledger state together:

| Condition | harnessStatus | Next action |
| --- | --- | --- |
| Open critical/major blockers exist and are fixable | `continue` | Revise blockers and run another full sweep |
| `unresolvedDecisionItems > 0` or `unresolvedEvidenceItems > 0` | `blocked` | Stop with `needs_decision` or `missing_evidence` |
| New blocker has `novelIssueSource=revision_introduced` | `continue` | Add an open blocker to the ledger and revise again |
| New blocker has `novelIssueSource=scope_expansion` | `blocked` | Stop with `needs_decision` or `missing_evidence`, or restart with a new surface |
| New blocker has `novelIssueSource=unsupported` | no status change | Record as non-blocking or stale |
| `latentMissedBlockerStreak >= 2` and `escapedBlockerStreak >= 2` | `blocked` | Stop with `review_process_defect` |
| No open blockers, held-out sweep not yet run | `continue` | Run held-out sweep |
| No open blockers after held-out sweep | `passed` | Stop |
| Plateau, oscillation, repeated `latent_missed` blockers, missing evidence, or external budget | `blocked` | Stop with unresolved ledger or review process defect |

Review scores are reviewer opinions. The Issue Ledger is the convergence state.

## Harness Output Contract

Every iteration must end with one JSON block:

```json
{
  "schemaVersion": "lunatalk.review-loop.v1",
  "reviewType": "plan",
  "iteration": 4,
  "harnessStatus": "continue|passed|blocked",
  "reason": "short reason for the status",
  "reasonCode": "open_blockers|held_out_required|converged|plateau|oscillation|artifact_instability|missing_evidence|needs_decision|review_process_defect|reviewer_failure|budget_exhausted",
  "activeReviewers": ["A", "B", "C"],
  "surfaceId": "stable hash or short name for the bounded review surface",
  "scores": {"A": 1, "B": 2, "C": 1},
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
    "maxMaterialRevisionAttempts": 1,
    "heldOutSweepComplete": false,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "<path-or-hash-or-artifact-id>",
    "currentSurfaceSnapshot": "<path-or-hash-or-artifact-id>",
    "latestRevisionDiff": "<path-or-hash-or-artifact-id>"
  },
  "ledger": [],
  "nextAction": {
    "type": "revise|fresh_full_sweep|held_out_sweep|stop",
    "summary": "what the harness should do next"
  }
}
```

The harness must store this JSON as the Cell result and use it to decide whether
to schedule the next loop iteration.

## Failure Handling

- Retry a failed reviewer once with the same artifact and role.
- If a reviewer still fails, mark that role as a coverage gap and continue only
  if fewer than half of active reviewers failed.
- If half or more active reviewers fail, return `blocked` with reason
  reasonCode `reviewer_failure`; do not spin.
- JSON parse failure is a reviewer failure.

## References

- `references/reviewer-prompts.md`: role-specific reviewer prompts.
- `references/scoring-rubric.md`: scoring, severity, and JSON parsing.
- `references/report-template.md`: final report format.

## Eval Seeds

Use `evals/evals.json` with Skill Creator when improving this skill. The evals
cover missed-blocker discovery, anti-anchoring, held-out convergence, and
plateau exit behavior.
