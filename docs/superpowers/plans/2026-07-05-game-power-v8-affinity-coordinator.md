# Game Power V8 Affinity Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a guarded profile-stage affinity coordinator that uses existing sampling evidence to bind stable foreground thread roles during controlled A/B runs, records apply/restore evidence, rejects no-op or failed affinity runs, and keeps daemon affinity writes disabled.

**Architecture:** Extend `game_power_profile.py` with foreground role affinity write/restore helpers, CLI subcommands, aggregate-plan resolver, compact-mask planning, and summary/aggregate evidence fields. Wire `scripts/profile-game-power-on-device.sh` to run a new `gpu-priority-affinity` policy by copying an aggregate report or affinity plan to the device and resolving the first ready candidate automatically; the wrapper does not pass free-form role/cpu debug values through SSH env. Update tests and README to state the boundary: automated profiler experiment now exists, daemon/Decky default affinity still does not.

**Tech Stack:** Python 3.13, pytest, bash profiler wrapper, Linux `taskset`, existing harness required sweep.

---

## File Map

- Modify `src/steamos_intel_handheld/game_power_profile.py`
  - Add `FOREGROUND_AFFINITY_WRITE_VARIANTS`.
  - Add role/cpu parsing helpers.
  - Add `resolve_foreground_affinity_candidate()`.
  - Add compact-mask planner and foreground-affinity evidence fields on
    `RunSummary` / aggregate output.
  - Add `apply_foreground_affinity_writes()` and
    `restore_foreground_affinity_writes()`.
  - Add `apply-foreground-affinity` and `restore-foreground-affinity` CLI
    subcommands.
  - Rename affinity experiment plan wording from "soft" to "guarded hard
    compact" because `sched_setaffinity`/`taskset` is a hard mask.
- Modify `scripts/profile-game-power-on-device.sh`
  - Add `PROFILE_GAME_POWER_AFFINITY_PLAN_JSON`.
  - Support `gpu-priority-affinity`.
  - Copy the local aggregate/plan JSON to the device when provided.
  - Resolve role/cpus from the copied plan.
  - Apply affinity after `restore-affinity.json` is captured and before the run.
  - Restore affinity on both success and failure paths.
- Modify `tests/test_game_power_profile.py`
  - Cover apply/restore helper behavior, role filtering, malformed masks, and
    revised plan naming.
- Modify `tests/test_integration_assets.py`
  - Assert the wrapper exposes and restores the new guarded policy.
- Modify `README.md`
  - Document V8 as a profiler-only automated affinity experiment and keep the
    daemon boundary explicit.

## Task 1: Tests For Foreground Affinity Writer

**Files:**
- Modify: `tests/test_game_power_profile.py`

- [ ] **Step 1: Add failing tests for apply/restore**

Add tests that create a synthetic `restore-affinity.json` with foreground and
background threads. Inject a fake `command_runner` and assert only current
foreground role matches are written:

```python
def test_apply_foreground_affinity_writes_only_matching_foreground_role(tmp_path):
    restore = tmp_path / "restore-affinity.json"
    output = tmp_path / "foreground-affinity-writes.json"
    restore.write_text(json.dumps({
        "appid": "1091500",
        "threads": [
            {"tid": 201, "comm": "Worker Thread", "cgroup": "0::/user.slice/app-steam-app1091500.scope", "cpus_allowed_list": "0-7"},
            {"tid": 301, "comm": "Render Thread", "cgroup": "0::/user.slice/app-steam-app1091500.scope", "cpus_allowed_list": "0-7"},
            {"tid": 401, "comm": "Worker Thread", "cgroup": "0::/user.slice/steam.service", "cpus_allowed_list": "0-7"},
        ],
    }))
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="pid 201's current affinity list: 0,1\n", stderr="")

    payload = apply_foreground_affinity_writes(
        restore,
        output,
        role_key="foreground-game:worker-thread",
        preferred_cpus="0,1",
        variant="foreground-role-compact",
        command_runner=runner,
    )

    assert payload["write_policy"] == "guarded-foreground-affinity"
    assert payload["writes"][0]["tid"] == 201
    assert payload["writes"][0]["original_cpus_allowed_list"] == "0-7"
    assert commands == [["taskset", "-pc", "0,1", "201"]]
```

Add a restore test that consumes the writes JSON and runs `taskset -pc 0-7 201`.
Add a negative test where `role_key="background-helper:worker-thread"` raises
`ValueError`.
Add tests for:

- zero matching foreground threads: report is written with
  `matched_thread_count == 0`, `written_count == 0`, and CLI exits nonzero.
- role present only in non-foreground cgroups: same fail-closed behavior.
- one successful write followed by one nonzero `taskset` result: report records
  `partial_failure: true`, restore is invoked by the wrapper before sampling,
  and the run is not summarized as valid affinity evidence.
- missing `taskset` / `FileNotFoundError`: report records `taskset-missing`.
- restore nonzero return code, output mismatch, missing original mask, and
  skipped entries: `restored` is false except for skipped non-written entries.
- raw plan resolver shape, observe-only rejection, missing candidate rejection,
  wrong variant rejection, non-foreground role rejection, and malformed CPUs.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py -q
```

Expected: import/name failures for the new helpers.

## Task 2: Implement Profile CLI Writer

**Files:**
- Modify: `src/steamos_intel_handheld/game_power_profile.py`

- [ ] **Step 1: Add constants and parsers**

Add:

```python
FOREGROUND_AFFINITY_WRITE_VARIANTS = {"foreground-role-compact"}
```

Add these helper functions: `_parse_cpu_list(value: str) -> list[int]`,
`_format_cpu_list(cpus: list[int]) -> str`,
`_thread_role_key_from_restore_item(item: dict[str, object]) -> str | None`,
`_plan_compact_affinity_mask(candidate: dict[str, object]) -> str`,
and `resolve_foreground_affinity_candidate(plan_json: str | Path) -> dict[str, object]`.

Parsing must accept `0,1`, `0-1`, and `0,2-3`, reject empty input, reject
negative CPUs, and return a sorted unique list.

`_plan_compact_affinity_mask()` must use `preferred_cpus`, `thread_count_median`
or `observed_run_count`, and any current allowed/effective CPU evidence in the
candidate. It rejects empty masks and rejects a single-CPU mask when the stable
role represents multiple observed threads.

`resolve_foreground_affinity_candidate()` must accept either the full aggregate
report shape (`{"comparisons": [comparison objects]}`) or a raw
`affinity_experiment_plan` shape. It must choose the first plan with
`mode == "ready-for-guarded-experiment"` and the first candidate whose
`guarded_variant == "foreground-role-compact"`, then return
`{"role_key": "foreground-game:worker-thread", "preferred_cpus": "0,1",
"guarded_variant": "foreground-role-compact"}` for the first eligible
candidate. It must reject missing candidates with `ValueError`.

- [ ] **Step 2: Add apply helper**

Implement `apply_foreground_affinity_writes(restore_affinity_json: str | Path,
output: str | Path, *, role_key: str, preferred_cpus: str, variant: str,
command_runner: Any | None = None) -> dict[str, object]`.

Rules:

- `variant` must be in `FOREGROUND_AFFINITY_WRITE_VARIANTS`.
- `role_key` must start with `foreground-game:`.
- CPU mask must parse to at least one CPU.
- Iterate `payload["threads"]`.
- Recompute the role key from current `comm` and `cgroup`.
- Revalidate `/proc/<pid>/task/<tid>` before each write when available. The
  writer must compare current `comm`, current cgroup, and current affinity to
  the restore snapshot. Stale or mismatched tasks are recorded as
  `stale-thread` and not written.
- Write only matching role-key threads.
- Use `command_runner or subprocess.run` with `check=False`,
  `capture_output=True`, and `text=True`.
- Command shape: `["taskset", "-pc", cpu_list, str(tid)]`.
- Capture `matched_thread_count`, `written_count`, `failed_count`,
  `partial_failure`, `original_cpus_allowed_list`, `proposed_cpus`,
  `returncode`, `stdout`, `stderr`, and `status`.
- Always write the report. If `matched_thread_count == 0`,
  `written_count == 0`, or `failed_count > 0`, the CLI exits nonzero before
  sampling can start.

- [ ] **Step 3: Add restore helper**

Implement `restore_foreground_affinity_writes(writes_json: str | Path,
output: str | Path, *, command_runner: Any | None = None) -> dict[str, object]`.

Restore only entries with `status == "written"`. Missing original masks produce
`restore-missing-original-mask`. Nonzero `taskset` return codes produce
`restore-failed`. If taskset output contains a current affinity mask that does
not match the original, produce `restore-mismatch`. Write a report with
`write_policy="restore-foreground-affinity"` and `restored: bool`.

- [ ] **Step 4: Add summary and aggregate evidence fields**

Add `foreground_affinity_write_count`, `foreground_affinity_failed_count`,
`foreground_affinity_matched_thread_count`, `foreground_affinity_role_key`,
`foreground_affinity_preferred_cpus`,
`foreground_affinity_restore_restored`, and
`foreground_affinity_valid_evidence` to `RunSummary`.

When summarizing a run, load `foreground-affinity-writes.json` and
`foreground-affinity-restore.json` when present. For policy
`gpu-priority-affinity`, valid evidence requires `written_count > 0`,
`failed_count == 0`, and restore report `restored is True`.

Aggregate output must carry medians/counts for these fields or at minimum expose
`valid_foreground_affinity_count` and reject affinity candidate comparisons when
the candidate's valid count does not match sample count.

- [ ] **Step 5: Add CLI subcommands**

Add parser entries:

```python
apply_foreground = subcommands.add_parser("apply-foreground-affinity")
apply_foreground.add_argument("--restore-affinity-json", required=True)
apply_foreground.add_argument("--output", required=True)
apply_foreground.add_argument("--role-key", required=True)
apply_foreground.add_argument("--preferred-cpus", required=True)
apply_foreground.add_argument("--variant", choices=sorted(FOREGROUND_AFFINITY_WRITE_VARIANTS), required=True)

restore_foreground = subcommands.add_parser("restore-foreground-affinity")
restore_foreground.add_argument("--writes-json", required=True)
restore_foreground.add_argument("--output", required=True)
```

Route them in `main()`. `apply-foreground-affinity` exits with code 1 after
writing the report when no writes succeeded or any write failed.

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py -q
```

Expected: pass or fail only on wrapper/docs tests not yet changed.

## Task 3: Wire Device Profiler Policy

**Files:**
- Modify: `scripts/profile-game-power-on-device.sh`
- Modify: `tests/test_integration_assets.py`

- [ ] **Step 1: Add wrapper tests first**

Add an integration asset test asserting:

- `AFFINITY_PLAN_JSON='$remote_affinity_plan_json'`
- `resolve_foreground_affinity_candidate()`
- `gpu-priority-affinity`
- `apply_foreground_affinity_variant()`
- `restore_foreground_affinity_variant()`
- `apply-foreground-affinity`
- `restore-foreground-affinity`
- `foreground-affinity-writes.json`
- `foreground-affinity-restore.json`
- `restore_foreground_affinity_variant "$run_dir" || restored=false`
- `if ! apply_foreground_affinity_variant`
- no `PROFILE_GAME_POWER_AFFINITY_ROLE_KEY` or
  `PROFILE_GAME_POWER_AFFINITY_CPUS` remote env interpolation

- [ ] **Step 2: Implement wrapper wiring**

Add top-level env reads:

```bash
affinity_plan_json="${PROFILE_GAME_POWER_AFFINITY_PLAN_JSON:-}"
```

After `remote_root` is created, copy the plan when provided:

```bash
remote_affinity_plan_json=""
if [ -n "$affinity_plan_json" ]; then
  remote_affinity_plan_json="$remote_root/affinity-experiment-plan.json"
  scp "$affinity_plan_json" "$target:$remote_affinity_plan_json"
fi
```

Pass only the remote plan path into the ssh env. Add functions mirroring
background shaping:

Add shell functions named `resolve_foreground_affinity_candidate`,
`apply_foreground_affinity_variant`, and `restore_foreground_affinity_variant`.

For policy `gpu-priority-affinity`, set mode to `gpu-priority`, keep CPU cap
off, resolve role key and CPU mask from `AFFINITY_PLAN_JSON`, and set
`foreground_affinity_variant="foreground-role-compact"`. If a ready plan is not
present, exit with status 2 before any affinity write.

Call apply after `snapshot_affinity_restore_state` and before sampling using:

```bash
if ! apply_foreground_affinity_variant "$run_dir" "$foreground_affinity_variant"; then
  restore_foreground_affinity_variant "$run_dir" || true
  exit 1
fi
```

Restore on both game-power failure and success paths. If restore reports false,
set `restored=false`.

- [ ] **Step 3: Run wrapper tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_game_power_profile_wrapper_supports_foreground_affinity_policy_variant -q
```

Expected: pass.

## Task 4: Plan Naming And Documentation

**Files:**
- Modify: `src/steamos_intel_handheld/game_power_profile.py`
- Modify: `tests/test_game_power_profile.py`
- Modify: `README.md`

- [ ] **Step 1: Rename plan wording**

Change `candidate_control` from `soft-compact-preferred-cpus` to
`guarded-hard-compact-affinity`. Change `guarded_variant` from
`foreground-role-soft-compact` to `foreground-role-compact`.

- [ ] **Step 2: Update tests**

Update assertions in the guarded affinity experiment plan tests to expect the
new names and `write_policy` still `disabled`.

- [ ] **Step 3: Update README**

Document the new flow:

```bash
PROFILE_GAME_POWER_POLICIES="off gpu-priority-affinity off" \
PROFILE_GAME_POWER_CAPTURE_MODE=controlled \
PROFILE_GAME_POWER_AFFINITY_PLAN_JSON=".cache/game-power/profiles/aggregate.json" \
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

State that this is guarded device-profiler automation only and not daemon or
Decky runtime affinity. State that this is a two-phase loop: discovery
aggregate, then candidate affinity run. It is not yet a fully closed-loop daemon
that discovers the latest compatible plan by itself.

## Task 5: Verification

**Files:**
- No additional code changes expected.

- [ ] **Step 1: Run focused tests**

Run the start-here harness listing once:

```bash
scripts/harness.py list --json
```

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py tests/test_integration_assets.py -q
```

Expected: pass.

- [ ] **Step 2: Run required sweep**

Run:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

Expected: `local: pass`.

- [ ] **Step 3: Inspect harness status**

Run:

```bash
scripts/harness.py status --json
```

Expected: `freshness.status == "fresh"` and `pending_verification == []` for
local required checks.

- [ ] **Step 4: Report boundaries**

Final report must separate:

- Local/unit evidence: focused pytest and required sweep.
- Device evidence: not run unless explicitly requested.
- Performance claim: not made until `game-power-profile-device` runs on a real
  foreground game.
