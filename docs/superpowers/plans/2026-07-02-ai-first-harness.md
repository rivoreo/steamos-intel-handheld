# AI-First Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the project harness easier for coding agents to discover, choose, run, and report with minimal human intervention.

**Architecture:** Keep `scripts/check-local.sh` as the fast authoritative gate. Add a small machine-readable harness manifest plus a lightweight CLI for listing/running checks, and add a concise root `AGENTS.md` that points agents at the right command instead of duplicating long docs. Keep device, release, and QEMU gates explicit and opt-in so agents do not accidentally run network, root, or hardware actions.

**Tech Stack:** Bash, Python standard library, pytest, TOML via `tomllib`, GitHub Actions summaries.

---

### Task 1: Add AI-readable harness contract tests

**Files:**
- Create: `tests/test_harness_manifest.py`
- Modify: `tests/test_harness_policy.py`

- [ ] **Step 1: Write failing tests**

Add tests that require:

- a root `AGENTS.md` with concise setup/test guidance
- a root `harness.toml` with `local`, `device-full`, `release-artifact`, and `qemu-mangoapp-rootfs` checks
- `scripts/harness.py list --json` to expose the manifest to agents
- `scripts/harness.py run device-full` to refuse hardware checks unless requirements are explicitly allowed
- `scripts/check-local.sh` to run `bash -n scripts/*.sh`

- [ ] **Step 2: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_harness_manifest.py tests/test_harness_policy.py -q
```

Expected: failure because the manifest, CLI, AGENTS.md, and shell syntax gate do not exist yet.

### Task 2: Implement manifest, agent guide, and local gate improvements

**Files:**
- Create: `AGENTS.md`
- Create: `harness.toml`
- Create: `scripts/harness.py`
- Modify: `scripts/check-local.sh`
- Modify: `docs/ai-development-harness.md`
- Modify: `docs/tdd-workflow.md`
- Modify: `docs/steamos-qemu-build-env.md`

- [ ] **Step 1: Implement the smallest passing changes**

Add:

- concise root agent guidance
- TOML metadata for local, device, release artifact, and QEMU/rootfs gates
- `scripts/harness.py list --json` and guarded `scripts/harness.py run <id>`
- `bash -n scripts/*.sh` in the local gate
- GitHub Actions job summary output from `check-local.sh` when `$GITHUB_STEP_SUMMARY` is set
- docs updates for actual ruff scope and canonical `root@10.100.0.19` examples

- [ ] **Step 2: Run green tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_harness_manifest.py tests/test_harness_policy.py -q
```

Expected: all selected tests pass.

### Task 3: Full verification

**Files:**
- All touched files

- [ ] **Step 1: Run syntax verification**

Run:

```bash
bash -n scripts/*.sh
```

Expected: exits 0.

- [ ] **Step 2: Run full local harness**

Run:

```bash
PYTHON=.venv/bin/python scripts/check-local.sh
```

Expected: ruff, shell syntax, pytest, and compileall pass.

- [ ] **Step 3: Confirm worktree status**

Run:

```bash
git status --short
```

Expected: only intentional harness/docs/test changes are present.
