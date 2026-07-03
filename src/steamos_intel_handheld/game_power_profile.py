#!/usr/bin/env python3
"""Profiler and comparison helpers for game-power policy experiments."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from statistics import mean
from typing import Any


class CaptureMode(str, Enum):
    CONTROLLED = "controlled"
    IMPORTED = "imported"


@dataclass(frozen=True)
class MangoHudFpsSummary:
    avg_fps: float | None = None
    one_percent_low_fps: float | None = None
    point_one_percent_low_fps: float | None = None
    ninety_seven_percentile_fps: float | None = None
    avg_frametime_ms: float | None = None
    p95_frametime_ms: float | None = None
    p99_frametime_ms: float | None = None
    capture_mode: CaptureMode = CaptureMode.IMPORTED


@dataclass(frozen=True)
class GamePowerLogSummary:
    samples: int
    avg_package_w: float | None = None
    avg_core_w: float | None = None
    avg_uncore_w: float | None = None
    avg_core_share: float | None = None
    avg_uncore_share: float | None = None
    avg_render_busy: float | None = None
    actions: dict[str, int] | None = None


@dataclass(frozen=True)
class RunSummary:
    appid: str
    tdp_w: int
    policy: str
    capture_mode: CaptureMode = CaptureMode.IMPORTED
    avg_fps: float | None = None
    one_percent_low_fps: float | None = None
    point_one_percent_low_fps: float | None = None
    avg_frametime_ms: float | None = None
    p95_frametime_ms: float | None = None
    p99_frametime_ms: float | None = None
    avg_package_w: float | None = None
    avg_core_w: float | None = None
    avg_uncore_w: float | None = None
    avg_core_share: float | None = None
    avg_uncore_share: float | None = None
    avg_render_busy: float | None = None
    cpu_pressure_some_avg10_peak: float | None = None
    cpu_pressure_full_avg10_peak: float | None = None
    actions: dict[str, int] | None = None
    restored: bool = False


class PolicyVerdict(str, Enum):
    BETTER = "better"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    NEEDS_CONTROLLED_CAPTURE = "needs-controlled-capture"


@dataclass(frozen=True)
class PolicyComparison:
    baseline_policy: str
    candidate_policy: str
    verdict: PolicyVerdict
    reason: str


def parse_mangohud_summary_csv(
    path: str | Path,
    *,
    capture_mode: CaptureMode = CaptureMode.IMPORTED,
) -> MangoHudFpsSummary:
    with Path(path).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"MangoHud summary CSV is empty: {path}")
    row = rows[0]
    return MangoHudFpsSummary(
        avg_fps=_float(row.get("Average FPS")),
        one_percent_low_fps=_float(row.get("1% Min FPS")),
        point_one_percent_low_fps=_float(row.get("0.1% Min FPS")),
        ninety_seven_percentile_fps=_float(row.get("97% Percentile FPS")),
        capture_mode=capture_mode,
    )


def parse_mangohud_fps_csv(
    path: str | Path,
    *,
    capture_mode: CaptureMode = CaptureMode.IMPORTED,
) -> MangoHudFpsSummary:
    fps_values: list[float] = []
    frametime_values: list[float] = []
    with Path(path).open(newline="") as handle:
        for row in csv.DictReader(handle):
            fps = _float(row.get("fps"))
            frametime = _float(row.get("frametime"))
            if fps is not None:
                fps_values.append(fps)
            if frametime is not None:
                frametime_values.append(frametime)
    if not fps_values and not frametime_values:
        raise ValueError(f"MangoHud FPS CSV has no fps or frametime rows: {path}")
    return MangoHudFpsSummary(
        avg_fps=round(mean(fps_values), 3) if fps_values else None,
        one_percent_low_fps=_low_fps(fps_values, 0.01),
        point_one_percent_low_fps=_low_fps(fps_values, 0.001),
        avg_frametime_ms=round(mean(frametime_values), 3) if frametime_values else None,
        p95_frametime_ms=_high_percentile(frametime_values, 0.95),
        p99_frametime_ms=_high_percentile(frametime_values, 0.99),
        capture_mode=capture_mode,
    )


def parse_game_power_jsonl(path: str | Path) -> GamePowerLogSummary:
    package_w: list[float] = []
    core_w: list[float] = []
    uncore_w: list[float] = []
    render_busy: list[float] = []
    actions: dict[str, int] = {}
    samples = 0
    with Path(path).open() as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            samples += 1
            _append_float(package_w, row.get("package_w"))
            _append_float(core_w, row.get("core_w"))
            _append_float(uncore_w, row.get("uncore_w"))
            _append_float(render_busy, row.get("render_busy"))
            action = str(row.get("action") or "")
            if action:
                actions[action] = actions.get(action, 0) + 1
    avg_package = _avg(package_w)
    avg_core = _avg(core_w)
    avg_uncore = _avg(uncore_w)
    return GamePowerLogSummary(
        samples=samples,
        avg_package_w=avg_package,
        avg_core_w=avg_core,
        avg_uncore_w=avg_uncore,
        avg_core_share=_ratio(avg_core, avg_package),
        avg_uncore_share=_ratio(avg_uncore, avg_package),
        avg_render_busy=_avg(render_busy),
        actions=actions,
    )


def merge_run_summary(
    *,
    appid: str,
    tdp_w: int,
    policy: str,
    fps: MangoHudFpsSummary,
    power: GamePowerLogSummary | None,
    pressure: dict[str, float] | None = None,
    restored: bool,
) -> RunSummary:
    pressure = pressure or {}
    return RunSummary(
        appid=appid,
        tdp_w=tdp_w,
        policy=policy,
        capture_mode=fps.capture_mode,
        avg_fps=fps.avg_fps,
        one_percent_low_fps=fps.one_percent_low_fps,
        point_one_percent_low_fps=fps.point_one_percent_low_fps,
        avg_frametime_ms=fps.avg_frametime_ms,
        p95_frametime_ms=fps.p95_frametime_ms,
        p99_frametime_ms=fps.p99_frametime_ms,
        avg_package_w=power.avg_package_w if power else None,
        avg_core_w=power.avg_core_w if power else None,
        avg_uncore_w=power.avg_uncore_w if power else None,
        avg_core_share=power.avg_core_share if power else None,
        avg_uncore_share=power.avg_uncore_share if power else None,
        avg_render_busy=power.avg_render_busy if power else None,
        cpu_pressure_some_avg10_peak=pressure.get("cpu_pressure_some_avg10_peak"),
        cpu_pressure_full_avg10_peak=pressure.get("cpu_pressure_full_avg10_peak"),
        actions=power.actions if power else None,
        restored=restored,
    )


def compare_run_summaries(baseline: RunSummary, candidate: RunSummary) -> PolicyComparison:
    if baseline.capture_mode != CaptureMode.CONTROLLED:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.NEEDS_CONTROLLED_CAPTURE,
            "baseline uses imported capture; controlled A/B capture is required",
        )
    if candidate.capture_mode != CaptureMode.CONTROLLED:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.NEEDS_CONTROLLED_CAPTURE,
            "candidate uses imported capture; controlled A/B capture is required",
        )
    if not baseline.restored or not candidate.restored:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "restore verification did not pass for both runs",
        )

    low_gain = _percent_change(baseline.one_percent_low_fps, candidate.one_percent_low_fps)
    avg_gain = _percent_change(baseline.avg_fps, candidate.avg_fps)
    p99_gain = _lower_is_better_change(baseline.p99_frametime_ms, candidate.p99_frametime_ms)

    if low_gain is not None and low_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            f"1% low improved by {low_gain:.1f}% with average FPS change {avg_gain or 0:.1f}%",
        )
    if p99_gain is not None and p99_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        reason = (
            f"p99 frametime improved by {p99_gain:.1f}% "
            f"with average FPS change {avg_gain or 0:.1f}%"
        )
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            reason,
        )
    if avg_gain is not None and avg_gain >= 5.0 and (low_gain is None or low_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            f"average FPS improved by {avg_gain:.1f}% without low-percentile regression",
        )
    if low_gain is not None and low_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"1% low worsened by {abs(low_gain):.1f}%",
        )
    if p99_gain is not None and p99_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"p99 frametime worsened by {abs(p99_gain):.1f}%",
        )
    return PolicyComparison(
        baseline.policy,
        candidate.policy,
        PolicyVerdict.INCONCLUSIVE,
        "candidate did not meet improvement or rejection thresholds",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    summarize = subcommands.add_parser("summarize")
    summarize.add_argument("--appid", required=True)
    summarize.add_argument("--tdp-w", required=True, type=int)
    summarize.add_argument("--policy", required=True)
    summarize.add_argument(
        "--capture-mode",
        choices=[mode.value for mode in CaptureMode],
        default=CaptureMode.IMPORTED.value,
    )
    summarize.add_argument("--mangohud-csv")
    summarize.add_argument("--mangohud-summary-csv")
    summarize.add_argument("--game-power-jsonl")
    summarize.add_argument("--pressure-jsonl")
    summarize.add_argument("--output", required=True)
    summarize.add_argument("--restored", choices=["true", "false"], default="true")

    compare = subcommands.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    return parser


def run_summarize(args: argparse.Namespace) -> Path:
    capture_mode = CaptureMode(args.capture_mode)
    if args.mangohud_summary_csv:
        fps = parse_mangohud_summary_csv(args.mangohud_summary_csv, capture_mode=capture_mode)
    elif args.mangohud_csv:
        fps = parse_mangohud_fps_csv(args.mangohud_csv, capture_mode=capture_mode)
    else:
        raise SystemExit("summarize requires --mangohud-csv or --mangohud-summary-csv")

    power = parse_game_power_jsonl(args.game_power_jsonl) if args.game_power_jsonl else None
    pressure = summarize_pressure_jsonl(args.pressure_jsonl) if args.pressure_jsonl else None
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "appid": args.appid,
        "tdp_w": args.tdp_w,
        "policy": args.policy,
        "capture_mode": capture_mode.value,
    }
    summary = merge_run_summary(
        appid=args.appid,
        tdp_w=args.tdp_w,
        policy=args.policy,
        fps=fps,
        power=power,
        pressure=pressure,
        restored=args.restored == "true",
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / "summary.json").write_text(
        json.dumps(_json_ready(asdict(summary)), indent=2, sort_keys=True) + "\n"
    )
    return output / "summary.json"


def run_compare(args: argparse.Namespace) -> PolicyComparison:
    baseline = _load_run_summary(args.baseline)
    candidate = _load_run_summary(args.candidate)
    return compare_run_summaries(baseline, candidate)


def _load_run_summary(path: str | Path) -> RunSummary:
    payload = json.loads(Path(path).read_text())
    if "capture_mode" in payload:
        payload["capture_mode"] = CaptureMode(payload["capture_mode"])
    return RunSummary(**payload)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def parse_pressure_file(text: str) -> dict[str, dict[str, float]]:
    parsed: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        category = parts[0]
        parsed[category] = {}
        for item in parts[1:]:
            key, value = item.split("=", 1)
            parsed[category][key] = float(value)
    return parsed


def summarize_pressure_jsonl(path: str | Path) -> dict[str, float]:
    some_peak = 0.0
    full_peak = 0.0
    with Path(path).open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            cpu = row.get("cpu") or {}
            some_peak = max(some_peak, _pressure_avg10(cpu, "some"))
            full_peak = max(full_peak, _pressure_avg10(cpu, "full"))
    return {
        "cpu_pressure_some_avg10_peak": round(some_peak, 3),
        "cpu_pressure_full_avg10_peak": round(full_peak, 3),
    }


def _pressure_avg10(cpu: dict[str, object], category: str) -> float:
    values = cpu.get(category)
    if not isinstance(values, dict):
        return 0.0
    return float(values.get("avg10") or 0.0)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "summarize":
        path = run_summarize(args)
        print(path)
        return
    if args.command == "compare":
        comparison = run_compare(args)
        print(json.dumps(_json_ready(asdict(comparison)), sort_keys=True))
        return
    raise SystemExit(f"unsupported command: {args.command}")


def _float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _append_float(values: list[float], value: object) -> None:
    parsed = _float(value)
    if parsed is not None:
        values.append(parsed)


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def _ratio(part: float | None, total: float | None) -> float | None:
    if part is None or total is None or total <= 0:
        return None
    return round(part / total, 3)


def _percent_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / old * 100.0


def _lower_is_better_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (old - new) / old * 100.0


def _low_fps(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    count = max(1, math.ceil(len(ordered) * fraction))
    return round(mean(ordered[:count]), 3)


def _high_percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)


if __name__ == "__main__":
    main()
