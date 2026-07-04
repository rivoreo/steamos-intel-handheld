# AGENTS.md

## Start Here

- Read `harness.toml` first. It is the machine-readable map of local, device,
  release, and QEMU checks.
- List available checks with:

  ```bash
  scripts/harness.py list --json
  ```

- Inspect the current trusted-suite status with:

  ```bash
  scripts/harness.py status --json
  ```

  Check `freshness`, `pending_verification`, `gate_matrix`, each row's
  `evidence_state`, and `evidence_artifact_results` before trusting a report.
- `scripts/harness-hook.py` does not run checks. It does not change repository state.
  It reminds or blocks on pending verification, and denies `git commit` while
  required verification is pending.

## Local Loop

- After any code or policy change, run the required sweep:

  ```bash
  scripts/harness.py sweep required --report .cache/harness/required.json
  ```

- Prefer the repo venv when present:

  ```bash
  PYTHON=.venv/bin/python scripts/check-local.sh
  ```

- If `.venv` is missing, install dev dependencies with `python -m pip install -e ".[dev]"`
  and then run `scripts/check-local.sh`.
- For focused TDD, run the smallest relevant `.venv/bin/python -m pytest ...`
  command first, then the required sweep. Do not stop after the focused test.

## Heavy Checks

- Do not run device, QEMU, release, or network-heavy checks unless the user
  asked for that validation or the task specifically requires it.
- When a heavy check is explicitly needed, prefer `scripts/harness.py run <id>`
  with the required `--allow-*` flags and `--report ...`; the report captures
  output and validates declared `evidence_artifacts`.
- Current handheld examples use `root@10.100.0.19`.
- Device-facing changes need `scripts/verify-on-device.sh root@10.100.0.19`
  evidence before claiming hardware validation.

## Reporting

- Report exact commands run and whether they passed.
- Separate local/unit evidence from real-device, QEMU, and release-artifact
  evidence.
- Do not claim MangoHud sensor, gamescope, SteamOS Manager, EC, or package
  release behavior is verified unless the matching harness layer was run.
