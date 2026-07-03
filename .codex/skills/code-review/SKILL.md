---
name: code-review
description: Use this skill for reviewing code changes, diffs, pull requests, completed implementation tasks, or merge readiness when the review should run as an adversarial, self-terminating convergence loop. Reviewer lanes attack the diff, an independent verifier tries to refute every blocker against the real code before it can block, and a hard iteration budget guarantees the loop always terminates with a harness-readable status. Trigger for Code Review, PR review, review this diff, merge gate, implementation review, or Goal/Cell review steps over code.
---

# Code Review

Adversarial review loop for code changes. It gives two guarantees the naive
"review N rounds" pattern cannot:

1. **Blockers are verified against the real code, not asserted.** A finding
   blocks merge readiness only after an independent verifier read the cited
   code and failed to refute it. Reviewer opinion alone never drives another
   patch iteration.
2. **The loop always terminates.** Convergence semantics decide *why* it
   stops; the iteration budget guarantees *that* it stops. No input can
   produce an unbounded number of review rounds.

## Core Model

Three harness outcomes:

- `passed`: no verified blockers remain and a held-out full-diff sweep found
  no new evidence-backed blocker.
- `continue`: verified, fixable blockers remain; patch and run one more sweep.
- `blocked`: plateaued, oscillating, needs a human/product/security decision,
  lacks required evidence, review process is defective, or the iteration
  budget is exhausted.

## Iteration Budget (hard backstop)

Default budget: **5 sweeps total** (discovery + verification sweeps + held-out
sweeps combined). The harness may set a different budget explicitly, but never
exceed the active budget silently, and never treat "no budget given" as
"unlimited".

Why a hard cap when convergence semantics exist: the rules are executed by a
model, and a misread rule or a churny diff can defeat them. The budget turns a
defective loop into a `blocked` result with a readable ledger instead of a
runaway. Hitting it is a valid outcome: return `blocked` with
`reasonCode: "budget_exhausted"` and the full unresolved ledger.

Two secondary brakes, checked every iteration:

- **Monotonic progress (carryover)**: from iteration 3 onward, the blockers
  that were already open before the last patch must strictly shrink each
  iteration — a patch must actually resolve what it claims to fix. If the
  carryover set fails to shrink, return `blocked` as plateau. Brand-new
  first-seen findings do not count against this rule: fixing last round's
  blocker while a fresh sweep uncovers a different one is progress, not
  plateau. New findings are governed by attribution (the `latent_missed`
  streak, artifact instability) and by the budget.
- **Per-issue cap**: an issue still open after 2 material fix attempts with no
  new actionable evidence is a plateau, not a todo.

## Inputs

1. What changed: short implementation summary.
2. Requirements or plan: task text, plan file, issue, or acceptance criteria.
3. Git range: base SHA and head SHA, or an explicit diff/file list.
4. Verification evidence: tests/build/smoke commands already run, if any.
5. For iteration > 1: `originalDiffSnapshot`, `currentDiffSnapshot`,
   `latestPatchDiff`.
6. Known risk areas or intentional deviations.

## Intake Checkpoint (before the first sweep)

Scan the requirements and diff summary for gaps that would predictably prevent
convergence. Two classes, handled differently:

- **Reviewability gaps** — missing acceptance criteria, no stated intended
  behavior for a changed path, unknown test baseline. These only need *a*
  defensible answer for review to proceed.
- **Deferred decisions** — product, security-posture, data-retention,
  pricing, release, or ownership choices the change implements one way while
  the requirements leave them undecided. These need *the owner's* answer; a
  review must never supply it.

Handling:

- **Interactive session**: batch both classes into one ask, at most 3
  questions (use AskUserQuestion when available).
- **Headless/harness run**: write each *reviewability gap* into the surface
  as an explicit `ASSUMPTION:` line and proceed — reviewers treat stated
  assumptions as given, so ambiguity cannot oscillate as findings. Seed each
  *deferred decision* into the ledger as `needs_decision`. Do not invent an
  answer for a choice that moves money, access, legal exposure, or release
  posture. Review everything else normally; the terminal state cannot be
  `passed` while `needs_decision` items remain.

This is the only checkpoint before the loop. Never pause mid-sweep to ask
about an individual finding.

## Reviewer Lanes

Use independent reviewers or independent passes. Each lane may be one subagent
or one clearly separated review pass.

| ID | Lane | Focus |
| --- | --- | --- |
| A | Correctness & Plan Alignment | Requirement coverage, behavior, regressions, API contracts |
| B | Risk & Safety | Security, auth, data loss, concurrency, migrations, rollback |
| C | Tests & Verification | TDD evidence, real behavior coverage, missing tests, gate upkeep |
| D | Maintainability | Simplicity, local patterns, coupling, readability, abstractions |
| E | UX/Product Impact | User-visible changes, i18n, error states, design-system compliance, rollout |

A, B, C, and D are active by default. Activate E when the diff touches UI,
user-facing API contracts, release behavior, user-consumed docs, or product
workflows.

**Lane E is not generic for UI diffs.** When the diff touches
`desktop/`/`mobile/`/`admin/` Vue/CSS or `cloudflare-worker/` HTML/CSS, lane E
must read `DESIGN.md` and `.claude/skills/lunatalk-ui-review/SKILL.md` (plus
the surface-matched `lunatalk-ui-glass` / `-ambient` / `-primitives` /
`-loading` skill) before reviewing, and run the taboo checklist against the
actual changed styles: px-vs-rpx, radius tiers, token colors, motion
durations, glass recipes, loading/empty/error states, platform parity.
Findings must cite the specific rule violated. A UI finding without a rule
citation is weak evidence and will likely be refuted.

**Lane C owns gate upkeep.** Per the repo's Gate 保養規則, closing the loop
requires the full trusted test set (server: `./scripts/test-offline.sh` or
full `go test` with tunnel; frontend: full build + existing suites), not just
the tests the patch added. Narrow `-run` filters are development iteration,
not verification evidence. If only narrow runs exist, that is a missing-
evidence finding, and any test that went red must be attributed on the spot
(fix / update-with-rationale / register in the skip ledger) per the rule.

## Domain Skill Packs

Reviewers judge against knowledge, and this repo encodes its domain knowledge
as skills. When freezing the surface, map the diff's domains to skills and
inject them into the matching lane's prompt as required reading. A lane with
the domain pack cites specific rules, which is what survives verification.

| Diff touches | Lane | Required pack |
| --- | --- | --- |
| UI files (desktop/mobile/admin/worker) | E | `DESIGN.md`, `lunatalk-ui-review`, surface-matched `lunatalk-ui-*`, `ui-ux-pro-max` (external benchmark; internal authority wins on conflict) |
| Chat pipeline, prompt structure, context/cache, billing/points | A, B | `chat-framework` |
| Worldbook recall / activation | A, B | `lunatalk-worldbook-recall-eval` |
| New API relay, channel affinity, prompt_cache_key | A, B | `newapi-relay-diagnose` |
| MCP gateway, card_writer, Moonloom surfaces | A, C | `moonloom:using-moonloom` + MCP parity gate in root `CLAUDE.md` |
| Deploy scripts, release steps | B, C | Deploy Charter in root `CLAUDE.md` |
| New/changed server APIs or background jobs | C | Observability/Prometheus gate in root `CLAUDE.md` |
| User-facing message copy | E | `.claude/rules/user-facing-message.md` |

Inject a pack only when the diff actually touches that domain. Scan the
session's available-skills list for mappings this table misses.

## Adversarial Structure

The loop is adversarial in both directions:

- **Lanes attack the diff.** Their stance is "find the ways this change fails
  in production", not "give feedback". Approval must be earned by a genuine
  failed attack.
- **A verifier attacks the findings.** Every critical/important finding goes
  to an independent skeptic whose only job is to refute it **against the real
  code**: open the cited file at the cited line, read the surrounding
  behavior, check whether existing middleware/validation/tests already handle
  the claimed gap, and run cheap verification commands when they settle the
  question. Verdicts:
  - `REFUTED`: the code already handles it, the citation misreads the code,
    the claim disputes a stated `ASSUMPTION:`/intentional deviation, the
    issue is outside the diff surface, or the evidence is too vague to act on
    ("might be slow", "consider caching"). Quote the refuting code
    (file:line) or surface rule.
  - `CONFIRMED`: refutation genuinely failed — the gap is real, in-surface,
    and evidence-backed. One sentence on why.
  - `NEEDS_DECISION`: real concern, but the answer is an owner-level choice
    not encoded in the requirements.

  Judge each finding independently; there is no verdict quota. When evidence
  is thin, refute — a wrongly dropped finding costs one comment; a wrongly
  kept one costs an entire patch iteration.

Lanes know their findings face refutation, so they front-load evidence:
file:line citations plus what the surrounding code actually does. A claim
about existing code the reviewer did not read is unsupported and will be
refuted — read the touched files, not just the diff hunks.

Only verifier-`CONFIRMED` critical/important findings become open blockers.
`REFUTED` findings are downgraded to suggestions with the refutation recorded.

## Convergence Loop

### 1. Bound Review Surface

Freeze the surface before the first lane runs: diff boundaries (base/head
range, changed files, generated/ignored paths), requirement boundaries (plan,
acceptance criteria, intentional deviations), evidence boundaries (available
test/build/smoke results and known gaps), active lanes, and domain packs.

Review the entire bounded diff — the full base-to-head diff plus the
touched-file context needed to understand it — not only the latest patch. Do
not expand into a whole-repository audit. Findings that need unrelated files
or broader product scope become `needs_decision` or `missing_evidence`, never
`open`.

Store attribution evidence when the surface is frozen:

```json
{
  "attributionEvidence": {
    "originalDiffSnapshot": "git range, hash, or artifact id for the first reviewed diff",
    "currentDiffSnapshot": "git range, hash, or artifact id for the current diff",
    "latestPatchDiff": "diff from the previous reviewed head to the current head"
  }
}
```

These must let later sweeps compare original vs current vs latest patch; a
prose changelog alone cannot classify a late blocker.

### 2. Discovery Sweep

Fresh full-diff sweep. Each active lane receives the diff/git range,
requirements, its lane focus, its domain packs, and the output contract.

```bash
git diff --stat <base>..<head>
git diff <base>..<head>
git diff --check <base>..<head>
```

Lanes read touched files directly when the diff is not enough. Findings must
cite file:line, requirements, or concrete test evidence.

### 3. Verify Findings

Send all critical/important findings to the verifier (per Adversarial
Structure). The verifier gets the diff, requirements, repo access, and the
findings — stripped of the lane's score and reasoning, so it judges the
finding, not the reviewer.

### 4. Issue Ledger

Normalize verified findings into the Issue Ledger — the loop's only memory.
Never feed prior review prose into later sweeps.

```json
{
  "dedupeKey": "stable lowercase key",
  "severity": "critical|important|minor|suggestion",
  "status": "open|resolved|wontfix|needs_decision|missing_evidence|refuted|stale",
  "verification": "confirmed|refuted|needs_decision|not_required",
  "lane": "A|B|C|D|E",
  "firstSeenIteration": 1,
  "lastSeenIteration": 1,
  "materialFixAttempts": 0,
  "novelIssueSource": "none|revision_introduced|latent_missed|scope_expansion|unsupported",
  "file": "path/to/file.ext",
  "line": 42,
  "evidence": "specific code/test/requirement evidence",
  "finding": "what is wrong",
  "recommendation": "specific fix",
  "disposition": "why it is open/resolved/refuted/non-blocking"
}
```

Only `status: open` items with `verification: confirmed` and severity
critical/important block. Minor and suggestion items never block and never
drive another iteration.

### 5. Patch

Patch open blockers with the smallest code and test changes that satisfy the
requirements. Preserve TDD: failing test first (Red), minimal implementation
(Green), then refactor. Produce a changelog mapping ledger keys to commits or
edits. Increment `materialFixAttempts` only when the change could plausibly
resolve that item. Re-run the trusted verification set after patching.

### 6. Verification Sweep

Next iteration is another fresh full-diff sweep over the current base-to-head
diff — not a delta-only pass. Lanes receive: current diff, requirements,
Issue Ledger (statuses only), fix changelog, verification evidence,
attributionEvidence. Each lane rechecks addressed blockers and fresh-sweeps
the whole diff for regressions. New critical/important findings go through
the verifier before entering the ledger.

### 7. Attribute Late Findings

After discovery plus one verification sweep, every new confirmed
critical/important finding must be attributed from `attributionEvidence`
before it affects convergence:

| `novelIssueSource` | Meaning | Gate behavior |
| --- | --- | --- |
| `revision_introduced` | The latest patch created it | Normal open blocker; reset `latentMissedStreak` — code-quality problem, not a review miss |
| `latent_missed` | Existed in the original diff; earlier sweeps missed it | Open blocker; increment `latentMissedStreak`. Streak ≥ 2 across consecutive sweeps → `blocked` with `review_process_defect` — fix the review surface/lanes, don't patch forever |
| `scope_expansion` | Needs files/state outside the frozen surface | Not `open`; `blocked` with `needs_decision`/`missing_evidence`, or restart with a new surface |
| `unsupported` | No concrete evidence | Minor/suggestion; never blocks |

If patches keep introducing *different* `revision_introduced` blockers in the
same area after material fixes, that is artifact instability — `blocked`, not
more loops. If attribution evidence is missing, return `blocked` with
`missing_evidence` instead of guessing.

### 8. Decision Checkpoint

When the ledger holds `needs_decision` items and no open fixable blockers
remain:

- **Interactive**: batch all decision items into one AskUserQuestion call,
  fold answers into the requirements, continue with the budget unchanged.
- **Headless**: return `blocked` with `reasonCode: "needs_decision"` and the
  exact decision list.

Ask once per loop at most.

### 9. Held-Out Sweep

When no open blockers remain, run a held-out sweep before `passed`: a fresh
lane set receives only the final diff, requirements, and test/build evidence
— no ledger, no changelog. Held-out critical/important findings still go
through the verifier.

- No confirmed blockers → `passed`. Minor/suggestion findings are recorded as
  non-blocking notes.
- A confirmed blocker → add to ledger, continue (budget permitting).
- Maximum 2 held-out sweeps. A confirmed blocker on the second one returns
  `blocked` with the ledger — code that keeps failing fresh eyes needs a
  human, not a third sweep.

### 10. Stop Conditions

Return `blocked` when any of these holds:

- Iteration budget exhausted (`budget_exhausted`).
- Carryover blockers did not strictly shrink across consecutive iterations
  from iteration 3 onward (plateau).
- Same blocker open after 2 material fixes with no new actionable evidence.
- Fixes toggle between incompatible lane demands not resolvable by evidence.
- Remaining blockers need an owner-level decision (headless), or decision
  questions went unanswered.
- Generated code, dependency state, migrations, or test evidence required for
  review are missing (`missing_evidence`).
- `latentMissedStreak >= 2` (`review_process_defect`).
- Patches repeatedly introduce new critical/important blockers in the same
  area (artifact instability).
- Second held-out sweep found a confirmed blocker.

Blocked is a valid convergence outcome: it tells the harness to stop
automatic review and surface the unresolved ledger.

## Gate Logic

| Condition | harnessStatus | Next action |
| --- | --- | --- |
| Confirmed open critical/important blockers, fixable, budget remains | `continue` | Patch, fresh full-diff sweep |
| Finding refuted by verifier | no change | Downgrade to suggestion, record refutation |
| `needs_decision` items, interactive | `continue` | Decision checkpoint (one batched ask) |
| `needs_decision` items, headless or already asked | `blocked` | Stop with decision list |
| `latentMissedStreak >= 2` | `blocked` | Stop with `review_process_defect` |
| No open blockers, held-out not yet run | `continue` | Held-out sweep |
| No confirmed blockers after held-out | `passed` | Stop |
| Budget exhausted / plateau / oscillation / instability / 2nd held-out blocker | `blocked` | Stop with unresolved ledger |

The verified Issue Ledger is the convergence state. Reviewer text is
supporting evidence.

## Harness Output Contract

Every iteration ends with one JSON block:

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "code",
  "iteration": 3,
  "budget": {"maxSweeps": 5, "sweepsUsed": 3},
  "harnessStatus": "continue|passed|blocked",
  "reason": "short reason for the status",
  "reasonCode": "open_blockers|held_out_required|converged|plateau|oscillation|artifact_instability|missing_evidence|needs_decision|review_process_defect|reviewer_failure|budget_exhausted",
  "base": "<base-sha-or-label>",
  "head": "<head-sha-or-label>",
  "surfaceId": "stable hash or short name for the bounded review surface",
  "activeLanes": ["A", "B", "C", "D"],
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
    "maxMaterialFixAttempts": 1,
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
    "originalDiffSnapshot": "<range-or-hash-or-artifact-id>",
    "currentDiffSnapshot": "<range-or-hash-or-artifact-id>",
    "latestPatchDiff": "<range-or-hash-or-artifact-id>"
  },
  "ledger": [],
  "verification": {
    "commandsRun": [],
    "trustedSetRun": false,
    "missingEvidence": []
  },
  "nextAction": {
    "type": "patch|fresh_full_sweep|held_out_sweep|ask_user|stop",
    "summary": "what the harness should do next"
  }
}
```

The harness stores this JSON as the Cell result and uses it to decide whether
to schedule the next iteration.

### LunaTalk Review Evidence Marker

When this loop ends `passed` for a LunaTalk submodule's change, run

```bash
node .claude/scripts/gate-event.mjs review-passed <submodule>
```

once per reviewed submodule. The commit review gate (WO-T6) uses this marker
as evidence that a convergence review covered the current edits; without it,
substantial commits (≥3 files) get flagged as `review_gap`. Never emit the
marker for `blocked` or `needs_decision` outcomes — a false marker leaves a
contradiction in the gate-event ledger that the weekly retro will surface.

## Failure Handling

- Retry a failed lane once with the same diff, requirements, and lane.
- If it still fails, mark the lane as a coverage gap and continue only if
  fewer than half of active lanes failed; otherwise return `blocked` with
  `reviewer_failure`.
- Verifier failure: retry once; if it still fails, treat the affected
  findings as `needs_decision` rather than silently confirming or dropping.
- JSON parse failure is a lane failure.

## Review Calibration

- Lead with findings, ordered by severity, with file:line references.
- Do not mark preferences or style nits as blockers.
- Missing tests can be important when behavior changed without evidence;
  missing full-trusted-set evidence is `missing_evidence`, not silence.
- If the implementation is correct but the plan is wrong, say so and mark it
  `needs_decision` when the code cannot resolve it alone.
- Do not claim readiness without fresh verification evidence.

## Eval Seeds

Use `evals/evals.json` with Skill Creator when improving this skill. The evals
cover termination under churn, adversarial refutation against real code, UI
design-system depth, checkpoint behavior, full-diff blocker discovery,
anti-anchoring, held-out convergence, and plateau exit.
