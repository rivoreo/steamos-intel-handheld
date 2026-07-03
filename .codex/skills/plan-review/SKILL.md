---
name: plan-review
description: Use this skill for plan, architecture, product, technical design, or implementation-plan review when the review should improve the artifact through an adversarial, self-terminating convergence loop. Reviewers attack the plan, an independent verifier tries to refute every blocker before it can block, and a hard iteration budget guarantees the loop always terminates with a harness-readable status. Trigger for /plan-review, design review, plan gate, architecture review, UI/UX plan review, or any LunaTalk workflow phase that needs independent reviewers before implementation.
---

# Plan Review

Adversarial review loop for plans and design documents. It gives two guarantees
the naive "review N rounds" pattern cannot:

1. **Blockers are verified, not asserted.** A finding blocks convergence only
   after an independent verifier tried to refute it and failed. Reviewer opinion
   alone never drives another iteration.
2. **The loop always terminates.** Convergence semantics decide *why* it stops;
   the iteration budget guarantees *that* it stops. There is no input that can
   produce an unbounded number of review rounds.

## Core Model

Three harness outcomes:

- `passed`: no verified blockers remain and a held-out sweep found no new
  evidence-backed blocker.
- `continue`: verified, fixable blockers remain; revise and run one more sweep.
- `blocked`: plateaued, oscillating, needs a human decision, lacks required
  evidence, review process is defective, or the iteration budget is exhausted.

## Iteration Budget (hard backstop)

Default budget: **5 sweeps total** (discovery + verification sweeps + held-out
sweeps combined). The harness may set a different budget explicitly, but the
skill must never exceed the active budget silently, and must never treat "no
budget given" as "unlimited".

Why a hard cap when convergence semantics exist: convergence rules are executed
by a model, and a misread rule or a churny artifact can defeat them. The budget
is the circuit breaker that turns a defective loop into a `blocked` result with
a readable ledger, instead of a 200-round runaway. Hitting the budget is not
failure noise — it returns `blocked` with `reasonCode: "budget_exhausted"` and
the full unresolved ledger, which is itself a useful review result.

Two secondary brakes, checked every iteration:

- **Monotonic progress (carryover)**: from iteration 3 onward, the blockers
  that were already open before the last revision must strictly shrink each
  iteration — a revise step must actually resolve what it claims to address,
  and one that doesn't will not start doing so on round 12. If the carryover
  set fails to shrink, return `blocked` as plateau. Brand-new first-seen
  findings do not count against this rule: a healthy loop that fixes last
  round's blocker while a fresh sweep uncovers a different one is making
  progress, not plateauing. New findings are governed by attribution (the
  `latent_missed` streak, artifact instability) and by the budget.
- **Per-issue cap**: an issue still open after 2 material revision attempts with
  no new fixable evidence is a plateau, not a todo.

## Inputs

1. Plan artifact: inline text or file path.
2. Context summary: problem, constraints, scope, acceptance criteria.
3. Relevant code/document references.
4. For iteration > 1: `originalSurfaceSnapshot`, `currentSurfaceSnapshot`,
   `latestRevisionDiff`.
5. Optional concern list.

## Reviewer Roles

| ID | Role | Focus |
| --- | --- | --- |
| A | Architecture & Feasibility | Architecture, feasibility, API/data contracts, integration, performance |
| B | Completeness & Risk | Requirements, edge cases, security, failures, migration, rollback |
| C | Quality & Conventions | Project conventions, testability, clarity, reuse, maintainability |
| D | UX/UI Design | User journeys, states, design-system compliance, platform parity |
| E | Product & Business Value | MVP scope, priority, business value, success metrics, rollout |

A, B, C are always active. Activate D when the plan changes UI, user journeys,
user-visible states, or frontend data shapes. Activate E when the plan changes
product behavior, user-facing APIs, workflow scope, pricing, release strategy,
or measurable business outcomes.

**Reviewer D is not generic.** When D is active, its prompt must require reading
`DESIGN.md` and `.claude/skills/lunatalk-ui-review/SKILL.md` before reviewing,
plus the surface-matched UI skill (`lunatalk-ui-glass` / `-ambient` /
`-primitives` / `-loading`) and the `ui-ux-pro-max` skill for industry-level
benchmarking. D's findings must cite the specific design-system rule violated,
and D must run the screen × state matrix check. A plan that touches UI but does
not enumerate its screens and their loading/empty/error states is itself a
major finding. Full rubric in `references/reviewer-prompts.md`.

## Domain Skill Packs

Reviewers judge against knowledge, and this repo already encodes its domain
knowledge as skills. When freezing the review surface, map the plan's domains
to skills and inject them into the matching reviewer's prompt as required
reading. A reviewer without the domain pack produces generic findings that the
verifier will refute; a reviewer with it cites specific rules, which is what
makes findings survive.

Known mappings (scan the session's available-skills list for others — this
table is illustrative, not exhaustive):

| Plan touches | Reviewer | Required skill pack |
| --- | --- | --- |
| Any UI/UX surface | D | `DESIGN.md`, `lunatalk-ui-review`, surface-matched `lunatalk-ui-*`, `ui-ux-pro-max` |
| Chat pipeline, prompt structure, context/cache, billing/points | A, B | `chat-framework` |
| Worldbook recall / activation | A, B | `lunatalk-worldbook-recall-eval` |
| New API relay, channel affinity, prompt_cache_key | A, B | `newapi-relay-diagnose` |
| MCP tools/resources, Moonloom surfaces | A, C | `moonloom:using-moonloom` + the MCP parity gate in root `CLAUDE.md` |
| Deployment, release, rollout steps | B, C | Deploy Charter + Observability/Prometheus gate in root `CLAUDE.md` |
| User-facing message copy | D, E | `.claude/rules/user-facing-message.md` |

Two rules keep this from bloating the loop: inject a pack only when the plan
actually touches that domain, and when an external-benchmark skill (like
`ui-ux-pro-max`) conflicts with an internal authority (like `DESIGN.md`), the
internal authority wins — the external skill exists to raise the bar, not to
overrule the design system.

## Adversarial Structure

The loop is adversarial in both directions:

- **Reviewers attack the plan.** Their stance is "find the reasons this plan
  fails in production", not "give feedback". A +2 must be earned by a genuine
  failed attack, not granted by default.
- **A verifier attacks the findings.** Every critical/major finding goes to an
  independent skeptic whose only job is to refute it using the plan text and
  context. Verdicts: `CONFIRMED` / `REFUTED` / `NEEDS_DECISION`. Only
  `CONFIRMED` findings become open blockers. `REFUTED` findings are downgraded
  to suggestions with the refutation recorded.

Why both sides: attack-only review generates churn — plausible-sounding
blockers that restart the loop forever. Verification-only review goes soft.
Attack plus refutation is what makes "no blockers remain" mean something.

Reviewers know their findings will face refutation. This is stated in their
prompt so they front-load evidence instead of padding the finding list.

## Convergence Loop

### 1. Bound Review Surface

Freeze a bounded review surface before the first reviewer runs. A review whose
scope can expand forever cannot converge. The surface includes: exact plan
files/sections under review, the context available to reviewers, active roles,
and explicit out-of-scope areas.

Review the entire bounded artifact, not only new or changed paragraphs.
Reviewers may mark blockers only inside this surface — unrelated documents,
files, tests, or harness behavior stay outside the blocker surface unless
explicitly listed as context. Findings that need external facts or broader
scope become `needs_decision` or `missing_evidence`, never `open`.

Store attribution evidence when the surface is frozen:

```json
{
  "attributionEvidence": {
    "originalSurfaceSnapshot": "path, hash, or stored text of the first reviewed surface",
    "currentSurfaceSnapshot": "path, hash, or stored text of the current surface",
    "latestRevisionDiff": "diff or section-level change summary from the previous iteration"
  }
}
```

These must allow a later comparison of original vs current vs latest change. A
prose changelog alone cannot classify whether a late blocker was introduced by
a revision or missed from the start.

### 2. Intake Checkpoint (before the first sweep)

Scan the context summary for gaps that would predictably prevent convergence.
They come in two classes with different handling:

- **Reviewability gaps** — missing acceptance criteria, undefined target
  platforms, ambiguous scope words ("optimize", "improve") with no measurable
  target. These only need *a* defensible answer for review to proceed.
- **Deferred decisions** — product, pricing, revenue-share, access-gating,
  legal, security-posture, or release choices the plan explicitly leaves
  undecided. These need *the owner's* answer; a review must never supply it.

Handling:

- **Interactive session**: batch both classes into one ask, at most 3
  questions (use AskUserQuestion when available). Fold answers into the
  context summary.
- **Headless/harness run**: do not wait for a human. Write each
  *reviewability gap* into the surface as an explicit `ASSUMPTION:` line and
  proceed — reviewers treat stated assumptions as given, so ambiguity cannot
  re-enter as oscillating findings. For each *deferred decision*, seed the
  ledger with a `needs_decision` item instead. Do not invent an assumption
  for a choice that moves money, access, legal exposure, or release posture:
  a loop that assumes "10% platform cut" can silently pass a plan no human
  approved. Review everything else normally; the loop's terminal state cannot
  be `passed` while `needs_decision` items remain — it exits `blocked` with
  the exact decision list (see Decision Checkpoint).

This is the only checkpoint before the loop. Never pause mid-sweep to ask
about an individual finding — that converts the convergence loop into an
unbounded conversation.

### 3. Discovery Sweep

Fresh full sweep. Each active reviewer receives only: the current plan
artifact, the context summary, its role rubric from
`references/reviewer-prompts.md`, and the JSON output format from
`references/scoring-rubric.md`. They review the whole artifact and must cite
evidence: section names, file paths, contracts, contradictions. Findings
without evidence are suggestions, not blocker candidates.

### 4. Verify Findings

Send all critical/major findings to the verifier (prompt in
`references/reviewer-prompts.md`). The verifier gets the plan, the context
summary, and the findings — not the reviewer's reasoning or score. For each
finding it must attempt a refutation and return `CONFIRMED`, `REFUTED`
(with the refuting evidence), or `NEEDS_DECISION` (valid concern, but the
answer is a product/human choice). When evidence is thin, refute — the cost of
a wrongly-dropped blocker is one missed comment; the cost of a wrongly-kept one
is a whole extra loop iteration.

### 5. Issue Ledger

Normalize verified findings into the Issue Ledger — the loop's only memory.
Never feed previous review prose into later reviewers.

```json
{
  "dedupeKey": "stable lowercase key",
  "severity": "critical|major|minor|suggestion",
  "status": "open|resolved|wontfix|needs_decision|missing_evidence|refuted|stale",
  "verification": "confirmed|refuted|needs_decision|not_required",
  "reviewer": "A|B|C|D|E",
  "firstSeenIteration": 1,
  "lastSeenIteration": 1,
  "materialRevisionAttempts": 0,
  "novelIssueSource": "none|revision_introduced|latent_missed|scope_expansion|unsupported",
  "evidence": "specific section/file/contract evidence",
  "finding": "what is wrong",
  "recommendation": "specific fix",
  "disposition": "why it is open/resolved/refuted/non-blocking"
}
```

Only `status: open` items with `verification: confirmed` and severity
critical/major are blockers. Minor and suggestion items never block, never
drive another iteration, and are reported at the end.

### 6. Revise

The main agent revises the plan. Produce a changelog mapping every addressed
blocker to a concrete change (format in `references/scoring-rubric.md`). Do not
broaden scope while revising unless the ledger requires it. Increment
`materialRevisionAttempts` only when the change could plausibly resolve that
item.

### 7. Verification Sweep

Next iteration is another fresh full sweep — not a delta pass. Reviewers
receive: revised artifact, current Issue Ledger (statuses only), revision
changelog, attributionEvidence, context summary, role rubric. Each reviewer
does two things: verify that addressed blockers were actually handled, and
fresh-sweep the whole artifact for regressions. New critical/major findings go
through the verifier (step 4) before entering the ledger.

### 8. Attribute Late Findings

After discovery plus one verification sweep, every new confirmed critical/major
finding must be attributed from `attributionEvidence` (not reviewer memory)
before it affects convergence:

| `novelIssueSource` | Meaning | Gate behavior |
| --- | --- | --- |
| `revision_introduced` | Latest revision created it | Normal open blocker; reset `latentMissedStreak` — this is artifact quality, not a review miss |
| `latent_missed` | Existed in the original surface; earlier sweeps missed it | Open blocker; increment `latentMissedStreak`. Streak ≥ 2 across consecutive sweeps → `blocked` with `review_process_defect` — fix the review surface/rubric, don't revise the artifact forever |
| `scope_expansion` | Needs facts outside the frozen surface | Not `open` in this loop; `blocked` with `needs_decision`/`missing_evidence`, or restart with a new surface |
| `unsupported` | No concrete evidence | Minor/suggestion; never blocks |

If revisions keep introducing *different* `revision_introduced` blockers in the
same section after material fixes, that is artifact instability — `blocked`,
not more loops. If `attributionEvidence` is missing, return `blocked` with
`missing_evidence` instead of guessing.

### 9. Decision Checkpoint

When the ledger holds `needs_decision` items and no open fixable blockers
remain:

- **Interactive session**: batch all decision items into one question set for
  the user (AskUserQuestion, one call). Fold answers into the context summary
  and continue the loop with the budget unchanged.
- **Headless/harness run**: return `blocked` with `reasonCode:
  "needs_decision"` and the exact decision list.

Ask once per loop at most. If decisions remain unanswered, that is `blocked`,
not a re-ask.

### 10. Held-Out Sweep

When no open blockers remain, run a held-out sweep before `passed`: a fresh
reviewer set receives only the final plan, context summary, and acceptance
criteria — no ledger, no changelog. Held-out critical/major findings still go
through the verifier.

- No confirmed blockers → converged, `passed`. Minor/suggestion findings are
  recorded as non-blocking notes; they do not reopen the loop.
- A confirmed blocker → add to ledger, continue (budget permitting).
- Maximum 2 held-out sweeps per review. If the second held-out sweep still
  finds a confirmed blocker, return `blocked` with the ledger — a plan that
  keeps failing fresh eyes needs a human, not a third sweep.

### 11. Stop Conditions

Return `blocked` when any of these holds:

- Iteration budget exhausted (`budget_exhausted`).
- Carryover blockers (open before the last revision) did not strictly shrink
  across consecutive iterations from iteration 3 onward (plateau).
- Same blocker open after 2 material revisions with no new fixable evidence.
- Reviewers oscillate between incompatible requirements not resolvable by
  technical evidence.
- Remaining blockers need a product/legal/security/release decision
  (headless), or the user left decision questions unanswered.
- Required code, data, or requirements are missing (`missing_evidence`).
- `latentMissedStreak >= 2` (`review_process_defect`).
- Revisions repeatedly introduce new critical/major blockers in the same
  section (artifact instability).
- Second held-out sweep found a confirmed blocker.

When blocked, include unresolved ledger items, attempted changes, and the
specific decision or evidence needed to resume.

## Gate Logic

| Condition | harnessStatus | Next action |
| --- | --- | --- |
| Confirmed open critical/major blockers, fixable, budget remains | `continue` | Revise, fresh full sweep |
| Finding refuted by verifier | no change | Downgrade to suggestion, record refutation |
| `needs_decision` items, interactive | `continue` | Decision checkpoint (one batched ask) |
| `needs_decision` items, headless or already asked | `blocked` | Stop with decision list |
| `latentMissedStreak >= 2` | `blocked` | Stop with `review_process_defect` |
| No open blockers, held-out not yet run | `continue` | Held-out sweep |
| No confirmed blockers after held-out | `passed` | Stop |
| Budget exhausted / plateau / oscillation / instability / 2nd held-out blocker | `blocked` | Stop with unresolved ledger |

Review scores are reviewer opinions. The verified Issue Ledger is the
convergence state.

## Harness Output Contract

Every iteration ends with one JSON block:

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 3,
  "budget": {"maxSweeps": 5, "sweepsUsed": 3},
  "harnessStatus": "continue|passed|blocked",
  "reason": "short reason for the status",
  "reasonCode": "open_blockers|held_out_required|converged|plateau|oscillation|artifact_instability|missing_evidence|needs_decision|review_process_defect|reviewer_failure|budget_exhausted",
  "activeReviewers": ["A", "B", "C", "D"],
  "surfaceId": "stable hash or short name for the bounded review surface",
  "scores": {"A": 1, "B": 2, "C": 1, "D": -1},
  "convergence": {
    "openBlockers": 1,
    "confirmedFindings": 3,
    "refutedFindings": 2,
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
    "recordedAssumptions": []
  },
  "attributionEvidence": {
    "originalSurfaceSnapshot": "<path-or-hash-or-artifact-id>",
    "currentSurfaceSnapshot": "<path-or-hash-or-artifact-id>",
    "latestRevisionDiff": "<path-or-hash-or-artifact-id>"
  },
  "ledger": [],
  "nextAction": {
    "type": "revise|fresh_full_sweep|held_out_sweep|ask_user|stop",
    "summary": "what the harness should do next"
  }
}
```

The harness stores this JSON as the Cell result and uses it to schedule (or
not schedule) the next iteration.

## Failure Handling

- Retry a failed reviewer once with the same artifact and role.
- If it still fails, mark the role as a coverage gap and continue only if
  fewer than half of active reviewers failed; otherwise return `blocked` with
  `reviewer_failure`.
- Verifier failure: retry once; if it still fails, treat the affected findings
  as `needs_decision` rather than silently confirming or dropping them.
- JSON parse failure is a reviewer failure (fallback layers in
  `references/scoring-rubric.md`).

## References

- `references/reviewer-prompts.md`: role rubrics, adversarial preamble,
  verifier prompt, Reviewer D LunaTalk UI rubric.
- `references/scoring-rubric.md`: scoring, severity, verification verdicts,
  JSON parsing.
- `references/report-template.md`: final report format.

## Eval Seeds

Use `evals/evals.json` with Skill Creator when improving this skill. The evals
cover termination under churn, adversarial refutation of weak findings, UI
design-system depth, checkpoint batching, missed-blocker discovery,
anti-anchoring, held-out convergence, and plateau exit.
