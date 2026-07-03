#!/usr/bin/env python3
"""Profiler and comparison helpers for game-power policy experiments."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from statistics import mean, median
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
class ThreadAffinitySummary:
    samples: int
    observed_threads: int
    hot_threads: list[dict[str, object]]


@dataclass(frozen=True)
class RunSummary:
    appid: str
    tdp_w: int
    policy: str
    capture_mode: CaptureMode = CaptureMode.IMPORTED
    duration_s: float | None = None
    warmup_s: float | None = None
    poll_s: float | None = None
    epp: str | None = None
    pcore_max_mhz: int | None = None
    ecore_max_mhz: int | None = None
    cpu_cap_enabled: bool | None = None
    cpu_cap_core_share_threshold: float | None = None
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
    thread_affinity_samples: int | None = None
    thread_affinity_observed_threads: int | None = None
    thread_affinity_hot_threads: list[dict[str, object]] | None = None
    actions: dict[str, int] | None = None
    restored: bool = False


@dataclass(frozen=True)
class PolicyAggregate:
    appid: str
    tdp_w: int
    policy: str
    capture_mode: CaptureMode
    sample_count: int
    restored_count: int
    duration_s: float | None = None
    warmup_s: float | None = None
    poll_s: float | None = None
    epp: str | None = None
    pcore_max_mhz: int | None = None
    ecore_max_mhz: int | None = None
    cpu_cap_enabled: bool = False
    cpu_cap_core_share_threshold: float | None = None
    avg_fps_median: float | None = None
    one_percent_low_fps_median: float | None = None
    point_one_percent_low_fps_median: float | None = None
    avg_frametime_ms_median: float | None = None
    p95_frametime_ms_median: float | None = None
    p99_frametime_ms_median: float | None = None
    avg_package_w_median: float | None = None
    avg_core_w_median: float | None = None
    avg_uncore_w_median: float | None = None
    avg_core_share_median: float | None = None
    avg_uncore_share_median: float | None = None
    avg_render_busy_median: float | None = None
    cpu_pressure_some_avg10_peak_median: float | None = None
    cpu_pressure_full_avg10_peak_median: float | None = None


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
    return _parse_mangohud_summary_row(rows[0], capture_mode=capture_mode)


def parse_mangohud_fps_csv(
    path: str | Path,
    *,
    capture_mode: CaptureMode = CaptureMode.IMPORTED,
) -> MangoHudFpsSummary:
    fps_values: list[float] = []
    frametime_values: list[float] = []
    with Path(path).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
        for row in rows:
            fps = _float(row.get("fps"))
            frametime = _float(row.get("frametime"))
            if fps is not None:
                fps_values.append(fps)
            if frametime is not None:
                frametime_values.append(frametime)
    if not fps_values and not frametime_values and rows:
        summary = _parse_mangohud_summary_row(rows[0], capture_mode=capture_mode)
        if summary.avg_fps is not None or summary.one_percent_low_fps is not None:
            return summary
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


def summarize_thread_affinity_jsonl(
    path: str | Path,
    *,
    hot_thread_limit: int = 5,
) -> ThreadAffinitySummary:
    samples = 0
    threads: dict[int, dict[str, object]] = {}
    with Path(path).open() as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            samples += 1
            row = json.loads(text)
            for item in row.get("threads") or []:
                if not isinstance(item, dict):
                    continue
                tid = _optional_int(item.get("tid"))
                if tid is None:
                    continue
                state = threads.setdefault(
                    tid,
                    {
                        "tid": tid,
                        "comm": None,
                        "cgroup": None,
                        "cpu_first": None,
                        "cpu_last": None,
                        "migration_first": None,
                        "migration_last": None,
                        "voluntary_ctxt_switches_first": None,
                        "voluntary_ctxt_switches_last": None,
                        "nonvoluntary_ctxt_switches_first": None,
                        "nonvoluntary_ctxt_switches_last": None,
                        "cpus_seen": set(),
                        "affinity_masks": set(),
                    },
                )
                comm = _optional_str(item.get("comm"))
                if comm:
                    state["comm"] = comm
                cgroup = _optional_str(item.get("cgroup"))
                if cgroup:
                    state["cgroup"] = cgroup
                cpu_time_s = _float(item.get("cpu_time_s"))
                if cpu_time_s is not None:
                    if state["cpu_first"] is None:
                        state["cpu_first"] = cpu_time_s
                    state["cpu_last"] = cpu_time_s
                migrations = _optional_int(item.get("migration_count"))
                if migrations is not None:
                    if state["migration_first"] is None:
                        state["migration_first"] = migrations
                    state["migration_last"] = migrations
                voluntary_switches = _optional_int(item.get("voluntary_ctxt_switches"))
                if voluntary_switches is not None:
                    if state["voluntary_ctxt_switches_first"] is None:
                        state["voluntary_ctxt_switches_first"] = voluntary_switches
                    state["voluntary_ctxt_switches_last"] = voluntary_switches
                involuntary_switches = _optional_int(
                    item.get("nonvoluntary_ctxt_switches")
                )
                if involuntary_switches is not None:
                    if state["nonvoluntary_ctxt_switches_first"] is None:
                        state["nonvoluntary_ctxt_switches_first"] = involuntary_switches
                    state["nonvoluntary_ctxt_switches_last"] = involuntary_switches
                current_cpu = _optional_int(item.get("current_cpu"))
                if current_cpu is not None:
                    cpus_seen = state["cpus_seen"]
                    assert isinstance(cpus_seen, set)
                    cpus_seen.add(current_cpu)
                affinity = _optional_str(item.get("affinity"))
                if affinity:
                    affinity_masks = state["affinity_masks"]
                    assert isinstance(affinity_masks, set)
                    affinity_masks.add(affinity)

    hot_threads = [_thread_hotspot(state) for state in threads.values()]
    hot_threads.sort(
        key=lambda item: (
            -float(item["cpu_time_s_delta"]),
            -int(item["migration_delta"]),
            int(item["tid"]),
        )
    )
    return ThreadAffinitySummary(
        samples=samples,
        observed_threads=len(threads),
        hot_threads=hot_threads[:hot_thread_limit],
    )


def merge_run_summary(
    *,
    appid: str,
    tdp_w: int,
    policy: str,
    fps: MangoHudFpsSummary,
    power: GamePowerLogSummary | None,
    pressure: dict[str, float] | None = None,
    thread_affinity: ThreadAffinitySummary | None = None,
    epp: str | None = None,
    pcore_max_mhz: int | None = None,
    ecore_max_mhz: int | None = None,
    cpu_cap_enabled: bool | None = None,
    cpu_cap_core_share_threshold: float | None = None,
    duration_s: float | None = None,
    warmup_s: float | None = None,
    poll_s: float | None = None,
    restored: bool,
) -> RunSummary:
    pressure = pressure or {}
    return RunSummary(
        appid=appid,
        tdp_w=tdp_w,
        policy=policy,
        capture_mode=fps.capture_mode,
        duration_s=duration_s,
        warmup_s=warmup_s,
        poll_s=poll_s,
        epp=epp,
        pcore_max_mhz=pcore_max_mhz,
        ecore_max_mhz=ecore_max_mhz,
        cpu_cap_enabled=cpu_cap_enabled,
        cpu_cap_core_share_threshold=cpu_cap_core_share_threshold,
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
        thread_affinity_samples=thread_affinity.samples if thread_affinity else None,
        thread_affinity_observed_threads=(
            thread_affinity.observed_threads if thread_affinity else None
        ),
        thread_affinity_hot_threads=thread_affinity.hot_threads if thread_affinity else None,
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


def aggregate_run_summaries(runs: list[RunSummary]) -> PolicyAggregate:
    if not runs:
        raise ValueError("cannot aggregate an empty run set")
    first = runs[0]
    first_experiment = _experiment_settings(first)
    first_tunables = _effective_tunables(first)
    for run in runs[1:]:
        if run.appid != first.appid:
            raise ValueError("cannot aggregate runs with different appids")
        if run.tdp_w != first.tdp_w:
            raise ValueError("cannot aggregate runs with different TDP values")
        if run.policy != first.policy:
            raise ValueError("cannot aggregate runs with different policies")
        if run.capture_mode != first.capture_mode:
            raise ValueError("cannot aggregate runs with different capture modes")
        if _experiment_settings(run) != first_experiment:
            raise ValueError("cannot aggregate runs with different capture timing")
        if _effective_tunables(run) != first_tunables:
            raise ValueError("cannot aggregate runs with different effective tunables")
    duration_s, warmup_s, poll_s = first_experiment
    epp, pcore_max_mhz, ecore_max_mhz, cpu_cap_enabled, threshold = first_tunables
    return PolicyAggregate(
        appid=first.appid,
        tdp_w=first.tdp_w,
        policy=first.policy,
        capture_mode=first.capture_mode,
        sample_count=len(runs),
        restored_count=sum(1 for run in runs if run.restored),
        duration_s=duration_s,
        warmup_s=warmup_s,
        poll_s=poll_s,
        epp=epp,
        pcore_max_mhz=pcore_max_mhz,
        ecore_max_mhz=ecore_max_mhz,
        cpu_cap_enabled=cpu_cap_enabled,
        cpu_cap_core_share_threshold=threshold,
        avg_fps_median=_median([run.avg_fps for run in runs]),
        one_percent_low_fps_median=_median([run.one_percent_low_fps for run in runs]),
        point_one_percent_low_fps_median=_median(
            [run.point_one_percent_low_fps for run in runs]
        ),
        avg_frametime_ms_median=_median([run.avg_frametime_ms for run in runs]),
        p95_frametime_ms_median=_median([run.p95_frametime_ms for run in runs]),
        p99_frametime_ms_median=_median([run.p99_frametime_ms for run in runs]),
        avg_package_w_median=_median([run.avg_package_w for run in runs]),
        avg_core_w_median=_median([run.avg_core_w for run in runs]),
        avg_uncore_w_median=_median([run.avg_uncore_w for run in runs]),
        avg_core_share_median=_median([run.avg_core_share for run in runs]),
        avg_uncore_share_median=_median([run.avg_uncore_share for run in runs]),
        avg_render_busy_median=_median([run.avg_render_busy for run in runs]),
        cpu_pressure_some_avg10_peak_median=_median(
            [run.cpu_pressure_some_avg10_peak for run in runs]
        ),
        cpu_pressure_full_avg10_peak_median=_median(
            [run.cpu_pressure_full_avg10_peak for run in runs]
        ),
    )


def compare_policy_aggregates(
    baseline: PolicyAggregate,
    candidate: PolicyAggregate,
    *,
    min_runs: int = 3,
) -> PolicyComparison:
    if baseline.appid != candidate.appid or baseline.tdp_w != candidate.tdp_w:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "baseline and candidate do not target the same appid/TDP",
        )
    if baseline.sample_count < min_runs or candidate.sample_count < min_runs:
        baseline_unit = "run" if baseline.sample_count == 1 else "runs"
        candidate_unit = "run" if candidate.sample_count == 1 else "runs"
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.INCONCLUSIVE,
            (
                f"baseline has {baseline.sample_count} {baseline_unit} and candidate has "
                f"{candidate.sample_count} {candidate_unit}; min-runs is {min_runs}"
            ),
        )
    if baseline.capture_mode != CaptureMode.CONTROLLED:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.NEEDS_CONTROLLED_CAPTURE,
            "baseline aggregate uses imported capture; controlled A/B capture is required",
        )
    if candidate.capture_mode != CaptureMode.CONTROLLED:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.NEEDS_CONTROLLED_CAPTURE,
            "candidate aggregate uses imported capture; controlled A/B capture is required",
        )
    if (
        baseline.restored_count != baseline.sample_count
        or candidate.restored_count != candidate.sample_count
    ):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "restore verification did not pass for every aggregated run",
        )

    low_gain = _percent_change(
        baseline.one_percent_low_fps_median,
        candidate.one_percent_low_fps_median,
    )
    avg_gain = _percent_change(baseline.avg_fps_median, candidate.avg_fps_median)
    p99_gain = _lower_is_better_change(
        baseline.p99_frametime_ms_median,
        candidate.p99_frametime_ms_median,
    )

    if low_gain is not None and low_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            (
                f"median 1% low improved by {low_gain:.1f}% "
                f"with median average FPS change {avg_gain or 0:.1f}%"
            ),
        )
    if p99_gain is not None and p99_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            (
                f"median p99 frametime improved by {p99_gain:.1f}% "
                f"with median average FPS change {avg_gain or 0:.1f}%"
            ),
        )
    if avg_gain is not None and avg_gain >= 5.0 and (low_gain is None or low_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            f"median average FPS improved by {avg_gain:.1f}% without low-percentile regression",
        )
    if low_gain is not None and low_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"median 1% low worsened by {abs(low_gain):.1f}%",
        )
    if p99_gain is not None and p99_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"median p99 frametime worsened by {abs(p99_gain):.1f}%",
        )
    return PolicyComparison(
        baseline.policy,
        candidate.policy,
        PolicyVerdict.INCONCLUSIVE,
        "candidate medians did not meet improvement or rejection thresholds",
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
    summarize.add_argument("--thread-affinity-jsonl")
    summarize.add_argument("--epp")
    summarize.add_argument("--pcore-max-mhz", type=int)
    summarize.add_argument("--ecore-max-mhz", type=int)
    summarize.add_argument("--cpu-cap-enabled", choices=["true", "false"])
    summarize.add_argument("--cpu-cap-core-share-threshold", type=float)
    summarize.add_argument("--duration-s", type=float)
    summarize.add_argument("--warmup-s", type=float)
    summarize.add_argument("--poll-s", type=float)
    summarize.add_argument("--output", required=True)
    summarize.add_argument("--restored", choices=["true", "false"], default="true")

    compare = subcommands.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)

    aggregate = subcommands.add_parser("aggregate")
    aggregate.add_argument("--root", action="append", required=True)
    aggregate.add_argument("--baseline-policy", default="off")
    aggregate.add_argument("--candidate-policy", action="append", required=True)
    aggregate.add_argument("--appid")
    aggregate.add_argument("--tdp-w", type=int)
    aggregate.add_argument("--duration-s", type=float)
    aggregate.add_argument("--warmup-s", type=float)
    aggregate.add_argument("--poll-s", type=float)
    aggregate.add_argument(
        "--capture-mode",
        choices=[mode.value for mode in CaptureMode],
        default=CaptureMode.CONTROLLED.value,
    )
    aggregate.add_argument("--min-runs", type=int, default=3)
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
    thread_affinity = (
        summarize_thread_affinity_jsonl(args.thread_affinity_jsonl)
        if args.thread_affinity_jsonl
        else None
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "appid": args.appid,
        "tdp_w": args.tdp_w,
        "policy": args.policy,
        "capture_mode": capture_mode.value,
        "epp": args.epp,
        "pcore_max_mhz": args.pcore_max_mhz,
        "ecore_max_mhz": args.ecore_max_mhz,
        "cpu_cap_enabled": _optional_bool(args.cpu_cap_enabled),
        "cpu_cap_core_share_threshold": args.cpu_cap_core_share_threshold,
        "duration_s": args.duration_s,
        "warmup_s": args.warmup_s,
        "poll_s": args.poll_s,
        "thread_affinity_jsonl": bool(args.thread_affinity_jsonl),
    }
    summary = merge_run_summary(
        appid=args.appid,
        tdp_w=args.tdp_w,
        policy=args.policy,
        fps=fps,
        power=power,
        pressure=pressure,
        thread_affinity=thread_affinity,
        epp=args.epp,
        pcore_max_mhz=args.pcore_max_mhz,
        ecore_max_mhz=args.ecore_max_mhz,
        cpu_cap_enabled=_optional_bool(args.cpu_cap_enabled),
        cpu_cap_core_share_threshold=args.cpu_cap_core_share_threshold,
        duration_s=args.duration_s,
        warmup_s=args.warmup_s,
        poll_s=args.poll_s,
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


def run_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    capture_mode = CaptureMode(args.capture_mode)
    summaries = []
    for path in _discover_summary_paths(args.root):
        summary = _load_run_summary(path)
        if args.appid and summary.appid != args.appid:
            continue
        if args.tdp_w is not None and summary.tdp_w != args.tdp_w:
            continue
        if args.duration_s is not None and summary.duration_s != args.duration_s:
            continue
        if args.warmup_s is not None and summary.warmup_s != args.warmup_s:
            continue
        if args.poll_s is not None and summary.poll_s != args.poll_s:
            continue
        if summary.capture_mode != capture_mode:
            continue
        if summary.policy != args.baseline_policy and summary.policy not in args.candidate_policy:
            continue
        summaries.append(summary)

    groups: dict[tuple[object, ...], list[RunSummary]] = defaultdict(list)
    for summary in summaries:
        groups[_profile_group_key(summary)].append(summary)

    baseline_keys_by_context: dict[tuple[object, ...], list[tuple[object, ...]]] = defaultdict(
        list
    )
    for key in groups:
        _appid, _tdp_w, policy = key[:3]
        if policy == args.baseline_policy:
            baseline_keys_by_context[_comparison_context_key(key)].append(key)

    comparisons = []
    candidate_keys = sorted(
        (key for key in groups if key[2] in args.candidate_policy),
        key=_sortable_group_key,
    )
    for candidate_key in candidate_keys:
        appid, tdp_w, _candidate_policy = candidate_key[:3]
        baseline_keys = baseline_keys_by_context.get(_comparison_context_key(candidate_key), [])
        for baseline_key in sorted(baseline_keys, key=_sortable_group_key):
            baseline_runs = groups[baseline_key]
            candidate_runs = groups[candidate_key]
            baseline = aggregate_run_summaries(baseline_runs)
            candidate = aggregate_run_summaries(candidate_runs)
            comparison = compare_policy_aggregates(
                baseline,
                candidate,
                min_runs=args.min_runs,
            )
            comparisons.append(
                {
                    "appid": appid,
                    "tdp_w": tdp_w,
                    "baseline": asdict(baseline),
                    "candidate": asdict(candidate),
                    "comparison": asdict(comparison),
                }
            )

    return {
        "baseline_policy": args.baseline_policy,
        "candidate_policies": args.candidate_policy,
        "capture_mode": capture_mode,
        "min_runs": args.min_runs,
        "comparisons": comparisons,
    }


def _load_run_summary(path: str | Path) -> RunSummary:
    payload = json.loads(Path(path).read_text())
    if "capture_mode" in payload:
        payload["capture_mode"] = CaptureMode(payload["capture_mode"])
    return RunSummary(**payload)


def _discover_summary_paths(roots: list[str]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        path = Path(root)
        if path.is_file():
            paths.append(path)
            continue
        summary = path / "summary.json"
        if summary.is_file():
            paths.append(summary)
            continue
        paths.extend(sorted(path.rglob("summary.json")))
    return sorted(paths)


def _profile_group_key(run: RunSummary) -> tuple[object, ...]:
    return (
        run.appid,
        run.tdp_w,
        run.policy,
        *_experiment_settings(run),
        *_effective_tunables(run),
    )


def _comparison_context_key(group_key: tuple[object, ...]) -> tuple[object, ...]:
    return (group_key[0], group_key[1], *group_key[3:6])


def _experiment_settings(run: RunSummary) -> tuple[float | None, float | None, float | None]:
    return (run.duration_s, run.warmup_s, run.poll_s)


def _effective_tunables(
    run: RunSummary,
) -> tuple[str | None, int | None, int | None, bool, float | None]:
    if run.policy == "off":
        return (None, None, None, False, None)
    if run.cpu_cap_enabled:
        return (
            run.epp,
            run.pcore_max_mhz,
            run.ecore_max_mhz,
            True,
            run.cpu_cap_core_share_threshold,
        )
    return (run.epp, None, None, False, None)


def _sortable_group_key(key: tuple[object, ...]) -> tuple[str, ...]:
    return tuple("" if item is None else str(item) for item in key)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _thread_hotspot(state: dict[str, object]) -> dict[str, object]:
    cpu_first = _float(state.get("cpu_first"))
    cpu_last = _float(state.get("cpu_last"))
    migration_first = _optional_int(state.get("migration_first"))
    migration_last = _optional_int(state.get("migration_last"))
    voluntary_first = _optional_int(state.get("voluntary_ctxt_switches_first"))
    voluntary_last = _optional_int(state.get("voluntary_ctxt_switches_last"))
    involuntary_first = _optional_int(state.get("nonvoluntary_ctxt_switches_first"))
    involuntary_last = _optional_int(state.get("nonvoluntary_ctxt_switches_last"))
    cpus_seen = state.get("cpus_seen")
    affinity_masks = state.get("affinity_masks")
    return {
        "tid": int(state["tid"]),
        "comm": state.get("comm"),
        "cpu_time_s_delta": round(max(0.0, (cpu_last or 0.0) - (cpu_first or 0.0)), 3),
        "migration_delta": max(0, (migration_last or 0) - (migration_first or 0)),
        "voluntary_ctxt_switches_delta": max(
            0, (voluntary_last or 0) - (voluntary_first or 0)
        ),
        "nonvoluntary_ctxt_switches_delta": max(
            0, (involuntary_last or 0) - (involuntary_first or 0)
        ),
        "cpus_seen": sorted(cpus_seen) if isinstance(cpus_seen, set) else [],
        "affinity_masks": (
            sorted(affinity_masks) if isinstance(affinity_masks, set) else []
        ),
        "cgroup": state.get("cgroup"),
    }


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
    if args.command == "aggregate":
        report = run_aggregate(args)
        print(json.dumps(_json_ready(report), sort_keys=True))
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


def _parse_mangohud_summary_row(
    row: dict[str, object],
    *,
    capture_mode: CaptureMode,
) -> MangoHudFpsSummary:
    return MangoHudFpsSummary(
        avg_fps=_float(row.get("Average FPS")),
        one_percent_low_fps=_float(row.get("1% Min FPS")),
        point_one_percent_low_fps=_float(row.get("0.1% Min FPS")),
        ninety_seven_percentile_fps=_float(row.get("97% Percentile FPS")),
        avg_frametime_ms=_float(row.get("Average Frame Time")),
        capture_mode=capture_mode,
    )


def _append_float(values: list[float], value: object) -> None:
    parsed = _float(value)
    if parsed is not None:
        values.append(parsed)


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def _median(values: list[float | None]) -> float | None:
    parsed = [value for value in values if value is not None]
    return round(median(parsed), 3) if parsed else None


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
