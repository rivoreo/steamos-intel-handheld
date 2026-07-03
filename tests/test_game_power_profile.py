import csv
import json
import subprocess
import sys
from pathlib import Path

from steamos_intel_handheld.game_power_profile import (
    CaptureMode,
    CpuTopologySummary,
    FpsTargetDiscovery,
    GamePowerLogSummary,
    MangoHudFpsSummary,
    PolicyVerdict,
    ProcessCgroupSummary,
    RestoreAffinitySummary,
    RunSummary,
    ThreadAffinitySummary,
    ThreadSchedstatSummary,
    aggregate_run_summaries,
    build_affinity_advice,
    build_background_shaping_advice,
    compare_policy_aggregates,
    compare_run_summaries,
    parse_game_power_jsonl,
    parse_gamescope_fps_target_from_argv,
    parse_mangohud_fps_csv,
    parse_mangohud_summary_csv,
    parse_pressure_file,
    summarize_cpu_topology,
    summarize_pressure_jsonl,
    summarize_process_cgroups_jsonl,
    summarize_restore_affinity_json,
    summarize_thread_affinity_jsonl,
    summarize_thread_schedstat_jsonl,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
    assert summary.files == [
        "cpu.uclamp.max",
        "cpu.uclamp.min",
        "cpuset.cpus",
        "cpuset.cpus.effective",
    ]


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

    assert verdict.verdict == PolicyVerdict.BETTER
    assert "1% low improved" in verdict.reason


def test_compare_run_summaries_accepts_power_saving_when_target_is_sustained():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        capture_mode=CaptureMode.CONTROLLED,
        fps_target=40.0,
        avg_fps=42.0,
        one_percent_low_fps=31.0,
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
        one_percent_low_fps=30.8,
        p99_frametime_ms=35.6,
        avg_package_w=20.2,
        restored=True,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.BETTER
    assert "target sustained" in verdict.reason
    assert "package power reduced" in verdict.reason


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

    assert verdict.verdict == PolicyVerdict.BETTER
    assert "median 1% low improved" in verdict.reason


def test_compare_policy_aggregates_accepts_median_power_saving_at_target():
    baseline = aggregate_run_summaries(
        [
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="off",
                capture_mode=CaptureMode.CONTROLLED,
                fps_target=40.0,
                avg_fps=42.0,
                one_percent_low_fps=31.0,
                p99_frametime_ms=35.0,
                avg_package_w=22.0,
                restored=True,
            ),
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="off",
                capture_mode=CaptureMode.CONTROLLED,
                fps_target=40.0,
                avg_fps=41.5,
                one_percent_low_fps=30.6,
                p99_frametime_ms=35.4,
                avg_package_w=21.8,
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
                fps_target=40.0,
                avg_fps=40.9,
                one_percent_low_fps=30.4,
                p99_frametime_ms=35.8,
                avg_package_w=20.2,
                restored=True,
            ),
            RunSummary(
                appid="1091500",
                tdp_w=22,
                policy="gpu-priority",
                capture_mode=CaptureMode.CONTROLLED,
                fps_target=40.0,
                avg_fps=40.6,
                one_percent_low_fps=30.3,
                p99_frametime_ms=35.7,
                avg_package_w=20.0,
                restored=True,
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
    assert summary["restore_affinity_files"] == [
        "cpu.uclamp.max",
        "cpu.uclamp.min",
        "cpuset.cpus.effective",
    ]
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
    assert payload["verdict"] == "better"
    assert payload["candidate_policy"] == "gpu-priority"


def test_profile_cli_aggregate_scans_profile_root_and_compares_repeated_runs(tmp_path):
    runs = [
        ("001-off", "off", 54.0, 40.0),
        ("002-gpu", "gpu-priority", 55.0, 43.0),
        ("003-off", "off", 55.0, 40.5),
        ("004-gpu", "gpu-priority", 56.0, 44.0),
    ]
    for dirname, policy, avg_fps, low_fps in runs:
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
    assert payload["comparisons"][0]["baseline"]["sample_count"] == 2
    assert payload["comparisons"][0]["candidate"]["sample_count"] == 2
    assert payload["comparisons"][0]["candidate"]["one_percent_low_fps_median"] == 43.5


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
                    "restore_affinity_thread_count": 3,
                    "restore_affinity_cgroup_count": 1,
                    "restore_affinity_files": [
                        "cpu.uclamp.max",
                        "cpu.uclamp.min",
                        "cpuset.cpus.effective",
                    ],
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
        "candidate_control": "soft-compact-preferred-cpus",
        "guarded_variant": "foreground-role-soft-compact",
        "preferred_cpus": [0, 1],
        "fallback": "restore-original-affinity-and-cgroup-state",
        "observed_run_count": 2,
        "run_coverage": 1.0,
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
                    "restore_affinity_cgroup_count": 2,
                    "restore_affinity_files": [
                        "cpu.uclamp.max",
                        "cpu.uclamp.min",
                        "cpu.weight",
                        "cpuset.cpus.effective",
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

    comparison = json.loads(result.stdout)["comparisons"][0]
    candidate_background = comparison["candidate_background_shaping_candidates"]
    assert candidate_background[0] == {
        "candidate_key": "steam-helper:0::/user.slice/app-steam-client.scope",
        "cgroup": "0::/user.slice/app-steam-client.scope",
        "classification": "steam-helper",
        "suggested_action": "future-cpu-weight-candidate",
        "observed_run_count": 2,
        "run_coverage": 1.0,
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
        "cpu_time_s_delta_median": 2.2,
    }
    assert "background/helper cgroup candidate is stable across candidate runs" in plan[
        "reasons"
    ]


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
