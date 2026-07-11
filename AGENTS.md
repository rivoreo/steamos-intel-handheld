# AGENTS.md

## Subagent Delegation

- Agents have standing authorization to delegate within the user's original task;
  the user does not need to request subagents or approve each delegation.
- Use is optional, not required for every task, and does not expand task scope or
  authority; destructive actions, device access, and external side effects keep
  existing approval boundaries.
- The main agent owns decomposition and integration and must personally verify results.
- After deciding to delegate, consult `model-tier-prompting`; it is advisory, not a
  permission gate.

## Start Here

- Read `harness.toml`, the machine-readable map of local, device, release, and
  QEMU checks. List checks with:

  ```bash
  scripts/harness.py list --json
  ```

- Inspect trusted-suite state with:

  ```bash
  scripts/harness.py status --json
  ```

  Before trusting a report, check `freshness`, `pending_verification`, `gate_matrix`,
  each `evidence_state`, and `evidence_artifact_results`.
- `scripts/harness-hook.py` does not run checks and does not change repository state.
  It reminds or blocks on pending verification; it
  denies `git commit` while required verification is pending.

## Local Loop

- After code or policy changes, run:

  ```bash
  scripts/harness.py sweep required --report .cache/harness/required.json
  ```

- Prefer the repo venv:

  ```bash
  PYTHON=.venv/bin/python scripts/check-local.sh
  ```

- If `.venv` is missing, install dev dependencies with
  `python -m pip install -e ".[dev]"`.
- Focused TDD starts with the smallest relevant pytest command but ends with the
  required sweep.

## Repo Skills

Local Codex skills live in `.codex/skills/`:

- `model-tier-prompting`: model-aware prompt and tier guidance.
- `refine`: turn rough ideas into confirmable task briefs.

## Heavy Checks

- Do not run device, QEMU, release, or network-heavy checks unless the user
  asked for that validation or the task specifically requires it.
- When explicitly needed, use `scripts/harness.py run <id>` with required
  `--allow-*` flags; `--report` captures output and
  validates declared `evidence_artifacts`.
- Current target: `root@10.100.0.19`. Device claims require
  `scripts/verify-on-device.sh root@10.100.0.19` evidence.

## Reporting

- Report exact commands and outcomes; separate local evidence from device, QEMU,
  and release evidence.
- Do not claim MangoHud sensor, gamescope, SteamOS Manager, EC, or package
  release behavior is verified unless the matching harness layer was run.
