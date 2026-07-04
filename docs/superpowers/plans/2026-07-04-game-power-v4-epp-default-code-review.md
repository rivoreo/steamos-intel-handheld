# Code Review Report: Game Power V4 EPP-Only Default

## Summary

| Lane | Focus | Score | Verdict |
| --- | --- | ---: | --- |
| A | Correctness & Plan Alignment | +2 | APPROVE |
| B | Risk & Safety | +2 | APPROVE |
| C | Tests & Verification | +2 | APPROVE |
| D | Maintainability | +1 | APPROVE |
| E | Product/Docs Impact | +1 | APPROVE |

**Overall Result**: APPROVED WITH NOTES
**Harness Status**: passed
**Review Iterations**: 1 discovery + 1 held-out sweep, budget 2/5
**Findings**: 0 confirmed blockers / 0 needs-decision

## Review Surface

Changed code and packaged assets:

- `src/steamos_intel_handheld/power_control.py`
- `data/systemd/steamos-intel-handheld-power-control.service`
- `README.md`
- `docs/design.md`
- `tests/test_power_control_cli.py`
- `tests/test_integration_assets.py`
- `docs/superpowers/specs/2026-07-04-game-power-v4-epp-default-design.md`
- `docs/superpowers/plans/2026-07-04-game-power-v4-epp-default-plan-review.md`

Requirements:

- installed daemon default remains `gpu-priority`
- CPU max-frequency cap is off by default
- explicit CPU-cap profiling path remains available
- local Harness and guarded handheld validation must pass

## Verification Evidence

- RED:
  - `.venv/bin/python -m pytest tests/test_power_control_cli.py::test_parser_configures_game_power_defaults_gpu_priority_epp_only tests/test_integration_assets.py::test_power_control_service_enables_game_power_governor_by_default tests/test_integration_assets.py::test_docs_describe_game_power_governor_default_epp_only_and_reversible -q`
  - failed before implementation on parser default, systemd unit, and README.
- Focused GREEN:
  - same command, passed.
  - `.venv/bin/python -m pytest tests/test_power_control_cli.py tests/test_integration_assets.py -q`
  - `61 passed`.
- Required local gate:
  - `PYTHON=.venv/bin/python scripts/check-local.sh`
  - `413 passed`.
  - `scripts/harness.py sweep required --report .cache/harness/required.json`
  - `local: pass`.
- Diff hygiene:
  - `git diff --check`
  - passed with no output.
- Device deployment:
  - `scripts/install-on-device.sh root@10.100.0.19`
  - installed service and Decky plugin files.
  - `systemctl is-active steamos-intel-handheld-power-control.service`
  - `active`.
  - `systemctl show ... -p ExecStart -p ActiveState -p SubState`
  - active `ExecStart` includes `--game-power-cpu-cap off`.
- Foreground-game verifier:
  - `VERIFY_GAME_POWER_APPID=1091500 scripts/verify-game-power-on-device.sh root@10.100.0.19`
  - observed AppID `1091500`, reached `gpu-priority-epp`, and restored CPU policy.
- Full device verifier:
  - first default run failed because the verifier assumed `battery-maxq` and the
    active device state was AC Performance (`rapl-pl2 expected 25, got 37`).
  - rerun with `VERIFY_TDP_POLICY_MODE=ac-performance scripts/verify-on-device.sh root@10.100.0.19`
  - passed and restored 30W.
- Short controlled profile:
  - `PROFILE_GAME_POWER_CAPTURE_MODE=controlled PROFILE_GAME_POWER_TDPS=12 PROFILE_GAME_POWER_POLICIES='off gpu-priority' PROFILE_GAME_POWER_REPEATS=1 PROFILE_GAME_POWER_DURATION_S=45 PROFILE_GAME_POWER_FPS_TARGET=40 PROFILE_GAME_POWER_SCENE_EVIDENCE='current-cyberpunk-scene-v4-epp-default-2026-07-04' scripts/profile-game-power-on-device.sh root@10.100.0.19`
  - artifacts copied to `.cache/game-power/profiles/`.
  - candidate run actions: `gpu-priority-epp: 21`, `observe-only: 1`,
    `cpu_cap_enabled: false`, `restored: true`.
  - action-equivalence and runtime telemetry aggregate status: `pass`.

## Lane Findings

No critical or important findings survived verification.

### Non-Blocking Notes

- The short 12W profile is not a positive FPS claim. It validates the EPP-only
  action path and restore behavior. Its average FPS was effectively neutral
  against the paired baseline within a single 45s repeat.
- The first full-device verifier failure was a verifier mode mismatch, not a
  Game Power regression. The AC Performance rerun matched the active device
  policy and passed.

## Harness Output

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "code",
  "iteration": 1,
  "budget": {"maxSweeps": 5, "sweepsUsed": 2},
  "harnessStatus": "passed",
  "reason": "no verified blocking findings remain after held-out sweep",
  "reasonCode": "converged",
  "base": "main-before-v4-epp-default",
  "head": "working-tree",
  "surfaceId": "game-power-v4-epp-default-diff",
  "activeLanes": ["A", "B", "C", "D", "E"],
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 0,
    "refutedFindings": 0,
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
    "maxMaterialFixAttempts": 0,
    "heldOutSweepsUsed": 1,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "userCheckpoints": {
    "intakeAsked": false,
    "decisionAsked": false,
    "recordedAssumptions": []
  },
  "attributionEvidence": {
    "originalDiffSnapshot": "working tree before commit",
    "currentDiffSnapshot": "working tree before commit",
    "latestPatchDiff": "initial implementation"
  },
  "ledger": [],
  "verification": {
    "commandsRun": [
      ".venv/bin/python -m pytest tests/test_power_control_cli.py::test_parser_configures_game_power_defaults_gpu_priority_epp_only tests/test_integration_assets.py::test_power_control_service_enables_game_power_governor_by_default tests/test_integration_assets.py::test_docs_describe_game_power_governor_default_epp_only_and_reversible -q",
      ".venv/bin/python -m pytest tests/test_power_control_cli.py tests/test_integration_assets.py -q",
      "PYTHON=.venv/bin/python scripts/check-local.sh",
      "scripts/harness.py sweep required --report .cache/harness/required.json",
      "git diff --check",
      "scripts/install-on-device.sh root@10.100.0.19",
      "VERIFY_GAME_POWER_APPID=1091500 scripts/verify-game-power-on-device.sh root@10.100.0.19",
      "VERIFY_TDP_POLICY_MODE=ac-performance scripts/verify-on-device.sh root@10.100.0.19",
      "PROFILE_GAME_POWER_CAPTURE_MODE=controlled PROFILE_GAME_POWER_TDPS=12 PROFILE_GAME_POWER_POLICIES='off gpu-priority' PROFILE_GAME_POWER_REPEATS=1 PROFILE_GAME_POWER_DURATION_S=45 PROFILE_GAME_POWER_FPS_TARGET=40 PROFILE_GAME_POWER_SCENE_EVIDENCE='current-cyberpunk-scene-v4-epp-default-2026-07-04' scripts/profile-game-power-on-device.sh root@10.100.0.19"
    ],
    "trustedSetRun": true,
    "missingEvidence": []
  },
  "nextAction": {
    "type": "stop",
    "summary": "Stage, commit, and push the reviewed change."
  }
}
```
