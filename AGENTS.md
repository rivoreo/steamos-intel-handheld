# AGENTS.md

## Start Here

- Read `harness.toml` first. It is the machine-readable map of local, device,
  release, and QEMU checks.
- List available checks with:

  ```bash
  scripts/harness.py list --json
  ```

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
- Current handheld examples use `root@10.100.0.19`.
- Device-facing changes need `scripts/verify-on-device.sh root@10.100.0.19`
  evidence before claiming hardware validation.

## Reporting

- Report exact commands run and whether they passed.
- Separate local/unit evidence from real-device, QEMU, and release-artifact
  evidence.
- Do not claim MangoHud sensor, gamescope, SteamOS Manager, EC, or package
  release behavior is verified unless the matching harness layer was run.
