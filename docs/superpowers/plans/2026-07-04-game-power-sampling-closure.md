# Game Power Sampling Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Game Power sampling closure: rolling evidence,
bounded persistent hint aggregates, safe repeat-launch warm-up, context reset,
contradiction handling, and session-close evidence.

**Architecture:** Keep per-sample classification and rolling hysteresis in
`GamePowerController`. Put AppID/context ownership, session summaries,
restore/write outcomes, hint loading, cache persistence, and JSONL
session-close events in `GamePowerGovernor`. Keep platform-specific context
wiring in `power_control.py`, with standalone CLI persistence opt-in.

**Tech Stack:** Python dataclasses, pytest, JSONL, atomic file replace, Linux
sysfs/proc helpers already present in `game_power.py`, repo harness
`scripts/harness.py`.

---

## File Structure

- Modify `src/steamos_intel_handheld/game_power.py`
  - Add rolling-window controller state and evidence enrichment.
  - Add `GamePowerHintPolicy`, `GamePowerHintContext`,
    `GamePowerSessionSummary`, `GamePowerActuatorOutcome`, and
    `GamePowerHintStore`.
  - Add canonical context-key helper and deterministic PL1/FPS/runtime context
    normalization.
  - Extend `GamePowerGovernor` with context/session lifecycle, hint use,
    contradiction handling, cache persistence, and session-close JSONL events.
  - Add standalone `--hint-cache` option.
- Modify `src/steamos_intel_handheld/power_control.py`
  - Add service-side `--game-power-hint-cache`.
  - Wire a context provider using `TdpBackend.current_power_source()`.
- Modify `tests/test_game_power.py`
  - Add focused RED tests for rolling majority, hint store, cache safety,
    contradiction, canonical keys, session close, and governor behavior.
- Modify `tests/test_power_control_cli.py`
  - Prove service wiring enables the default hint cache and passes a
    power-source context provider.

## Task 1: Rolling Evidence Controller

- [ ] Add RED tests in `tests/test_game_power.py`:
  - `test_controller_rolling_majority_blocks_activation_after_two_recent_positives`
  - `test_active_controller_rolling_majority_blocks_restore_after_two_recent_negatives`
  - `test_rolling_window_zero_and_one_preserve_legacy_hysteresis`
  - `test_controller_classification_evidence_includes_post_update_rolling_state`
- [ ] Run:
  `.venv/bin/python -m pytest tests/test_game_power.py -k "rolling_majority or rolling_window_zero or post_update_rolling" -q`
  and verify these tests fail because the fields/behavior do not exist.
- [ ] Implement `rolling_window_samples`, `hinted_activate_samples`, controller
  rolling deque, majority gating, legacy bypass for `<= 1`, and post-update
  evidence enrichment.
- [ ] Re-run the focused tests and verify they pass.

## Task 2: Hint Context And Store

- [ ] Add RED tests in `tests/test_game_power.py`:
  - `test_hint_context_key_uses_canonical_json_hash_and_rejects_mismatch`
  - `test_pl1_bucket_rounding_is_deterministic`
  - `test_hint_store_promotes_after_two_clean_matching_sessions`
  - `test_hint_store_ignores_oversized_invalid_and_malformed_cache`
  - `test_hint_store_prunes_oldest_records_and_respects_byte_limit`
  - `test_runtime_unaware_hint_uses_short_age_limit`
- [ ] Run:
  `.venv/bin/python -m pytest tests/test_game_power.py -k "hint_context_key or pl1_bucket or hint_store" -q`
  and verify the tests fail.
- [ ] Implement `GamePowerHintPolicy`, `GamePowerHintContext`,
  `canonical_hint_key()`, `pl1_bucket_w()`, `GamePowerSessionSummary`, and
  `GamePowerHintStore` with aggregate/promotion/load/prune rules.
- [ ] Re-run the focused tests and verify they pass.

## Task 3: Governor Session Lifecycle

- [ ] Add RED tests in `tests/test_game_power.py`:
  - `test_matching_hint_reduces_warmup_but_requires_current_positive_sample`
  - `test_context_change_restores_before_aggregate_update`
  - `test_restore_failure_write_failure_and_unknown_restore_block_promotion`
  - `test_contradicted_hinted_session_cannot_learn_or_repair`
  - `test_contradiction_limit_blocks_reloaded_hint`
  - `test_session_close_jsonl_emits_bounded_persistence_outcome`
  - `test_observe_and_incomplete_context_do_not_write_hints`
- [ ] Run:
  `.venv/bin/python -m pytest tests/test_game_power.py -k "hint or context_change or session_close or incomplete_context" -q`
  and verify the tests fail.
- [ ] Implement governor context provider support, active-context reset,
  restore outcome recording, session summaries, hint application, current-session
  contradiction, session-close cache updates, and JSONL session-close events.
- [ ] Re-run the focused tests and verify they pass.

## Task 4: Service Wiring And CLI

- [ ] Add RED tests in `tests/test_power_control_cli.py`:
  - `test_parser_configures_game_power_hint_cache_default`
  - `test_build_game_power_governor_wires_power_source_context_provider`
- [ ] Run:
  `.venv/bin/python -m pytest tests/test_power_control_cli.py -k "game_power_hint_cache or context_provider" -q`
  and verify the tests fail.
- [ ] Add `--hint-cache` to standalone `game_power.py`.
- [ ] Add `--game-power-hint-cache` to `power_control.py`.
- [ ] Pass `GamePowerHintStore` and a context provider into
  `GamePowerGovernor` when the service is active.
- [ ] Re-run the focused service tests and verify they pass.

## Task 5: Verification And Commit

- [ ] Run focused regression:
  `.venv/bin/python -m pytest tests/test_game_power.py tests/test_power_control_cli.py`
- [ ] Run required sweep:
  `scripts/harness.py sweep required --report .cache/harness/required.json`
- [ ] If local verification passes, install/deploy to the handheld if reachable
  and run guarded `game-power-device` only when a foreground game is available.
- [ ] Stage only the sampling-closure files, inspect `git diff --cached
  --name-status`, commit, and push.

## Self-Review

- The plan covers every accepted design requirement: rolling evidence,
  persistent hints, context reset, corruption/pruning, restore/write outcome,
  contradiction repair, session-close observability, canonical keys, service
  wiring, focused tests, and required sweep.
- No FPS, p99, or 1% low improvement is claimed by local tests.
- No Decky UI or user-tunable measured P/E-core frequency controls are in
  scope.
