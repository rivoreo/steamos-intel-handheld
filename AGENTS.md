# AGENTS.md

Work from the repository and current evidence. State the intent and important
constraints, then choose the implementation path that best fits what you find.
Do not turn examples or historical plans into mandatory procedure.

## Verification

- Use the smallest check that can falsify the current change while iterating.
- Run the full local closure suite for broad integration changes, PR/CI
  readiness, releases, or when explicitly requested:

  ```bash
  PYTHON=.venv/bin/python scripts/check-local.sh
  ```

## Boundaries

- Do not run device, QEMU, release, signing, publishing, or network-heavy checks
  unless the task or requested claim reaches that boundary.
- Device scripts require `--allow-device`, QEMU/rootfs actions require
  `--allow-qemu`, and signed repository assembly requires `--allow-release`.
- Current device target: `root@10.100.0.19`. Device claims require actual
  on-device evidence; local success is not device, QEMU, or release evidence.

## Delegation and skills

- Delegate independent work when it reduces latency or adds useful independent
  scrutiny. Delegation does not expand scope or side-effect authority, and the
  main agent integrates and verifies the result.
- Repo-local skills live in `.codex/skills/`. Use them when their domain is
  actually in scope; skill routing is advisory.

## Reporting

Report exact commands and outcomes. Separate local evidence from device, QEMU,
release, and production claims, and name any layer that was not run.
