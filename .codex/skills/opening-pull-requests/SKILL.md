---
name: opening-pull-requests
description: Use when preparing, opening, updating, or marking ready a pull request or merge request, especially for upstream/open-source repos, forks, submodules, GitHub gh CLI workflows, PR titles/bodies, verification evidence, reviewer-facing summaries, draft-to-ready checks, or avoiding private/local context leaks.
---

# Opening Pull Requests

Prepare PRs for reviewers who do not share your chat history, local machine,
private integration repo, deployment scripts, or target hardware access.

## Core Rule

Write the PR from the maintainer's point of view. Every claim, command, path,
and log line must be understandable from the repository receiving the PR.

If a reviewer cannot run a command, inspect a path, or know what a local project
means, rewrite it as portable evidence or remove it.

## Baseline Failure This Skill Prevents

Bad verification text:

```markdown
- PYTHON=.venv/bin/python scripts/check-local.sh in the private integration
  repo: 54 tests passed
- deployed that binary to the target through our drop-in; running process uses
  /opt/<local-project>/bin/mangoapp
```

Why it fails:
- It references another repo the reviewer may not know.
- It gives a command that does not exist in the PR repo.
- It exposes a private install path that is not part of upstream.
- It makes the evidence look stronger than what upstream can reproduce.

Better:

```markdown
- built the standalone `mangoapp` target from this branch in a SteamOS x86_64
  build environment; produced BuildID `...`
- smoke-tested that rebuilt `mangoapp` on SteamOS hardware by temporarily
  overriding the stock `gamescope-mangoapp.service`; verified the running
  process BuildID matched `...`
```

## Workflow

1. Identify the audience and repo boundary.
   - Upstream PR: write only with upstream repo concepts unless local evidence is
     clearly labeled as smoke testing.
   - Internal PR: use project-specific paths only if the reviewer can access them.
   - Submodule PR: do not mention the parent integration repo unless it is needed
     to explain real-device validation.

2. Inspect the actual PR surface.

   For a new PR, use only repository and branch facts that exist before the PR
   is created:
   ```bash
   git status --short --branch
   git remote -v
   git branch -vv
   git diff --stat <base>..HEAD
   git log --oneline <base>..HEAD
   ```

   For an existing PR, read the current reviewer-facing state before editing:
   ```bash
   gh pr view <number> --json title,body,isDraft,comments,reviews,headRefOid
   ```

3. Run or collect evidence before claiming readiness.
   Prefer upstream-reproducible commands:
   ```bash
   git diff --check <base>..HEAD
   meson test -C build
   ninja -C build
   npm test
   pytest
   ```
   Use repo-appropriate commands. If no upstream test was run, say what was run
   instead and why.

4. Translate private or local evidence.
   - Keep: public target OS/version, hardware model, binary BuildID, relevant
     sensor paths, short logs proving the changed behavior.
   - Rewrite: parent-repo scripts, private paths, local cache dirs, hostnames,
     SSH targets, organization-only service names.
   - Drop: unrelated checks, secrets, IPs, usernames, one-off helper commands,
     "it works on my machine" prose.

5. Write the PR body using this shape:
   ```markdown
   ## Summary
   - user-visible or maintainer-visible change
   - compatibility or fallback behavior
   - docs/tests updated, if relevant

   ## Why
   Problem, affected environment, and why this approach preserves existing paths.

   ## Compatibility
   Existing behavior intentionally preserved, known limitations, not supported.

   ## Verification
   - command or build the reviewer understands
   - portable smoke test description
   - concise relevant output
   ```

6. Gate before opening or marking ready.
   - No TODOs, "previously", "should", "probably", or stale draft language.
   - No commands from another repo unless explicitly labeled as external evidence.
   - No private paths, IPs, local usernames, or org-only deployment assumptions.
   - No claim that upstream tests passed unless they ran in the upstream repo.
   - Body explains why existing supported platforms are not broken.
   - For existing PRs, comments/reviews have been read before editing or pushing.
   - For new PRs, branch facts are checked before create; after create, `gh pr
     view` confirms the intended head commit.

## Command Guardrails

Before `gh pr create`, `gh pr edit`, or `gh pr ready`:

For a new PR:
```bash
git diff --check <base>..HEAD
git status --short --branch
git log --oneline <base>..HEAD
git remote -v
git branch -vv
```

For an existing PR:
```bash
git diff --check <base>..HEAD
git status --short --branch
gh pr view <number> --json title,body,isDraft,comments,reviews,headRefOid
```

When updating an existing PR:
- Read the current body first; do not overwrite reviewer-requested details.
- Remove stale TODOs after verification.
- Keep draft if verification is incomplete or the body depends on private context.
- Mark ready only after the body is self-contained and evidence is current.

## Review The Body Like A Maintainer

Ask these questions before publishing:

| PR text mentions | Reviewer question | Fix |
| --- | --- | --- |
| Another repo's script | "Where is this script?" | Replace with upstream command or describe as external smoke test. |
| Local path under `/opt`, `/tmp`, `.cache` | "Is this part of the project?" | Use a generic install/deploy description or omit. |
| Private host/IP/user | "Can I access this?" | Remove it; keep only hardware/OS facts. |
| Integration test count | "What does this test?" | Name the relevant behavior or omit unrelated count. |
| Hardware proof | "What exactly was observed?" | Include short sensor path/log output tied to the change. |
| Unsupported metric | "Is this faked?" | Say it is not synthesized and how unavailable data is shown. |

## Red Flags

Stop and revise when the PR body says:
- "in my integration repo"
- "using our script"
- "deployed to `/opt/<org-or-project>/...`"
- "tests passed" without naming the repo and command
- "verified before" or "previously tested"
- "TODO before marking ready"
- "safe placeholder"
- logs longer than needed to prove the behavior

## Final Check

The final answer to the user should include the PR URL, pushed branch/commit,
verification commands actually run, and any remaining dirty worktree files that
were intentionally not included.
