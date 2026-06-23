# AI Development Harness

This repository is arranged so AI agents can make changes with tight feedback.

## Local loop

Use hardware-free tests first:

```bash
scripts/check-local.sh
```

The tests build fake RAPL sysfs trees under pytest temporary directories, so no
root privileges or SteamOS host is needed for backend behavior.

`scripts/check-local.sh` runs:

- `ruff check .`
- `pytest`
- `compileall`

## TDD contract

All production behavior changes must follow `docs/tdd-workflow.md`.

The required loop is:

- RED: write or update the focused test first and capture the expected failure.
- GREEN: make the smallest production change and capture the same test passing.
- VERIFY: run `scripts/check-local.sh`.

Hardware-facing changes also need device verification with
`scripts/verify-on-device.sh`.

Pull requests must include RED evidence, GREEN evidence, and Verification
evidence using `.github/pull_request_template.md`.

## Device loop

Use the scripts against a root SSH target:

```bash
scripts/collect-device-info.sh root@192.168.128.214
scripts/install-on-device.sh root@192.168.128.214
scripts/verify-on-device.sh root@192.168.128.214
scripts/configure-gamescope-display-workaround.sh enable root@192.168.128.214
```

The verifier checks:

- systemd service is active
- SteamOS Manager sees `TdpLimit1`
- `steamosctl set-tdp-limit` updates central D-Bus state
- the remote D-Bus service reports the same value
- Intel RAPL PL1 matches the requested value
- TDP is restored to the requested restore wattage
- no systemd failed units remain

Display workaround changes must also capture:

- the user service state for `steamos-intel-handheld-gamescope-display.service`
- evidence that `gamescopectl composite_force 1` was applied
- before/after DRM plane samples showing whether the primary plane still
  switches between `XR30` 1920x1200 and `XB24`

## Editing rules for agents

- Add or update a failing test before changing production behavior.
- Do not edit production code until the RED command has been run and failed for
  the expected reason.
- Do not report a change as complete until `scripts/check-local.sh` passes.
- Keep D-Bus names stable unless there is a migration note.
- Do not add a new hardware profile without a `collect-device-info.sh` capture
  summarized in `docs/hardware/`.
- Do not make boot-time TDP enforcement the default without documenting the
  SteamOS policy interaction.
