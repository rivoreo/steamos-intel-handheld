"""S5 profiler tests: phase metrics, contract v2, export-verdicts, aggregates."""

import json
from pathlib import Path

import pytest

from steamos_intel_handheld.game_power import (
    GAME_POWER_POLICY_VERSION_V9,
    topology_fingerprint,
)
from steamos_intel_handheld.game_power_profile import (
    PolicyComparison,
    PolicyVerdict,
    _cpu_policies_from_topology_json,
    _gate_new_lane_evidence,
    aggregate_color_ledger,
    aggregate_gpu_floor_evidence,
    aggregate_sched_ext_evidence,
    build_parser,
    export_verdicts,
    main,
    replay_action_equivalence,
    resolve_topology_fingerprint,
    run_summarize,
    summarize_phase_metrics,
    validate_runtime_telemetry,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def tb_row(elapsed_s, phase, ladder_step=0, **extra):
    row = {
        "elapsed_s": elapsed_s,
        "appid": "1091500",
        "action": "target-balance-trim",
        "phase": phase,
        "phase_reason_codes": [],
        "ladder_step": ladder_step,
        "classification": {"primary": "fps-target-satisfied", "advisories": []},
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Per-phase metrics
# ---------------------------------------------------------------------------
def test_summarize_phase_metrics_counts_seconds_loading_and_ladder(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(
        path,
        [
            tb_row(0.0, "loading", ladder_step=0),
            tb_row(2.0, "loading", ladder_step=0),
            tb_row(4.0, "at-target", ladder_step=1),
            tb_row(6.0, "at-target", ladder_step=2),
            tb_row(8.0, "above-target", ladder_step=2),
            # second loading episode
            tb_row(10.0, "loading", ladder_step=0),
            tb_row(12.0, "at-target", ladder_step=1),
        ],
    )

    metrics = summarize_phase_metrics(path, poll_s=2.0)

    assert metrics["phase_rows"] == 7
    assert metrics["seconds_per_phase"]["loading"] == pytest.approx(6.0)
    assert metrics["seconds_per_phase"]["at-target"] == pytest.approx(6.0)
    assert metrics["seconds_per_phase"]["above-target"] == pytest.approx(2.0)
    assert metrics["loading_episode_count"] == 2
    assert metrics["loading_total_s"] == pytest.approx(6.0)
    assert metrics["ladder_step_histogram"] == {"0": 3, "1": 2, "2": 2}
    # p99 per phase is intentionally not aligned/faked.
    assert metrics["per_phase_p99_frame_ms"] is None
    assert "not cleanly alignable" in metrics["per_phase_p99_frame_ms_note"]


def test_summarize_phase_metrics_returns_none_without_phase_rows(tmp_path):
    path = tmp_path / "gpu-priority.jsonl"
    write_jsonl(
        path,
        [{"appid": "1091500", "action": "gpu-priority-epp", "elapsed_s": 0.0}],
    )
    assert summarize_phase_metrics(path) is None


def test_run_summarize_embeds_phase_metrics_for_target_balance(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    mangohud.write_text("fps,frametime\n30,33.3\n31,32.0\n")
    jsonl = tmp_path / "game-power.jsonl"
    write_jsonl(
        jsonl,
        [
            tb_row(0.0, "loading", ladder_step=0),
            tb_row(2.0, "at-target", ladder_step=1),
        ],
    )
    output = tmp_path / "run"
    args = build_parser().parse_args(
        [
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "17",
            "--policy",
            "target-balance",
            "--mangohud-csv",
            str(mangohud),
            "--game-power-jsonl",
            str(jsonl),
            "--poll-s",
            "2",
            "--duration-s",
            "60",
            "--output",
            str(output),
        ]
    )
    run_summarize(args)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["phase_metrics"]["loading_episode_count"] == 1
    assert summary["phase_metrics"]["ladder_step_histogram"] == {"0": 1, "1": 1}


# ---------------------------------------------------------------------------
# restore_covered_cgroups wiring in the color ledger
# ---------------------------------------------------------------------------
def test_run_summarize_colors_uncovered_foreground_role_e(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    mangohud.write_text("fps,frametime\n30,33.3\n31,32.0\n")
    fg = "0::/user.slice/app-steam-app1091500-1.scope"
    schedstat = tmp_path / "thread-schedstat.jsonl"
    write_jsonl(
        schedstat,
        [
            {
                "threads": [
                    {
                        "tid": 101,
                        "comm": "RenderThread",
                        "cgroup": fg,
                        "run_time_ns": 0,
                        "runqueue_wait_ns": 0,
                        "timeslices": 0,
                        "current_cpu": 0,
                    }
                ]
            },
            {
                "threads": [
                    {
                        "tid": 101,
                        "comm": "RenderThread",
                        "cgroup": fg,
                        "run_time_ns": 5_000_000_000,
                        "runqueue_wait_ns": 60_000_000,
                        "timeslices": 400,
                        "current_cpu": 1,
                    }
                ]
            },
        ],
    )
    # restore-affinity snapshot that does NOT cover the foreground cgroup.
    restore = tmp_path / "restore-affinity.json"
    restore.write_text(
        json.dumps(
            {
                "threads": [],
                "cgroups": [
                    {
                        "cgroup": "/system.slice/other.scope",
                        "files": {"cpu.weight": "100"},
                    }
                ],
            }
        )
    )
    output = tmp_path / "run"
    args = build_parser().parse_args(
        [
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "17",
            "--policy",
            "target-balance",
            "--mangohud-csv",
            str(mangohud),
            "--thread-schedstat-jsonl",
            str(schedstat),
            "--restore-affinity-json",
            str(restore),
            "--duration-s",
            "10",
            "--output",
            str(output),
        ]
    )
    run_summarize(args)
    ledger = json.loads((output / "color-ledger.json").read_text())
    colors = {entry["role_key"]: entry["color"] for entry in ledger["entries"]}
    # Uncovered foreground cgroup must color E, not A.
    assert colors["foreground-game:renderthread"] == "E"


# ---------------------------------------------------------------------------
# Telemetry contract v2
# ---------------------------------------------------------------------------
def tb_contract_rows():
    return [
        {
            "appid": "1091500",
            "action": "target-balance-trim",
            "phase": "at-target",
            "phase_reason_codes": ["fps-target-satisfied"],
            "ladder_step": 1,
            "color_ledger": {"truncated": False, "entries": []},
            "verdict_ledger_health": {"status": "ready", "entry_count": 0},
            "gated_lanes": {},
            "classification": {"primary": "fps-target-satisfied", "advisories": []},
            "pressure": {
                "cpu": [
                    {"scope": "foreground_cgroup", "supported": True, "some_avg10": 1.0}
                ],
                "memory": [],
                "io": [],
            },
        }
    ]


def test_validate_runtime_telemetry_v2_passes_with_target_balance_fields(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, tb_contract_rows())

    verdict = validate_runtime_telemetry(
        game_power_jsonl=path,
        require_classification=True,
        require_pressure=True,
        require_target_balance_contract=True,
    )

    assert verdict["schema_version"] == "game-power-runtime-telemetry-contract-v2"
    assert verdict["status"] == "pass"
    assert verdict["phase_samples"] == 1
    assert verdict["ladder_step_samples"] == 1
    assert verdict["color_ledger_samples"] == 1
    assert verdict["verdict_ledger_health_samples"] == 1


def test_validate_runtime_telemetry_v2_fails_when_fields_missing(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(
        path,
        [
            {
                "appid": "1091500",
                "action": "gpu-priority-epp",
                "classification": {"primary": "gpu-package-bound", "advisories": []},
            }
        ],
    )
    with pytest.raises(ValueError) as excinfo:
        validate_runtime_telemetry(
            game_power_jsonl=path,
            require_target_balance_contract=True,
        )
    message = str(excinfo.value)
    assert "phase telemetry rows are missing" in message
    assert "ladder_step telemetry rows are missing" in message
    assert "color_ledger telemetry rows are missing" in message
    assert "verdict_ledger_health telemetry rows are missing" in message


def test_validate_runtime_telemetry_v1_unchanged_when_flag_off(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, tb_contract_rows())
    verdict = validate_runtime_telemetry(game_power_jsonl=path)
    assert verdict["schema_version"] == "game-power-runtime-telemetry-contract-v1"
    assert "phase_samples" not in verdict


# ---------------------------------------------------------------------------
# Replay equivalence (target-balance additive)
# ---------------------------------------------------------------------------
def test_replay_action_equivalence_adds_zero_delta_target_balance(tmp_path):
    output = tmp_path / "action-equivalence.json"
    verdict = replay_action_equivalence(output)
    assert verdict["action_delta_count"] == 0
    assert verdict["reason_delta_count"] == 0
    assert verdict["phase_delta_count"] == 0
    assert verdict["ladder_delta_count"] == 0
    assert verdict["status"] == "pass"
    names = {s["name"] for s in verdict["target_balance_scenarios"]}
    assert {"target-balance-no-game", "target-balance-no-target"} <= names
    assert json.loads(output.read_text()) == verdict


# ---------------------------------------------------------------------------
# Topology fingerprint (imported, not re-hashed)
# ---------------------------------------------------------------------------
def lnl_topology_json(tmp_path, ecore_capacity=676):
    cpus = []
    for cpu in range(4):
        cpus.append(
            {
                "cpu": cpu,
                "policy": f"policy{cpu}",
                "capacity": 1024,
                "max_freq_khz": 4_800_000,
            }
        )
    for cpu in range(4, 8):
        cpus.append(
            {
                "cpu": cpu,
                "policy": f"policy{cpu}",
                "capacity": ecore_capacity,
                "max_freq_khz": 3_700_000,
            }
        )
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "cpu-topology.json"
    path.write_text(json.dumps({"cpus": cpus}))
    return path


def test_resolve_topology_fingerprint_matches_imported_helper(tmp_path):
    path = lnl_topology_json(tmp_path)
    fingerprint = resolve_topology_fingerprint(path)
    # Uses the imported daemon helper, not a re-hash.
    assert fingerprint == topology_fingerprint(_cpu_policies_from_topology_json(path))
    assert fingerprint.startswith("4p4e-nosmt-")


def test_resolve_topology_fingerprint_changes_with_topology(tmp_path):
    first = resolve_topology_fingerprint(lnl_topology_json(tmp_path / "a", 676))
    second = resolve_topology_fingerprint(lnl_topology_json(tmp_path / "b", 700))
    assert first != second


def test_f1_fingerprint_labels_real_mixed_pcore_capacities_as_4p4e(tmp_path):
    # Real device: cpu0/1 capacity 1005, cpu2/3 capacity 1024, cpu4-7 676.
    cpus = []
    for cpu, capacity in enumerate([1005, 1005, 1024, 1024, 676, 676, 676, 676]):
        cpus.append(
            {
                "cpu": cpu,
                "policy": f"policy{cpu}",
                "capacity": capacity,
                "max_freq_khz": 4_800_000 if capacity > 700 else 3_700_000,
            }
        )
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "cpu-topology.json"
    path.write_text(json.dumps({"cpus": cpus}))
    assert resolve_topology_fingerprint(path).startswith("4p4e-nosmt-")


# ---------------------------------------------------------------------------
# export-verdicts
# ---------------------------------------------------------------------------
def better_report(candidate_policy, *, actuators=None, verdict="better"):
    inner = {
        "baseline_policy": "off",
        "candidate_policy": candidate_policy,
        "verdict": verdict,
        "reason": "target sustained while median package power reduced by 7.0%",
        "claim_scope": {
            "appid": "1903340",
            "fps_target": 30.0,
            "candidate_policy": candidate_policy,
        },
    }
    comparison = {"appid": "1903340", "tdp_w": 17, "comparison": inner}
    if actuators is not None:
        comparison["candidate_policy_actuators"] = actuators
    return {"comparisons": [comparison]}


def test_export_verdicts_maps_guarded_lane_policies_to_actuators():
    reports = [
        better_report("gpu-priority-bg-weight"),
        better_report("gpu-priority-bg-uclamp"),
        better_report("gpu-priority-affinity"),
    ]
    result = export_verdicts(
        reports, topology_fingerprint="lnl-x", kernel="6.16.12-valve24.4"
    )
    actuators = {entry["actuator"] for entry in result["entries"]}
    assert actuators == {"bg-weight", "bg-uclamp", "compact-affinity"}
    entry = result["entries"][0]
    assert entry["verdict"] == "BETTER"
    assert entry["policy_version"] == GAME_POWER_POLICY_VERSION_V9
    assert entry["topology_fingerprint"] == "lnl-x"
    assert entry["kernel"] == "6.16.12-valve24.4"
    assert entry["fps_target"] == 30.0
    assert entry["claim_scope"]["appid"] == "1903340"


def test_export_verdicts_uses_explicit_target_balance_actuators():
    reports = [better_report("target-balance", actuators=["ladder-step-5", "uclamp-min"])]
    result = export_verdicts(reports, topology_fingerprint="lnl-x", kernel="k")
    actuators = sorted(entry["actuator"] for entry in result["entries"])
    assert actuators == ["ladder-step-5", "uclamp-min"]


def test_export_verdicts_skips_target_balance_without_declared_actuator():
    reports = [better_report("target-balance")]
    result = export_verdicts(reports, topology_fingerprint="lnl-x", kernel="k")
    assert result["entries"] == []
    assert result["skipped"][0]["reason"] == "no-actuator-mapping-for-candidate-policy"


def test_export_verdicts_ignores_non_better_comparisons():
    reports = [better_report("gpu-priority-bg-weight", verdict="inconclusive")]
    result = export_verdicts(reports, topology_fingerprint="lnl-x", kernel="k")
    assert result["entries"] == []


def test_c16_export_verdicts_maps_new_target_balance_lane_policies():
    reports = [
        better_report("target-balance-uclampmin"),
        better_report("target-balance-ladder5"),
    ]
    result = export_verdicts(reports, topology_fingerprint="lnl-x", kernel="k")
    actuators = sorted(entry["actuator"] for entry in result["entries"])
    assert actuators == ["ladder-step-5", "uclamp-min"]


def test_c13_export_verdicts_buckets_tdp_to_daemon_buckets():
    report = better_report("gpu-priority-bg-weight")
    report["comparisons"][0]["tdp_w"] = 16  # within +-2W of the 17W bucket
    result = export_verdicts([report], topology_fingerprint="lnl-x", kernel="k")
    assert result["entries"][0]["tdp_w"] == 17


def test_c13_export_verdicts_skips_off_bucket_tdp():
    report = better_report("gpu-priority-bg-weight")
    report["comparisons"][0]["tdp_w"] = 25  # >2W from both 22 and 30
    result = export_verdicts([report], topology_fingerprint="lnl-x", kernel="k")
    assert result["entries"] == []
    assert result["skipped"][0]["reason"] == "tdp-outside-verdict-buckets"


def test_c14_export_verdicts_skips_entries_without_fps_target():
    report = better_report("gpu-priority-bg-weight")
    del report["comparisons"][0]["comparison"]["claim_scope"]["fps_target"]
    result = export_verdicts([report], topology_fingerprint="lnl-x", kernel="k")
    assert result["entries"] == []
    assert result["skipped"][0]["reason"] == "missing-fps-target-in-claim-scope"


def test_c17_validate_runtime_telemetry_fails_on_missing_replay_delta_keys(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, tb_contract_rows())
    replay_path = tmp_path / "action-equivalence.json"
    replay_path.write_text(json.dumps({"status": "pass"}))  # no delta keys

    with pytest.raises(ValueError) as excinfo:
        validate_runtime_telemetry(
            game_power_jsonl=path,
            action_replay_json=replay_path,
        )
    assert "action replay equivalence" in str(excinfo.value)


def test_export_verdicts_cli_writes_file(tmp_path):
    report_path = tmp_path / "aggregate.json"
    report_path.write_text(json.dumps(better_report("gpu-priority-bg-weight")))
    topo = lnl_topology_json(tmp_path)
    out = tmp_path / "verdicts.json"
    main(
        [
            "export-verdicts",
            "--aggregate",
            str(report_path),
            "--cpu-topology-json",
            str(topo),
            "--kernel",
            "6.16.12-valve24.4",
            "--out",
            str(out),
        ]
    )
    payload = json.loads(out.read_text())
    assert payload["entries"][0]["actuator"] == "bg-weight"
    assert payload["entries"][0]["topology_fingerprint"].startswith("4p4e-nosmt-")


# ---------------------------------------------------------------------------
# Aggregate color ledger + new-lane evidence
# ---------------------------------------------------------------------------
def write_color_ledger(run_dir: Path, entries: list[dict]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "color-ledger.json").write_text(
        json.dumps({"truncated": False, "entries": entries})
    )
    return run_dir / "summary.json"


def test_aggregate_color_ledger_rolls_up_role_stability(tmp_path):
    entry = {
        "role_key": "foreground-game:renderthread",
        "color": "A",
        "tid_count": 1,
        "cpu_time_ms_per_s": 500.0,
        "runqueue_wait_ms_per_s": 30.0,
        "cpus_seen": [0, 1],
        "actuator": "uclamp-min",
        "actuator_state": "advisory",
        "blocking_reason_codes": [],
    }
    paths = [
        write_color_ledger(tmp_path / "run-1", [entry]),
        write_color_ledger(tmp_path / "run-2", [entry]),
    ]
    result = aggregate_color_ledger(paths)
    assert result["run_count_with_ledger"] == 2
    assert result["color_counts"] == {"A": 1}
    role = result["roles"][0]
    assert role["observed_run_count"] == 2
    assert role["role_stability_ratio"] == 1.0
    assert role["cpu_time_ms_per_s_median"] == 500.0
    assert role["actuator_recommendation"] == "uclamp-min"


def test_aggregate_sched_ext_evidence_requires_every_run(tmp_path):
    def write(run_dir, valid):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "sched-ext-state.json").write_text(
            json.dumps(
                {
                    "valid": valid,
                    "before": {"state": "disabled"},
                    "during": {"state": "enabled", "root_ops": "lavd"},
                    "after": {"state": "disabled"},
                }
            )
        )
        return run_dir / "summary.json"

    complete = aggregate_sched_ext_evidence(
        [write(tmp_path / "a", True), write(tmp_path / "b", True)]
    )
    assert complete["evidence_complete"] is True
    assert complete["root_ops"] == ["lavd"]

    partial = aggregate_sched_ext_evidence(
        [write(tmp_path / "c", True), write(tmp_path / "d", False)]
    )
    assert partial["evidence_complete"] is False


def test_aggregate_gpu_floor_evidence_requires_restore(tmp_path):
    def write(run_dir, valid, restored):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "gpu-freq-restore.json").write_text(
            json.dumps(
                {
                    "valid": valid,
                    "restored": restored,
                    "floor_mhz": 1600,
                    "gpu_floor_scope": "run",
                }
            )
        )
        return run_dir / "summary.json"

    complete = aggregate_gpu_floor_evidence(
        [write(tmp_path / "a", True, True), write(tmp_path / "b", True, True)]
    )
    assert complete["evidence_complete"] is True
    assert complete["floor_mhz"] == [1600]
    assert complete["gpu_floor_scope"] == ["run"]

    missing = aggregate_gpu_floor_evidence(
        [write(tmp_path / "c", True, True), write(tmp_path / "d", True, False)]
    )
    assert missing["evidence_complete"] is False


def test_c16_aggregate_foreground_uclamp_evidence_requires_restore(tmp_path):
    from steamos_intel_handheld.game_power_profile import (
        aggregate_foreground_uclamp_evidence,
    )

    def write(run_dir, valid, restored):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "foreground-uclamp-restore.json").write_text(
            json.dumps({"valid": valid, "restored": restored, "floor_value": "25.00"})
        )
        return run_dir / "summary.json"

    complete = aggregate_foreground_uclamp_evidence(
        [write(tmp_path / "a", True, True), write(tmp_path / "b", True, True)]
    )
    assert complete["evidence_complete"] is True

    partial = aggregate_foreground_uclamp_evidence(
        [write(tmp_path / "c", True, True), write(tmp_path / "d", False, False)]
    )
    assert partial["evidence_complete"] is False

    missing = aggregate_foreground_uclamp_evidence(
        [write(tmp_path / "e", True, True), tmp_path / "f" / "summary.json"]
    )
    assert missing["evidence_complete"] is False


def test_c16_gate_rejects_uclampmin_without_complete_evidence():
    comparison = _better_comparison("target-balance-uclampmin")
    gated = _gate_new_lane_evidence(
        comparison,
        candidate_policy="target-balance-uclampmin",
        sched_ext_evidence={"evidence_complete": True},
        gpu_floor_evidence={"evidence_complete": True},
        foreground_uclamp_evidence={"evidence_complete": False},
    )
    assert gated.verdict == PolicyVerdict.REJECTED
    assert "uclamp" in gated.reason


def test_c16_gate_keeps_uclampmin_with_complete_evidence():
    comparison = _better_comparison("target-balance-uclampmin")
    gated = _gate_new_lane_evidence(
        comparison,
        candidate_policy="target-balance-uclampmin",
        sched_ext_evidence={"evidence_complete": True},
        gpu_floor_evidence={"evidence_complete": True},
        foreground_uclamp_evidence={"evidence_complete": True},
    )
    assert gated.verdict == PolicyVerdict.BETTER


def _better_comparison(candidate_policy):
    return PolicyComparison(
        "off",
        candidate_policy,
        PolicyVerdict.BETTER,
        "target sustained while median package power reduced by 7.0%",
        claim_scope={"fps_target": 30.0},
    )


def test_gate_rejects_scx_lavd_without_complete_sched_ext_evidence():
    comparison = _better_comparison("scx-lavd")
    gated = _gate_new_lane_evidence(
        comparison,
        candidate_policy="scx-lavd",
        sched_ext_evidence={"evidence_complete": False},
        gpu_floor_evidence={"evidence_complete": True},
    )
    assert gated.verdict == PolicyVerdict.REJECTED
    assert "sched_ext state evidence" in gated.reason


def test_gate_rejects_gpufloor_without_complete_restore_evidence():
    comparison = _better_comparison("target-balance-gpufloor")
    gated = _gate_new_lane_evidence(
        comparison,
        candidate_policy="target-balance-gpufloor",
        sched_ext_evidence={"evidence_complete": True},
        gpu_floor_evidence={"evidence_complete": False},
    )
    assert gated.verdict == PolicyVerdict.REJECTED
    assert "gpu-freq restore evidence" in gated.reason


def test_gate_keeps_better_when_evidence_complete():
    comparison = _better_comparison("scx-lavd")
    gated = _gate_new_lane_evidence(
        comparison,
        candidate_policy="scx-lavd",
        sched_ext_evidence={"evidence_complete": True},
        gpu_floor_evidence={"evidence_complete": True},
    )
    assert gated.verdict == PolicyVerdict.BETTER


def test_gate_leaves_unrelated_candidate_untouched():
    comparison = _better_comparison("target-balance")
    gated = _gate_new_lane_evidence(
        comparison,
        candidate_policy="target-balance",
        sched_ext_evidence={"evidence_complete": False},
        gpu_floor_evidence={"evidence_complete": False},
    )
    assert gated.verdict == PolicyVerdict.BETTER


def test_export_verdicts_root_requires_candidate_policy(tmp_path):
    with pytest.raises(SystemExit):
        main(
            [
                "export-verdicts",
                "--root",
                str(tmp_path),
                "--topology-fingerprint",
                "lnl-x",
                "--kernel",
                "k",
            ]
        )


# ---------------------------------------------------------------------------
# V10 Slice C: telemetry contract v3 (additive), replay, export mapping
# ---------------------------------------------------------------------------
def v10_row(*, ladder_step=0, trim_rungs=None, gpu_freq_caps=None, soft_pl1_w=None,
            persona="battery", frame_feed_status="live", **extra):
    row = {
        "elapsed_s": 1.0,
        "appid": "1091500",
        "action": "target-balance-trim",
        "phase": "at-target",
        "phase_reason_codes": [],
        "ladder_step": ladder_step,
        "classification": {"primary": "fps-target-satisfied", "advisories": []},
        "color_ledger": {"truncated": False, "entries": []},
        "verdict_ledger_health": {"status": "ready", "entry_count": 0},
        "gated_lanes": {},
        "pressure": {
            "cpu": [{"scope": "foreground_cgroup", "supported": True, "some_avg10": 1.0}],
            "memory": [],
            "io": [],
        },
        "persona": persona,
        "trim_rungs_active": list(trim_rungs or []),
        "frame_feed_status": frame_feed_status,
        "gpu_freq_caps": gpu_freq_caps,
        "soft_pl1_w": soft_pl1_w,
        "boost_active": False,
        "boost_reason": None,
        "limiter_state": "unknown",
    }
    row.update(extra)
    return row


def v10_rows():
    return [
        v10_row(ladder_step=1, trim_rungs=["G1"],
                gpu_freq_caps={"min_mhz": None, "max_mhz": 1657}),
        v10_row(ladder_step=4, trim_rungs=["G1", "G2", "G3", "P1"],
                gpu_freq_caps={"min_mhz": None, "max_mhz": 1072}, soft_pl1_w=17),
    ]


def test_validate_v10_contract_passes_with_v10_fields(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, v10_rows())
    verdict = validate_runtime_telemetry(
        game_power_jsonl=path,
        require_classification=True,
        require_pressure=True,
        require_v10_contract=True,
    )
    assert verdict["schema_version"] == "game-power-runtime-telemetry-contract-v3"
    assert verdict["status"] == "pass"
    assert verdict["persona_samples"] == 2
    assert verdict["persona_missing_rows"] == 0
    assert verdict["trim_rungs_active_samples"] == 2
    assert verdict["frame_feed_status_samples"] == 2
    assert verdict["gpu_freq_caps_present"] is True
    assert verdict["soft_pl1_w_present"] is True
    # v10 implies the v2 target-balance field checks too.
    assert verdict["phase_samples"] == 2


def test_validate_v10_contract_fails_when_persona_missing_on_a_row(tmp_path):
    rows = v10_rows()
    del rows[1]["persona"]
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, rows)
    with pytest.raises(ValueError, match="persona is missing on 1 emitted row"):
        validate_runtime_telemetry(game_power_jsonl=path, require_v10_contract=True)


def test_validate_v10_contract_presence_if_active_gpu_caps(tmp_path):
    # A G rung is active but no row carries gpu_freq_caps -> fail.
    rows = [v10_row(ladder_step=1, trim_rungs=["G1"], gpu_freq_caps=None)]
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, rows)
    with pytest.raises(ValueError, match="gpu_freq_caps telemetry is missing"):
        validate_runtime_telemetry(game_power_jsonl=path, require_v10_contract=True)


def test_validate_v10_contract_presence_if_active_soft_pl1(tmp_path):
    rows = [v10_row(ladder_step=1, trim_rungs=["P1"], soft_pl1_w=None)]
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, rows)
    with pytest.raises(ValueError, match="soft_pl1_w telemetry is missing"):
        validate_runtime_telemetry(game_power_jsonl=path, require_v10_contract=True)


def test_validate_v10_contract_no_g_rung_needs_no_gpu_caps(tmp_path):
    # Only C rungs active: neither gpu_freq_caps nor soft_pl1_w is required.
    rows = [v10_row(ladder_step=1, trim_rungs=["C1"])]
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, rows)
    verdict = validate_runtime_telemetry(
        game_power_jsonl=path, require_v10_contract=True
    )
    assert verdict["status"] == "pass"
    assert verdict["g_rung_active"] is False
    assert verdict["p_rung_active"] is False


def test_validate_v1_v2_byte_identical_when_v10_flag_off(tmp_path):
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, v10_rows())
    v1 = validate_runtime_telemetry(game_power_jsonl=path)
    assert v1["schema_version"] == "game-power-runtime-telemetry-contract-v1"
    assert "persona_samples" not in v1
    v2 = validate_runtime_telemetry(
        game_power_jsonl=path, require_target_balance_contract=True
    )
    assert v2["schema_version"] == "game-power-runtime-telemetry-contract-v2"
    assert "persona_samples" not in v2


def test_replay_action_equivalence_adds_zero_delta_v10(tmp_path):
    verdict = replay_action_equivalence(tmp_path / "eq.json")
    assert verdict["rung_delta_count"] == 0
    assert verdict["boost_delta_count"] == 0
    assert verdict["status"] == "pass"
    names = {s["name"] for s in verdict["v10_scenarios"]}
    assert {"v10-battery-full-ladder", "v10-gpu-cap-only", "v10-soft-pl1-only"} <= names
    gpu_cap = next(s for s in verdict["v10_scenarios"] if s["name"] == "v10-gpu-cap-only")
    # The gpu-cap-only lane climbs only G rungs.
    assert gpu_cap["trim_rung_sequences"][-1] == ["G1", "G2", "G3"]


def test_validate_rejects_replay_artifact_with_rung_delta(tmp_path):
    replay = tmp_path / "eq.json"
    replay.write_text(json.dumps({
        "action_delta_count": 0, "reason_delta_count": 0,
        "phase_delta_count": 0, "ladder_delta_count": 0,
        "rung_delta_count": 1, "boost_delta_count": 0,
    }))
    path = tmp_path / "game-power.jsonl"
    write_jsonl(path, v10_rows())
    with pytest.raises(ValueError, match="action replay equivalence failed"):
        validate_runtime_telemetry(game_power_jsonl=path, action_replay_json=replay)


def test_export_verdicts_maps_v10_single_lane_policies():
    reports = [better_report("v10-gpu-cap"), better_report("v10-soft-pl1")]
    result = export_verdicts(reports, topology_fingerprint="lnl-x", kernel="k")
    actuators = {entry["actuator"] for entry in result["entries"]}
    assert actuators == {"gpu-cap", "soft-pl1"}


def test_export_verdicts_skips_v10_battery_without_declared_actuator():
    result = export_verdicts(
        [better_report("v10-battery")], topology_fingerprint="lnl-x", kernel="k"
    )
    assert result["entries"] == []
    assert result["skipped"][0]["reason"] == "no-actuator-mapping-for-candidate-policy"
