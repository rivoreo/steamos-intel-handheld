#!/usr/bin/env python3
"""Profiler and comparison helpers for game-power policy experiments."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from statistics import mean


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
