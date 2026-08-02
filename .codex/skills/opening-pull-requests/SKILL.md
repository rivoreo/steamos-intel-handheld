---
name: opening-pull-requests
description: Prepare, open, update, or mark ready a pull or merge request, especially for upstream repos, forks, submodules, GitHub gh workflows, reviewer-facing evidence, or avoiding private/local context leaks.
---

# Opening pull requests

Prepare a self-contained change for reviewers who cannot see the conversation,
private integration repositories, local paths, deployment scripts, credentials,
or target hardware.

## Authority

Preparing a PR does not authorize staging, committing, pushing, changing
branches, or editing remote state. Perform those writes only when the user asks
for the corresponding action. Write through `gh` or another forge only when the
user asks to open, update, or mark the PR ready.

## Workflow

1. Confirm the receiving repository, base, head, fork/submodule relationship,
   and whether the PR should be draft.
2. Inspect the complete base-to-head diff and commits. Exclude unrelated or
   private integration changes.
3. Run verification proportionate to the changed behavior and repository
   guidance. Do not paste commands that were not run.
4. Write a maintainer-facing title and body:
   - problem and user-visible impact;
   - concise implementation;
   - exact verification and outcomes;
   - compatibility, migration, device, or release limitations.
5. Remove private paths, IPs, credentials, production data, hidden workflow
   details, and references that exist only in another repository. Translate
   useful private evidence into reproducible public facts.
6. When authorized, create or update the PR and read back the remote title,
   body, base/head, draft state, and URL.

## Evidence boundaries

Local tests are local evidence. Device, QEMU, release, CI, and production claims
need their matching results. If a check could not run, say so plainly and
explain the impact on review readiness.

Do not expose raw transcripts or private identifiers. Do not mark a PR ready
while known merge blockers, missing required evidence, or unintended diff
content remain.

## Output

For preparation-only requests, return a copy-ready title and body plus exact
verification and blockers. After an authorized remote mutation, also report the
read-back URL, base/head, and draft state.
