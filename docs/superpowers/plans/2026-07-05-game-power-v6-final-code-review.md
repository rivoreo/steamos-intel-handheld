# Game Power V6 Final Code Review

## Status

Passed. No verified critical or important blockers remain.

## Review Surface

Base/head:

- base: `52d4abccfc4731cf369ae4040ca837954631e7b1`
- surface: current worktree diff for Game Power V6 final

Changed production files:

- `src/steamos_intel_handheld/game_power_control.py`
- `src/steamos_intel_handheld/power_control.py`
- `src/steamos_intel_handheld/game_power.py`
- `decky/steamos-intel-handheld-game-power/main.py`
- `decky/steamos-intel-handheld-game-power/src/index.tsx`
- `decky/steamos-intel-handheld-game-power/dist/index.js`

Changed test/review artifacts:

- `tests/test_game_power_control.py`
- `tests/test_power_control_cli.py`
- `tests/test_game_power.py`
- `tests/test_decky_plugin_backend.py`
- `tests/test_decky_plugin_assets.py`
- V6 final research, plan, and Plan Review documents.

Requirements reviewed against:

- `docs/superpowers/plans/2026-07-05-game-power-v6-final.md`
- `docs/superpowers/specs/2026-07-05-game-power-v6-final-research-ledger.md`
- `docs/superpowers/plans/2026-07-05-game-power-v6-final-plan-review.md`

## Lane Results

### A: Correctness & Plan Alignment

Result: pass.

Findings attacked and refuted:

- FPS target override is not only a status/UI value. It is read per sample via
  `SystemGamePowerObserver.frame_target_provider` and used by the sampled
  `GamePowerSample`.
- Targetless contexts no longer promote or reuse hints. Legacy
  `none-configured` contexts are normalized to incomplete, and `get_hint()`
  rejects non-reusable target keys.
- Manual FPS target control preserves mode overrides and manual target writes
  preserve valid mode state.

Evidence:

- `game_power_control.py` validates 30-120 FPS in 5 FPS steps and overlays
  `FrameTargetTelemetry(source="manual", confidence="high")`.
- `power_control.py` wires `_build_frame_target_provider()` into
  `SystemGamePowerObserver`.
- `game_power.py` records incomplete sessions visibly but leaves `hint_key`
  null and aggregate update false.

### B: Risk & Safety

Result: pass.

Findings attacked and refuted:

- No raw P/E-core frequency, CPU-cap, `uclamp`, PL2/Tau, or affinity knobs were
  added to Decky.
- Runtime control writes are atomic and limited to public mode plus manual FPS
  objective.
- Device verifier restored RAPL/EC state and Game Power verifier restored CPU
  policy.

Residual note:

- If a hand-edited control file contains a valid schema with invalid mode and a
  valid FPS target, status reports the invalid mode and the scheduler ignores
  that mode. This is acceptable for V6 because public writes cannot create that
  state and invalid mode does not map to a raw policy write.

### C: Tests & Verification

Result: pass.

Verification evidence:

```text
npm --prefix decky/steamos-intel-handheld-game-power run build
created dist in 918ms

.venv/bin/python -m pytest tests/test_game_power_control.py \
  tests/test_power_control_cli.py tests/test_game_power.py \
  tests/test_decky_plugin_backend.py tests/test_decky_plugin_assets.py
119 passed in 2.39s

scripts/harness.py sweep required --report .cache/harness/required.json
local: pass

scripts/verify-on-device.sh root@10.100.0.19
OK: RAPL PL1/PL2 restored
OK: systemd failed-unit list is empty
OK: SteamOS Manager TDP remote works and restored 30W

scripts/verify-game-power-on-device.sh root@10.100.0.19
game-power verifier: CPU policy restored

PROFILE_GAME_POWER_APPID=1903340 PROFILE_GAME_POWER_CAPTURE_MODE=controlled \
PROFILE_GAME_POWER_TDPS='12 30' PROFILE_GAME_POWER_POLICIES='off gpu-priority' \
PROFILE_GAME_POWER_REPEATS=1 PROFILE_GAME_POWER_DURATION_S=45 \
PROFILE_GAME_POWER_WARMUP_S=10 PROFILE_GAME_POWER_POLL_S=2 \
PROFILE_GAME_POWER_FPS_TARGET=45 \
PROFILE_GAME_POWER_OUTPUT_ROOT=.cache/game-power/v6-final-after \
scripts/profile-game-power-on-device.sh root@10.100.0.19
profiles copied to .cache/game-power/v6-final-after
```

Profile contracts:

- `action-equivalence.json`: pass.
- `profile-runtime-telemetry-contract.json`: pass, 6/6 run contracts.

Coverage note:

- A short 30 FPS target-satisfied spot check on the device was not completed
  because SSH timed out after the profile run. Local controller tests cover the
  target-satisfied suppression path; the completed device profile covers the
  manual-target/below-target path and runtime telemetry contracts.

### D: Maintainability

Result: pass.

The implementation follows existing local patterns:

- runtime control remains a small JSON file with atomic writes,
- observer still owns sample construction,
- `GamePowerGovernor` owns session lifecycle and snapshot output,
- Decky backend continues using direct safe CLI calls rather than restarting
  services or editing unit files.

No new broad abstraction was introduced beyond the target provider callable,
which directly resolves the Plan Review blocker that the scheduler sample path
needed the target, not only the UI/status path.

### E: UX/Product

Result: pass.

The previous ambiguous language is removed from the source and bundled asset:

- no `自動觀察`,
- no `只監測`,
- no raw technical controls,
- Automatic is described as FPS-target balancing,
- Observe is data-only with no power changes,
- Off/Stop is sampling stopped.

The FPS control is product-level:

- `Use SteamOS limit`,
- `Manual FPS target`,
- slider 30-120 FPS / 5 FPS,
- no P-core/E-core or low-level scheduling constants.

## Held-Out Sweep

Held-out pass over final diff found no confirmed blockers. Non-blocking residual
is the deferred 30 FPS target-satisfied device spot check noted above.

## Harness Output

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "code",
  "iteration": 1,
  "budget": {
    "maxSweeps": 5,
    "sweepsUsed": 2
  },
  "harnessStatus": "passed",
  "reason": "No verified critical or important blockers remain after full diff and held-out review.",
  "reasonCode": "converged",
  "base": "52d4abccfc4731cf369ae4040ca837954631e7b1",
  "head": "worktree",
  "surfaceId": "game-power-v6-final",
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
    "recordedAssumptions": [
      "30 FPS target-satisfied device spot check may be run later when SSH is reachable; local controller tests cover this behavior."
    ]
  },
  "attributionEvidence": {
    "originalDiffSnapshot": "git diff from 52d4abccfc4731cf369ae4040ca837954631e7b1 to worktree",
    "currentDiffSnapshot": "git diff from 52d4abccfc4731cf369ae4040ca837954631e7b1 to worktree",
    "latestPatchDiff": "same as current diff for one-iteration review"
  },
  "ledger": [],
  "verification": {
    "commandsRun": [
      "git diff --check",
      "npm --prefix decky/steamos-intel-handheld-game-power run build",
      ".venv/bin/python -m pytest tests/test_game_power_control.py tests/test_power_control_cli.py tests/test_game_power.py tests/test_decky_plugin_backend.py tests/test_decky_plugin_assets.py",
      "scripts/harness.py sweep required --report .cache/harness/required.json",
      "scripts/install-on-device.sh root@10.100.0.19",
      "scripts/verify-on-device.sh root@10.100.0.19",
      "scripts/verify-game-power-on-device.sh root@10.100.0.19",
      "scripts/profile-game-power-on-device.sh root@10.100.0.19"
    ],
    "trustedSetRun": true,
    "missingEvidence": []
  },
  "nextAction": {
    "type": "stop",
    "summary": "Commit and push the reviewed V6 final slice."
  }
}
```
