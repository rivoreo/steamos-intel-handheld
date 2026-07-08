import csv
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from steamos_intel_handheld.game_power_profile import (
    CaptureMode,
    CpuTopologySummary,
    FpsTargetDiscovery,
    GamePowerLogSummary,
    MangoHudFpsSummary,
    PolicyAggregate,
    PolicyComparison,
    PolicyVerdict,
    ProcessCgroupSummary,
    RestoreAffinitySummary,
    RunSummary,
    RuntimeTelemetryCounts,
    ThreadAffinitySummary,
    ThreadSchedstatSummary,
    aggregate_run_summaries,
    apply_background_shaping_writes,
    apply_foreground_affinity_writes,
    build_affinity_advice,
    build_background_shaping_advice,
    build_background_shaping_experiment_plan,
    build_parser,
    compare_policy_aggregates,
    compare_run_summaries,
    merge_run_summary,
    parse_game_power_jsonl,
    parse_gamescope_fps_target_from_argv,
    parse_mangohud_fps_csv,
    parse_mangohud_summary_csv,
    parse_pressure_file,
    replay_action_equivalence,
    resolve_foreground_affinity_candidate,
    restore_background_shaping_writes,
    restore_foreground_affinity_writes,
    run_summarize,
    summarize_cpu_topology,
    summarize_foreground_affinity_artifacts,
    summarize_pressure_jsonl,
    summarize_process_cgroups_jsonl,
    summarize_restore_affinity_json,
    summarize_thread_affinity_jsonl,
    summarize_thread_schedstat_jsonl,
    validate_runtime_telemetry,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def controlled_ab_run(
    *,
    policy: str,
    position: str,
    pair_id: str = "pair-1",
    candidate_policy: str = "gpu-priority",
    invocation_id: str = "invocation-1",
    base_s: float = 0.0,
    avg_fps: float = 42.0,
    one_percent_low_fps: float = 30.0,
    p99_frametime_ms: float = 35.0,
    avg_package_w: float | None = None,
    thermal_start_c: float = 61.0,
    thermal_end_c: float = 63.0,
    foreground_affinity_valid_evidence: bool | None = None,
    foreground_affinity_write_count: int | None = None,
    foreground_affinity_failed_count: int | None = None,
) -> RunSummary:
    position_offsets = {
        "baseline-before": 0.0,
        "candidate": 121.0,
        "baseline-after": 242.0,
    }
    offset = position_offsets[position]
    cooldown_started_at_s = base_s + offset
    cooldown_ended_at_s = cooldown_started_at_s + 60.0
    run_started_at_s = cooldown_ended_at_s + 0.3
    run_ended_at_s = run_started_at_s + 60.0
    return RunSummary(
        appid="1091500",
        tdp_w=22,
        policy=policy,
        capture_mode=CaptureMode.CONTROLLED,
        duration_s=60.0,
        warmup_s=10.0,
        poll_s=2.0,
        fps_target=40.0,
        fps_target_source="manual",
        avg_fps=avg_fps,
        one_percent_low_fps=one_percent_low_fps,
        p99_frametime_ms=p99_frametime_ms,
        avg_package_w=avg_package_w,
        restored=True,
        foreground_affinity_valid_evidence=foreground_affinity_valid_evidence,
        foreground_affinity_write_count=foreground_affinity_write_count,
        foreground_affinity_failed_count=foreground_affinity_failed_count,
        ab_order_strategy="paired-baseline",
        ab_run_order=f"off,{candidate_policy},off",
        ab_order_valid=True,
        ab_candidate_policy=candidate_policy,
        ab_invocation_id=invocation_id,
        ab_pair_id=pair_id,
        ab_pair_position=position,
        scene_evidence="save:dogtown-market-static",
        power_source_state="ac",
        power_source_start_state="ac",
        power_source_pre_run_state="ac",
        power_source_end_state="ac",
        power_source_samples=["ac", "ac", "ac"],
        power_source_stable=True,
        thermal_start_c=thermal_start_c,
        thermal_end_c=thermal_end_c,
        thermal_unavailable=False,
        thermal_source_kind="cpu-package",
        thermal_source_id="hwmon:coretemp:Package id 0",
        thermal_source_label="Package id 0",
        run_started_at_s=run_started_at_s,
        run_ended_at_s=run_ended_at_s,
        cooldown_rule="fixed-60s",
        cooldown_enforced=True,
        cooldown_started_at_s=cooldown_started_at_s,
        cooldown_ended_at_s=cooldown_ended_at_s,
        cooldown_elapsed_s=60.0,
    )


def controlled_ab_payload(**kwargs) -> dict[str, object]:
    run = controlled_ab_run(**kwargs)
    payload = asdict(run)
    payload["capture_mode"] = run.capture_mode.value
    return payload


def assert_ab_incomplete(verdict, expected_detail: str | None = None) -> None:
    assert verdict.verdict == PolicyVerdict.INCONCLUSIVE
    assert verdict.reason.startswith("A/B evidence incomplete:")
    if expected_detail:
        assert expected_detail in verdict.reason
    assert "exploratory only; cannot support a BETTER claim" in verdict.reason
    assert verdict.claim_scope is None


def test_parse_mangohud_summary_csv_reads_low_percentile_metrics(tmp_path):
    path = tmp_path / "mangohud-summary.csv"
    write_csv(
        path,
        [
            "0.1% Min FPS",
            "1% Min FPS",
            "97% Percentile FPS",
            "Average FPS",
            "Average Frame Time",
        ],
        [
            {
                "0.1% Min FPS": "24.1",
                "1% Min FPS": "31.2",
                "97% Percentile FPS": "45.8",
                "Average FPS": "42.3",
                "Average Frame Time": "23.6",
            }
        ],
    )

    summary = parse_mangohud_summary_csv(path)

    assert isinstance(summary, MangoHudFpsSummary)
    assert summary.avg_fps == 42.3
    assert summary.one_percent_low_fps == 31.2
    assert summary.point_one_percent_low_fps == 24.1
    assert summary.ninety_seven_percentile_fps == 45.8
    assert summary.avg_frametime_ms == 23.6
    assert summary.capture_mode == CaptureMode.IMPORTED


def test_parse_mangohud_fps_csv_accepts_summary_export(tmp_path):
    path = tmp_path / "mangohud.csv"
    write_csv(
        path,
        [
            "0.1% Min FPS",
            "1% Min FPS",
            "97% Percentile FPS",
            "Average FPS",
            "GPU Load",
            "CPU Load",
            "Average Frame Time",
        ],
        [
            {
                "0.1% Min FPS": "29.3602",
                "1% Min FPS": "29.7643",
                "97% Percentile FPS": "32.9791",
                "Average FPS": "31.9",
                "GPU Load": "88.1",
                "CPU Load": "52.0",
                "Average Frame Time": "31.3",
            }
        ],
    )

    summary = parse_mangohud_fps_csv(path)

    assert summary.avg_fps == 31.9
    assert summary.one_percent_low_fps == 29.7643
    assert summary.point_one_percent_low_fps == 29.3602
    assert summary.ninety_seven_percentile_fps == 32.9791
    assert summary.avg_frametime_ms == 31.3


def test_parse_mangohud_fps_csv_computes_average_and_frame_time_percentiles(tmp_path):
    path = tmp_path / "mangohud.csv"
    rows = [
        {"fps": "30", "frametime": "33.3"},
        {"fps": "40", "frametime": "25.0"},
        {"fps": "50", "frametime": "20.0"},
        {"fps": "60", "frametime": "16.7"},
    ]
    write_csv(path, ["fps", "frametime"], rows)

    summary = parse_mangohud_fps_csv(path)

    assert summary.avg_fps == 45.0
    assert summary.one_percent_low_fps == 30.0
    assert summary.point_one_percent_low_fps == 30.0
    assert summary.avg_frametime_ms == 23.75
    assert summary.p95_frametime_ms == 33.3
    assert summary.p99_frametime_ms == 33.3


def test_parse_mangohud_fps_csv_skips_system_info_preamble(tmp_path):
    # mangoapp per-frame logs begin with a two-line "os,cpu,gpu,..." banner
    # before the real "fps,frametime,..." header; the parser must skip it.
    path = tmp_path / "mangohud.csv"
    path.write_text(
        "os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
        "SteamOS,Intel Core Ultra 7 258V,,32365604,6.16.12-valve24.4,,powersave\n"
        "fps,frametime,cpu_load,gpu_load,elapsed\n"
        "30,33.3,50,71,514243727\n"
        "40,25.0,50,71,614426721\n"
        "50,20.0,50,71,714426721\n"
        "60,16.7,50,71,814426721\n"
    )

    summary = parse_mangohud_fps_csv(path)

    assert summary.avg_fps == 45.0
    assert summary.avg_frametime_ms == 23.75
    assert summary.p95_frametime_ms == 33.3
    assert summary.p99_frametime_ms == 33.3


def test_run_summarize_merges_raw_frametime_percentiles_into_summary(tmp_path):
    # The mangoapp *summary* CSV carries lows/97th but no p95/p99 frametime.
    # With the raw per-frame CSV (system-info preamble) also present, summarize
    # keeps the summary's lows and backfills p95/p99 from the raw CSV.
    summary_csv = tmp_path / "mangohud-summary.csv"
    write_csv(
        summary_csv,
        [
            "0.1% Min FPS",
            "1% Min FPS",
            "97% Percentile FPS",
            "Average FPS",
            "Average Frame Time",
        ],
        [
            {
                "0.1% Min FPS": "24.1",
                "1% Min FPS": "31.2",
                "97% Percentile FPS": "45.8",
                "Average FPS": "42.3",
                "Average Frame Time": "23.6",
            }
        ],
    )
    raw_csv = tmp_path / "mangohud.csv"
    raw_csv.write_text(
        "os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
        "SteamOS,Intel Core Ultra 7 258V,,0,6.16,,powersave\n"
        "fps,frametime,elapsed\n"
        "30,33.3,1\n"
        "40,25.0,2\n"
        "50,20.0,3\n"
        "60,16.7,4\n"
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
            "off",
            "--capture-mode",
            "controlled",
            "--mangohud-csv",
            str(raw_csv),
            "--mangohud-summary-csv",
            str(summary_csv),
            "--duration-s",
            "10",
            "--output",
            str(output),
        ]
    )

    run_summarize(args)

    summary = json.loads((output / "summary.json").read_text())
    # lows/avg kept from the summary CSV
    assert summary["avg_fps"] == 42.3
    assert summary["one_percent_low_fps"] == 31.2
    assert summary["point_one_percent_low_fps"] == 24.1
    # p95/p99 backfilled from the raw per-frame CSV
    assert summary["p95_frametime_ms"] == 33.3
    assert summary["p99_frametime_ms"] == 33.3


def test_parse_gamescope_fps_target_from_argv_uses_focused_limit_before_separator():
    discovery = parse_gamescope_fps_target_from_argv(
        [
            "gamescope",
            "-w",
            "1920",
            "-h",
            "1200",
            "-r",
            "40",
            "--",
            "game-binary",
            "-r",
            "999",
        ]
    )

    assert isinstance(discovery, FpsTargetDiscovery)
    assert discovery.fps_target == 40.0
    assert discovery.source == "gamescope-cmdline"
    assert discovery.confidence == "medium"
    assert discovery.raw == "-r 40"


def test_parse_gamescope_fps_target_from_argv_ignores_unlimited_or_missing_limit():
    assert parse_gamescope_fps_target_from_argv(["gamescope", "-r", "0"]).fps_target is None
    assert parse_gamescope_fps_target_from_argv(["gamescope", "--", "game"]).source == "unknown"


def test_parse_game_power_jsonl_averages_power_and_counts_actions(tmp_path):
    path = tmp_path / "game-power.jsonl"
    rows = [
        {
            "elapsed_s": 2.0,
            "appid": "1091500",
            "action": "gpu-priority-epp",
            "package_w": 22.0,
            "core_w": 7.0,
            "uncore_w": 9.0,
            "pl1_w": 22,
            "render_busy": 0.8,
        },
        {
            "elapsed_s": 4.0,
            "appid": "1091500",
            "action": "restore",
            "package_w": 20.0,
            "core_w": 6.0,
            "uncore_w": 8.0,
            "pl1_w": 22,
            "render_busy": 0.7,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    summary = parse_game_power_jsonl(path)

    assert isinstance(summary, GamePowerLogSummary)
    assert summary.samples == 2
    assert summary.avg_package_w == 21.0
    assert summary.avg_core_w == 6.5
    assert summary.avg_uncore_w == 8.5
    assert summary.avg_core_share == round(6.5 / 21.0, 3)
    assert summary.avg_uncore_share == round(8.5 / 21.0, 3)
    assert summary.actions == {"gpu-priority-epp": 1, "restore": 1}


def test_summarize_thread_affinity_jsonl_ranks_hot_threads_by_cpu_and_migrations(
    tmp_path,
):
    path = tmp_path / "thread-affinity.jsonl"
    rows = [
        {
            "elapsed_s": 0.0,
            "threads": [
                {
                    "tid": 101,
                    "comm": "GameThread",
                    "cpu_time_s": 10.0,
                    "migration_count": 3,
                    "voluntary_ctxt_switches": 20,
                    "nonvoluntary_ctxt_switches": 4,
                    "current_cpu": 0,
                    "affinity": "0-5",
                    "cgroup": "app-steam-app1091500.scope",
                },
                {
                    "tid": 102,
                    "comm": "Worker",
                    "cpu_time_s": 4.0,
                    "migration_count": 1,
                    "voluntary_ctxt_switches": 7,
                    "nonvoluntary_ctxt_switches": 1,
                    "current_cpu": 4,
                    "affinity": "0-5",
                    "cgroup": "app-steam-app1091500.scope",
                },
            ],
        },
        {
            "elapsed_s": 2.0,
            "threads": [
                {
                    "tid": 101,
                    "comm": "GameThread",
                    "cpu_time_s": 13.5,
                    "migration_count": 8,
                    "voluntary_ctxt_switches": 31,
                    "nonvoluntary_ctxt_switches": 9,
                    "current_cpu": 2,
                    "affinity": "0-5",
                    "cgroup": "app-steam-app1091500.scope",
                },
                {
                    "tid": 102,
                    "comm": "Worker",
                    "cpu_time_s": 4.4,
                    "migration_count": 2,
                    "voluntary_ctxt_switches": 8,
                    "nonvoluntary_ctxt_switches": 1,
                    "current_cpu": 4,
                    "affinity": "0-5",
                    "cgroup": "app-steam-app1091500.scope",
                },
            ],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    summary = summarize_thread_affinity_jsonl(path)

    assert isinstance(summary, ThreadAffinitySummary)
    assert summary.samples == 2
    assert summary.observed_threads == 2
    assert summary.hot_threads[0] == {
        "tid": 101,
        "comm": "GameThread",
        "cpu_time_s_delta": 3.5,
        "migration_delta": 5,
        "voluntary_ctxt_switches_delta": 11,
        "nonvoluntary_ctxt_switches_delta": 5,
        "cpus_seen": [0, 2],
        "affinity_masks": ["0-5"],
        "cgroup": "app-steam-app1091500.scope",
    }


def test_summarize_thread_schedstat_jsonl_ranks_runqueue_wait(tmp_path):
    path = tmp_path / "thread-schedstat.jsonl"
    rows = [
        {
            "elapsed_s": 0.0,
            "threads": [
                {
                    "tid": 101,
                    "comm": "GameThread",
                    "run_time_ns": 10_000_000_000,
                    "runqueue_wait_ns": 100_000_000,
                    "timeslices": 100,
                    "current_cpu": 0,
                    "cgroup": "app-steam-app1091500.scope",
                },
                {
                    "tid": 102,
                    "comm": "Worker",
                    "run_time_ns": 4_000_000_000,
                    "runqueue_wait_ns": 10_000_000,
                    "timeslices": 30,
                    "current_cpu": 2,
                    "cgroup": "app-steam-app1091500.scope",
                },
            ],
        },
        {
            "elapsed_s": 2.0,
            "threads": [
                {
                    "tid": 101,
                    "comm": "GameThread",
                    "run_time_ns": 13_000_000_000,
                    "runqueue_wait_ns": 240_000_000,
                    "timeslices": 130,
                    "current_cpu": 1,
                    "cgroup": "app-steam-app1091500.scope",
                },
                {
                    "tid": 102,
                    "comm": "Worker",
                    "run_time_ns": 4_500_000_000,
                    "runqueue_wait_ns": 15_000_000,
                    "timeslices": 35,
                    "current_cpu": 2,
                    "cgroup": "app-steam-app1091500.scope",
                },
            ],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    summary = summarize_thread_schedstat_jsonl(path)

    assert isinstance(summary, ThreadSchedstatSummary)
    assert summary.samples == 2
    assert summary.observed_threads == 2
    assert summary.hot_threads[0] == {
        "tid": 101,
        "comm": "GameThread",
        "run_time_s_delta": 3.0,
        "runqueue_wait_ms_delta": 140.0,
        "timeslices_delta": 30,
        "runqueue_wait_per_slice_ms": 4.667,
        "runqueue_wait_ratio": 0.045,
        "cpus_seen": [0, 1],
        "cgroup": "app-steam-app1091500.scope",
    }


def test_run_summarize_emits_color_ledger_from_thread_artifacts(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    write_csv(
        mangohud,
        ["fps", "frametime"],
        [{"fps": "30", "frametime": "33.3"}, {"fps": "31", "frametime": "32.0"}],
    )
    schedstat = tmp_path / "thread-schedstat.jsonl"
    fg = "0::/user.slice/app-steam-app1091500-1.scope"
    schedstat.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
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
            ]
        )
        + "\n"
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
            "--duration-s",
            "10",
            "--output",
            str(output),
        ]
    )

    run_summarize(args)

    ledger = json.loads((output / "color-ledger.json").read_text())
    colors = {entry["role_key"]: entry["color"] for entry in ledger["entries"]}
    assert colors["foreground-game:renderthread"] == "A"
    entry = ledger["entries"][0]
    assert entry["actuator_state"] == "advisory"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["color_ledger_json"] is True


def test_summarize_process_cgroups_jsonl_ranks_background_cpu_candidates(tmp_path):
    path = tmp_path / "process-cgroups.jsonl"
    rows = [
        {
            "elapsed_s": 0.0,
            "processes": [
                {
                    "pid": 101,
                    "comm": "Cyberpunk2077",
                    "cpu_time_s": 20.0,
                    "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                },
                {
                    "pid": 201,
                    "comm": "steamwebhelper",
                    "cpu_time_s": 10.0,
                    "cgroup": "0::/user.slice/app-steam-client.scope",
                },
                {
                    "pid": 301,
                    "comm": "mangoapp",
                    "cpu_time_s": 3.0,
                    "cgroup": "0::/user.slice/gamescope-mangoapp.service",
                },
            ],
        },
        {
            "elapsed_s": 2.0,
            "processes": [
                {
                    "pid": 101,
                    "comm": "Cyberpunk2077",
                    "cpu_time_s": 24.0,
                    "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                },
                {
                    "pid": 201,
                    "comm": "steamwebhelper",
                    "cpu_time_s": 12.5,
                    "cgroup": "0::/user.slice/app-steam-client.scope",
                },
                {
                    "pid": 301,
                    "comm": "mangoapp",
                    "cpu_time_s": 3.4,
                    "cgroup": "0::/user.slice/gamescope-mangoapp.service",
                },
            ],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    summary = summarize_process_cgroups_jsonl(path, appid="1091500")

    assert isinstance(summary, ProcessCgroupSummary)
    assert summary.samples == 2
    assert summary.observed_processes == 3
    assert summary.foreground_processes == 1
    assert summary.background_candidates[0] == {
        "cgroup": "0::/user.slice/app-steam-client.scope",
        "classification": "steam-helper",
        "cpu_time_s_delta": 2.5,
        "process_count": 1,
        "pids": [201],
        "commands": ["steamwebhelper"],
    }


def test_build_background_shaping_advice_outputs_observe_only_candidates(tmp_path):
    process_cgroups = ProcessCgroupSummary(
        samples=2,
        observed_processes=3,
        foreground_processes=1,
        background_candidates=[
            {
                "cgroup": "0::/user.slice/app-steam-client.scope",
                "classification": "steam-helper",
                "cpu_time_s_delta": 2.5,
                "process_count": 1,
                "pids": [201],
                "commands": ["steamwebhelper"],
            },
            {
                "cgroup": "0::/user.slice/gamescope-mangoapp.service",
                "classification": "gamescope-helper",
                "cpu_time_s_delta": 0.4,
                "process_count": 1,
                "pids": [301],
                "commands": ["mangoapp"],
            },
        ],
    )

    advice = build_background_shaping_advice(
        appid="1091500",
        process_cgroups=process_cgroups,
        avg_core_share=0.43,
        avg_render_busy=0.91,
        fps_target=40.0,
        avg_fps=38.5,
    )

    assert advice["mode"] == "observe-only"
    assert advice["write_policy"] == "disabled"
    assert advice["appid"] == "1091500"
    assert advice["candidates"][0]["suggested_action"] == "future-cpu-weight-candidate"
    assert "background/helper CPU time is visible outside the foreground app cgroup" in advice[
        "reasons"
    ]


def test_summarize_restore_affinity_json_counts_threads_cgroups_and_files(tmp_path):
    path = tmp_path / "restore-affinity.json"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [
                    {
                        "pid": 101,
                        "tid": 101,
                        "comm": "GameThread",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-3",
                    },
                    {
                        "pid": 101,
                        "tid": 102,
                        "comm": "Worker",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-3",
                    },
                ],
                "cgroups": [
                    {
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "files": {
                            "cpu.uclamp.min": "0.00",
                            "cpu.uclamp.max": "max",
                            "cpuset.cpus": "",
                            "cpuset.cpus.effective": "0-3",
                        },
                    }
                ],
            }
        )
        + "\n"
    )

    summary = summarize_restore_affinity_json(path)

    assert isinstance(summary, RestoreAffinitySummary)
    assert summary.thread_count == 2
    assert summary.cgroup_count == 1
    assert summary.cgroups == ["0::/user.slice/app-steam-app1091500.scope"]
    assert summary.files == [
        "cpu.uclamp.max",
        "cpu.uclamp.min",
        "cpuset.cpus",
        "cpuset.cpus.effective",
    ]
    assert summary.cgroup_files == {
        "0::/user.slice/app-steam-app1091500.scope": [
            "cpu.uclamp.max",
            "cpu.uclamp.min",
            "cpuset.cpus",
            "cpuset.cpus.effective",
        ]
    }
    assert summary.cgroup_file_values == {
        "0::/user.slice/app-steam-app1091500.scope": {
            "cpu.uclamp.max": "max",
            "cpu.uclamp.min": "0.00",
            "cpuset.cpus": "",
            "cpuset.cpus.effective": "0-3",
        }
    }


def test_apply_background_shaping_writes_only_lowers_helper_cgroups(tmp_path):
    foreground = tmp_path / "foreground"
    helper = tmp_path / "helper"
    already_lower = tmp_path / "already-lower"
    broad_user = tmp_path / "broad-user"
    for path in (foreground, helper, already_lower, broad_user):
        path.mkdir()
        (path / "cpu.weight").write_text("100\n")
    (already_lower / "cpu.weight").write_text("50\n")

    restore = tmp_path / "restore-affinity.json"
    restore.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [],
                "cgroups": [
                    {
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "path": str(foreground),
                        "files": {"cpu.weight": "100"},
                    },
                    {
                        "cgroup": "0::/user.slice/app-steam-client.scope",
                        "path": str(helper),
                        "files": {"cpu.weight": "100"},
                    },
                    {
                        "cgroup": "0::/user.slice/steamwebhelper.scope",
                        "path": str(already_lower),
                        "files": {"cpu.weight": "50"},
                    },
                    {
                        "cgroup": "0::/user.slice/",
                        "path": str(broad_user),
                        "files": {"cpu.weight": "100"},
                    },
                ],
            }
        )
        + "\n"
    )
    writes = tmp_path / "background-shaping-writes.json"

    payload = apply_background_shaping_writes(
        restore,
        writes,
        appid="1091500",
        variant="cpu-weight-80",
    )

    assert (foreground / "cpu.weight").read_text() == "100\n"
    assert (helper / "cpu.weight").read_text() == "80\n"
    assert (already_lower / "cpu.weight").read_text() == "50\n"
    assert (broad_user / "cpu.weight").read_text() == "100\n"
    assert payload["write_policy"] == "guarded-background-shaping"
    assert payload["variant"] == "cpu-weight-80"
    assert payload["writes"] == [
        {
            "cgroup": "0::/user.slice/app-steam-client.scope",
            "path": str(helper),
            "control_file": "cpu.weight",
            "original_value": "100",
            "proposed_value": "80",
            "status": "written",
            "method": "direct-cgroup-file",
        }
    ]
    assert json.loads(writes.read_text()) == payload


def test_restore_background_shaping_writes_restores_original_values(tmp_path):
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "cpu.weight").write_text("80\n")
    writes = tmp_path / "background-shaping-writes.json"
    writes.write_text(
        json.dumps(
            {
                "mode": "background-shaping-writes",
                "write_policy": "guarded-background-shaping",
                "writes": [
                    {
                        "cgroup": "0::/user.slice/app-steam-client.scope",
                        "path": str(helper),
                        "control_file": "cpu.weight",
                        "original_value": "100",
                        "proposed_value": "80",
                        "status": "written",
                    }
                ],
            }
        )
        + "\n"
    )
    output = tmp_path / "background-shaping-restore.json"

    payload = restore_background_shaping_writes(writes, output)

    assert (helper / "cpu.weight").read_text() == "100\n"
    assert payload["restored"] is True
    assert payload["restores"] == [
        {
            "cgroup": "0::/user.slice/app-steam-client.scope",
            "path": str(helper),
            "control_file": "cpu.weight",
            "restored_value": "100",
            "current_value": "100",
            "status": "restored",
            "method": "direct-cgroup-file",
        }
    ]
    assert json.loads(output.read_text()) == payload


def test_background_shaping_writer_uses_systemd_user_property_when_cpu_file_missing(
    tmp_path,
):
    helper = tmp_path / "steam-launcher.service"
    helper.mkdir()
    restore = tmp_path / "restore-affinity.json"
    restore.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [],
                "cgroups": [
                    {
                        "cgroup": (
                            "0::/user.slice/user-1000.slice/user@1000.service/"
                            "app.slice/steam-launcher.service"
                        ),
                        "path": str(helper),
                        "files": {"cgroup.type": "domain"},
                    }
                ],
            }
        )
        + "\n"
    )
    state = {"CPUWeight": "[not set]"}
    calls: list[list[str]] = []

    def runner(command: list[str]) -> str:
        calls.append(command)
        if "show" in command:
            return f"CPUWeight={state['CPUWeight']}\n"
        if "set-property" in command:
            value = command[-1].split("=", 1)[1]
            state["CPUWeight"] = value or "[not set]"
            return ""
        raise AssertionError(f"unexpected command: {command}")

    writes = tmp_path / "background-shaping-writes.json"
    apply_payload = apply_background_shaping_writes(
        restore,
        writes,
        appid="1091500",
        variant="cpu-weight-80",
        command_runner=runner,
    )

    assert apply_payload["writes"] == [
        {
            "cgroup": (
                "0::/user.slice/user-1000.slice/user@1000.service/"
                "app.slice/steam-launcher.service"
            ),
            "path": str(helper),
            "control_file": "cpu.weight",
            "original_value": "[not set]",
            "proposed_value": "80",
            "status": "written",
            "method": "systemd-user-property",
            "unit": "steam-launcher.service",
            "property": "CPUWeight",
        }
    ]
    assert state["CPUWeight"] == "80"

    output = tmp_path / "background-shaping-restore.json"
    restore_payload = restore_background_shaping_writes(
        writes,
        output,
        command_runner=runner,
    )

    assert restore_payload["restored"] is True
    assert restore_payload["restores"] == [
        {
            "cgroup": (
                "0::/user.slice/user-1000.slice/user@1000.service/"
                "app.slice/steam-launcher.service"
            ),
            "path": str(helper),
            "control_file": "cpu.weight",
            "restored_value": "[not set]",
            "current_value": "[not set]",
            "status": "restored",
            "method": "systemd-user-property",
            "unit": "steam-launcher.service",
            "property": "CPUWeight",
        }
    ]
    assert state["CPUWeight"] == "[not set]"
    assert any(command[-1] == "CPUWeight=80" for command in calls)
    assert any(command[-1] == "CPUWeight=" for command in calls)


def test_background_shaping_writer_prefers_systemd_user_property_for_user_services(
    tmp_path,
):
    helper = tmp_path / "steam-launcher.service"
    helper.mkdir()
    (helper / "cpu.weight").write_text("100\n")
    restore = tmp_path / "restore-affinity.json"
    restore.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [],
                "cgroups": [
                    {
                        "cgroup": (
                            "0::/user.slice/user-1000.slice/user@1000.service/"
                            "app.slice/steam-launcher.service"
                        ),
                        "path": str(helper),
                        "files": {"cpu.weight": "100"},
                    }
                ],
            }
        )
        + "\n"
    )
    state = {"CPUWeight": "[not set]"}
    calls: list[list[str]] = []

    def runner(command: list[str]) -> str:
        calls.append(command)
        if "show" in command:
            return f"CPUWeight={state['CPUWeight']}\n"
        if "set-property" in command:
            value = command[-1].split("=", 1)[1]
            state["CPUWeight"] = value or "[not set]"
            return ""
        raise AssertionError(f"unexpected command: {command}")

    payload = apply_background_shaping_writes(
        restore,
        tmp_path / "background-shaping-writes.json",
        appid="1091500",
        variant="cpu-weight-80",
        command_runner=runner,
    )

    assert (helper / "cpu.weight").read_text() == "100\n"
    assert payload["writes"] == [
        {
            "cgroup": (
                "0::/user.slice/user-1000.slice/user@1000.service/"
                "app.slice/steam-launcher.service"
            ),
            "path": str(helper),
            "control_file": "cpu.weight",
            "original_value": "[not set]",
            "proposed_value": "80",
            "status": "written",
            "method": "systemd-user-property",
            "unit": "steam-launcher.service",
            "property": "CPUWeight",
        }
    ]
    assert any(command[-1] == "CPUWeight=80" for command in calls)


def test_background_shaping_writer_does_not_match_steamos_service_names(tmp_path):
    steamos_service = tmp_path / "steamos-intel-handheld-power-control.service"
    steamos_service.mkdir()
    restore = tmp_path / "restore-affinity.json"
    restore.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [],
                "cgroups": [
                    {
                        "cgroup": (
                            "0::/system.slice/"
                            "steamos-intel-handheld-power-control.service"
                        ),
                        "path": str(steamos_service),
                        "files": {"cgroup.type": "domain"},
                    }
                ],
            }
        )
        + "\n"
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> str:
        calls.append(command)
        return "CPUWeight=[not set]\n"

    payload = apply_background_shaping_writes(
        restore,
        tmp_path / "background-shaping-writes.json",
        appid="1091500",
        variant="cpu-weight-80",
        command_runner=runner,
    )

    assert payload["writes"] == []
    assert calls == []


def test_background_shaping_writer_only_matches_explicit_gamescope_helpers(tmp_path):
    cgroups = {
        "gamescope-session.service": (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/gamescope-session.service"
        ),
        "gamescope-mangoapp.service": (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/gamescope-mangoapp.service"
        ),
        "xdg-desktop-portal-gamescope.service": (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/xdg-desktop-portal-gamescope.service"
        ),
        "ibus-gamescope.service": (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/ibus-gamescope.service"
        ),
        "gamescope-xbindkeys.service": (
            "0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/gamescope-xbindkeys.service"
        ),
    }
    restore_cgroups = []
    for unit, cgroup in cgroups.items():
        path = tmp_path / unit
        path.mkdir()
        restore_cgroups.append(
            {
                "cgroup": cgroup,
                "path": str(path),
                "files": {"cgroup.type": "domain"},
            }
        )
    restore = tmp_path / "restore-affinity.json"
    restore.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [],
                "cgroups": restore_cgroups,
            }
        )
        + "\n"
    )
    state = {
        "gamescope-session.service": "[not set]",
        "gamescope-mangoapp.service": "[not set]",
        "xdg-desktop-portal-gamescope.service": "[not set]",
        "ibus-gamescope.service": "[not set]",
        "gamescope-xbindkeys.service": "[not set]",
    }

    def runner(command: list[str]) -> str:
        unit = command[-3] if "show" in command else command[-2]
        if "show" in command:
            return f"CPUWeight={state[unit]}\n"
        if "set-property" in command:
            state[unit] = command[-1].split("=", 1)[1] or "[not set]"
            return ""
        raise AssertionError(f"unexpected command: {command}")

    payload = apply_background_shaping_writes(
        restore,
        tmp_path / "background-shaping-writes.json",
        appid="1091500",
        variant="cpu-weight-80",
        command_runner=runner,
    )

    assert [item["unit"] for item in payload["writes"]] == [
        "gamescope-session.service",
        "gamescope-mangoapp.service",
    ]
    assert state == {
        "gamescope-session.service": "80",
        "gamescope-mangoapp.service": "80",
        "xdg-desktop-portal-gamescope.service": "[not set]",
        "ibus-gamescope.service": "[not set]",
        "gamescope-xbindkeys.service": "[not set]",
    }


def test_apply_foreground_affinity_writes_only_matching_foreground_role(tmp_path):
    restore = tmp_path / "restore-affinity.json"
    output = tmp_path / "foreground-affinity-writes.json"
    restore.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "threads": [
                    {
                        "pid": 200,
                        "tid": 201,
                        "comm": "Worker Thread",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-7",
                    },
                    {
                        "pid": 300,
                        "tid": 301,
                        "comm": "Render Thread",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-7",
                    },
                    {
                        "pid": 400,
                        "tid": 401,
                        "comm": "Worker Thread",
                        "cgroup": "0::/user.slice/steam.service",
                        "cpus_allowed_list": "0-7",
                    },
                ],
            }
        )
    )
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="pid 201's current affinity list: 0,1\n",
            stderr="",
        )

    payload = apply_foreground_affinity_writes(
        restore,
        output,
        role_key="foreground-game:worker-thread",
        preferred_cpus="0,1",
        variant="foreground-role-compact",
        command_runner=runner,
        proc_root=None,
    )

    assert payload["mode"] == "foreground-affinity-writes"
    assert payload["write_policy"] == "guarded-foreground-affinity"
    assert payload["role_key"] == "foreground-game:worker-thread"
    assert payload["preferred_cpus"] == "0,1"
    assert payload["matched_thread_count"] == 1
    assert payload["written_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["valid"] is True
    assert payload["writes"] == [
        {
            "tid": 201,
            "pid": 200,
            "comm": "Worker Thread",
            "role_key": "foreground-game:worker-thread",
            "original_cpus_allowed_list": "0-7",
            "proposed_cpus": "0,1",
            "returncode": 0,
            "status": "written",
            "stdout": "pid 201's current affinity list: 0,1\n",
            "stderr": "",
        }
    ]
    assert commands == [["taskset", "-pc", "0,1", "201"]]
    assert json.loads(output.read_text()) == payload


def test_apply_foreground_affinity_writes_rejects_unsafe_inputs(tmp_path):
    restore = tmp_path / "restore-affinity.json"
    restore.write_text(json.dumps({"threads": []}))

    unsafe_inputs = [
        ("background-helper:worker-thread", "0,1"),
        ("foreground-game:worker-thread", ""),
        ("foreground-game:worker-thread", "-1"),
    ]
    for role_key, preferred_cpus in unsafe_inputs:
        try:
            apply_foreground_affinity_writes(
                restore,
                tmp_path / "writes.json",
                role_key=role_key,
                preferred_cpus=preferred_cpus,
                variant="foreground-role-compact",
                proc_root=None,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {role_key!r}")


def test_restore_foreground_affinity_writes_restores_original_masks(tmp_path):
    writes = tmp_path / "foreground-affinity-writes.json"
    output = tmp_path / "foreground-affinity-restore.json"
    writes.write_text(
        json.dumps(
            {
                "mode": "foreground-affinity-writes",
                "write_policy": "guarded-foreground-affinity",
                "writes": [
                    {
                        "tid": 201,
                        "pid": 200,
                        "comm": "Worker Thread",
                        "role_key": "foreground-game:worker-thread",
                        "original_cpus_allowed_list": "0-7",
                        "proposed_cpus": "0,1",
                        "status": "written",
                    },
                    {
                        "tid": 301,
                        "original_cpus_allowed_list": "0-7",
                        "status": "skipped",
                    },
                ],
            }
        )
    )
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "pid 201's current affinity list: 0,1\n"
                "pid 201's new affinity list: 0-7\n"
            ),
            stderr="",
        )

    payload = restore_foreground_affinity_writes(
        writes,
        output,
        command_runner=runner,
        proc_root=None,
    )

    assert payload["mode"] == "foreground-affinity-restore"
    assert payload["write_policy"] == "restore-foreground-affinity"
    assert payload["restored"] is True
    assert payload["restores"][0]["status"] == "restored"
    assert commands == [["taskset", "-pc", "0-7", "201"]]
    assert json.loads(output.read_text()) == payload


def test_resolve_foreground_affinity_candidate_accepts_aggregate_report(tmp_path):
    report = tmp_path / "aggregate.json"
    report.write_text(
        json.dumps(
            {
                "comparisons": [
                    {
                        "candidate_policy": "gpu-priority",
                        "affinity_experiment_plan": {
                            "mode": "ready-for-guarded-experiment",
                            "write_policy": "disabled",
                            "candidates": [
                                {
                                    "role_key": "foreground-game:worker-thread",
                                    "guarded_variant": "foreground-role-compact",
                                    "preferred_cpus": [0, 1],
                                }
                            ],
                        },
                    }
                ]
            }
        )
    )

    assert resolve_foreground_affinity_candidate(report) == {
        "role_key": "foreground-game:worker-thread",
        "preferred_cpus": "0,1",
        "guarded_variant": "foreground-role-compact",
    }


def test_apply_foreground_affinity_writes_fails_closed_without_matching_threads(tmp_path):
    restore = tmp_path / "restore-affinity.json"
    output = tmp_path / "foreground-affinity-writes.json"
    restore.write_text(
        json.dumps(
            {
                "threads": [
                    {
                        "pid": 400,
                        "tid": 401,
                        "comm": "Worker Thread",
                        "cgroup": "0::/user.slice/steam.service",
                        "cpus_allowed_list": "0-7",
                    }
                ]
            }
        )
    )

    payload = apply_foreground_affinity_writes(
        restore,
        output,
        role_key="foreground-game:worker-thread",
        preferred_cpus="0,1",
        variant="foreground-role-compact",
        proc_root=None,
    )

    assert payload["matched_thread_count"] == 0
    assert payload["written_count"] == 0
    assert payload["valid"] is False
    assert json.loads(output.read_text())["valid"] is False


def test_apply_foreground_affinity_writes_records_partial_failure(tmp_path):
    restore = tmp_path / "restore-affinity.json"
    output = tmp_path / "foreground-affinity-writes.json"
    restore.write_text(
        json.dumps(
            {
                "threads": [
                    {
                        "pid": 200,
                        "tid": 201,
                        "comm": "Worker Thread",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-7",
                    },
                    {
                        "pid": 200,
                        "tid": 202,
                        "comm": "Worker Thread",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-7",
                    },
                ]
            }
        )
    )
    calls = 0

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            0 if calls == 1 else 1,
            stdout="",
            stderr="" if calls == 1 else "no such task",
        )

    payload = apply_foreground_affinity_writes(
        restore,
        output,
        role_key="foreground-game:worker-thread",
        preferred_cpus="0,1",
        variant="foreground-role-compact",
        command_runner=runner,
        proc_root=None,
    )

    assert payload["matched_thread_count"] == 2
    assert payload["written_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["partial_failure"] is True
    assert payload["valid"] is False
    assert [item["status"] for item in payload["writes"]] == ["written", "write-failed"]


def test_restore_foreground_affinity_writes_detects_mismatch(tmp_path):
    writes = tmp_path / "foreground-affinity-writes.json"
    output = tmp_path / "foreground-affinity-restore.json"
    writes.write_text(
        json.dumps(
            {
                "writes": [
                    {
                        "tid": 201,
                        "pid": 200,
                        "comm": "Worker Thread",
                        "role_key": "foreground-game:worker-thread",
                        "original_cpus_allowed_list": "0-7",
                        "status": "written",
                    }
                ]
            }
        )
    )

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="pid 201's current affinity list: 0,1\n",
            stderr="",
        )

    payload = restore_foreground_affinity_writes(
        writes,
        output,
        command_runner=runner,
        proc_root=None,
    )

    assert payload["restored"] is False
    assert payload["restores"][0]["status"] == "restore-mismatch"


def test_summarize_foreground_affinity_artifacts_requires_clean_restore(tmp_path):
    writes = tmp_path / "foreground-affinity-writes.json"
    restore = tmp_path / "foreground-affinity-restore.json"
    writes.write_text(
        json.dumps(
            {
                "role_key": "foreground-game:worker-thread",
                "preferred_cpus": "0,1",
                "matched_thread_count": 2,
                "written_count": 2,
                "failed_count": 0,
            }
        )
    )
    restore.write_text(json.dumps({"restored": True}))

    summary = summarize_foreground_affinity_artifacts(writes, restore)

    assert summary == {
        "role_key": "foreground-game:worker-thread",
        "preferred_cpus": "0,1",
        "matched_thread_count": 2,
        "written_count": 2,
        "failed_count": 0,
        "restore_restored": True,
        "valid_evidence": True,
    }
    restore.write_text(json.dumps({"restored": False}))
    assert summarize_foreground_affinity_artifacts(writes, restore)["valid_evidence"] is False


def test_merge_run_summary_preserves_boolean_foreground_affinity_evidence():
    summary = merge_run_summary(
        appid="1903340",
        tdp_w=17,
        policy="gpu-priority-affinity",
        fps=MangoHudFpsSummary(avg_fps=25.7, capture_mode=CaptureMode.CONTROLLED),
        power=None,
        foreground_affinity={
            "role_key": "foreground-game:foreground-work",
            "preferred_cpus": "2,3",
            "matched_thread_count": 2,
            "written_count": 2,
            "failed_count": 0,
            "restore_restored": True,
            "valid_evidence": True,
        },
        restored=True,
    )

    assert summary.foreground_affinity_restore_restored is True
    assert summary.foreground_affinity_valid_evidence is True


def test_resolve_foreground_affinity_candidate_rejects_unsafe_shapes(tmp_path):
    cases = [
        {"mode": "observe-only", "candidates": []},
        {
            "mode": "ready-for-guarded-experiment",
            "candidates": [{"role_key": "background:worker", "preferred_cpus": [0, 1]}],
        },
        {
            "mode": "ready-for-guarded-experiment",
            "candidates": [
                {
                    "role_key": "foreground-game:worker-thread",
                    "guarded_variant": "wrong",
                    "preferred_cpus": [0, 1],
                }
            ],
        },
        {
            "mode": "ready-for-guarded-experiment",
            "candidates": [
                {
                    "role_key": "foreground-game:worker-thread",
                    "guarded_variant": "foreground-role-compact",
                    "preferred_cpus": [0],
                    "thread_count_median": 2,
                }
            ],
        },
    ]
    for index, payload in enumerate(cases):
        path = tmp_path / f"plan-{index}.json"
        path.write_text(json.dumps(payload))
        try:
            resolve_foreground_affinity_candidate(path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for case {index}")

    raw = tmp_path / "raw.json"
    raw.write_text(
        json.dumps(
            {
                "mode": "ready-for-guarded-experiment",
                "candidates": [
                    {
                        "role_key": "foreground-game:render-thread",
                        "guarded_variant": "foreground-role-compact",
                        "preferred_cpus": "0-1",
                    }
                ],
            }
        )
    )
    assert resolve_foreground_affinity_candidate(raw)["preferred_cpus"] == "0,1"


def test_summarize_cpu_topology_groups_policy_domains_and_core_classes(tmp_path):
    path = tmp_path / "cpu-topology.json"
    path.write_text(
        json.dumps(
            {
                "cpus": [
                    {
                        "cpu": 0,
                        "online": True,
                        "policy": "policy0",
                        "core_type": "p-core",
                        "capacity": 1024,
                        "thread_siblings": "0,4",
                        "core_id": 0,
                        "physical_package_id": 0,
                        "max_freq_khz": 4700000,
                        "epp": "balance_performance",
                    },
                    {
                        "cpu": 1,
                        "online": True,
                        "policy": "policy1",
                        "core_type": "e-core",
                        "capacity": 640,
                        "thread_siblings": "1",
                        "core_id": 1,
                        "physical_package_id": 0,
                        "max_freq_khz": 3200000,
                        "epp": "balance_power",
                    },
                    {
                        "cpu": 2,
                        "online": False,
                        "policy": "policy1",
                        "core_type": "e-core",
                        "capacity": 640,
                        "thread_siblings": "2",
                    },
                ]
            }
        )
        + "\n"
    )

    summary = summarize_cpu_topology(path)

    assert isinstance(summary, CpuTopologySummary)
    assert summary.cpu_count == 3
    assert summary.online_cpu_count == 2
    assert summary.core_class_counts == {"e-core": 2, "p-core": 1}
    assert summary.policy_domains == [
        {
            "policy": "policy0",
            "cpus": [0],
            "core_classes": ["p-core"],
            "max_freq_khz": 4700000,
            "epp": "balance_performance",
        },
        {
            "policy": "policy1",
            "cpus": [1, 2],
            "core_classes": ["e-core"],
            "max_freq_khz": 3200000,
            "epp": "balance_power",
        },
    ]


def test_build_affinity_advice_outputs_observe_only_preferred_set_candidates(tmp_path):
    topology_path = tmp_path / "cpu-topology.json"
    topology_path.write_text(
        json.dumps(
            {
                "cpus": [
                    {"cpu": 0, "online": True, "core_type": "p-core", "capacity": 1024},
                    {"cpu": 1, "online": True, "core_type": "p-core", "capacity": 1024},
                    {"cpu": 2, "online": True, "core_type": "e-core", "capacity": 640},
                    {"cpu": 3, "online": True, "core_type": "e-core", "capacity": 640},
                ]
            }
        )
        + "\n"
    )
    thread_affinity = ThreadAffinitySummary(
        samples=2,
        observed_threads=2,
        hot_threads=[
            {
                "tid": 101,
                "comm": "GameThread",
                "cpu_time_s_delta": 3.5,
                "migration_delta": 5,
                "voluntary_ctxt_switches_delta": 11,
                "nonvoluntary_ctxt_switches_delta": 5,
                "cpus_seen": [0, 2],
                "affinity_masks": ["0-3"],
                "cgroup": "app-steam-app1091500.scope",
            },
            {
                "tid": 102,
                "comm": "Worker",
                "cpu_time_s_delta": 0.4,
                "migration_delta": 1,
                "voluntary_ctxt_switches_delta": 1,
                "nonvoluntary_ctxt_switches_delta": 0,
                "cpus_seen": [3],
                "affinity_masks": ["0-3"],
                "cgroup": "app-steam-app1091500.scope",
            },
        ],
    )
    thread_schedstat = ThreadSchedstatSummary(
        samples=2,
        observed_threads=1,
        hot_threads=[
            {
                "tid": 101,
                "comm": "GameThread",
                "run_time_s_delta": 3.0,
                "runqueue_wait_ms_delta": 140.0,
                "timeslices_delta": 30,
                "runqueue_wait_per_slice_ms": 4.667,
                "runqueue_wait_ratio": 0.045,
                "cpus_seen": [0, 1],
                "cgroup": "app-steam-app1091500.scope",
            }
        ],
    )

    advice = build_affinity_advice(
        topology=summarize_cpu_topology(topology_path),
        thread_affinity=thread_affinity,
        thread_schedstat=thread_schedstat,
        fps_target=40.0,
        avg_fps=38.5,
        avg_core_share=0.42,
        avg_render_busy=0.93,
    )

    assert advice["mode"] == "observe-only"
    assert advice["write_policy"] == "disabled"
    assert advice["preferred_latency_cpus"] == [0, 1]
    assert advice["ranked_threads"][0]["tid"] == 101
    assert advice["ranked_threads"][0]["classification"] == "latency-hot"
    assert advice["ranked_threads"][0]["runqueue_wait_ms_delta"] == 140.0
    assert advice["ranked_threads"][0]["runqueue_wait_per_slice_ms"] == 4.667
    assert advice["ranked_threads"][0]["migration_harm_score"] > 0
    assert "hard affinity is profiler-only" in advice["reasons"]


def test_build_affinity_advice_groups_threads_by_stable_role_key(tmp_path):
    topology = CpuTopologySummary(
        cpu_count=4,
        online_cpu_count=4,
        core_class_counts={"p-core": 2, "e-core": 2},
        policy_domains=[],
        cpus=[
            {"cpu": 0, "online": True, "core_type": "p-core", "capacity": 1024},
            {"cpu": 1, "online": True, "core_type": "p-core", "capacity": 1024},
            {"cpu": 2, "online": True, "core_type": "e-core", "capacity": 640},
            {"cpu": 3, "online": True, "core_type": "e-core", "capacity": 640},
        ],
    )
    thread_affinity = ThreadAffinitySummary(
        samples=2,
        observed_threads=3,
        hot_threads=[
            {
                "tid": 201,
                "comm": "Worker Thread",
                "cpu_time_s_delta": 1.2,
                "migration_delta": 3,
                "voluntary_ctxt_switches_delta": 4,
                "nonvoluntary_ctxt_switches_delta": 2,
                "cpus_seen": [0, 2],
                "affinity_masks": ["0-3"],
                "cgroup": "0::/user.slice/app-steam-app1091500.scope",
            },
            {
                "tid": 202,
                "comm": "Worker Thread",
                "cpu_time_s_delta": 0.8,
                "migration_delta": 2,
                "voluntary_ctxt_switches_delta": 3,
                "nonvoluntary_ctxt_switches_delta": 1,
                "cpus_seen": [1, 3],
                "affinity_masks": ["0-3"],
                "cgroup": "0::/user.slice/app-steam-app1091500.scope",
            },
            {
                "tid": 301,
                "comm": "Render Thread",
                "cpu_time_s_delta": 0.4,
                "migration_delta": 0,
                "voluntary_ctxt_switches_delta": 1,
                "nonvoluntary_ctxt_switches_delta": 0,
                "cpus_seen": [2],
                "affinity_masks": ["0-3"],
                "cgroup": "0::/user.slice/app-steam-app1091500.scope",
            },
        ],
    )
    thread_schedstat = ThreadSchedstatSummary(
        samples=2,
        observed_threads=2,
        hot_threads=[
            {
                "tid": 201,
                "comm": "Worker Thread",
                "run_time_s_delta": 1.1,
                "runqueue_wait_ms_delta": 80.0,
                "timeslices_delta": 20,
                "runqueue_wait_per_slice_ms": 4.0,
                "runqueue_wait_ratio": 0.068,
                "cpus_seen": [0, 2],
                "cgroup": "0::/user.slice/app-steam-app1091500.scope",
            },
            {
                "tid": 202,
                "comm": "Worker Thread",
                "run_time_s_delta": 0.7,
                "runqueue_wait_ms_delta": 20.0,
                "timeslices_delta": 10,
                "runqueue_wait_per_slice_ms": 2.0,
                "runqueue_wait_ratio": 0.028,
                "cpus_seen": [1, 3],
                "cgroup": "0::/user.slice/app-steam-app1091500.scope",
            },
        ],
    )

    advice = build_affinity_advice(
        topology=topology,
        thread_affinity=thread_affinity,
        thread_schedstat=thread_schedstat,
        fps_target=40.0,
        avg_fps=36.0,
        avg_core_share=0.45,
        avg_render_busy=0.8,
    )

    assert advice["ranked_threads"][0]["role_key"] == "foreground-game:worker-thread"
    assert advice["role_candidates"][0] == {
        "role_key": "foreground-game:worker-thread",
        "comm": "Worker Thread",
        "cgroup_role": "foreground-game",
        "classification": "latency-hot",
        "thread_count": 2,
        "tids": [201, 202],
        "cpu_time_s_delta": 2.0,
        "migration_delta": 5,
        "runqueue_wait_ms_delta": 100.0,
        "runqueue_wait_per_slice_ms_max": 4.0,
        "migration_harm_score_max": advice["ranked_threads"][0]["migration_harm_score"],
        "cpus_seen": [0, 1, 2, 3],
        "preferred_cpu_overlap": [0, 1],
        "suggested_action": "prefer-latency-cpus",
    }


def test_compare_run_summaries_accepts_better_one_percent_low_without_avg_regression():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        capture_mode=CaptureMode.CONTROLLED,
        avg_fps=40.0,
        one_percent_low_fps=30.0,
        p99_frametime_ms=36.0,
        restored=True,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        capture_mode=CaptureMode.CONTROLLED,
        avg_fps=39.8,
        one_percent_low_fps=32.0,
        p99_frametime_ms=35.0,
        restored=True,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.INCONCLUSIVE
    assert verdict.reason.startswith("A/B evidence incomplete:")
    assert "single-run compare is exploratory only" in verdict.reason
    assert "cannot support a BETTER claim" in verdict.reason


def test_compare_run_summaries_accepts_power_saving_when_target_is_sustained():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        capture_mode=CaptureMode.CONTROLLED,
        fps_target=40.0,
        avg_fps=42.0,
        one_percent_low_fps=32.0,
        p99_frametime_ms=35.0,
        avg_package_w=22.0,
        restored=True,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        capture_mode=CaptureMode.CONTROLLED,
        fps_target=40.0,
        avg_fps=40.4,
        one_percent_low_fps=32.1,
        p99_frametime_ms=35.6,
        avg_package_w=20.2,
        restored=True,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.INCONCLUSIVE
    assert verdict.reason.startswith("A/B evidence incomplete:")
    assert "single-run compare is exploratory only" in verdict.reason
    assert "cannot support a BETTER claim" in verdict.reason


def test_compare_run_summaries_rejects_imported_candidate_as_non_automated_ab():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        avg_fps=40.0,
        capture_mode=CaptureMode.CONTROLLED,
        restored=True,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        avg_fps=44.0,
        capture_mode=CaptureMode.IMPORTED,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.NEEDS_CONTROLLED_CAPTURE
    assert "candidate" in verdict.reason


def test_compare_run_summaries_rejects_imported_baseline_as_non_automated_ab():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        avg_fps=40.0,
        capture_mode=CaptureMode.IMPORTED,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        avg_fps=44.0,
        capture_mode=CaptureMode.CONTROLLED,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.NEEDS_CONTROLLED_CAPTURE
    assert "baseline" in verdict.reason


def test_aggregate_run_summaries_uses_medians_and_counts_restore_state():
    runs = [
        RunSummary(
            appid="1091500",
            tdp_w=22,
            policy="off",
            capture_mode=CaptureMode.CONTROLLED,
            avg_fps=54.0,
            one_percent_low_fps=40.0,
            avg_package_w=22.0,
            avg_core_share=0.31,
            restored=True,
        ),
        RunSummary(
            appid="1091500",
            tdp_w=22,
            policy="off",
            capture_mode=CaptureMode.CONTROLLED,
            avg_fps=56.0,
            one_percent_low_fps=42.0,
            avg_package_w=21.8,
            avg_core_share=0.29,
            restored=False,
        ),
        RunSummary(
            appid="1091500",
            tdp_w=22,
            policy="off",
            capture_mode=CaptureMode.CONTROLLED,
            avg_fps=120.0,
            one_percent_low_fps=12.0,
            avg_package_w=22.4,
            avg_core_share=0.35,
            restored=True,
        ),
    ]

    aggregate = aggregate_run_summaries(runs)

    assert aggregate.appid == "1091500"
    assert aggregate.tdp_w == 22
    assert aggregate.policy == "off"
    assert aggregate.capture_mode == CaptureMode.CONTROLLED
    assert aggregate.sample_count == 3
    assert aggregate.restored_count == 2
    assert aggregate.avg_fps_median == 56.0
    assert aggregate.one_percent_low_fps_median == 40.0
    assert aggregate.avg_package_w_median == 22.0
    assert aggregate.avg_core_share_median == 0.31


def test_aggregate_carries_ab_run_order_evidence():
    aggregate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                one_percent_low_fps=43.0,
                thermal_start_c=62.0,
                thermal_end_c=64.0,
            )
        ]
    )

    assert aggregate.ab_order_strategy == "paired-baseline"
    assert aggregate.ab_run_orders == ["off,gpu-priority,off"]
    assert aggregate.ab_candidate_policy == "gpu-priority"
    assert aggregate.ab_pair_ids == ["pair-1"]
    assert aggregate.ab_pair_position_counts == {"candidate": 1}
    assert aggregate.ab_pair_position_counts_by_id == {"pair-1": {"candidate": 1}}
    assert aggregate.scene_evidence == "save:dogtown-market-static"
    assert aggregate.power_source_sample_signatures == ["ac,ac,ac"]
    assert aggregate.thermal_pair_readings_by_id["pair-1"]["candidate"] == {
        "thermal_start_c": 62.0,
        "thermal_end_c": 64.0,
    }
    assert aggregate.run_interval_by_pair_id["pair-1"]["candidate"] == {
        "run_started_at_s": 181.3,
        "run_ended_at_s": 241.3,
    }
    assert aggregate.cooldown_interval_by_pair_id["pair-1"]["candidate"] == {
        "cooldown_started_at_s": 121.0,
        "cooldown_ended_at_s": 181.0,
        "cooldown_elapsed_s": 60.0,
        "cooldown_run_gap_s": 0.3,
    }
    assert aggregate.ab_evidence_complete is True


def test_aggregate_marks_power_source_evidence_gaps_incomplete():
    valid = controlled_ab_run(policy="gpu-priority", position="candidate")
    variants = [
        replace(valid, power_source_state="unknown"),
        replace(valid, power_source_state="mixed", power_source_stable=False),
        replace(valid, power_source_samples=["ac", "ac"]),
        replace(valid, power_source_samples=["ac", "battery", "ac"]),
        replace(valid, power_source_pre_run_state="battery"),
        replace(valid, power_source_stable=False),
    ]

    for run in variants:
        aggregate = aggregate_run_summaries([run])
        assert aggregate.ab_evidence_complete is False


def test_aggregate_marks_thermal_cooldown_order_and_legacy_gaps_incomplete():
    valid = controlled_ab_run(policy="gpu-priority", position="candidate")
    late_run_start = valid.cooldown_ended_at_s + 5.5
    variants = [
        replace(valid, thermal_unavailable=True),
        replace(valid, thermal_start_c=None),
        replace(valid, thermal_source_id=None),
        replace(valid, cooldown_enforced=False),
        replace(valid, cooldown_elapsed_s=59.0),
        replace(valid, cooldown_elapsed_s=61.5),
        replace(valid, cooldown_rule="return-to-60C"),
        replace(
            valid,
            run_started_at_s=late_run_start,
            run_ended_at_s=late_run_start + 60.0,
        ),
        replace(valid, ab_order_strategy="randomized"),
        RunSummary(
            appid="1091500",
            tdp_w=22,
            policy="gpu-priority",
            capture_mode=CaptureMode.CONTROLLED,
            restored=True,
        ),
    ]

    for run in variants:
        aggregate = aggregate_run_summaries([run])
        assert aggregate.ab_evidence_complete is False


def test_compare_policy_aggregates_marks_pairwise_evidence_gaps_incomplete():
    def aggregate_pair(*, before=None, candidate=None, after=None):
        before = before or controlled_ab_run(policy="off", position="baseline-before")
        candidate = candidate or controlled_ab_run(
            policy="gpu-priority",
            position="candidate",
            avg_fps=42.0,
            one_percent_low_fps=33.0,
            thermal_start_c=62.0,
            thermal_end_c=64.0,
        )
        after = after or controlled_ab_run(
            policy="off",
            position="baseline-after",
            thermal_start_c=61.5,
            thermal_end_c=63.5,
        )
        return aggregate_run_summaries([before, after]), aggregate_run_summaries(
            [candidate]
        )

    baseline, candidate = aggregate_pair(
        candidate=replace(
            controlled_ab_run(policy="gpu-priority", position="candidate"),
            thermal_source_id="hwmon:other:Package id 0",
        )
    )
    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "thermal source identity differs",
    )

    baseline, candidate = aggregate_pair(
        candidate=controlled_ab_run(
            policy="gpu-priority",
            position="candidate",
            thermal_start_c=80.0,
            thermal_end_c=83.0,
        )
    )
    # D7: START-temperature parity is the (only) thermal parity gate; a start
    # mismatch still rejects.
    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "aggregate start thermal medians differ too much",
    )

    wrong_order = "off,gpu-priority-cpu-cap,off"
    baseline, candidate = aggregate_pair(
        before=replace(
            controlled_ab_run(policy="off", position="baseline-before"),
            ab_run_order=wrong_order,
        ),
        candidate=replace(
            controlled_ab_run(policy="gpu-priority", position="candidate"),
            ab_run_order=wrong_order,
        ),
        after=replace(
            controlled_ab_run(policy="off", position="baseline-after"),
            ab_run_order=wrong_order,
        ),
    )
    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "paired-baseline run order does not match candidate policy",
    )

    baseline, candidate = aggregate_pair(
        candidate=replace(
            controlled_ab_run(policy="gpu-priority", position="candidate"),
            scene_evidence="save:other-static-scene",
        )
    )
    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "scene evidence differs",
    )


def test_compare_policy_aggregates_marks_pair_shape_gaps_incomplete():
    baseline = aggregate_run_summaries(
        [controlled_ab_run(policy="off", position="baseline-before")]
    )
    candidate = aggregate_run_summaries(
        [controlled_ab_run(policy="gpu-priority", position="candidate")]
    )
    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "baseline sample count must be exactly twice candidate sample count",
    )

    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(policy="off", position="baseline-before"),
            controlled_ab_run(policy="off", position="baseline-after", base_s=-100.0),
        ]
    )
    candidate = aggregate_run_summaries(
        [controlled_ab_run(policy="gpu-priority", position="candidate")]
    )
    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "paired-baseline run intervals are not monotonic",
    )

    non_off_baseline = aggregate_run_summaries(
        [controlled_ab_run(policy="gpu-priority", position="candidate")]
    )
    assert_ab_incomplete(
        compare_policy_aggregates(non_off_baseline, candidate, min_runs=1),
        "baseline policy must be off",
    )


def test_compare_policy_aggregates_never_returns_better_without_complete_ab_evidence():
    baseline = aggregate_run_summaries(
        [
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="off",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=54.0,
                one_percent_low_fps=40.0,
                restored=True,
            ),
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="off",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=55.0,
                one_percent_low_fps=40.5,
                restored=True,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="gpu-priority",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=55.0,
                one_percent_low_fps=43.0,
                restored=True,
            ),
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="gpu-priority",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=56.0,
                one_percent_low_fps=44.0,
                restored=True,
            ),
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=2)

    assert verdict.verdict == PolicyVerdict.INCONCLUSIVE
    assert verdict.reason.startswith("A/B evidence incomplete:")
    assert "exploratory only; cannot support a BETTER claim" in verdict.reason
    assert verdict.claim_scope is None


def test_compare_policy_aggregates_better_includes_claim_scope_at_comparison_json_path():
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(policy="off", position="baseline-before"),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                thermal_start_c=61.5,
                thermal_end_c=63.5,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=42.0,
                one_percent_low_fps=33.0,
                thermal_start_c=62.0,
                thermal_end_c=64.0,
            )
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=1)
    payload = {"comparisons": [{"comparison": asdict(verdict)}]}
    comparison = payload["comparisons"][0]["comparison"]

    assert comparison["verdict"] == PolicyVerdict.BETTER
    assert comparison["thermal_pair_start_delta_max_c"] == 1.0
    assert comparison["thermal_pair_end_delta_max_c"] == 1.0
    assert comparison["cooldown_run_gap_s_max"] == 0.3
    assert comparison["cooldown_interval_reuse_count"] == 0
    assert comparison["claim_scope"]["appid"] == "1091500"
    assert comparison["claim_scope"]["candidate_policy"] == "gpu-priority"
    assert comparison["claim_scope"]["pair_count"] == 1
    assert (
        comparison["claim_scope"]["evidence_boundary"]
        == "scene/profile-specific controlled result; not a general performance claim"
    )
    assert (
        "BETTER (scene/profile-specific controlled result; not a general performance claim)"
        in comparison["human_summary"]
    )
    assert (
        "guarded foreground-game artifacts are required for this captured profile only"
        in comparison["human_summary"]
    )


def test_compare_policy_aggregates_thermal_gate_is_start_only_cooler_end_passes():
    # D7: a power-saving candidate necessarily ends cooler; gating on END temps
    # made a BETTER power verdict structurally unreachable. With matched START
    # temps, a candidate that ends 7 C cooler must PASS the thermal gate.
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                thermal_start_c=61.0,
                thermal_end_c=70.0,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                thermal_start_c=61.0,
                thermal_end_c=70.0,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=42.0,
                one_percent_low_fps=33.0,  # +10% 1% low -> BETTER
                thermal_start_c=61.0,  # matched start
                thermal_end_c=63.0,  # 7 C cooler at the end
            )
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=1)

    assert verdict.verdict == PolicyVerdict.BETTER
    # END delta is reported context, not a rejection reason.
    assert verdict.thermal_start_delta_c == 0.0
    assert verdict.thermal_end_delta_c == 7.0
    assert verdict.thermal_pair_end_delta_max_c == 7.0
    assert verdict.claim_scope["thermal_end_delta_c"] == 7.0
    assert verdict.claim_scope["thermal_start_delta_c"] == 0.0


def test_compare_policy_aggregates_thermal_gate_still_rejects_start_mismatch():
    # D7: START-temperature parity (the pairing confound control) is preserved
    # exactly -- a candidate that STARTS 7 C hotter than its paired baselines is
    # still rejected even though its ends match.
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                thermal_start_c=61.0,
                thermal_end_c=63.0,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                thermal_start_c=61.0,
                thermal_end_c=63.0,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=42.0,
                one_percent_low_fps=33.0,
                thermal_start_c=68.0,  # start mismatch (+7 C)
                thermal_end_c=63.0,  # ends matched
            )
        ]
    )

    assert_ab_incomplete(
        compare_policy_aggregates(baseline, candidate, min_runs=1),
        "aggregate start thermal medians differ too much",
    )


def test_compare_policy_aggregates_non_better_has_null_claim_scope():
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(policy="off", position="baseline-before"),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                thermal_start_c=61.5,
                thermal_end_c=63.5,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=42.0,
                one_percent_low_fps=30.1,
                thermal_start_c=62.0,
                thermal_end_c=64.0,
            )
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=1)
    payload = {"comparisons": [{"comparison": asdict(verdict)}]}
    comparison = payload["comparisons"][0]["comparison"]

    assert comparison["verdict"] == PolicyVerdict.INCONCLUSIVE
    assert comparison["claim_scope"] is None
    assert comparison["human_summary"] is None
    assert comparison["thermal_pair_start_delta_max_c"] == 1.0
    assert comparison["thermal_pair_end_delta_max_c"] == 1.0
    assert comparison["cooldown_run_gap_s_max"] == 0.3


def test_compare_policy_aggregates_requires_repeated_controlled_runs():
    baseline = aggregate_run_summaries(
        [
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="off",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=54.0,
                one_percent_low_fps=40.0,
                restored=True,
            )
        ]
    )
    candidate = aggregate_run_summaries(
        [
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="gpu-priority",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=57.0,
                one_percent_low_fps=44.0,
                restored=True,
            ),
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="gpu-priority",
                capture_mode=CaptureMode.CONTROLLED,
                avg_fps=58.0,
                one_percent_low_fps=45.0,
                restored=True,
            ),
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=2)

    assert verdict.verdict == PolicyVerdict.INCONCLUSIVE
    assert "baseline has 1 run" in verdict.reason


def test_compare_policy_aggregates_accepts_median_low_improvement():
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                avg_fps=54.0,
                one_percent_low_fps=40.0,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                avg_fps=55.0,
                one_percent_low_fps=40.5,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=54.2,
                one_percent_low_fps=40.2,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=55.2,
                one_percent_low_fps=40.7,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=55.0,
                one_percent_low_fps=43.0,
            ),
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=56.0,
                one_percent_low_fps=44.0,
            ),
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=2)

    assert verdict.verdict == PolicyVerdict.BETTER
    assert "median 1% low improved" in verdict.reason


def test_compare_policy_aggregates_rejects_affinity_candidate_without_valid_evidence():
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                avg_fps=54.0,
                one_percent_low_fps=40.0,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                avg_fps=55.0,
                one_percent_low_fps=40.5,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority-affinity",
                position="candidate",
                avg_fps=57.0,
                one_percent_low_fps=44.0,
                foreground_affinity_valid_evidence=False,
                foreground_affinity_write_count=0,
                foreground_affinity_failed_count=0,
            )
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=1)

    assert verdict.verdict == PolicyVerdict.REJECTED
    assert "foreground affinity evidence did not pass" in verdict.reason
    assert candidate.foreground_affinity_valid_count == 0


def test_compare_policy_aggregates_accepts_median_power_saving_at_target():
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                avg_fps=42.0,
                one_percent_low_fps=32.0,
                avg_package_w=22.0,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                avg_fps=41.5,
                one_percent_low_fps=32.0,
                avg_package_w=21.8,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=42.1,
                one_percent_low_fps=32.2,
                avg_package_w=22.1,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=41.9,
                one_percent_low_fps=32.0,
                avg_package_w=21.9,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=40.9,
                one_percent_low_fps=32.0,
                avg_package_w=20.2,
            ),
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=40.6,
                one_percent_low_fps=32.0,
                avg_package_w=20.0,
            ),
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=2)

    assert verdict.verdict == PolicyVerdict.BETTER
    assert "target sustained" in verdict.reason
    assert "median package power reduced" in verdict.reason


def test_profile_cli_summarize_writes_manifest_and_summary_json(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    game_power = tmp_path / "game-power.jsonl"
    pressure = tmp_path / "cgroup-pressure.jsonl"
    thread_affinity = tmp_path / "thread-affinity.jsonl"
    thread_schedstat = tmp_path / "thread-schedstat.jsonl"
    cpu_topology = tmp_path / "cpu-topology.json"
    process_cgroups = tmp_path / "process-cgroups.jsonl"
    restore_affinity = tmp_path / "restore-affinity.json"
    output = tmp_path / "profile"
    write_csv(
        mangohud,
        ["fps", "frametime"],
        [{"fps": "40", "frametime": "25.0"}, {"fps": "44", "frametime": "22.7"}],
    )
    game_power.write_text(
        json.dumps(
            {
                "elapsed_s": 2.0,
                "appid": "1091500",
                "action": "gpu-priority-epp",
                "package_w": 22.0,
                "core_w": 7.0,
                "uncore_w": 9.0,
                "render_busy": 0.8,
            }
        )
        + "\n"
    )
    pressure.write_text(
        json.dumps(
            {
                "elapsed_s": 1.0,
                "cpu": {"some": {"avg10": 2.5}, "full": {"avg10": 0.4}},
            }
        )
        + "\n"
    )
    thread_affinity.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "elapsed_s": 0.0,
                    "threads": [
                        {
                            "tid": 101,
                            "comm": "GameThread",
                            "cpu_time_s": 10.0,
                            "migration_count": 3,
                            "voluntary_ctxt_switches": 20,
                            "nonvoluntary_ctxt_switches": 4,
                            "current_cpu": 0,
                            "affinity": "0-5",
                            "cgroup": "app-steam-app1091500.scope",
                        }
                    ],
                },
                {
                    "elapsed_s": 2.0,
                    "threads": [
                        {
                            "tid": 101,
                            "comm": "GameThread",
                            "cpu_time_s": 13.5,
                            "migration_count": 8,
                            "voluntary_ctxt_switches": 31,
                            "nonvoluntary_ctxt_switches": 9,
                            "current_cpu": 2,
                            "affinity": "0-5",
                            "cgroup": "app-steam-app1091500.scope",
                        }
                    ],
                },
            ]
        )
        + "\n"
    )
    thread_schedstat.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "elapsed_s": 0.0,
                    "threads": [
                        {
                            "tid": 101,
                            "comm": "GameThread",
                            "run_time_ns": 10_000_000_000,
                            "runqueue_wait_ns": 100_000_000,
                            "timeslices": 100,
                            "current_cpu": 0,
                            "cgroup": "app-steam-app1091500.scope",
                        }
                    ],
                },
                {
                    "elapsed_s": 2.0,
                    "threads": [
                        {
                            "tid": 101,
                            "comm": "GameThread",
                            "run_time_ns": 13_000_000_000,
                            "runqueue_wait_ns": 240_000_000,
                            "timeslices": 130,
                            "current_cpu": 1,
                            "cgroup": "app-steam-app1091500.scope",
                        }
                    ],
                },
            ]
        )
        + "\n"
    )
    cpu_topology.write_text(
        json.dumps(
            {
                "cpus": [
                    {"cpu": 0, "online": True, "core_type": "p-core", "capacity": 1024},
                    {"cpu": 1, "online": True, "core_type": "p-core", "capacity": 1024},
                    {"cpu": 2, "online": True, "core_type": "e-core", "capacity": 640},
                    {"cpu": 3, "online": True, "core_type": "e-core", "capacity": 640},
                ]
            }
        )
        + "\n"
    )
    process_cgroups.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "elapsed_s": 0.0,
                    "processes": [
                        {
                            "pid": 101,
                            "comm": "Cyberpunk2077",
                            "cpu_time_s": 20.0,
                            "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        },
                        {
                            "pid": 201,
                            "comm": "steamwebhelper",
                            "cpu_time_s": 10.0,
                            "cgroup": "0::/user.slice/app-steam-client.scope",
                        },
                    ],
                },
                {
                    "elapsed_s": 2.0,
                    "processes": [
                        {
                            "pid": 101,
                            "comm": "Cyberpunk2077",
                            "cpu_time_s": 24.0,
                            "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        },
                        {
                            "pid": 201,
                            "comm": "steamwebhelper",
                            "cpu_time_s": 12.5,
                            "cgroup": "0::/user.slice/app-steam-client.scope",
                        },
                    ],
                },
            ]
        )
        + "\n"
    )
    restore_affinity.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "mode": "restore-snapshot",
                "write_policy": "snapshot-only",
                "threads": [
                    {
                        "pid": 101,
                        "tid": 101,
                        "comm": "GameThread",
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "cpus_allowed_list": "0-3",
                    }
                ],
                "cgroups": [
                    {
                        "cgroup": "0::/user.slice/app-steam-app1091500.scope",
                        "files": {
                            "cpu.uclamp.min": "0.00",
                            "cpu.uclamp.max": "max",
                            "cpuset.cpus.effective": "0-3",
                        },
                    }
                ],
            }
        )
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--policy",
            "gpu-priority",
            "--capture-mode",
            "imported",
            "--mangohud-csv",
            str(mangohud),
            "--game-power-jsonl",
            str(game_power),
            "--pressure-jsonl",
            str(pressure),
            "--thread-affinity-jsonl",
            str(thread_affinity),
            "--thread-schedstat-jsonl",
            str(thread_schedstat),
            "--cpu-topology-json",
            str(cpu_topology),
            "--process-cgroups-jsonl",
            str(process_cgroups),
            "--restore-affinity-json",
            str(restore_affinity),
            "--fps-target",
            "40",
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    advice = json.loads((output / "affinity-advice.json").read_text())
    background = json.loads((output / "background-shaping.json").read_text())
    assert "summary.json" in result.stdout
    assert manifest["appid"] == "1091500"
    assert manifest["policy"] == "gpu-priority"
    assert manifest["capture_mode"] == "imported"
    assert manifest["fps_target"] == 40.0
    assert manifest["fps_target_source"] == "manual"
    assert manifest["thread_schedstat_jsonl"] is True
    assert manifest["cpu_topology_json"] is True
    assert manifest["affinity_advice_json"] is True
    assert manifest["process_cgroups_jsonl"] is True
    assert manifest["background_shaping_json"] is True
    assert manifest["restore_affinity_json"] is True
    assert summary["avg_fps"] == 42.0
    assert summary["fps_target"] == 40.0
    assert summary["fps_target_source"] == "manual"
    assert summary["target_frame_ms"] == 25.0
    assert summary["avg_fps_target_ratio"] == 1.05
    assert summary["fps_target_met"] is True
    assert summary["avg_uncore_w"] == 9.0
    assert summary["cpu_pressure_some_avg10_peak"] == 2.5
    assert summary["cpu_pressure_full_avg10_peak"] == 0.4
    assert summary["thread_affinity_samples"] == 2
    assert summary["thread_affinity_observed_threads"] == 1
    assert summary["thread_affinity_hot_threads"][0]["migration_delta"] == 5
    assert summary["thread_affinity_hot_threads"][0]["nonvoluntary_ctxt_switches_delta"] == 5
    assert summary["thread_schedstat_samples"] == 2
    assert summary["thread_schedstat_observed_threads"] == 1
    assert summary["thread_schedstat_hot_threads"][0]["runqueue_wait_ms_delta"] == 140.0
    assert summary["restore_affinity_thread_count"] == 1
    assert summary["restore_affinity_cgroup_count"] == 1
    assert summary["restore_affinity_cgroups"] == [
        "0::/user.slice/app-steam-app1091500.scope"
    ]
    assert summary["restore_affinity_files"] == [
        "cpu.uclamp.max",
        "cpu.uclamp.min",
        "cpuset.cpus.effective",
    ]
    assert summary["restore_affinity_cgroup_files"] == {
        "0::/user.slice/app-steam-app1091500.scope": [
            "cpu.uclamp.max",
            "cpu.uclamp.min",
            "cpuset.cpus.effective",
        ]
    }
    assert summary["restore_affinity_cgroup_file_values"] == {
        "0::/user.slice/app-steam-app1091500.scope": {
            "cpu.uclamp.max": "max",
            "cpu.uclamp.min": "0.00",
            "cpuset.cpus.effective": "0-3",
        }
    }
    assert summary["restored"] is True
    assert advice["mode"] == "observe-only"
    assert advice["preferred_latency_cpus"] == [0, 1]
    assert advice["ranked_threads"][0]["tid"] == 101
    assert advice["ranked_threads"][0]["runqueue_wait_ms_delta"] == 140.0
    assert background["mode"] == "observe-only"
    assert background["candidates"][0]["classification"] == "steam-helper"


def test_profile_cli_summarize_records_policy_tunables(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    output = tmp_path / "profile"
    write_csv(
        mangohud,
        ["Average FPS", "1% Min FPS", "0.1% Min FPS", "Average Frame Time"],
        [
            {
                "Average FPS": "31.9",
                "1% Min FPS": "29.7",
                "0.1% Min FPS": "29.3",
                "Average Frame Time": "31.3",
            }
        ],
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--policy",
            "gpu-priority-cpu-cap",
            "--capture-mode",
            "imported",
            "--mangohud-csv",
            str(mangohud),
            "--epp",
            "balance_power",
            "--pcore-max-mhz",
            "3000",
            "--ecore-max-mhz",
            "2200",
            "--cpu-cap-enabled",
            "true",
            "--cpu-cap-core-share-threshold",
            "0.31",
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["epp"] == "balance_power"
    assert manifest["pcore_max_mhz"] == 3000
    assert manifest["ecore_max_mhz"] == 2200
    assert manifest["cpu_cap_enabled"] is True
    assert manifest["cpu_cap_core_share_threshold"] == 0.31
    assert summary["epp"] == "balance_power"
    assert summary["pcore_max_mhz"] == 3000
    assert summary["ecore_max_mhz"] == 2200
    assert summary["cpu_cap_enabled"] is True
    assert summary["cpu_cap_core_share_threshold"] == 0.31


def test_profile_cli_summarize_records_capture_timing(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    output = tmp_path / "profile"
    write_csv(
        mangohud,
        ["Average FPS", "1% Min FPS", "Average Frame Time"],
        [{"Average FPS": "55.5", "1% Min FPS": "42.0", "Average Frame Time": "18.0"}],
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--policy",
            "gpu-priority",
            "--capture-mode",
            "controlled",
            "--mangohud-csv",
            str(mangohud),
            "--duration-s",
            "15",
            "--warmup-s",
            "5",
            "--poll-s",
            "2",
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["duration_s"] == 15.0
    assert manifest["warmup_s"] == 5.0
    assert manifest["poll_s"] == 2.0
    assert summary["duration_s"] == 15.0
    assert summary["warmup_s"] == 5.0
    assert summary["poll_s"] == 2.0


def test_summary_records_ab_evidence_fields(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    output = tmp_path / "profile"
    write_csv(
        mangohud,
        ["Average FPS", "1% Min FPS", "Average Frame Time"],
        [{"Average FPS": "42.0", "1% Min FPS": "31.0", "Average Frame Time": "23.8"}],
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--policy",
            "gpu-priority",
            "--capture-mode",
            "controlled",
            "--mangohud-csv",
            str(mangohud),
            "--ab-order-strategy",
            "paired-baseline",
            "--ab-run-order",
            "off,gpu-priority,off",
            "--ab-order-valid",
            "true",
            "--ab-candidate-policy",
            "gpu-priority",
            "--ab-invocation-id",
            "invocation-1",
            "--ab-pair-id",
            "pair-1",
            "--ab-pair-position",
            "candidate",
            "--scene-evidence",
            "save:dogtown-market-static",
            "--power-source-state",
            "ac",
            "--power-source-start-state",
            "ac",
            "--power-source-pre-run-state",
            "ac",
            "--power-source-end-state",
            "ac",
            "--power-source-samples",
            "ac,ac,ac",
            "--power-source-stable",
            "true",
            "--thermal-start-c",
            "61.0",
            "--thermal-end-c",
            "63.5",
            "--thermal-unavailable",
            "false",
            "--thermal-source-kind",
            "cpu-package",
            "--thermal-source-id",
            "hwmon:coretemp:Package id 0",
            "--thermal-source-label",
            "Package id 0",
            "--run-started-at-s",
            "12405.3",
            "--run-ended-at-s",
            "12465.3",
            "--cooldown-rule",
            "fixed-60s",
            "--cooldown-enforced",
            "true",
            "--cooldown-started-at-s",
            "12345.0",
            "--cooldown-ended-at-s",
            "12405.0",
            "--cooldown-elapsed-s",
            "60.0",
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert summary["ab_order_strategy"] == "paired-baseline"
    assert summary["ab_pair_position"] == "candidate"
    assert summary["power_source_samples"] == ["ac", "ac", "ac"]
    assert summary["thermal_source_id"] == "hwmon:coretemp:Package id 0"
    assert summary["cooldown_elapsed_s"] == 60.0
    assert manifest["ab_invocation_id"] == "invocation-1"
    assert manifest["run_started_at_s"] == 12405.3


def test_profile_cli_compare_reads_two_summary_files(tmp_path):
    baseline = tmp_path / "off-summary.json"
    candidate = tmp_path / "gpu-summary.json"
    baseline.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "tdp_w": 22,
                "policy": "off",
                "capture_mode": "controlled",
                "avg_fps": 40.0,
                "one_percent_low_fps": 30.0,
                "p99_frametime_ms": 36.0,
                "restored": True,
            }
        )
    )
    candidate.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "tdp_w": 22,
                "policy": "gpu-priority",
                "capture_mode": "controlled",
                "avg_fps": 40.2,
                "one_percent_low_fps": 32.0,
                "p99_frametime_ms": 35.0,
                "restored": True,
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "compare",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["verdict"] == "inconclusive"
    assert payload["candidate_policy"] == "gpu-priority"
    assert payload["reason"].startswith("A/B evidence incomplete:")
    assert "single-run compare is exploratory only" in payload["reason"]


def test_profile_cli_aggregate_scans_profile_root_and_compares_repeated_runs(tmp_path):
    runs = [
        ("001-off-before", "off", "baseline-before", "pair-1", 0.0, 54.0, 40.0),
        ("002-gpu", "gpu-priority", "candidate", "pair-1", 0.0, 55.0, 43.0),
        ("003-off-after", "off", "baseline-after", "pair-1", 0.0, 55.0, 40.5),
        ("004-off-before", "off", "baseline-before", "pair-2", 500.0, 54.2, 40.2),
        ("005-gpu", "gpu-priority", "candidate", "pair-2", 500.0, 56.0, 44.0),
        ("006-off-after", "off", "baseline-after", "pair-2", 500.0, 55.2, 40.7),
    ]
    for dirname, policy, position, pair_id, base_s, avg_fps, low_fps in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                controlled_ab_payload(
                    policy=policy,
                    position=position,
                    pair_id=pair_id,
                    base_s=base_s,
                    avg_fps=avg_fps,
                    one_percent_low_fps=low_fps,
                )
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "2",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["min_runs"] == 2
    assert payload["comparisons"][0]["comparison"]["verdict"] == "better"
    assert payload["comparisons"][0]["baseline"]["sample_count"] == 4
    assert payload["comparisons"][0]["candidate"]["sample_count"] == 2
    assert payload["comparisons"][0]["candidate"]["one_percent_low_fps_median"] == 43.5


def test_aggregate_groups_split_profile_root_by_ab_candidate_policy(tmp_path):
    runs = [
        (
            "001-gpu-off-before",
            "off",
            "baseline-before",
            "gpu-priority",
            "pair-gpu",
            0.0,
            54.0,
            40.0,
        ),
        (
            "002-gpu",
            "gpu-priority",
            "candidate",
            "gpu-priority",
            "pair-gpu",
            0.0,
            56.0,
            43.0,
        ),
        (
            "003-gpu-off-after",
            "off",
            "baseline-after",
            "gpu-priority",
            "pair-gpu",
            0.0,
            55.0,
            40.5,
        ),
        (
            "004-cap-off-before",
            "off",
            "baseline-before",
            "gpu-priority-cpu-cap",
            "pair-cap",
            500.0,
            50.0,
            36.0,
        ),
        (
            "005-cap",
            "gpu-priority-cpu-cap",
            "candidate",
            "gpu-priority-cpu-cap",
            "pair-cap",
            500.0,
            52.0,
            39.0,
        ),
        (
            "006-cap-off-after",
            "off",
            "baseline-after",
            "gpu-priority-cpu-cap",
            "pair-cap",
            500.0,
            51.0,
            36.5,
        ),
    ]
    for dirname, policy, position, candidate_policy, pair_id, base_s, avg_fps, low_fps in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                controlled_ab_payload(
                    policy=policy,
                    position=position,
                    candidate_policy=candidate_policy,
                    pair_id=pair_id,
                    base_s=base_s,
                    avg_fps=avg_fps,
                    one_percent_low_fps=low_fps,
                )
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--candidate-policy",
            "gpu-priority-cpu-cap",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "1",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    comparisons = {
        item["candidate"]["policy"]: item
        for item in payload["comparisons"]
    }
    assert set(comparisons) == {"gpu-priority", "gpu-priority-cpu-cap"}
    assert comparisons["gpu-priority"]["baseline"]["sample_count"] == 2
    assert comparisons["gpu-priority-cpu-cap"]["baseline"]["sample_count"] == 2
    assert comparisons["gpu-priority"]["baseline"]["ab_candidate_policy"] == "gpu-priority"
    assert (
        comparisons["gpu-priority-cpu-cap"]["baseline"]["ab_candidate_policy"]
        == "gpu-priority-cpu-cap"
    )
    assert payload["incomplete_groups"] == []


def test_aggregate_filters_v3_baselines_to_requested_candidate_policy(tmp_path):
    runs = [
        ("001-gpu-off-before", "off", "baseline-before", "gpu-priority", "pair-gpu"),
        ("002-gpu", "gpu-priority", "candidate", "gpu-priority", "pair-gpu"),
        ("003-gpu-off-after", "off", "baseline-after", "gpu-priority", "pair-gpu"),
        (
            "004-cap-off-before",
            "off",
            "baseline-before",
            "gpu-priority-cpu-cap",
            "pair-cap",
        ),
        (
            "005-cap",
            "gpu-priority-cpu-cap",
            "candidate",
            "gpu-priority-cpu-cap",
            "pair-cap",
        ),
        (
            "006-cap-off-after",
            "off",
            "baseline-after",
            "gpu-priority-cpu-cap",
            "pair-cap",
        ),
    ]
    for dirname, policy, position, candidate_policy, pair_id in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                controlled_ab_payload(
                    policy=policy,
                    position=position,
                    candidate_policy=candidate_policy,
                    pair_id=pair_id,
                    avg_fps=55.0,
                    one_percent_low_fps=40.0,
                )
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "1",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert [item["candidate"]["policy"] for item in payload["comparisons"]] == [
        "gpu-priority"
    ]
    assert payload["comparisons"][0]["baseline"]["ab_candidate_policy"] == "gpu-priority"
    assert payload["incomplete_groups"] == []


def test_aggregate_reports_candidate_without_matching_baseline_as_incomplete_group(
    tmp_path,
):
    run_dir = tmp_path / "candidate-only"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps(
            controlled_ab_payload(
                policy="gpu-priority",
                position="candidate",
                avg_fps=56.0,
                one_percent_low_fps=43.0,
            )
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "1",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["comparisons"] == []
    assert payload["incomplete_groups"] == [
        {
            "baseline_policy": "off",
            "candidate_policy": "gpu-priority",
            "ab_candidate_policy": "gpu-priority",
            "ab_run_order": "off,gpu-priority,off",
            "missing_side": "baseline",
            "verdict": "inconclusive",
            "reason": (
                "A/B evidence incomplete: missing matching baseline group; "
                "exploratory only; cannot support a BETTER claim"
            ),
        }
    ]


def test_profile_cli_aggregate_includes_affinity_role_stability(tmp_path):
    runs = [
        ("001-off", "off", 54.0, 40.0, None),
        ("002-gpu", "gpu-priority", 55.0, 43.0, (2.0, 90.0, 12.0)),
        ("003-off", "off", 55.0, 40.5, None),
        ("004-gpu", "gpu-priority", 56.0, 44.0, (2.4, 110.0, 14.0)),
    ]
    for dirname, policy, avg_fps, low_fps, role_values in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "appid": "1091500",
                    "tdp_w": 22,
                    "policy": policy,
                    "capture_mode": "controlled",
                    "avg_fps": avg_fps,
                    "one_percent_low_fps": low_fps,
                    "restored": True,
                }
            )
        )
        if role_values is None:
            continue
        cpu_time, wait_ms, harm = role_values
        (run_dir / "affinity-advice.json").write_text(
            json.dumps(
                {
                    "mode": "observe-only",
                    "role_candidates": [
                        {
                            "role_key": "foreground-game:worker-thread",
                            "comm": "Worker Thread",
                            "cgroup_role": "foreground-game",
                            "classification": "latency-hot",
                            "thread_count": 2,
                            "tids": [201, 202],
                            "cpu_time_s_delta": cpu_time,
                            "migration_delta": 5,
                            "runqueue_wait_ms_delta": wait_ms,
                            "runqueue_wait_per_slice_ms_max": 4.0,
                            "migration_harm_score_max": harm,
                            "cpus_seen": [0, 1, 2, 3],
                            "preferred_cpu_overlap": [0, 1],
                            "suggested_action": "prefer-latency-cpus",
                        }
                    ],
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "2",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    comparison = json.loads(result.stdout)["comparisons"][0]
    assert comparison["baseline_affinity_roles"] == []
    assert comparison["candidate_affinity_roles"][0] == {
        "role_key": "foreground-game:worker-thread",
        "comm": "Worker Thread",
        "cgroup_role": "foreground-game",
        "classification": "latency-hot",
        "suggested_action": "prefer-latency-cpus",
        "observed_run_count": 2,
        "run_coverage": 1.0,
        "thread_count_median": 2.0,
        "cpu_time_s_delta_median": 2.2,
        "migration_delta_median": 5.0,
        "runqueue_wait_ms_delta_median": 100.0,
        "runqueue_wait_per_slice_ms_max_median": 4.0,
        "migration_harm_score_max_median": 13.0,
        "cpus_seen": [0, 1, 2, 3],
        "preferred_cpu_overlap": [0, 1],
    }


def test_profile_cli_aggregate_builds_guarded_affinity_experiment_plan(tmp_path):
    runs = [
        (
            "001-off-before",
            "off",
            "baseline-before",
            "pair-1",
            0.0,
            54.0,
            40.0,
            None,
        ),
        (
            "002-gpu",
            "gpu-priority",
            "candidate",
            "pair-1",
            0.0,
            55.0,
            43.0,
            (2.0, 90.0, 12.0),
        ),
        (
            "003-off-after",
            "off",
            "baseline-after",
            "pair-1",
            0.0,
            55.0,
            40.5,
            None,
        ),
        (
            "004-off-before",
            "off",
            "baseline-before",
            "pair-2",
            500.0,
            54.2,
            40.2,
            None,
        ),
        (
            "005-gpu",
            "gpu-priority",
            "candidate",
            "pair-2",
            500.0,
            56.0,
            44.0,
            (2.4, 110.0, 14.0),
        ),
        (
            "006-off-after",
            "off",
            "baseline-after",
            "pair-2",
            500.0,
            55.2,
            40.7,
            None,
        ),
    ]
    for dirname, policy, position, pair_id, base_s, avg_fps, low_fps, role_values in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        payload = controlled_ab_payload(
            policy=policy,
            position=position,
            pair_id=pair_id,
            base_s=base_s,
            avg_fps=avg_fps,
            one_percent_low_fps=low_fps,
        )
        payload.update(
            {
                "restore_affinity_thread_count": 3,
                "restore_affinity_cgroup_count": 1,
                "restore_affinity_files": [
                    "cpu.uclamp.max",
                    "cpu.uclamp.min",
                    "cpuset.cpus.effective",
                ],
            }
        )
        (run_dir / "summary.json").write_text(
            json.dumps(payload)
        )
        if role_values is None:
            continue
        cpu_time, wait_ms, harm = role_values
        (run_dir / "affinity-advice.json").write_text(
            json.dumps(
                {
                    "mode": "observe-only",
                    "role_candidates": [
                        {
                            "role_key": "foreground-game:worker-thread",
                            "comm": "Worker Thread",
                            "cgroup_role": "foreground-game",
                            "classification": "latency-hot",
                            "thread_count": 2,
                            "tids": [201, 202],
                            "cpu_time_s_delta": cpu_time,
                            "migration_delta": 5,
                            "runqueue_wait_ms_delta": wait_ms,
                            "runqueue_wait_per_slice_ms_max": 4.0,
                            "migration_harm_score_max": harm,
                            "cpus_seen": [0, 1, 2, 3],
                            "preferred_cpu_overlap": [0, 1],
                            "suggested_action": "prefer-latency-cpus",
                        }
                    ],
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "2",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    plan = json.loads(result.stdout)["comparisons"][0]["affinity_experiment_plan"]
    assert plan["mode"] == "ready-for-guarded-experiment"
    assert plan["write_policy"] == "disabled"
    assert plan["strategy"] == "adaptive-compact-preferred-set"
    assert json.loads(result.stdout)["comparisons"][0]["candidate"][
        "restore_affinity_snapshot_count"
    ] == 2
    assert plan["candidates"][0] == {
        "role_key": "foreground-game:worker-thread",
        "comm": "Worker Thread",
        "control_scope": "foreground-game-role",
        "candidate_control": "guarded-hard-compact-affinity",
        "guarded_variant": "foreground-role-compact",
        "preferred_cpus": [0, 1],
        "fallback": "restore-original-affinity-and-cgroup-state",
        "observed_run_count": 2,
        "run_coverage": 1.0,
        "thread_count_median": 2.0,
        "classification": "latency-hot",
        "runqueue_wait_ms_delta_median": 100.0,
        "runqueue_wait_per_slice_ms_max_median": 4.0,
        "migration_harm_score_max_median": 13.0,
    }
    assert "candidate policy comparison is better" in plan["reasons"]
    assert "hard per-TID affinity remains profiler-only" in plan["reasons"]
    assert "restore-affinity snapshots are available for every aggregated run" in plan[
        "reasons"
    ]


def test_profile_cli_aggregate_builds_background_shaping_experiment_plan(tmp_path):
    runs = [
        (
            "001-off-before",
            "off",
            "baseline-before",
            "pair-1",
            0.0,
            54.0,
            40.0,
            None,
        ),
        ("002-gpu", "gpu-priority", "candidate", "pair-1", 0.0, 55.0, 43.0, 2.0),
        (
            "003-off-after",
            "off",
            "baseline-after",
            "pair-1",
            0.0,
            55.0,
            40.5,
            None,
        ),
        (
            "004-off-before",
            "off",
            "baseline-before",
            "pair-2",
            500.0,
            54.2,
            40.2,
            None,
        ),
        (
            "005-gpu",
            "gpu-priority",
            "candidate",
            "pair-2",
            500.0,
            56.0,
            44.0,
            2.4,
        ),
        (
            "006-off-after",
            "off",
            "baseline-after",
            "pair-2",
            500.0,
            55.2,
            40.7,
            None,
        ),
    ]
    for dirname, policy, position, pair_id, base_s, avg_fps, low_fps, helper_cpu_s in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        payload = controlled_ab_payload(
            policy=policy,
            position=position,
            pair_id=pair_id,
            base_s=base_s,
            avg_fps=avg_fps,
            one_percent_low_fps=low_fps,
        )
        payload.update(
            {
                "restore_affinity_thread_count": 3,
                "restore_affinity_cgroup_count": 2,
                "restore_affinity_files": [
                    "cpu.uclamp.max",
                    "cpu.uclamp.min",
                    "cpu.weight",
                    "cpuset.cpus.effective",
                ],
                "restore_affinity_cgroups": [
                    "0::/user.slice/app-steam-app1091500.scope",
                    "0::/user.slice/app-steam-client.scope",
                ],
                "restore_affinity_cgroup_files": {
                    "0::/user.slice/app-steam-app1091500.scope": [
                        "cpu.uclamp.max",
                        "cpu.uclamp.min",
                        "cpu.weight",
                        "cpuset.cpus.effective",
                    ],
                    "0::/user.slice/app-steam-client.scope": [
                        "cpu.uclamp.max",
                        "cpu.weight",
                    ],
                },
                "restore_affinity_cgroup_file_values": {
                    "0::/user.slice/app-steam-app1091500.scope": {
                        "cpu.uclamp.max": "max",
                        "cpu.uclamp.min": "0.00",
                        "cpu.weight": "100",
                        "cpuset.cpus.effective": "0-7",
                    },
                    "0::/user.slice/app-steam-client.scope": {
                        "cpu.uclamp.max": "max",
                        "cpu.weight": "100",
                    },
                },
            }
        )
        (run_dir / "summary.json").write_text(
            json.dumps(payload)
        )
        if helper_cpu_s is None:
            continue
        (run_dir / "background-shaping.json").write_text(
            json.dumps(
                {
                    "mode": "observe-only",
                    "write_policy": "disabled",
                    "appid": "1091500",
                    "candidates": [
                        {
                            "cgroup": "0::/user.slice/app-steam-client.scope",
                            "classification": "steam-helper",
                            "cpu_time_s_delta": helper_cpu_s,
                            "process_count": 2,
                            "pids": [201, 202],
                            "commands": ["steamwebhelper"],
                            "suggested_action": "future-cpu-weight-candidate",
                        }
                    ],
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "2",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    comparison = json.loads(result.stdout)["comparisons"][0]
    candidate_background = comparison["candidate_background_shaping_candidates"]
    assert candidate_background[0] == {
        "candidate_key": "steam-helper:0::/user.slice/app-steam-client.scope",
        "cgroup": "0::/user.slice/app-steam-client.scope",
        "classification": "steam-helper",
        "suggested_action": "future-cpu-weight-candidate",
        "observed_run_count": 2,
        "run_coverage": 1.0,
        "restore_snapshot_observed_run_count": 2,
        "restore_snapshot_run_coverage": 1.0,
        "cpu_time_s_delta_median": 2.2,
        "process_count_median": 2.0,
        "commands": ["steamwebhelper"],
    }
    plan = comparison["background_shaping_experiment_plan"]
    assert plan["mode"] == "ready-for-guarded-experiment"
    assert plan["write_policy"] == "disabled"
    assert plan["strategy"] == "background-helper-soft-cap"
    assert plan["candidates"][0] == {
        "candidate_key": "steam-helper:0::/user.slice/app-steam-client.scope",
        "cgroup": "0::/user.slice/app-steam-client.scope",
        "classification": "steam-helper",
        "control_scope": "background-helper-cgroup",
        "candidate_control": "cpu.weight-or-uclamp-max-soft-cap",
        "guarded_variant": "background-helper-soft-cap",
        "fallback": "restore-original-cgroup-cpu-controller-state",
        "observed_run_count": 2,
        "run_coverage": 1.0,
        "restore_snapshot_observed_run_count": 2,
        "restore_snapshot_run_coverage": 1.0,
        "cpu_time_s_delta_median": 2.2,
        "restore_files": ["cpu.uclamp.max", "cpu.weight"],
        "restore_values": {
            "cpu.uclamp.max": ["max"],
            "cpu.weight": ["100"],
        },
        "dry_run_writes": [
            {
                "variant": "background-helper-cpu-weight-80",
                "control_file": "cpu.weight",
                "proposed_value": "80",
                "value_policy": "lower-only-min-current-or-80",
                "restore_values_observed": ["100"],
                "write_mode": "one-control-per-ab-run",
            },
            {
                "variant": "background-helper-uclamp-max-85",
                "control_file": "cpu.uclamp.max",
                "proposed_value": "85.00",
                "value_policy": "lower-only-max-85-percent",
                "restore_values_observed": ["max"],
                "write_mode": "one-control-per-ab-run",
            },
        ],
        "acceptance_thresholds": {
            "avg_fps_regression_max_pct": -2.0,
            "one_percent_low_regression_max_pct": -3.0,
            "p99_frametime_regression_max_pct": -3.0,
            "target_power_saving_min_pct": 5.0,
        },
    }
    assert "background/helper cgroup candidate is stable across candidate runs" in plan[
        "reasons"
    ]
    assert plan["readiness"] == {
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


def test_profile_cli_aggregate_requires_background_cgroup_restore_coverage(
    tmp_path,
):
    runs = [
        ("001-off", "off", 54.0, 40.0, None),
        ("002-gpu", "gpu-priority", 55.0, 43.0, 2.0),
        ("003-off", "off", 55.0, 40.5, None),
        ("004-gpu", "gpu-priority", 56.0, 44.0, 2.4),
    ]
    for dirname, policy, avg_fps, low_fps, helper_cpu_s in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "appid": "1091500",
                    "tdp_w": 22,
                    "policy": policy,
                    "capture_mode": "controlled",
                    "avg_fps": avg_fps,
                    "one_percent_low_fps": low_fps,
                    "restore_affinity_thread_count": 3,
                    "restore_affinity_cgroup_count": 1,
                    "restore_affinity_files": [
                        "cpu.uclamp.max",
                        "cpu.weight",
                    ],
                    "restore_affinity_cgroups": [
                        "0::/user.slice/app-steam-app1091500.scope",
                    ],
                    "restored": True,
                }
            )
        )
        if helper_cpu_s is None:
            continue
        (run_dir / "background-shaping.json").write_text(
            json.dumps(
                {
                    "mode": "observe-only",
                    "write_policy": "disabled",
                    "appid": "1091500",
                    "candidates": [
                        {
                            "cgroup": "0::/user.slice/app-steam-client.scope",
                            "classification": "steam-helper",
                            "cpu_time_s_delta": helper_cpu_s,
                            "process_count": 2,
                            "pids": [201, 202],
                            "commands": ["steamwebhelper"],
                            "suggested_action": "future-cpu-weight-candidate",
                        }
                    ],
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "2",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    plan = json.loads(result.stdout)["comparisons"][0][
        "background_shaping_experiment_plan"
    ]
    assert plan["mode"] == "observe-only"
    assert plan["candidates"] == []
    readiness = plan["readiness"]
    assert readiness["ready_for_guarded_experiment"] is False
    assert readiness["write_policy_disabled"] is True
    assert readiness["candidate_restore_coverage"] is False
    assert readiness["candidate_stability"] is True
    assert readiness["candidate_guarded"] is False
    assert "candidate_restore_coverage_missing" in readiness["blocking_reason_codes"]
    assert "no_guarded_background_candidate" in readiness["blocking_reason_codes"]
    assert plan["write_policy"] == "disabled"
    assert "candidate background cgroups are missing from restore-affinity snapshots" in plan[
        "reasons"
    ]


def background_aggregate(policy: str) -> PolicyAggregate:
    return PolicyAggregate(
        appid="1091500",
        tdp_w=22,
        policy=policy,
        capture_mode=CaptureMode.CONTROLLED,
        sample_count=2,
        restored_count=2,
        restore_affinity_snapshot_count=2,
        restore_affinity_files=["cpu.uclamp.max", "cpu.weight"],
        restore_affinity_cgroups=[
            "0::/user.slice/app-steam-app1091500.scope",
            "0::/user.slice/app-steam-client.scope",
        ],
        restore_affinity_cgroup_files={
            "0::/user.slice/app-steam-client.scope": [
                "cpu.uclamp.max",
                "cpu.weight",
            ],
        },
        restore_affinity_cgroup_file_values={
            "0::/user.slice/app-steam-client.scope": {
                "cpu.uclamp.max": ["max"],
                "cpu.weight": ["100"],
            },
        },
    )


def test_background_shaping_readiness_distinguishes_restore_coverage_from_stability():
    plan = build_background_shaping_experiment_plan(
        baseline=background_aggregate("off"),
        candidate=background_aggregate("gpu-priority"),
        comparison=PolicyComparison(
            baseline_policy="off",
            candidate_policy="gpu-priority",
            verdict=PolicyVerdict.BETTER,
            reason="candidate policy comparison is better",
        ),
        baseline_candidates=[],
        candidate_candidates=[
            {
                "candidate_key": "steam-helper:0::/user.slice/app-steam-client.scope",
                "cgroup": "0::/user.slice/app-steam-client.scope",
                "classification": "steam-helper",
                "suggested_action": "future-cpu-weight-candidate",
                "observed_run_count": 2,
                "run_coverage": 1.0,
                "restore_snapshot_observed_run_count": 2,
                "restore_snapshot_run_coverage": 1.0,
                "cpu_time_s_delta_median": 0.2,
                "process_count_median": 2.0,
                "commands": ["steamwebhelper"],
            }
        ],
        min_runs=2,
    )

    readiness = plan["readiness"]
    assert readiness["candidate_restore_coverage"] is True
    assert readiness["candidate_stability"] is False
    assert readiness["candidate_guarded"] is False
    assert "candidate_restore_coverage_missing" not in readiness["blocking_reason_codes"]
    assert "no_guarded_background_candidate" in readiness["blocking_reason_codes"]


def test_profile_cli_aggregate_requires_restore_snapshot_for_guarded_affinity_plan(
    tmp_path,
):
    runs = [
        ("001-off", "off", 54.0, 40.0, None),
        ("002-gpu", "gpu-priority", 55.0, 43.0, (2.0, 90.0, 12.0)),
        ("003-off", "off", 55.0, 40.5, None),
        ("004-gpu", "gpu-priority", 56.0, 44.0, (2.4, 110.0, 14.0)),
    ]
    for dirname, policy, avg_fps, low_fps, role_values in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "appid": "1091500",
                    "tdp_w": 22,
                    "policy": policy,
                    "capture_mode": "controlled",
                    "avg_fps": avg_fps,
                    "one_percent_low_fps": low_fps,
                    "restored": True,
                }
            )
        )
        if role_values is None:
            continue
        cpu_time, wait_ms, harm = role_values
        (run_dir / "affinity-advice.json").write_text(
            json.dumps(
                {
                    "mode": "observe-only",
                    "role_candidates": [
                        {
                            "role_key": "foreground-game:worker-thread",
                            "comm": "Worker Thread",
                            "cgroup_role": "foreground-game",
                            "classification": "latency-hot",
                            "thread_count": 2,
                            "tids": [201, 202],
                            "cpu_time_s_delta": cpu_time,
                            "migration_delta": 5,
                            "runqueue_wait_ms_delta": wait_ms,
                            "runqueue_wait_per_slice_ms_max": 4.0,
                            "migration_harm_score_max": harm,
                            "cpus_seen": [0, 1, 2, 3],
                            "preferred_cpu_overlap": [0, 1],
                            "suggested_action": "prefer-latency-cpus",
                        }
                    ],
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "2",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    comparison = json.loads(result.stdout)["comparisons"][0]
    plan = comparison["affinity_experiment_plan"]
    assert comparison["candidate"]["restore_affinity_snapshot_count"] == 0
    assert plan["mode"] == "observe-only"
    assert "restore-affinity snapshots are missing for aggregated runs" in plan[
        "reasons"
    ]


def test_profile_cli_aggregate_keeps_cpu_cap_tuning_variants_separate(tmp_path):
    runs = [
        ("001-off", "off", 54.0, 40.0, False, 3000, 2200, 0.30),
        ("002-cap-a", "gpu-priority-cpu-cap", 56.0, 43.0, True, 3000, 2200, 0.30),
        ("003-cap-b", "gpu-priority-cpu-cap", 58.0, 45.0, True, 3200, 2400, 0.35),
    ]
    for dirname, policy, avg_fps, low_fps, cpu_cap, pcore, ecore, threshold in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "appid": "1091500",
                    "tdp_w": 22,
                    "policy": policy,
                    "capture_mode": "controlled",
                    "epp": "balance_power",
                    "pcore_max_mhz": pcore,
                    "ecore_max_mhz": ecore,
                    "cpu_cap_enabled": cpu_cap,
                    "cpu_cap_core_share_threshold": threshold,
                    "avg_fps": avg_fps,
                    "one_percent_low_fps": low_fps,
                    "restored": True,
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority-cpu-cap",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "1",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert len(payload["comparisons"]) == 2
    candidate_caps = {
        (
            item["candidate"]["pcore_max_mhz"],
            item["candidate"]["ecore_max_mhz"],
            item["candidate"]["cpu_cap_core_share_threshold"],
        )
        for item in payload["comparisons"]
    }
    assert candidate_caps == {(3000, 2200, 0.3), (3200, 2400, 0.35)}


def test_profile_cli_aggregate_keeps_capture_durations_separate(tmp_path):
    runs = [
        ("001-off-15", "off", 54.0, 40.0, 15.0),
        ("002-gpu-15", "gpu-priority", 56.0, 43.0, 15.0),
        ("003-off-60", "off", 55.0, 41.0, 60.0),
        ("004-gpu-60", "gpu-priority", 58.0, 45.0, 60.0),
    ]
    for dirname, policy, avg_fps, low_fps, duration_s in runs:
        run_dir = tmp_path / dirname
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(
            json.dumps(
                {
                    "appid": "1091500",
                    "tdp_w": 22,
                    "policy": policy,
                    "capture_mode": "controlled",
                    "epp": "balance_power",
                    "duration_s": duration_s,
                    "warmup_s": 5.0,
                    "poll_s": 2.0,
                    "avg_fps": avg_fps,
                    "one_percent_low_fps": low_fps,
                    "restored": True,
                }
            )
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--min-runs",
            "1",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert len(payload["comparisons"]) == 2
    assert all(
        item["baseline"]["duration_s"] == item["candidate"]["duration_s"]
        for item in payload["comparisons"]
    )
    durations = {item["candidate"]["duration_s"] for item in payload["comparisons"]}
    assert durations == {15.0, 60.0}

    filtered = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "aggregate",
            "--root",
            str(tmp_path),
            "--baseline-policy",
            "off",
            "--candidate-policy",
            "gpu-priority",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--duration-s",
            "15",
            "--min-runs",
            "1",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    filtered_payload = json.loads(filtered.stdout)
    assert len(filtered_payload["comparisons"]) == 1
    assert filtered_payload["comparisons"][0]["candidate"]["duration_s"] == 15.0


def test_parse_pressure_file_reads_some_and_full_avg10():
    text = (
        "some avg10=2.10 avg60=1.00 avg300=0.20 total=12345\n"
        "full avg10=0.30 avg60=0.10 avg300=0.00 total=456\n"
    )

    pressure = parse_pressure_file(text)

    assert pressure["some"]["avg10"] == 2.10
    assert pressure["full"]["avg10"] == 0.30


def test_summarize_pressure_jsonl_reports_peak_cpu_pressure(tmp_path):
    path = tmp_path / "cgroup-pressure.jsonl"
    rows = [
        {"elapsed_s": 1.0, "cpu": {"some": {"avg10": 1.2}, "full": {"avg10": 0.0}}},
        {"elapsed_s": 2.0, "cpu": {"some": {"avg10": 3.4}, "full": {"avg10": 0.2}}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    summary = summarize_pressure_jsonl(path)

    assert summary == {
        "cpu_pressure_some_avg10_peak": 3.4,
        "cpu_pressure_full_avg10_peak": 0.2,
    }


def test_parse_game_power_jsonl_counts_runtime_classification_and_target_metadata(tmp_path):
    path = tmp_path / "game-power.jsonl"
    rows = [
        {
            "appid": "1091500",
            "action": "gpu-priority-cpu-cap",
            "package_w": 22.0,
            "core_w": 8.0,
            "uncore_w": 9.0,
            "render_busy": 0.8,
            "fps_target": 40.0,
            "fps_target_source": "manual",
            "fps_target_confidence": "high",
            "frame_avg_fps": 56.0,
            "frame_p95_ms": 22.0,
            "frame_performance_sample_count": 20,
            "frame_performance_confidence": "high",
            "frame_performance_source": "mangohud-csv",
            "classification": {
                "primary": "fps-target-satisfied",
                "advisories": ["foreground-cpu-pressure"],
                "confidence": "high",
                "evidence": {},
            },
            "pressure": {
                "cpu": [
                    {
                        "scope": "foreground_cgroup",
                        "supported": True,
                        "some_avg10": 2.5,
                    }
                ],
                "memory": [],
                "io": [],
            },
        },
        {"appid": "1091500", "action": "observe-only", "classification": "bad"},
        {"appid": None, "action": "observe-only"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    summary = parse_game_power_jsonl(path)

    assert summary.classification_primary == {
        "fps-target-satisfied": 1,
        "unknown": 2,
    }
    assert summary.classification_advisories == {"foreground-cpu-pressure": 1}
    assert summary.classification_malformed == 1
    assert summary.fps_target_source_counts == {"manual": 1}
    assert summary.fps_target_confidence_counts == {"high": 1}
    assert summary.runtime_telemetry_counts == RuntimeTelemetryCounts(
        foreground_runtime_rows=2,
        unknown_foreground_rows=1,
        foreground_pressure_signals=1,
        supported_foreground_pressure_signals=1,
        unsupported_foreground_pressure_signals=0,
        frame_performance_rows=1,
        fps_target_satisfied_rows=1,
    )
    assert summary.classification_unknown_ratio == 0.5
    assert summary.pressure_supported_ratio == 1.0
    assert summary.pressure_unsupported_ratio == 0.0


def test_runtime_telemetry_counts_persist_for_weighted_aggregate_ratios(tmp_path):
    first = RunSummary(
        appid="1091500",
        tdp_w=12,
        policy="gpu-priority",
        runtime_telemetry_counts=RuntimeTelemetryCounts(
            foreground_runtime_rows=2,
            unknown_foreground_rows=1,
            foreground_pressure_signals=1,
            supported_foreground_pressure_signals=1,
        ),
        restored=True,
    )
    second = replace(
        first,
        runtime_telemetry_counts=RuntimeTelemetryCounts(
            foreground_runtime_rows=8,
            unknown_foreground_rows=1,
            foreground_pressure_signals=3,
            supported_foreground_pressure_signals=1,
            unsupported_foreground_pressure_signals=2,
        ),
    )
    root = tmp_path / "runs"
    for index, run in enumerate((first, second), start=1):
        run_dir = root / f"run-{index}"
        run_dir.mkdir(parents=True)
        payload = asdict(run)
        payload["capture_mode"] = run.capture_mode.value
        (run_dir / "summary.json").write_text(json.dumps(payload) + "\n")

    aggregate = aggregate_run_summaries(
        [
            RunSummary(**json.loads(path.read_text()))
            for path in sorted(root.glob("*/summary.json"))
        ]
    )

    assert aggregate.runtime_telemetry_counts == RuntimeTelemetryCounts(
        foreground_runtime_rows=10,
        unknown_foreground_rows=2,
        foreground_pressure_signals=4,
        supported_foreground_pressure_signals=2,
        unsupported_foreground_pressure_signals=2,
    )
    assert aggregate.classification_unknown_ratio == 0.2
    assert aggregate.pressure_supported_ratio == 0.5
    assert aggregate.pressure_unsupported_ratio == 0.5


def test_target_average_only_does_not_count_as_target_sustained():
    baseline = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="off",
                position="baseline-before",
                avg_fps=42.0,
                one_percent_low_fps=20.0,
                avg_package_w=22.0,
            ),
            controlled_ab_run(
                policy="off",
                position="baseline-after",
                avg_fps=42.0,
                one_percent_low_fps=20.0,
                avg_package_w=22.0,
            ),
        ]
    )
    candidate = aggregate_run_summaries(
        [
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                avg_fps=42.0,
                one_percent_low_fps=20.0,
                avg_package_w=20.0,
            ),
            controlled_ab_run(
                policy="gpu-priority",
                position="candidate",
                pair_id="pair-2",
                base_s=500.0,
                avg_fps=42.0,
                one_percent_low_fps=20.0,
                avg_package_w=20.0,
            ),
        ]
    )

    verdict = compare_policy_aggregates(baseline, candidate, min_runs=2)

    assert verdict.verdict == PolicyVerdict.INCONCLUSIVE
    assert "target sustained" not in verdict.reason


def test_validate_runtime_telemetry_requires_classification_pressure_and_target(tmp_path):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "action": "gpu-priority-cpu-cap",
                "fps_target": 40.0,
                "fps_target_source": "manual",
                "fps_target_confidence": "high",
                "target_frame_ms": 25.0,
                "classification": {"primary": "gpu-package-bound", "advisories": []},
                "pressure": {
                    "cpu": [
                        {"scope": "foreground_cgroup", "supported": True, "some_avg10": 1.0}
                    ],
                    "memory": [],
                    "io": [],
                },
            }
        )
        + "\n"
    )

    verdict = validate_runtime_telemetry(
        game_power_jsonl=path,
        require_classification=True,
        require_pressure=True,
        expect_fps_target=40.0,
        expect_fps_target_source="manual",
        expect_fps_target_confidence="high",
        expect_target_frame_ms=25.0,
        require_cpu_cap_action=True,
    )

    assert verdict["status"] == "pass"
    assert verdict["classification_samples"] == 1
    assert verdict["pressure_samples"] == 1
    assert verdict["cpu_cap_action_reached"] is True


def test_validate_runtime_telemetry_can_require_frame_performance_and_target_satisfied(
    tmp_path,
):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "action": "observe-only",
                "fps_target": 40.0,
                "frame_avg_fps": 56.0,
                "frame_p95_ms": 22.0,
                "frame_performance_sample_count": 20,
                "frame_performance_source": "mangohud-csv",
                "frame_performance_confidence": "high",
                "classification": {"primary": "fps-target-satisfied", "advisories": []},
            }
        )
        + "\n"
    )

    verdict = validate_runtime_telemetry(
        game_power_jsonl=path,
        require_frame_performance=True,
        require_fps_target_satisfied=True,
    )

    assert verdict["status"] == "pass"
    assert verdict["frame_performance_samples"] == 1
    assert verdict["fps_target_satisfied_samples"] == 1


def test_validate_runtime_telemetry_accepts_mangoapp_feed_frame_performance(
    tmp_path,
):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "action": "observe-only",
                "fps_target": 40.0,
                "frame_avg_fps": 56.0,
                "frame_p95_ms": 22.0,
                "frame_performance_sample_count": 20,
                "frame_performance_source": "mangoapp-feed",
                "frame_performance_confidence": "high",
                "classification": {"primary": "fps-target-satisfied", "advisories": []},
            }
        )
        + "\n"
    )

    verdict = validate_runtime_telemetry(
        game_power_jsonl=path,
        require_frame_performance=True,
    )

    assert verdict["status"] == "pass"
    assert verdict["frame_performance_samples"] == 1


def test_validate_runtime_telemetry_fails_when_v5_contract_is_missing(tmp_path):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "action": "gpu-priority-epp",
                "fps_target": 40.0,
                "classification": {"primary": "gpu-package-bound", "advisories": []},
            }
        )
        + "\n"
    )

    try:
        validate_runtime_telemetry(
            game_power_jsonl=path,
            require_frame_performance=True,
            require_fps_target_satisfied=True,
        )
    except ValueError as exc:
        assert "frame-performance telemetry rows are missing" in str(exc)
        assert "fps-target-satisfied classification was not reached" in str(exc)
    else:
        raise AssertionError("expected V5 runtime contract to fail")


def test_validate_runtime_telemetry_requires_high_confidence_frame_performance(
    tmp_path,
):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "action": "observe-only",
                "fps_target": 40.0,
                "frame_avg_fps": 56.0,
                "frame_p95_ms": 22.0,
                "frame_performance_sample_count": 2,
                "frame_performance_confidence": "low",
                "classification": {"primary": "fps-target-satisfied", "advisories": []},
            }
        )
        + "\n"
    )

    try:
        validate_runtime_telemetry(
            game_power_jsonl=path,
            require_frame_performance=True,
        )
    except ValueError as exc:
        assert "frame-performance telemetry rows are missing" in str(exc)
    else:
        raise AssertionError("expected low-confidence frame telemetry to fail")


def test_validate_runtime_telemetry_requires_known_frame_performance_source(
    tmp_path,
):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "action": "observe-only",
                "fps_target": 40.0,
                "frame_avg_fps": 56.0,
                "frame_p95_ms": 22.0,
                "frame_performance_sample_count": 20,
                "frame_performance_confidence": "high",
                "classification": {"primary": "fps-target-satisfied", "advisories": []},
            }
        )
        + "\n"
    )

    try:
        validate_runtime_telemetry(
            game_power_jsonl=path,
            require_frame_performance=True,
        )
    except ValueError as exc:
        assert "frame-performance telemetry rows are missing" in str(exc)
    else:
        raise AssertionError("expected missing frame telemetry source to fail")


def test_replay_action_equivalence_outputs_zero_delta_artifact(tmp_path):
    output = tmp_path / "action-equivalence.json"

    verdict = replay_action_equivalence(output)

    assert verdict["schema_version"] == "game-power-action-equivalence-v1"
    assert verdict["action_delta_count"] == 0
    assert verdict["reason_delta_count"] == 0
    assert json.loads(output.read_text()) == verdict
