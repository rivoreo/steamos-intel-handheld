import csv
import json
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
        ["0.1% Min FPS", "1% Min FPS", "97% Percentile FPS", "Average FPS"],
        [
            {
                "0.1% Min FPS": "24.1",
                "1% Min FPS": "31.2",
                "97% Percentile FPS": "45.8",
                "Average FPS": "42.3",
            }
        ],
    )

    summary = parse_mangohud_summary_csv(path)

    assert isinstance(summary, MangoHudFpsSummary)
    assert summary.avg_fps == 42.3
    assert summary.one_percent_low_fps == 31.2
    assert summary.point_one_percent_low_fps == 24.1
    assert summary.ninety_seven_percentile_fps == 45.8
    assert summary.capture_mode == CaptureMode.IMPORTED


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
