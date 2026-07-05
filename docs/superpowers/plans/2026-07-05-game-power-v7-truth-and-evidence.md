# Game Power V7 Truth And Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build V7 as the local truth/evidence layer for Game Power so the daemon, Decky UI, and profiler can state exactly when target/frame evidence is usable, without promoting unverified scheduler writes.

**Architecture:** V7 adds two read-side contracts: `runtime_control_health` on the daemon config path and `evidence_readiness` in runtime snapshots. Decky renders that readiness as user-facing local evidence state after backend stale/error sanitization, while profiler aggregate output gains machine-readable readiness gates for future guarded background-shaping experiments. The installed governor remains default-safe: no cgroup, uclamp, affinity, cpuset, sched_ext, RAPL/PL1/Tau, or default CPU-cap promotion is introduced by this local plan.

**Tech Stack:** Python >=3.10 compatible code running in the repo venv, pytest, existing Game Power governor/profile modules, Decky TypeScript/React assets, `npm --prefix decky/steamos-intel-handheld-game-power run build`, and repo Harness `scripts/harness.py`.

---

## Current V7 Boundary

V7 is not the near-Ultimate scheduler. It is the evidence and claim gate that an Ultimate scheduler needs before stronger writes are credible.

This plan can finish the local V7 layer:

- Runtime control fails closed when the control file or any active override is invalid.
- Runtime snapshots expose whether the daemon has local target/frame evidence strong enough for product copy.
- Decky never shows target/frame-ready copy from stale, errored, stopped, observe-only, or control-invalid snapshots.
- Background-shaping aggregate output names exactly which gates block a guarded experiment.

This plan cannot honestly claim Ultimate scheduler behavior because that requires guarded device evidence across foreground frame pacing, power savings, restore safety, background shaping, and cross-game repeats. Those checks remain under guarded Harness layers such as `game-power-device` and `game-power-profile-device`.

## Plan Review Iteration 1 Ledger

The first review found confirmed blockers. This revision addresses them before implementation:

| ID | Confirmed blocker | Revision in this plan |
| --- | --- | --- |
| A1 | `control_ready` had no data flow from runtime-control parsing into runtime snapshots. | Task 1 adds `runtime_control_health` to `GamePowerConfig`; Task 2 consumes it in `evidence_readiness`. |
| A2 | `write_policy` was incorrectly specified as always `epp-only`. | Task 2 derives `disabled`, `epp-only`, or `epp-plus-cpu-cap-explicit` from mode and `cpu_cap_enabled`. |
| A3 | Background plan referenced stale symbols and test names. | Task 4 uses `PolicyVerdict.BETTER` and current tests `test_profile_cli_aggregate_builds_background_shaping_experiment_plan` and `test_profile_cli_aggregate_requires_background_cgroup_restore_coverage`. |
| B1 | Decky could pass through a stale snapshot with `claim_ready=true`. | Task 3 makes `_public_runtime_snapshot()` override readiness to unavailable after stale/error calculation. |
| B2 | A valid mode plus invalid `fps_target_override` could leave automatic active. | Task 1 fails closed for partial invalid payloads and repairs invalid saved FPS override on set-mode. |
| B3 | `claim_ready` could be confused with live-but-low-confidence frame data. | Task 2 requires known target, non-low target confidence, high-confidence frame data, finite avg/p95, and `sample_count >= frame_performance_min_samples`. |
| B4 | `learning_ready` was named but undefined. | Task 2 defines it from `learning.status == "ready"` and `learning.reusable_next_launch is True`, forced false outside usable automatic readiness. |
| C1 | Decky tests were copy-only and did not prove typed data usage. | Task 3 asserts source type, `runtime?.evidence_readiness` rendering, source copy, and rebuilt bundle copy. |
| D1 | Decky lacked a screen/state matrix. | Task 3 defines state-to-copy behavior for unavailable, control-invalid, stopped, view-data-only, power-signals-only, and target-aware-live. |
| D2 | Copy overstated hardware proof. | Task 3 uses local evidence wording only. |
| E1 | No product success metrics were defined. | Task 5 adds local KPIs and Harness acceptance gates. |
| E2 | Background readiness lacked stable machine-readable blockers. | Task 4 adds `blocking_reason_codes` while preserving human `reasons`. |

## Plan Review Iteration 2 Ledger

The second review confirmed five additional blockers. This revision folds them into the executable tasks:

| ID | Confirmed blocker | Revision in this plan |
| --- | --- | --- |
| F1 | Decky rendering used `t.evidenceLabel` without adding or testing that localized copy key. | Task 3 adds `evidenceLabel` to `Copy`, English and Traditional Chinese locales, and source/bundle assertions. |
| F2 | Existing `modeLabel()` and `runtimeHeadline()` could still show target-aware wording from raw target/frame fields. | Task 3 makes `evidence_readiness.status === "target-aware-live" && claim_ready === true` the single UI gate for target-aware wording and replaces the old raw-condition asset test. |
| F3 | Background readiness conflated restore coverage with fully guarded candidate stability. | Task 4 splits candidate restore coverage from guarded candidate stability and adds a restore-covered-but-not-stable negative case. |
| F4 | Decky backend could pass through malformed or contradictory `evidence_readiness` dicts. | Task 3 adds backend readiness sanitization and tests for missing fields, wrong types, unknown status, and contradictory ready claims. |
| F5 | Finite frame data was specified but not explicitly tested; current frame-source state can mark NaN/inf as live. | Task 2 adds NaN/inf tests and hardens frame-source/readiness finite checks. |

## Plan Review Iteration 3 Ledger

The third review confirmed four final executability and fail-closed gaps:

| ID | Confirmed blocker | Revision in this plan |
| --- | --- | --- |
| G1 | Task 2 RED snippet used unqualified names that current tests do not import. | Task 2 now uses `game_power.runtime_snapshot_payload()` and `game_power.GamePowerDecision()`, and names `import math` for non-finite tests. |
| G2 | Decky sanitizer did not reject `target-aware-live + claim_ready=true` when booleans or mode contradicted the claim. | Task 3 adds sanitizer and tests requiring automatic mode plus `target_ready`, `frame_ready`, and `control_ready` all true for target-aware-ready pass-through. |
| G3 | Non-finite or non-positive FPS targets could still be treated as target-ready. | Runtime contract and Task 2 require finite-positive target validation and tests for NaN, inf, zero, and negative targets. |
| G4 | Missing/string/NaN/inf runtime timestamps could bypass stale handling in Decky. | Task 3 adds timestamp validity tests and requires invalid timestamps to publish unavailable readiness. |

## Runtime Evidence Contract

`runtime_snapshot_payload()` must include:

```json
{
  "evidence_readiness": {
    "status": "target-aware-live",
    "target_ready": true,
    "frame_ready": true,
    "learning_ready": false,
    "claim_ready": true,
    "control_ready": true,
    "write_policy": "epp-only",
    "reasons": ["control ready", "fps target known", "frame data ready"]
  }
}
```

Allowed `status` values:

- `unavailable`: runtime snapshot is stale, errored, malformed for Decky, or otherwise not usable.
- `control-invalid`: runtime control health is invalid.
- `stopped`: Game Power mode is off.
- `view-data-only`: Game Power mode is observe.
- `target-aware-live`: automatic mode with valid control, known target, and high-confidence sufficient frame data.
- `power-signals-only`: automatic mode can still balance power signals, but target/frame evidence is not ready.

Boolean rules:

- `control_ready` is false only when runtime-control parsing reports invalid mode, invalid schema, invalid JSON shape, corrupt JSON, or invalid `fps_target_override`.
- `target_ready` requires `fps_target.status == "known"`, target confidence not equal to `low`, finite positive FPS, and finite positive target frame time.
- `frame_ready` requires `frame_source.status == "live"`, frame confidence `high`, finite avg FPS and p95 frame time, and `sample_count >= config.frame_performance_min_samples`. Non-finite avg FPS or p95 frame time must not be reported as live frame evidence.
- `learning_ready` requires sanitized learning state with `status == "ready"` and `reusable_next_launch is True`, and is forced false for stale/error/control-invalid/off/observe states.
- `claim_ready` requires `control_ready`, automatic mode, `target_ready`, and `frame_ready`, and is forced false for stale/error/control-invalid/off/observe states.
- `write_policy` is `disabled` for unavailable/control-invalid/off/observe, `epp-only` for automatic when `cpu_cap_enabled is False`, and `epp-plus-cpu-cap-explicit` for automatic when `cpu_cap_enabled is True`.

## Files

- Modify: `src/steamos_intel_handheld/game_power_control.py`
  - Runtime control parsing, fail-closed overlay, and invalid saved override repair.
- Modify: `tests/test_game_power_control.py`
  - Runtime-control regression coverage.
- Modify: `src/steamos_intel_handheld/game_power.py`
  - `GamePowerConfig.runtime_control_health`, evidence-readiness helper, runtime snapshot schema.
- Modify: `tests/test_game_power.py`
  - Runtime evidence-readiness coverage.
- Modify: `decky/steamos-intel-handheld-game-power/main.py`
  - Backend readiness defaults, pass-through, and stale/error sanitization.
- Modify: `decky/steamos-intel-handheld-game-power/src/index.tsx`
  - Readiness type, copy, and status rendering.
- Modify: `decky/steamos-intel-handheld-game-power/dist/index.js`
  - Rebuilt Decky bundle.
- Modify: `tests/test_decky_plugin_backend.py`
  - Backend snapshot compatibility and stale/error override coverage.
- Modify: `tests/test_decky_plugin_assets.py`
  - Source/bundle type, copy, and forbidden raw-action checks.
- Modify: `src/steamos_intel_handheld/game_power_profile.py`
  - Structured background-shaping readiness gates.
- Modify: `tests/test_game_power_profile.py`
  - Background readiness positive and negative coverage.
- Modify: `README.md`
  - V7 local truth boundary and guarded evidence matrix.

## Task 1: Runtime Control Health And Partial Invalid Fail-Closed

**Files:**

- Modify: `tests/test_game_power_control.py`
- Modify: `src/steamos_intel_handheld/game_power_control.py`
- Modify: `src/steamos_intel_handheld/game_power.py`

- [ ] **Step 1: Confirm existing fail-closed tests are present**

Confirm these already-added regression tests still pass after later edits:

```bash
.venv/bin/python -m pytest tests/test_game_power_control.py::test_runtime_control_fails_closed_for_corrupt_file tests/test_game_power_control.py::test_runtime_control_fails_closed_for_invalid_mode -q
```

Expected after current local slice: `2 passed`.

- [ ] **Step 2: Add failing tests for partial invalid control**

Add these tests to `tests/test_game_power_control.py`:

```python
def test_runtime_control_fails_closed_for_invalid_fps_target_override(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "automatic",
                "fps_target_override": {"fps": 37, "source": "decky"},
            }
        )
    )
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "automatic"
    assert status.fps_target_override.status == "invalid"
    assert effective.runtime_control_health == {
        "status": "invalid",
        "mode": "automatic",
        "override_active": True,
        "fps_target_override_status": "invalid",
        "reason": "invalid-fps-target-override",
    }


def test_runtime_control_fails_closed_for_unsupported_schema_version(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(json.dumps({"schema_version": 99, "mode": "automatic"}))
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "invalid"
    assert status.fps_target_override.status == "invalid"


def test_runtime_control_fails_closed_for_non_object_json(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(json.dumps(["automatic"]))
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "invalid"
    assert status.fps_target_override.status == "invalid"


def test_set_runtime_mode_drops_invalid_saved_fps_target_override(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "observe",
                "fps_target_override": {"fps": 37, "source": "decky"},
            }
        )
    )

    status = game_power_control.set_runtime_mode(path, "automatic", source="decky")
    raw = json.loads(path.read_text())

    assert status.mode == "automatic"
    assert status.fps_target_override.status == "auto"
    assert "fps_target_override" not in raw
```

- [ ] **Step 3: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_control.py::test_runtime_control_fails_closed_for_invalid_fps_target_override tests/test_game_power_control.py::test_runtime_control_fails_closed_for_unsupported_schema_version tests/test_game_power_control.py::test_runtime_control_fails_closed_for_non_object_json tests/test_game_power_control.py::test_set_runtime_mode_drops_invalid_saved_fps_target_override -q
```

Expected before implementation: at least the invalid FPS override and set-mode repair tests fail.

- [ ] **Step 4: Implement runtime control health**

In `src/steamos_intel_handheld/game_power.py`, extend `GamePowerConfig`:

```python
runtime_control_health: dict[str, object] | None = None
```

In `src/steamos_intel_handheld/game_power_control.py`, add a local helper:

```python
def _runtime_control_health_from_status(status: RuntimeControlStatus) -> dict[str, object]:
    if status.mode == "invalid":
        reason = "invalid-control-file"
        health_status = "invalid"
    elif status.fps_target_override.status == "invalid":
        reason = "invalid-fps-target-override"
        health_status = "invalid"
    else:
        reason = "control-ready"
        health_status = "ready"
    return {
        "status": health_status,
        "mode": status.mode,
        "override_active": status.override_active,
        "fps_target_override_status": status.fps_target_override.status,
        "reason": reason,
    }
```

Update `effective_config_from_runtime_file()` so invalid mode or invalid FPS override sets `mode=GamePowerMode.OFF`, valid explicit mode still applies only when control is healthy, valid manual FPS target still applies only when control is healthy, and `runtime_control_health` is always set from the helper.

Update `set_runtime_mode()` so an existing invalid saved `fps_target_override` is removed before writing the requested mode. Valid manual FPS overrides remain preserved.

- [ ] **Step 5: Verify runtime-control tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_control.py -q
```

Expected: all runtime-control tests pass.

## Task 2: Runtime Evidence Readiness Contract

**Files:**

- Modify: `tests/test_game_power.py`
- Modify: `src/steamos_intel_handheld/game_power.py`

- [ ] **Step 1: Add failing readiness tests**

Add tests to `tests/test_game_power.py` for:

```python
def test_runtime_snapshot_schema_reports_local_target_frame_evidence_ready():
    sample = GamePowerSample(
        appid="1091500",
        rapl=RaplPowerWindow(duration_s=2.0, package_w=22.0, core_w=5.0, uncore_w=11.0),
        pl1_w=30,
        frame_target=FrameTargetTelemetry(fps_target=40.0, source="manual", confidence="high"),
        frame_performance=FramePerformanceTelemetry(
            avg_fps=44.0,
            p95_frame_ms=24.0,
            sample_count=12,
            window_s=6.0,
            source="mangohud",
            confidence="high",
        ),
    )
    row = game_power.runtime_snapshot_payload(
        GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            runtime_control_health={"status": "ready", "reason": "control-ready"},
        ),
        sample,
        game_power.GamePowerDecision(GamePowerAction.GPU_PRIORITY_EPP, "package limited"),
        elapsed_s=1.0,
    )

    readiness = row["evidence_readiness"]
    assert readiness["status"] == "target-aware-live"
    assert readiness["target_ready"] is True
    assert readiness["frame_ready"] is True
    assert readiness["learning_ready"] is False
    assert readiness["claim_ready"] is True
    assert readiness["control_ready"] is True
    assert readiness["write_policy"] == "epp-only"
    assert readiness["reasons"] == [
        "control ready",
        "fps target known",
        "frame data ready",
    ]
```

Also add tests named:

- `test_runtime_snapshot_schema_reports_power_signals_only_for_missing_frame_data`
- `test_runtime_snapshot_schema_rejects_non_finite_or_non_positive_targets`
- `test_runtime_snapshot_schema_rejects_low_confidence_or_undersampled_frame_data`
- `test_runtime_snapshot_schema_rejects_non_finite_frame_data`
- `test_runtime_snapshot_schema_reports_control_invalid_readiness`
- `test_runtime_snapshot_schema_reports_cpu_cap_explicit_write_policy`
- `test_runtime_snapshot_schema_reports_learning_ready_only_when_reusable`
- `test_runtime_snapshot_schema_reports_unavailable_readiness_for_stale_or_error`
- `test_runtime_snapshot_schema_reports_stopped_and_view_data_only_readiness`

Each test should assert `claim_ready is False` outside the `target-aware-live` positive case. Add `import math` to `tests/test_game_power.py` for the non-finite tests. The target validity test must cover `fps_target=math.nan`, `fps_target=math.inf`, `fps_target=0.0`, and `fps_target=-40.0`, and assert `target_ready is False`, `claim_ready is False`, no `target-aware-live` status, and `json.dumps(row, allow_nan=False)` succeeds. The non-finite frame test must cover `avg_fps=math.nan`, `avg_fps=math.inf`, `p95_frame_ms=math.nan`, and `p95_frame_ms=math.inf`, and assert `frame_ready is False`, `claim_ready is False`, no `target-aware-live` status, and `json.dumps(row, allow_nan=False)` succeeds.

- [ ] **Step 2: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_runtime_snapshot_schema_reports_local_target_frame_evidence_ready tests/test_game_power.py::test_runtime_snapshot_schema_reports_power_signals_only_for_missing_frame_data tests/test_game_power.py::test_runtime_snapshot_schema_rejects_non_finite_or_non_positive_targets tests/test_game_power.py::test_runtime_snapshot_schema_rejects_low_confidence_or_undersampled_frame_data tests/test_game_power.py::test_runtime_snapshot_schema_rejects_non_finite_frame_data tests/test_game_power.py::test_runtime_snapshot_schema_reports_control_invalid_readiness tests/test_game_power.py::test_runtime_snapshot_schema_reports_cpu_cap_explicit_write_policy tests/test_game_power.py::test_runtime_snapshot_schema_reports_learning_ready_only_when_reusable tests/test_game_power.py::test_runtime_snapshot_schema_reports_unavailable_readiness_for_stale_or_error tests/test_game_power.py::test_runtime_snapshot_schema_reports_stopped_and_view_data_only_readiness -q
```

Expected before implementation: tests fail because `evidence_readiness` is absent.

- [ ] **Step 3: Implement readiness helper**

Add `evidence_readiness_from_runtime()` in `src/steamos_intel_handheld/game_power.py`. It must derive target and frame state using existing `target_state_from_telemetry()` and `frame_source_state_from_telemetry()` helpers, apply the boolean rules from this plan, and return a JSON-serializable `dict[str, object]`.

Harden `target_state_from_telemetry()` so non-finite or non-positive FPS targets are not reported as ready target evidence. Harden `frame_source_state_from_telemetry()` so non-finite `avg_fps` or `p95_frame_ms` is not reported as `status="live"`. It should return `status="malformed"` for NaN/inf avg FPS or p95 frame time, and `_round_or_none()` must not emit non-finite JSON values.

The helper must use this status priority:

1. `unavailable` for `stale` or `error`
2. `control-invalid` for invalid control health
3. `stopped` for `GamePowerMode.OFF`
4. `view-data-only` for `GamePowerMode.OBSERVE`
5. `target-aware-live` for automatic with `target_ready` and `frame_ready`
6. `power-signals-only` for other automatic states

- [ ] **Step 4: Include readiness in runtime snapshots**

Update `runtime_snapshot_payload()` to include:

```python
"evidence_readiness": evidence_readiness_from_runtime(
    config,
    sample,
    stale=stale,
    error=error,
    learning=learning or _default_learning_state(),
),
```

Ensure the existing `learning` field and the readiness helper receive the same sanitized learning object.

- [ ] **Step 5: Verify Game Power tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py -q
```

Expected: all Game Power tests pass.

## Task 3: Decky Evidence Readiness Backend And UI

**Files:**

- Modify: `tests/test_decky_plugin_backend.py`
- Modify: `tests/test_decky_plugin_assets.py`
- Modify: `decky/steamos-intel-handheld-game-power/main.py`
- Modify: `decky/steamos-intel-handheld-game-power/src/index.tsx`
- Modify: `decky/steamos-intel-handheld-game-power/dist/index.js`

- [ ] **Step 1: Add failing backend tests**

Add backend tests that assert:

- `_runtime_snapshot_unavailable()` includes `evidence_readiness.status == "unavailable"` and `claim_ready is False`.
- A valid runtime snapshot with readiness passes through when it is not stale and has no error.
- A valid runtime snapshot with `claim_ready is True` is overridden to unavailable when timestamp age makes it stale.
- A valid runtime snapshot with `error` is overridden to unavailable.
- A valid runtime snapshot with missing or non-dict `evidence_readiness` is sanitized to unavailable.
- A runtime snapshot with unknown readiness status, missing required fields, or non-boolean ready flags is sanitized to unavailable.
- A contradictory runtime snapshot with `mode == "off"`, `mode == "observe"`, or readiness `status == "control-invalid"` and `claim_ready is True` is sanitized to unavailable.
- A contradictory runtime snapshot with `status == "target-aware-live"` and `claim_ready is True`, but `target_ready`, `frame_ready`, or `control_ready` is false, is sanitized to unavailable.
- A contradictory runtime snapshot with `status == "target-aware-live"` and `claim_ready is True`, but `mode != "automatic"`, is sanitized to unavailable.
- A valid runtime snapshot with target-aware-ready readiness but missing, string, NaN, or infinite `timestamp_monotonic_s` is sanitized to unavailable before readiness reaches the UI.

The stale override test should write a runtime snapshot with:

```python
"evidence_readiness": {
    "status": "target-aware-live",
    "target_ready": True,
    "frame_ready": True,
    "learning_ready": False,
    "claim_ready": True,
    "control_ready": True,
    "write_policy": "epp-only",
    "reasons": ["control ready", "fps target known", "frame data ready"],
}
```

and then assert the public result has:

```python
assert result["runtime"]["stale"] is True
assert result["runtime"]["evidence_readiness"]["status"] == "unavailable"
assert result["runtime"]["evidence_readiness"]["claim_ready"] is False
```

- [ ] **Step 2: Add failing frontend static tests**

Extend `tests/test_decky_plugin_assets.py` to assert source and bundle contain:

- `type EvidenceReadiness`
- `evidence_readiness: EvidenceReadiness`
- `evidenceLabel: string`
- `evidenceLabel: "Local evidence"`
- `evidenceLabel: "本機證據"`
- `runtime?.evidence_readiness`
- `evidenceText(t, runtime?.evidence_readiness)`
- `isTargetAwareReady(runtime?.evidence_readiness)`
- `Local evidence`
- `Local target/frame evidence ready`
- `Local evidence: power signals only`
- `Local evidence unavailable`
- `View data only`
- `Game Power stopped`
- `本機證據`
- `本機 FPS 目標與影格資料可用`
- `本機證據：僅有功耗訊號`
- `本機證據不可用`
- `只看數據`
- `遊戲電力已停止`

Replace the existing raw-condition asset assertion:

```ts
!runtime?.stale &&
!runtime?.error &&
runtime?.fps_target?.status === "known" &&
runtime?.frame_source?.status === "live"
```

with readiness-gated assertions:

```ts
function isTargetAwareReady(readiness: EvidenceReadiness | null | undefined): boolean {
  return readiness?.status === "target-aware-live" && readiness?.claim_ready === true;
}
```

and checks that both `modeLabel()` and `runtimeHeadline()` use `isTargetAwareReady(runtime?.evidence_readiness)` rather than raw target/frame fields. Keep existing forbidden raw-action checks in place so the UI remains read-only for experiment proposals.

- [ ] **Step 3: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_decky_plugin_backend.py tests/test_decky_plugin_assets.py -q
```

Expected before implementation: backend readiness defaults and frontend copy/type checks fail.

- [ ] **Step 4: Implement backend defaults and readiness sanitization**

Add `_default_evidence_readiness()` and `_public_evidence_readiness()` to `decky/steamos-intel-handheld-game-power/main.py`:

```python
def _default_evidence_readiness(reason: str = "runtime-unavailable") -> dict:
    return {
        "status": "unavailable",
        "target_ready": False,
        "frame_ready": False,
        "learning_ready": False,
        "claim_ready": False,
        "control_ready": False,
        "write_policy": "disabled",
        "reasons": [reason],
    }
```

`_public_evidence_readiness(row, *, stale, error)` must:

- Return `_default_evidence_readiness("runtime-stale")` when `stale` is true.
- Return `_default_evidence_readiness("runtime-error")` when `error` is set.
- Return `_default_evidence_readiness("runtime-timestamp-invalid")` when `timestamp_monotonic_s` is missing, non-numeric, NaN, or infinite.
- Return `_default_evidence_readiness("runtime-readiness-invalid")` when readiness is missing, not a dict, has an unknown status, misses required fields, or has non-boolean `target_ready`, `frame_ready`, `learning_ready`, `claim_ready`, or `control_ready`.
- Return unavailable when `claim_ready is True` but status is not `target-aware-live`.
- Return unavailable when status is `target-aware-live` and `claim_ready is True`, but `mode != "automatic"` or any of `target_ready`, `frame_ready`, or `control_ready` is false.
- Return unavailable when `mode` is `off` or `observe` and incoming readiness claims `claim_ready is True`.
- Return unavailable when status is `control-invalid` and incoming readiness claims `control_ready is True` or `claim_ready is True`.
- Otherwise pass through sanitized readiness with known fields and string `reasons`.

Include the sanitized readiness in `_runtime_snapshot_unavailable()` and `_public_runtime_snapshot()` after timestamp validity, stale, and error are computed.

- [ ] **Step 5: Implement frontend state matrix**

Add `EvidenceReadiness` to `src/index.tsx` and include it in `RuntimeSnapshot`. Add `evidenceLabel` to the `Copy` type and both locale objects:

```ts
evidenceLabel: "Local evidence",
```

```ts
evidenceLabel: "本機證據",
```

Add `evidenceText(t, readiness)` with this state matrix:

| Status | English copy | Traditional Chinese copy |
| --- | --- | --- |
| `target-aware-live` with `claim_ready=true` | `Local target/frame evidence ready` | `本機 FPS 目標與影格資料可用` |
| `power-signals-only` | `Local evidence: power signals only` | `本機證據：僅有功耗訊號` |
| `view-data-only` | `View data only` | `只看數據` |
| `stopped` | `Game Power stopped` | `遊戲電力已停止` |
| `control-invalid` | `Local evidence unavailable` | `本機證據不可用` |
| `unavailable` or missing readiness | `Local evidence unavailable` | `本機證據不可用` |

Render readiness in the main runtime status block:

```tsx
<div style={detailStyle}>{t.evidenceLabel}: {evidenceText(t, runtime?.evidence_readiness)}</div>
```

Do not render target/frame-ready copy unless `readiness.status === "target-aware-live"` and `readiness.claim_ready === true`.

Add:

```ts
function isTargetAwareReady(readiness: EvidenceReadiness | null | undefined): boolean {
  return readiness?.status === "target-aware-live" && readiness?.claim_ready === true;
}
```

Update `modeLabel()` and `runtimeHeadline()` so target-aware wording uses only `isTargetAwareReady(runtime?.evidence_readiness)`. If readiness is not target-aware-ready, those helpers must fall back to neutral mode or power-signal/collecting/unavailable text and must not inspect raw `fps_target.status` plus `frame_source.status` as an alternate target-aware gate.

- [ ] **Step 6: Rebuild Decky bundle**

Run:

```bash
npm --prefix decky/steamos-intel-handheld-game-power run build
```

Expected: `decky/steamos-intel-handheld-game-power/dist/index.js` is rebuilt.

- [ ] **Step 7: Verify Decky tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_decky_plugin_backend.py tests/test_decky_plugin_assets.py -q
```

Expected: Decky backend/assets tests pass.

## Task 4: Background-Shaping Readiness Contract

**Files:**

- Modify: `tests/test_game_power_profile.py`
- Modify: `src/steamos_intel_handheld/game_power_profile.py`

- [ ] **Step 1: Add failing positive readiness assertions**

Extend `test_profile_cli_aggregate_builds_background_shaping_experiment_plan`:

```python
readiness = plan["readiness"]
assert readiness == {
    "comparison_better": True,
    "controlled_repeats": True,
    "baseline_controlled": True,
    "candidate_controlled": True,
    "baseline_restored": True,
    "candidate_restored": True,
    "restore_coverage": True,
    "candidate_restore_coverage": True,
    "candidate_stability": True,
    "candidate_guarded": True,
    "write_policy_disabled": True,
    "ready_for_guarded_experiment": True,
    "blocking_reason_codes": [],
}
```

Keep existing assertions that `plan["write_policy"] == "disabled"` and the guarded candidate details are present.

- [ ] **Step 2: Add failing negative readiness assertions**

Extend `test_profile_cli_aggregate_requires_background_cgroup_restore_coverage`:

```python
readiness = plan["readiness"]
assert readiness["ready_for_guarded_experiment"] is False
assert readiness["write_policy_disabled"] is True
assert readiness["candidate_restore_coverage"] is False
assert readiness["candidate_stability"] is True
assert readiness["candidate_guarded"] is False
assert "candidate_restore_coverage_missing" in readiness["blocking_reason_codes"]
assert "no_guarded_background_candidate" in readiness["blocking_reason_codes"]
assert plan["write_policy"] == "disabled"
```

Add a second negative fixture where the candidate has restore snapshot coverage but fails another guarded-candidate gate, such as CPU-time delta below `BACKGROUND_SHAPING_MIN_CPU_TIME_S`. Assert:

```python
assert readiness["candidate_restore_coverage"] is True
assert readiness["candidate_stability"] is False
assert readiness["candidate_guarded"] is False
assert "candidate_restore_coverage_missing" not in readiness["blocking_reason_codes"]
assert "no_guarded_background_candidate" in readiness["blocking_reason_codes"]
```

- [ ] **Step 3: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_profile_cli_aggregate_builds_background_shaping_experiment_plan tests/test_game_power_profile.py::test_profile_cli_aggregate_requires_background_cgroup_restore_coverage -q
```

Expected before implementation: tests fail because `plan["readiness"]` is absent.

- [ ] **Step 4: Implement structured readiness**

In `build_background_shaping_experiment_plan()`, compute booleans from existing values:

```python
comparison_better = comparison.verdict == PolicyVerdict.BETTER
controlled_repeats = baseline.sample_count >= min_runs and candidate.sample_count >= min_runs
baseline_controlled = baseline.capture_mode == CaptureMode.CONTROLLED
candidate_controlled = candidate.capture_mode == CaptureMode.CONTROLLED
baseline_restored = baseline.restored_count == baseline.sample_count
candidate_restored = candidate.restored_count == candidate.sample_count
restore_coverage = (
    baseline.restore_affinity_snapshot_count == baseline.sample_count
    and candidate.restore_affinity_snapshot_count == candidate.sample_count
    and _aggregate_has_cgroup_cpu_controller_restore(baseline)
    and _aggregate_has_cgroup_cpu_controller_restore(candidate)
)
candidate_restore_coverage = any(
    _background_candidate_has_restore_coverage(item)
    for item in candidate_candidates
)
candidate_stability = any(
    (_optional_int(item.get("observed_run_count")) or 0) >= BACKGROUND_SHAPING_MIN_OBSERVED_RUNS
    and (_float(item.get("run_coverage")) or 0.0) >= BACKGROUND_SHAPING_MIN_RUN_COVERAGE
    and (_float(item.get("cpu_time_s_delta_median")) or 0.0) >= BACKGROUND_SHAPING_MIN_CPU_TIME_S
    and item.get("suggested_action") in {
        "future-cpu-weight-candidate",
        "future-uclamp-max-candidate",
    }
    for item in candidate_candidates
)
candidate_guarded = bool(guarded_candidates)
write_policy_disabled = True
```

Build `blocking_reason_codes` with these stable strings when their matching gate is false:

- `candidate_policy_not_better`
- `controlled_repeats_missing`
- `baseline_capture_not_controlled`
- `candidate_capture_not_controlled`
- `baseline_restore_incomplete`
- `candidate_restore_incomplete`
- `cgroup_restore_snapshot_missing`
- `candidate_restore_coverage_missing`
- `no_guarded_background_candidate`

Return:

```python
"readiness": {
    "comparison_better": comparison_better,
    "controlled_repeats": controlled_repeats,
    "baseline_controlled": baseline_controlled,
    "candidate_controlled": candidate_controlled,
    "baseline_restored": baseline_restored,
    "candidate_restored": candidate_restored,
    "restore_coverage": restore_coverage,
    "candidate_restore_coverage": candidate_restore_coverage,
    "candidate_stability": candidate_stability,
    "candidate_guarded": candidate_guarded,
    "write_policy_disabled": write_policy_disabled,
    "ready_for_guarded_experiment": ready,
    "blocking_reason_codes": blocking_reason_codes,
},
```

Preserve top-level `reasons` for human text. Keep `write_policy` exactly `disabled`.

- [ ] **Step 5: Verify profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py -q
```

Expected: profile tests pass.

## Task 5: Documentation, KPIs, And Harness Evidence

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-05-game-power-v7-truth-and-evidence.md`

- [ ] **Step 1: Document the V7 local claim boundary**

Add README text near Game Power / Decky control docs:

```markdown
V7 exposes local evidence readiness so the UI can distinguish target/frame
runtime evidence from power-signal-only balancing. The UI only shows local
target/frame evidence as ready when runtime control is healthy, the mode is
automatic, the FPS target is known, and high-confidence frame data has enough
samples. Background-shaping readiness remains an advisory profiler output with
`write_policy=disabled` until guarded device A/B runs pass restore, pacing, and
power-saving gates.
```

- [ ] **Step 2: Acceptance KPIs**

Before implementation is complete, verify these local KPIs:

- No Decky target/frame-ready local evidence copy appears unless `evidence_readiness.status == "target-aware-live"` and `evidence_readiness.claim_ready is true`; this includes evidence text, mode labels, and runtime headlines.
- All readiness statuses have English and Traditional Chinese copy.
- Invalid control files and invalid partial overrides cannot leave automatic mode active.
- Non-finite or non-positive target/frame telemetry cannot produce target-aware-ready evidence or non-standard JSON NaN/Infinity output.
- Malformed runtime timestamps cannot pass target-aware-ready Decky readiness through to the UI.
- Background-shaping negative cases produce stable `blocking_reason_codes`.
- README states that hardware/performance claims require guarded device evidence.
- Harness required sweep is fresh and has no pending verification.

- [ ] **Step 3: Run focused V7 tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_control.py tests/test_game_power.py tests/test_game_power_profile.py tests/test_decky_plugin_backend.py tests/test_decky_plugin_assets.py -q
```

Expected: focused V7 test set passes.

- [ ] **Step 4: Run required sweep**

Run:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

Expected: `local: pass`.

- [ ] **Step 5: Confirm Harness trusted-suite status**

Run:

```bash
scripts/harness.py status --json
```

Expected: `freshness.status == "fresh"` and `pending_verification == []`.

- [ ] **Step 6: Check diff hygiene**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors. Existing unrelated untracked files may remain outside this implementation unless the user asks to include them.

## Plan Review Notes

Second-round plan review must verify:

- V7 remains a local evidence gate and does not claim Ultimate scheduler completion.
- Runtime-control health is wired into runtime snapshots.
- `claim_ready` cannot be true for stale/error/control-invalid/off/observe/low-confidence/undersampled states.
- `claim_ready` cannot be true for non-finite frame telemetry.
- `claim_ready` cannot be true for non-finite or non-positive target telemetry.
- Decky backend recomputes stale/error and sanitizes malformed readiness before exposing readiness.
- Decky backend treats missing, non-numeric, NaN, and infinite runtime timestamps as unavailable before exposing readiness.
- Decky target-aware wording is gated only by `evidence_readiness.status == "target-aware-live"` plus `claim_ready == true`.
- Decky copy says local evidence, not hardware proof.
- Background-shaping readiness stays advisory with `write_policy=disabled`.
- Product KPIs are testable with local tests and Harness required sweep.
