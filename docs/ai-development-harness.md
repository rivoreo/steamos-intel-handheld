# AI Development Harness

This repository is arranged so AI agents can make changes with tight feedback.
Root `AGENTS.md` is the short agent entry point, and `harness.toml` is the
machine-readable map of local, device, release, and QEMU checks.

## Control plane

`harness.toml` also declares the harness control plane:

- `trusted_suite`: the command that closes local changes.
- `iteration_hint`: a focused test command shape for TDD iteration only.
- `report_path`: the canonical JSON report location for the required sweep.

Use the CLI to inspect that control plane:

```bash
scripts/harness.py status --json
scripts/harness.py explain local
scripts/harness.py explain device-full
```

The status command is the closed-loop control-plane readout. It reads the
latest trusted-suite report, checks whether that report is fresh against the
current manifest and workspace snapshot, lists pending verification work, and
emits a `gate_matrix` for every check. Gate rows use the runtime guarded
classifier, so malformed required checks that contain guarded commands are
reported as blocked instead of appearing runnable. Each row also exposes
`evidence_artifacts`, the durable outputs that must be reachable before a gate
can be treated as verified.

The explain command is read-only: it describes a gate, its requirements, and
the required evidence artifacts without running device, release, QEMU, or
network-heavy commands.

## Report Freshness

`scripts/harness.py sweep required --report .cache/harness/required.json`
writes report context including the trusted suite, manifest hash, workspace
fingerprint, per-check results, and `evidence_artifact_results`. For the local
trusted suite, those artifact results prove the command output, ruff summary,
engineering policy summary, shell syntax check, pytest summary, and compileall
step were all reachable in the captured sweep output.

`scripts/harness.py status --json` marks a report missing, invalid, stale, or
fresh and gives the trusted command needed to clear pending local verification.
It does not treat a passing result as verified unless the declared evidence
artifacts are also verified. Old or hand-written reports that omit artifact
results stay pending until the trusted suite is rerun.

Generated Python caches and the harness report file itself are ignored when
computing freshness, so the local trusted sweep does not make its own report
stale.

## Guarded Evidence

Guarded checks are not part of the automatic local loop. Device, foreground
game, release-artifact, QEMU, and network-heavy checks still require an explicit
human decision before they are run.

When a guarded check is deliberately allowed, run it through the harness and
write a report:

```bash
scripts/harness.py run device-full \
  --allow-guarded \
  --allow-requirement root-ssh \
  --allow-requirement handheld \
  --report .cache/harness/device-full.json
```

`run` captures stdout and stderr in the JSON report while replaying output to the
terminal. A zero command exit is not enough: the report status is
`evidence-fail` unless every declared `evidence_artifact` is proven by a
registered marker. The verifier scripts therefore emit explicit success markers
for otherwise silent checks such as RAPL restore, failed-unit emptiness, CPU
policy restore, profile artifacts, and the QEMU mangoapp artifact path.

This gives agents a closed evidence ledger for manually approved heavy checks
without making hardware, QEMU, release, or network work run automatically.

## Hook Reminders

`.codex/hooks.json` wires `scripts/harness-hook.py` into `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `Stop`, and `SubagentStop`. The hook is
read-only and does not run checks: it does not change repository state, calls
`scripts/harness.py status --json`, reports pending verification, and injects
advisory context for matched repo-local skills.

On `PreToolUse`, `git commit` is denied while verification is pending and the
response names the trusted suite to run. On `Stop` and `SubagentStop`, pending
local verification blocks once per unchanged pending state. On
`UserPromptSubmit`, prompt/requirements tasks can load
`.codex/skills/model-tier-prompting/SKILL.md` or `.codex/skills/refine/SKILL.md`
as needed. The hook writes only dedupe state under the OS temp directory.

## Local loop

Use hardware-free tests first:

```bash
scripts/check-local.sh
```

The tests build fake RAPL sysfs trees under pytest temporary directories, so no
root privileges or SteamOS host is needed for backend behavior.

`scripts/check-local.sh` runs:

- `ruff check src tests scripts`
- `scripts/check-engineering-policy.py`
- `bash -n scripts/*.sh`
- `pytest`
- `compileall`

## TDD contract

All production behavior changes must follow `docs/tdd-workflow.md`.

The required loop is:

- RED: write or update the focused test first and capture the expected failure.
- GREEN: make the smallest production change and capture the same test passing.
- VERIFY: run the trusted suite from `harness.toml`.

Hardware-facing changes also need device verification with
`scripts/verify-on-device.sh`.

Pull requests must include RED evidence, GREEN evidence, and Verification
evidence using `.github/pull_request_template.md`.

## Device loop

Use the scripts against a root SSH target:

```bash
scripts/collect-device-info.sh root@10.100.0.19
scripts/install-on-device.sh root@10.100.0.19
scripts/verify-on-device.sh root@10.100.0.19
scripts/configure-gamescope-display-workaround.sh enable root@10.100.0.19
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
