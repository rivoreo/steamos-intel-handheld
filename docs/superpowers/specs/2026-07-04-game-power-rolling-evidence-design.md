# Game Power Sampling Closure Design

## Goal

Improve the Game Power governor's sampling loop so it is stable inside one
game session and can safely reuse validated observations on later launches of
the same game/context.

This is not a general auto-tuner and it must not open user-tunable CPU block
frequencies. The first complete version adds a bounded evidence loop:

1. short-term rolling evidence for second-to-second classification stability,
2. per-session summaries for the current foreground game/context,
3. durable aggregate and promoted hint records keyed by game and platform
   context,
4. explicit invalidation, repair, and fallback rules.

The result should make repeat launches warm up faster only when earlier
evidence was clean. Current samples always remain authoritative enough to
revoke the hint and restore CPU policy.

## Current State

`SystemGamePowerObserver.sample()` produces one `GamePowerSample` per
`poll_s` window. The default service uses `poll_s=2.0`. Each sample contains:

- foreground Steam AppID,
- RAPL package/core/uncore watts,
- current PL1,
- DRM fdinfo render busy,
- frame target metadata when provided,
- foreground/system pressure telemetry.

`GamePowerController` currently stores only:

- `_positive_samples`,
- `_negative_samples`,
- `_active`.

The default config enters after `activate_samples=2` consecutive positive
samples and restores after `restore_samples=3` consecutive negative samples.
No sample history is currently persisted to disk, and no AppID-specific profile
is loaded when a game starts.

## In Scope

- Add an in-memory rolling evidence buffer inside `GamePowerController`.
- Keep the buffer bounded by sample count, not wall-clock timers.
- Use the existing `_sample_supports_gpu_priority()` predicate as the source of
  positive/negative evidence.
- Require recent-window majority evidence before activation and restore.
- Preserve the existing behavior when the buffer size is set to `0` or `1`.
- Add a per-context session summary owned by `GamePowerGovernor`.
- Add a durable `GamePowerHintStore` with two record classes:
  - aggregate records that accumulate clean evidence across sessions,
  - promoted hint records that can reduce activation warm-up.
- Key hints by AppID, PL1/TDP bucket, power source, FPS target state, CPU
  topology signature, OS/kernel/driver signature, policy version, and a runtime
  signature when the platform can provide one.
- Allow a promoted hint to lower activation latency for the same game/context
  on a later launch, while runtime sampling can still revoke or restore.
- Expire or disable hints when signatures change, cache schema changes, or
  runtime samples contradict the hint.
- Include enough bounded state in classification evidence and JSONL output to
  debug why a decision waited, acted, ignored a hint, or rejected a hint.
- Add focused TDD tests for rolling evidence, context changes, hint load/use,
  hint aggregation/promotion, invalidation, corruption handling, restore/write
  outcome gating, and compatibility with existing hysteresis behavior.
- Close the change with `scripts/harness.py sweep required --report
  .cache/harness/required.json`.

## Out of Scope

- Runtime FPS outcome actuation from actual FPS, p99 frametime, or 1% low.
- New CPUFreq, RAPL, cgroup, uclamp, cpuset, affinity, sched_ext, or Decky
  write controls.
- Decky UI changes.
- Changing measured P-core/E-core caps or exposing them to users.
- Trying multiple new policy families automatically without profiler evidence.
- Treating one prior session as permanent truth.
- Persisting raw PID, process path, cgroup path, per-thread data, or measured
  P-core/E-core constants.
- Claiming FPS, frametime, or 1% low improvement without
  `game-power-profile-device` evidence.

## Contract Table

| Name | Default | Owner | Test seam |
| --- | --- | --- | --- |
| `rolling_window_samples` | `5` | `GamePowerConfig` | controller unit tests |
| `hinted_activate_samples` | `1` | `GamePowerConfig` | controller/governor tests |
| `min_hint_sessions` | `2` | `GamePowerHintPolicy` | hint store tests |
| `min_hint_samples` | `20` | `GamePowerHintPolicy` | hint store tests |
| `min_hint_positive_ratio` | `0.70` | `GamePowerHintPolicy` | hint store tests |
| `hint_contradiction_limit` | `3` | `GamePowerHintPolicy` | hint repair tests |
| `session_hint_contradiction_samples` | `2` | `GamePowerHintPolicy` | current-session disable tests |
| `policy_version` | `game-power-sampling-v1` | `GamePowerHintPolicy` | cache mismatch tests |
| `hint_cache_path` | `/var/lib/steamos-intel-handheld/game-power-hints.json` | service config | temp path tests |
| `state_dir` | `/var/lib/steamos-intel-handheld` | package/service | temp path tests |
| `pl1_bucket_w` | nearest integer watt from current PL1 | hint context provider | bucket boundary tests |
| `os_signature` | kernel release + graphics driver family | hint context provider | injected provider tests |
| `topology_signature` | P/E core topology + CPU model | hint context provider | injected provider tests |
| `runtime_signature` | Proton/runtime identifier when available, otherwise `unavailable` | hint context provider | injected provider tests |
| `max_aggregate_records` | `128` | `GamePowerHintPolicy` | eviction tests |
| `max_hint_entries` | `64` | `GamePowerHintPolicy` | eviction tests |
| `max_hint_cache_bytes` | `262144` | `GamePowerHintPolicy` | oversize-write tests |
| `max_aggregate_age_days` | `14` | `GamePowerHintPolicy` | stale cleanup tests |
| `max_hint_age_days` | `30` | `GamePowerHintPolicy` | stale cleanup tests |
| `max_runtime_unaware_hint_age_days` | `7` | `GamePowerHintPolicy` | runtime-unaware cleanup tests |

The first implementation keeps these as internal constants/config fields. Decky
does not expose them. Tests inject paths and context providers instead of
mutating global system state.

## Runtime Evidence Model

Add this config field:

```python
rolling_window_samples: int = 5
```

The controller keeps a deque of recent boolean evidence:

```python
_recent_positive: deque[bool]
```

Each non-off sample appends whether
`_sample_supports_gpu_priority(sample, active=self._active)` is true. Observe
mode may record read-only evidence for session summaries, but it must never
apply or promote hints.

Compatibility rule:

- When `rolling_window_samples <= 1`, rolling readiness and rolling majority
  checks are bypassed. The controller preserves the current consecutive
  hysteresis behavior exactly.

Activation rule:

- Keep the current `activate_samples` consecutive-positive requirement.
- If a promoted hint is active for the current context, use
  `hinted_activate_samples` as the consecutive-positive requirement.
- When `rolling_window_samples > 1`, also require the rolling window to have at
  least the selected activation sample count.
- When `rolling_window_samples > 1`, require a positive majority in the current
  window before entering GPU-priority.

Restore rule:

- Keep the current `restore_samples` consecutive-negative requirement.
- When `rolling_window_samples > 1`, also require the rolling window to have at
  least `restore_samples` samples.
- When `rolling_window_samples > 1`, require a negative majority in the current
  window before restoring.

This preserves the fast path when evidence is consistently positive or
consistently negative, but prevents one or two noisy samples from flipping
policy when the surrounding window disagrees.

Classification evidence is produced after counters and rolling state are
updated, so JSONL output describes the exact state used for the emitted
decision.

## Hint Context Provider

Add a small context contract used by `GamePowerGovernor` before it opens,
updates, or closes a session:

```python
@dataclass(frozen=True)
class GamePowerHintContext:
    appid: str
    pl1_w: int | None
    power_source: str
    fps_target: str
    topology_signature: str
    os_signature: str
    runtime_signature: str
    runtime_signature_known: bool
    policy_version: str
    complete: bool
```

Context sources:

- `appid`: foreground AppID from the current `GamePowerSample`.
- `pl1_w`: deterministic current PL1/TDP bucket from the current sample,
  rounded to the nearest integer watt after converting from microwatts. Values
  below `0.5 W` are invalid and make the context incomplete.
- `power_source`: `TdpBackend.current_power_source()` in the service path,
  normalized to `ac`, `battery`, or `unknown`.
- `fps_target`: frame target from runtime telemetry when available, normalized
  to a stable string such as `60`, or `none-configured` when no target is
  configured. `unknown` means the source failed and is not reusable.
- `topology_signature`: CPU model plus P/E topology helper.
- `os_signature`: kernel release plus graphics driver family helper.
- `runtime_signature`: Proton/runtime identifier when available. The first
  implementation may return `unavailable` because the current observer does
  not expose Proton/runtime identity.
- `policy_version`: `game-power-sampling-v1`.

Completion rule:

- A context is complete only when:
  - AppID is non-empty.
  - PL1 is known.
  - Power source is `ac` or `battery`; `unknown` is incomplete.
  - FPS target is numeric or `none-configured`; `unknown` is incomplete.
  - Topology signature is known.
  - OS signature is known.
  - Policy version is known.
- Runtime signature is a stronger optional dimension. When it is known, the
  key uses that exact value. When it is not available, the key uses
  `runtime=unavailable` and the hint is marked `runtime_unaware=true`.
- Runtime-unaware hints are allowed because they only reduce warm-up by one
  current-sample check, never skip sampling, and expire faster. They do not
  support claims that Proton/runtime changes are detected; if a runtime
  provider becomes available later, known-runtime keys supersede the
  unavailable bucket.
- Incomplete contexts may update in-memory rolling evidence and read-only
  session summaries.
- Incomplete contexts must not promote a durable hint.
- Incomplete contexts must not use a promoted durable hint, because the key
  cannot prove it still matches the launch conditions.

Standalone CLI behavior:

- If no context provider is configured, persistent hint use and promotion are
  disabled.
- Tests can inject a provider that returns complete or incomplete contexts.
- The service path wires the provider from `power_control.py`, where
  `TdpBackend.current_power_source()` is already available.

## Context Change Semantics

The active context key is:

```text
game-power-context-v1:<64 lowercase hex SHA-256 of canonical tuple bytes>
```

The canonical tuple is JSON encoded with sorted keys and compact separators:

```json
{
  "appid": "1091500",
  "fps_target": "60",
  "os_signature": "kernel-6.16-xe",
  "pl1_w": 22,
  "policy_version": "game-power-sampling-v1",
  "power_source": "battery",
  "runtime_signature": "proton-10",
  "topology_signature": "msi-claw-a1m"
}
```

The raw context remains stored inside each record for debugging and
key/context mismatch validation. Code must not build keys by concatenating raw
fields with delimiters.

When any component changes:

1. if policy is active, restore it and record `GamePowerActuatorOutcome`,
2. close the current session summary with the restore/write outcome,
3. attempt to update the durable aggregate only if the closed session is
   eligible,
4. reset rolling evidence and consecutive counters,
5. load a promoted hint for the new complete context if one exists,
6. start a new session for the new context.

An AppID change, PL1/TDP change, AC/battery transition, FPS-target transition,
runtime signature transition, topology signature transition, or OS signature
transition therefore cannot inherit stale rolling state.

If a sample's AppID no longer matches the active context, the sample is treated
as a context change, not as negative evidence for the prior game.

## Session Summary Model

Add an in-memory `GamePowerSessionSummary` owned by `GamePowerGovernor`. The
session starts when a complete or incomplete foreground context appears and
ends when the context disappears or changes. It tracks only bounded aggregate
counters:

```python
@dataclass
class GamePowerSessionSummary:
    context: GamePowerHintContext
    started_s: float
    samples: int = 0
    positive_samples: int = 0
    negative_samples: int = 0
    applied_samples: int = 0
    restored_samples: int = 0
    cpu_cap_samples: int = 0
    contradiction_samples: int = 0
    hint_was_used: bool = False
    hint_disabled: bool = False
    hint_disable_reason: str | None = None
    write_failed: bool = False
    restore_attempted: bool = False
    restore_succeeded: bool | None = None
    restore_error: str | None = None
```

The summary is not an actuator. It is evidence used to decide whether an
aggregate may be updated at the end of a session.

Eligibility rule:

- Context must be complete.
- Mode must be `gpu-priority`, not `off` or `observe`.
- Samples must be greater than zero.
- No write failure occurred.
- If any policy was applied or a restore was attempted, restore must have
  succeeded exactly.
- `contradiction_samples == 0`.
- The session did not disable a hint because of contradiction.
- Positive evidence ratio must be recorded, even when it is below the promotion
  threshold.

## Persistent Hint Store

Add a small JSON cache under the project state directory:

```text
/var/lib/steamos-intel-handheld/game-power-hints.json
```

For unit tests and development, the path is injectable. The file is written via
temporary file plus atomic replace. The default service is the only writer.
Standalone commands may opt in only by passing an explicit cache path.

Schema:

```json
{
  "schema_version": 1,
  "policy_version": "game-power-sampling-v1",
  "aggregates": {
    "game-power-context-v1:7ed63b...": {
      "context": {
        "appid": "1091500",
        "pl1_w": 22,
        "power_source": "battery",
        "fps_target": "60",
        "topology_signature": "msi-claw-a1m",
        "os_signature": "kernel-6.16-xe",
        "runtime_signature": "proton-10",
        "runtime_signature_known": true,
        "policy_version": "game-power-sampling-v1"
      },
      "observed_sessions": 1,
      "total_samples": 14,
      "positive_samples": 11,
      "cpu_cap_samples": 6,
      "clean_restore_sessions": 1,
      "last_observed_at": "2026-07-04T10:00:00Z"
    }
  },
  "entries": {
    "game-power-context-v1:7ed63b...": {
      "preferred_mode": "gpu-priority",
      "confidence": "medium",
      "observed_sessions": 2,
      "total_samples": 31,
      "positive_ratio": 0.82,
      "cpu_cap_ratio": 0.41,
      "last_validated_at": "2026-07-04T10:00:00Z",
      "contradiction_count": 0,
      "stale": false,
      "runtime_unaware": false
    }
  }
}
```

The first implementation supports only one preferred runtime family:
`gpu-priority`. It must not persist measured P-core/E-core frequencies or expose
internal thresholds.

Load rules:

- Missing file: start empty.
- Existing file larger than `max_hint_cache_bytes`: ignore for this run,
  expose `hint_store_load_error=cache_over_budget`, and do not parse it until a
  clean eligible session replaces it.
- Invalid JSON: ignore for this run, expose `hint_store_load_error` evidence,
  and do not overwrite until a clean eligible session closes.
- Unknown schema version: ignore the file.
- Unknown future fields: ignore those fields.
- Malformed aggregate or entry: drop that record only.
- Record key/context mismatch: drop that record.
- Policy version mismatch: ignore entries and aggregates for that version.

Write rules:

- Persist aggregate updates at session close when the session is eligible.
- Promote an entry from aggregate data only after:
  - at least `min_hint_sessions=2` completed eligible sessions for the same key,
  - at least `min_hint_samples=20` total samples for that key,
  - positive evidence ratio is at least `min_hint_positive_ratio=0.70`,
  - every session that applied a policy had exact restore success,
  - no write failure occurred in the contributing sessions.
- Use file locking when available. If locking fails, skip persistence for that
  session and expose `hint_store_write_skipped=lock_failed`.
- Write files with root-owned service defaults and non-executable regular file
  permissions.

Bounds and pruning:

- Keep at most `max_aggregate_records=128` aggregate records.
- Keep at most `max_hint_entries=64` promoted hint entries.
- Keep the serialized file at or below `max_hint_cache_bytes=262144`.
- Prune aggregates older than `max_aggregate_age_days=14`.
- Prune promoted entries older than `max_hint_age_days=30`.
- Prune runtime-unaware promoted entries older than
  `max_runtime_unaware_hint_age_days=7`.
- Before every write, drop stale records, unsupported policy-version records,
  malformed records, and oldest records by `last_observed_at` /
  `last_validated_at` until all count and byte limits are satisfied.
- If pruning cannot bring the file under the byte limit, skip the write and
  expose `hint_store_write_skipped=cache_over_budget`.

## Restore And Write Outcome Contract

`GamePowerGovernor` already tracks write failures through `_write_failed`. The
sampling closure needs a structured outcome instead of an implicit flag.

Add a restore/apply result contract at the governor boundary:

```python
@dataclass(frozen=True)
class GamePowerActuatorOutcome:
    attempted: bool
    succeeded: bool
    reason: str
```

Rules:

- A CPU policy apply exception marks the active session `write_failed=True`.
- A restore path records whether restore was attempted and whether it succeeded.
- A restore exception records `restore_succeeded=False` and a bounded reason.
- If the process exits before a session can observe restore success, the
  session is ineligible for promotion.
- Cache promotion never depends on assuming restore success from the absence of
  an exception in unrelated code.

This makes "safe to reuse next launch" depend on measured clean actuator
behavior, not just positive classification samples.

## Hint Use, Invalidation, And Repair

Hint use rule:

- A valid promoted hint may reduce the activation warm-up requirement from
  `activate_samples=2` to `hinted_activate_samples=1` for the matching complete
  context.
- A hint must not skip current sampling.
- A hint must not force CPU-cap action without current-sample support.
- Runtime contradiction samples can disable the hint for the current session.
- The controller still restores on negative evidence.

Current-session contradiction rule:

- A hint contradiction sample is a current sample where a hint was available or
  used, but `_sample_supports_gpu_priority(sample, active=self._active)` is
  false after the hinted warm-up path starts.
- `session_hint_contradiction_samples=2` consecutive contradiction samples
  disables the hint for the current session.
- A restore decision after a hint-assisted activation disables the hint for the
  current session immediately, even if the consecutive contradiction threshold
  has not been reached.
- Once disabled, the hint remains disabled until the context changes. Later
  positive samples in the same context can drive normal non-hinted activation,
  but they cannot re-enable or repair the hint during that same session.
- Current-session contradiction counters reset only on context change, service
  restart, or controller replacement.

Invalidation rule:

- Schema mismatch: ignore the file.
- Policy version mismatch: ignore records for that version.
- OS/kernel/driver signature mismatch is a safe key mismatch because
  `os_signature` is part of the canonical key. Old records age out through
  pruning; the first implementation does not perform secondary stale scans.
- Topology, power-source, FPS-target, AppID, or PL1 mismatch: key does not
  match, so the hint is not used.
- Runtime mismatch prevents hint use only when both old and current contexts
  have known runtime signatures. `runtime=unavailable` is a weaker
  runtime-unaware key with shorter age limits and no runtime-change detection
  claim.
- `contradiction_count >= hint_contradiction_limit`: do not use the hint.

Contradiction and repair rule:

- A session contradicts a hint when the hint was active but current evidence
  repeatedly fails the support predicate or triggers restore after activation.
- Each contradicted session increments `contradiction_count` by one.
- A contradicted session is ineligible for aggregate contribution, promotion,
  and same-session repair.
- A later clean eligible session with no contradiction samples and positive
  ratio at or above the promotion threshold decrements `contradiction_count` by
  one, down to zero.
- A stale entry stays stale until a newly promoted entry for the same exact key
  replaces it.

## Session-Close Event Contract

When a session ends because the AppID disappears, the context changes, mode is
turned off, runtime config reloads, or the governor exits normally, JSONL mode
emits one bounded session-close event after restore outcome and cache write
outcome are known.

Event shape:

```json
{
  "event": "game-power-session-close",
  "appid": "1091500",
  "hint_key": "game-power-context-v1:7ed63b...",
  "samples": 31,
  "positive_ratio": 0.82,
  "hint_was_used": true,
  "hint_disabled": false,
  "hint_disable_reason": null,
  "contradiction_samples": 0,
  "hint_contradiction_count_before": 0,
  "hint_contradiction_count_after": 0,
  "hint_repair_delta": 0,
  "hint_contradiction_limit_reached": false,
  "aggregate_updated": true,
  "hint_promoted": true,
  "promotion_skip_reason": null,
  "restore_attempted": true,
  "restore_succeeded": true,
  "write_failed": false,
  "cache_write_result": "written"
}
```

Allowed `cache_write_result` values are `not_configured`, `not_eligible`,
`written`, `lock_failed`, `cache_over_budget`, and `write_failed`.

Allowed `promotion_skip_reason` values are `not_enough_sessions`,
`not_enough_samples`, `positive_ratio_below_threshold`, `restore_not_clean`,
`write_failed`, `context_incomplete`, `hint_contradicted`, `mode_not_actuating`,
and `cache_not_configured`.

The event must not include raw process IDs, cgroup paths, process paths,
per-thread data, measured P/E-core constants, or raw samples.

## Evidence Contract

Extend `GamePowerClassification.evidence` with bounded, non-sensitive state
when available:

- `rolling_window_samples`
- `rolling_positive_samples`
- `rolling_negative_samples`
- `rolling_positive_ratio`
- `rolling_ready`
- `activation_required_samples`
- `hint_key`
- `hint_context_complete`
- `hint_confidence`
- `hint_used`
- `hint_reason`
- `hint_store_load_error`
- `hint_store_write_skipped`
- `runtime_signature_known`
- `runtime_unaware`
- `hint_contradiction_count`
- `hint_contradiction_limit_reached`
- `session_hint_contradiction_samples`
- `hint_session_contradictions`
- `hint_disabled`
- `hint_disable_reason`
- `session_samples`
- `session_positive_ratio`
- `restore_attempted`
- `restore_succeeded`
- `write_failed`

The new classification and cache evidence must not include PIDs, cgroup paths,
process paths, measured P/E-core constants, source file paths, or write-control
details.

Existing pressure telemetry currently has its own debug JSON fields. This
design does not expand that surface. If a future privacy cleanup removes
existing path-like pressure fields, it should be a separate compatibility
change with focused tests.

Decision reasons should stay human-readable and stable enough for tests:

- `waiting for activation hysteresis`
- `waiting for rolling activation evidence`
- `restore hysteresis reached`
- `waiting for rolling restore evidence`
- `validated hint reduced activation warmup`
- `runtime evidence contradicted hint`
- `hint ignored because context incomplete`
- `hint using runtime-unaware warmup bucket`
- `hint ignored because cache entry is stale`
- `hint ignored because contradiction limit reached`
- `hint disabled by current-session contradiction`
- `hint aggregate updated`
- `hint promotion skipped because restore was not clean`

## Mode And Runtime Reload Semantics

When runtime config changes, `GamePowerGovernor._refresh_config()` already
restores active policy and replaces the controller. The rolling buffer and
active session reset on mode changes, service restarts, and config changes.

When mode is `off`, the governor keeps the existing behavior: restore any
snapshot, sleep, and avoid sampling. Rolling evidence and session summaries are
not updated.

When mode is `observe`, the controller classifies samples as observe-only. It
may update read-only in-memory counters for evidence display, but it must not
write or promote persistent hints.

When context changes while policy is active, the governor restores the current
policy before starting the new context. The prior session is eligible only if
that restore outcome is clean.

## Success Metrics

The implementation must expose enough evidence for these metrics from JSONL or
test fixtures:

- `time_to_first_valid_action_samples`: number of samples before the first
  valid GPU-priority action.
- `hint_hit`: promoted hint existed for the complete context.
- `hint_used`: hint reduced activation warm-up for a current sample.
- `hint_disabled`: hint was present but not used, with a reason.
- `hint_contradiction_sessions`: sessions that incremented contradiction count.
- `exact_restore_ratio`: eligible sessions with clean restore divided by
  sessions that applied policy.
- `unwanted_flip_count`: repeated apply/restore transitions in one context.
- `cache_write_result`: session-close persistence outcome distribution.
- `promotion_skip_reason`: why eligible-looking sessions did not promote.
- `hint_repair_delta`: whether a clean later session repaired persistent
  contradiction count.

These are scheduler quality metrics, not FPS claims. FPS, p99 frametime, and
1% low claims require `game-power-profile-device` evidence.

## Testing Plan

Focused tests go in `tests/test_game_power.py` and reuse existing helpers where
possible.

Required RED tests:

1. With `rolling_window_samples=4` and `activate_samples=2`, a negative,
   negative, positive, positive sequence must not activate even though
   consecutive hysteresis is satisfied, because rolling majority is not
   positive.
2. With an already active controller, `rolling_window_samples=5`, and
   `restore_samples=2`, a positive, positive, positive, negative, negative
   sequence must not restore even though restore hysteresis is satisfied,
   because rolling majority is still positive.
3. Setting `rolling_window_samples=1` must preserve the current consecutive
   hysteresis behavior.
4. Setting `rolling_window_samples=0` must preserve the current consecutive
   hysteresis behavior.
5. JSONL classification evidence must include post-update rolling evidence
   fields when a GPU-priority controller evaluates a runtime sample.
6. Runtime config reload must reset rolling evidence because the controller is
   replaced.
7. A matching valid hint reduces activation warm-up but does not skip current
   sampling.
8. A first eligible session writes only an aggregate; a second matching
   eligible session promotes the hint.
9. A matching later launch uses the promoted hint for the same complete
   context.
10. AppID, PL1/TDP bucket, power source, FPS target, topology, OS signature, runtime
   signature, or target-AppID mismatch closes the current session and resets
   rolling evidence.
11. Unknown power source, unknown FPS target, unknown topology, or unknown OS
    signature makes the context incomplete and prevents durable hint use or
    promotion.
12. Runtime signature `unavailable` uses the runtime-unaware key, can promote a
    short-lived warm-up hint, and expires under the runtime-unaware age limit.
13. Hints are ignored on schema, policy, OS signature, topology, known runtime,
    FPS target, power-source, AppID, or PL1 mismatch.
14. Invalid JSON, malformed entries, malformed aggregates, and key/context
    mismatches drop only the unsafe data and do not crash the governor.
15. An oversized existing cache file is not parsed, exposes
    `hint_store_load_error=cache_over_budget`, and is not overwritten until a
    clean eligible session closes.
16. A lock failure skips persistence for that session and exposes
    `hint_store_write_skipped=lock_failed`.
17. Invalid JSON is not overwritten until a clean eligible session closes.
18. Cache pruning removes stale, unsupported-version, oldest aggregate, and
    oldest promoted records to enforce record and byte limits.
19. Restore failure, unknown restore outcome, or write failure makes the
    session ineligible for aggregate promotion.
20. Active context change restores first; if that restore fails, no aggregate is
    updated for the prior session.
21. `contradiction_count >= hint_contradiction_limit` blocks a reloaded hint.
22. `hinted_activate_samples` still requires at least one current positive
    sample, even if misconfigured to zero.
23. Two consecutive current-session contradiction samples disable the hint for
    the current session.
24. Restore after hint-assisted activation disables the hint for the current
    session immediately.
25. A contradicted hinted session cannot update aggregates, promote, or repair
    the hint it just contradicted.
26. Clean later eligible sessions repair contradiction count one step at a time.
27. Session-close JSONL events include restore, aggregate, promotion,
    contradiction count before/after, repair delta, and cache-write outcomes
    without sensitive raw data.
28. Canonical hint keys are stable under sorted JSON serialization, reject
    key/context mismatches, and are unaffected by delimiter characters in
    signatures.
29. PL1 bucket rounding is deterministic at integer-watt boundaries and small
    microwatt jitter inside a bucket does not fragment hints.
30. Observe mode does not write or promote hints.
31. Incomplete context records evidence but does not use or promote a durable
    hint.

Verification:

- Focused pytest:
  `.venv/bin/python -m pytest tests/test_game_power.py tests/test_power_control_cli.py`
- Required trusted suite:
  `scripts/harness.py sweep required --report .cache/harness/required.json`

Because production runtime behavior changes, deployment to the handheld should
be followed by guarded `game-power-device` evidence when a foreground game is
available. A full `game-power-profile-device` run is required only before
claiming FPS or 1% low improvement.

## Acceptance Criteria

- The controller remains reversible and restores CPU policy exactly as before.
- Stable positive evidence still activates within the existing default
  two-sample path without a hint.
- A promoted hint can reduce repeat-launch activation warm-up only after clean
  aggregate evidence from at least two matching sessions.
- Transient noise is filtered by the rolling majority requirement.
- A context change resets rolling evidence and cannot reuse stale samples.
- A stale, incomplete, corrupted, or contradicted hint cannot force policy.
- Incomplete contexts remain safe: they can be observed but cannot promote or
  use durable hints.
- Runtime-unaware hints are allowed only as short-lived warm-up hints; they do
  not claim Proton/runtime-change detection.
- The cache remains bounded by record count, file size, and age-based pruning.
- Contradicted hinted sessions cannot contribute to aggregate learning or
  repair the hint they contradicted.
- JSONL mode exposes a bounded session-close event for cache, promotion,
  restore, and contradiction outcomes.
- Persistent contradiction limit and repair changes are visible without reading
  the cache file directly.
- Hint keys are generated only through the canonical JSON-hash key builder.
- No raw samples, process IDs, cgroup paths, process paths, or measured
  P/E-core constants are persisted.
- Required sweep reports fresh, verified artifact evidence after the change.

## Plan Review Revision Changelog

### Iteration 1 -> Iteration 2

1. **[MAJOR/Reviewer A]** Defined the hint context provider and service wiring.
   - Finding: persistent hint key/invalidation depended on power source and
     platform signatures without an integration boundary.
   - Change: added `GamePowerHintContext`, completion rules, service provider
     sources, standalone behavior, and unknown-context restrictions.

2. **[MAJOR/Reviewer A, Reviewer C]** Added durable aggregates before promoted
   entries.
   - Finding: `min_hint_sessions=2` could not work if the first session was not
     persisted.
   - Change: cache schema now stores `aggregates` and `entries`; first sessions
     update aggregates, later sessions can promote entries.

3. **[MAJOR/Reviewer B]** Added context-change reset semantics.
   - Finding: AppID, PL1, AC/battery, FPS target, topology, OS, and runtime
     changes could inherit stale rolling state.
   - Change: added a single context key and mandatory close/reset/reload steps.

4. **[MAJOR/Reviewer B]** Added cache corruption and migration behavior.
   - Finding: invalid JSON, malformed records, key mismatches, and concurrent
     writes were undefined.
   - Change: added load/drop/write/lock rules and malformed-record handling.

5. **[MAJOR/Reviewer B]** Made restore/write outcomes explicit promotion gates.
   - Finding: promotion depended on exact restore success without a measured
     outcome contract.
   - Change: added `GamePowerActuatorOutcome` and session eligibility rules.

6. **[MAJOR/Reviewer C]** Added defaults, owners, and test seams.
   - Finding: new knobs and constants were not defined.
   - Change: added the contract table.

7. **[MAJOR/Reviewer E]** Added measurable success metrics and FPS-claim guard.
   - Finding: the design did not define how to measure scheduler quality.
   - Change: added metrics for time to first action, hint hit/use/disable,
     contradiction, restore ratio, and unwanted flips.

8. **[MAJOR/Reviewer E]** Expanded hint keys beyond AppID/TDP/power.
   - Finding: FPS target, topology, OS/driver, and runtime changes could make a
     cached hint unsafe.
   - Change: key now includes FPS target, topology, OS signature, runtime
     signature, and policy version; incomplete contexts cannot promote/use.

### Iteration 2 -> Iteration 3

1. **[MAJOR/Reviewer A, Reviewer B]** Resolved ambiguous runtime and unknown
   context semantics.
   - Finding: runtime signature was required for safety but no provider exists
     in current code; `unknown` values were ambiguous.
   - Change: runtime is now an optional stronger dimension. Known runtime keys
     supersede runtime-unaware keys; runtime-unaware hints are short-lived and
     cannot claim Proton/runtime-change detection. Power source, FPS target,
     topology, and OS unknown states are explicitly incomplete.

2. **[MAJOR/Reviewer B]** Added persistent cache bounds.
   - Finding: the cache was described as bounded but had no size, count, age,
     or eviction policy.
   - Change: added max aggregate records, max promoted entries, max serialized
     bytes, age limits, runtime-unaware age limits, and prune-before-write
     behavior.

3. **[MAJOR/Reviewer C]** Made rolling-window RED tests prove majority gating.
   - Finding: earlier test sequences could pass/fail under legacy consecutive
     hysteresis without proving rolling-majority behavior.
   - Change: tests now require cases where consecutive hysteresis is satisfied
     but rolling majority blocks activation or restore.

4. **[MAJOR/Reviewer C]** Reordered active context change around restore
   outcomes.
   - Finding: the old sequence could update aggregates before restore success
     was known.
   - Change: active context changes now restore and record actuator outcome
     before session close and aggregate eligibility.

5. **[MINOR/Reviewer B, Reviewer C]** Defined zero-window compatibility.
   - Finding: `rolling_window_samples=0` was in scope but not testable.
   - Change: `rolling_window_samples <= 1` now bypasses rolling readiness and
     majority checks, with required tests for both 0 and 1.

### Held-Out Sweep -> Iteration 4

1. **[MAJOR/Held-out]** Disqualified contradicted hinted sessions from
   learning and same-session repair.
   - Finding: contradiction samples were tracked but did not make a session
     ineligible for aggregate updates or repair.
   - Change: session eligibility now requires zero contradiction samples and no
     hint disablement; contradicted sessions cannot update aggregates, promote,
     or repair the hint they contradicted.

2. **[MAJOR/Held-out]** Defined deterministic current-session contradiction.
   - Finding: the plan did not define how many contradiction samples disable a
     current hint.
   - Change: added `session_hint_contradiction_samples=2`, immediate disable on
     restore-after-hint activation, reset semantics, and evidence fields.

3. **[MAJOR/Held-out]** Added session-close observability.
   - Finding: session-close aggregate/promotion/cache outcomes were not emitted
     in JSONL.
   - Change: added `game-power-session-close` event with bounded restore,
     aggregate, promotion, contradiction, and cache-write fields.

4. **[MAJOR/Held-out]** Expanded RED tests for dangerous persistence paths.
   - Finding: tests did not explicitly cover oversize load refusal, lock
     failure, invalid JSON replacement timing, contradiction-limit reload, or
     hinted activation requiring a current sample.
   - Change: added required tests for each failure path.

5. **[MINOR/Held-out]** Added canonical hint key and PL1 bucket contracts.
   - Finding: delimiter-based keys and unspecified PL1 bucket rounding could
     fragment or mismatch cache records.
   - Change: keys now use a sorted canonical JSON tuple hashed under
     `game-power-context-v1`, and PL1 buckets are nearest integer watts.
