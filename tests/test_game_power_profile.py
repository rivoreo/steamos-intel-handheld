import csv
import json
import subprocess
import sys
from pathlib import Path

from steamos_intel_handheld.game_power_profile import (
    CaptureMode,
    GamePowerLogSummary,
    MangoHudFpsSummary,
    PolicyVerdict,
    RunSummary,
    compare_run_summaries,
    parse_game_power_jsonl,
    parse_mangohud_fps_csv,
    parse_mangohud_summary_csv,
    parse_pressure_file,
    summarize_pressure_jsonl,
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


def test_profile_cli_summarize_writes_manifest_and_summary_json(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    game_power = tmp_path / "game-power.jsonl"
    pressure = tmp_path / "cgroup-pressure.jsonl"
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
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert "summary.json" in result.stdout
    assert manifest["appid"] == "1091500"
    assert manifest["policy"] == "gpu-priority"
    assert manifest["capture_mode"] == "imported"
    assert summary["avg_fps"] == 42.0
    assert summary["avg_uncore_w"] == 9.0
    assert summary["cpu_pressure_some_avg10_peak"] == 2.5
    assert summary["cpu_pressure_full_avg10_peak"] == 0.4
    assert summary["restored"] is True


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
    assert summary["epp"] == "balance_power"
    assert summary["pcore_max_mhz"] == 3000
    assert summary["ecore_max_mhz"] == 2200
    assert summary["cpu_cap_enabled"] is True


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
