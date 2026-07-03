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


FPS_TARGET_TOLERANCE = 0.98
TARGET_POWER_SAVING_MIN_PCT = 5.0
PACING_REGRESSION_REJECT_PCT = -3.0


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
class ThreadSchedstatSummary:
    samples: int
    observed_threads: int
    hot_threads: list[dict[str, object]]


@dataclass(frozen=True)
class CpuTopologySummary:
    cpu_count: int
    online_cpu_count: int
    core_class_counts: dict[str, int]
    policy_domains: list[dict[str, object]]
    cpus: list[dict[str, object]]


@dataclass(frozen=True)
class ProcessCgroupSummary:
    samples: int
    observed_processes: int
    foreground_processes: int
    background_candidates: list[dict[str, object]]


@dataclass(frozen=True)
class FpsTargetDiscovery:
    fps_target: float | None
    source: str
    confidence: str
    raw: str | None = None


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
    fps_target: float | None = None
    fps_target_source: str | None = None
    target_frame_ms: float | None = None
    avg_fps_target_ratio: float | None = None
    fps_target_met: bool | None = None
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
    thread_schedstat_samples: int | None = None
    thread_schedstat_observed_threads: int | None = None
    thread_schedstat_hot_threads: list[dict[str, object]] | None = None
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
    fps_target: float | None = None
    fps_target_source: str | None = None
    target_frame_ms: float | None = None
    avg_fps_target_ratio_median: float | None = None
    fps_target_met_count: int = 0
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


def parse_gamescope_fps_target_from_argv(argv: list[str]) -> FpsTargetDiscovery:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token == "-r" and index + 1 < len(argv):
            return _gamescope_fps_target(argv[index + 1], f"-r {argv[index + 1]}")
        for option in ("--framerate-limit", "--fps-limit"):
            if token == option and index + 1 < len(argv):
                return _gamescope_fps_target(
                    argv[index + 1],
                    f"{option} {argv[index + 1]}",
                )
            prefix = f"{option}="
            if token.startswith(prefix):
                return _gamescope_fps_target(token.removeprefix(prefix), token)
        index += 1
    return FpsTargetDiscovery(None, "unknown", "low")


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


def summarize_thread_schedstat_jsonl(
    path: str | Path,
    *,
    hot_thread_limit: int = 5,
) -> ThreadSchedstatSummary:
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
                        "run_time_first_ns": None,
                        "run_time_last_ns": None,
                        "runqueue_wait_first_ns": None,
                        "runqueue_wait_last_ns": None,
                        "timeslices_first": None,
                        "timeslices_last": None,
                        "cpus_seen": set(),
                    },
                )
                comm = _optional_str(item.get("comm"))
                if comm:
                    state["comm"] = comm
                cgroup = _optional_str(item.get("cgroup"))
                if cgroup:
                    state["cgroup"] = cgroup
                _update_first_last_int(
                    state,
                    "run_time",
                    item.get("run_time_ns"),
                    suffix="_ns",
                )
                _update_first_last_int(
                    state,
                    "runqueue_wait",
                    item.get("runqueue_wait_ns"),
                    suffix="_ns",
                )
                _update_first_last_int(
                    state,
                    "timeslices",
                    item.get("timeslices"),
                )
                current_cpu = _optional_int(item.get("current_cpu"))
                if current_cpu is not None:
                    cpus_seen = state["cpus_seen"]
                    assert isinstance(cpus_seen, set)
                    cpus_seen.add(current_cpu)

    hot_threads = [_schedstat_hotspot(state) for state in threads.values()]
    hot_threads.sort(
        key=lambda item: (
            -float(item["runqueue_wait_ms_delta"]),
            -float(item["runqueue_wait_per_slice_ms"]),
            -float(item["run_time_s_delta"]),
            int(item["tid"]),
        )
    )
    return ThreadSchedstatSummary(
        samples=samples,
        observed_threads=len(threads),
        hot_threads=hot_threads[:hot_thread_limit],
    )


def summarize_cpu_topology(path: str | Path) -> CpuTopologySummary:
    payload = json.loads(Path(path).read_text())
    raw_cpus = payload.get("cpus") if isinstance(payload, dict) else None
    if not isinstance(raw_cpus, list):
        raw_cpus = []

    cpus = [_normalize_cpu_topology_item(item) for item in raw_cpus]
    cpus.sort(key=lambda item: int(item["cpu"]))

    core_class_counts: dict[str, int] = {}
    policy_domain_state: dict[str, dict[str, object]] = {}
    for cpu in cpus:
        core_class = str(cpu["core_type"])
        core_class_counts[core_class] = core_class_counts.get(core_class, 0) + 1
        policy = _optional_str(cpu.get("policy"))
        if policy is None:
            continue
        domain = policy_domain_state.setdefault(
            policy,
            {
                "policy": policy,
                "cpus": [],
                "core_classes": set(),
                "max_freq_khz": None,
                "epp": None,
            },
        )
        domain_cpus = domain["cpus"]
        assert isinstance(domain_cpus, list)
        domain_cpus.append(cpu["cpu"])
        domain_classes = domain["core_classes"]
        assert isinstance(domain_classes, set)
        domain_classes.add(core_class)
        max_freq = _optional_int(cpu.get("max_freq_khz"))
        current_max = _optional_int(domain.get("max_freq_khz"))
        if max_freq is not None and (current_max is None or max_freq > current_max):
            domain["max_freq_khz"] = max_freq
        if domain["epp"] is None:
            domain["epp"] = _optional_str(cpu.get("epp"))

    policy_domains = []
    for domain in policy_domain_state.values():
        classes = domain["core_classes"]
        assert isinstance(classes, set)
        policy_domains.append(
            {
                "policy": domain["policy"],
                "cpus": sorted(domain["cpus"]),
                "core_classes": sorted(classes),
                "max_freq_khz": domain["max_freq_khz"],
                "epp": domain["epp"],
            }
        )
    policy_domains.sort(key=lambda item: str(item["policy"]))

    return CpuTopologySummary(
        cpu_count=len(cpus),
        online_cpu_count=sum(1 for cpu in cpus if cpu["online"] is True),
        core_class_counts=dict(sorted(core_class_counts.items())),
        policy_domains=policy_domains,
        cpus=cpus,
    )


def summarize_process_cgroups_jsonl(
    path: str | Path,
    *,
    appid: str,
    candidate_limit: int = 8,
) -> ProcessCgroupSummary:
    app_scope = f"app-steam-app{appid}"
    samples = 0
    observed_pids: set[int] = set()
    foreground_pids: set[int] = set()
    cgroups: dict[str, dict[str, object]] = {}

    with Path(path).open() as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            samples += 1
            row = json.loads(text)
            for item in row.get("processes") or []:
                if not isinstance(item, dict):
                    continue
                pid = _optional_int(item.get("pid"))
                cgroup = _optional_str(item.get("cgroup"))
                if pid is None or cgroup is None:
                    continue
                observed_pids.add(pid)
                if app_scope in cgroup:
                    foreground_pids.add(pid)
                    continue
                cpu_time_s = _float(item.get("cpu_time_s"))
                if cpu_time_s is None:
                    continue
                state = cgroups.setdefault(
                    cgroup,
                    {
                        "cgroup": cgroup,
                        "classification": _classify_background_cgroup(
                            cgroup,
                            item.get("comm"),
                        ),
                        "processes": {},
                        "commands": set(),
                    },
                )
                processes = state["processes"]
                assert isinstance(processes, dict)
                process = processes.setdefault(
                    pid,
                    {"first": cpu_time_s, "last": cpu_time_s},
                )
                assert isinstance(process, dict)
                process["last"] = cpu_time_s
                command = _optional_str(item.get("comm"))
                if command:
                    commands = state["commands"]
                    assert isinstance(commands, set)
                    commands.add(command)

    candidates = []
    for state in cgroups.values():
        candidate = _process_cgroup_candidate(state)
        if candidate["cpu_time_s_delta"] > 0:
            candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            -float(item["cpu_time_s_delta"]),
            -int(item["process_count"]),
            str(item["cgroup"]),
        )
    )
    return ProcessCgroupSummary(
        samples=samples,
        observed_processes=len(observed_pids),
        foreground_processes=len(foreground_pids),
        background_candidates=candidates[:candidate_limit],
    )


def build_affinity_advice(
    *,
    topology: CpuTopologySummary | None,
    thread_affinity: ThreadAffinitySummary | None,
    fps_target: float | None,
    avg_fps: float | None,
    avg_core_share: float | None,
    avg_render_busy: float | None,
    thread_schedstat: ThreadSchedstatSummary | None = None,
) -> dict[str, object]:
    preferred_latency_cpus = _preferred_latency_cpus(topology)
    ranked_threads = []
    if thread_affinity is not None:
        schedstat_by_tid = _schedstat_by_tid(thread_schedstat)
        ranked_threads = [
            _ranked_affinity_thread(item, preferred_latency_cpus, schedstat_by_tid)
            for item in thread_affinity.hot_threads
        ]
        ranked_threads.sort(
            key=lambda item: (
                -float(item["migration_harm_score"]),
                -float(item["cpu_time_s_delta"]),
                int(item["tid"]),
            )
        )
    role_candidates = _affinity_role_candidates(ranked_threads)
    reasons = [
        "hard affinity is profiler-only",
        "advisor output is observe-only until repeated A/B captures validate a policy",
    ]
    if fps_target is not None and avg_fps is not None:
        if avg_fps < fps_target * FPS_TARGET_TOLERANCE:
            reasons.append("average FPS is below target tolerance")
        else:
            reasons.append("average FPS is within target tolerance")
    if avg_render_busy is not None and avg_render_busy >= 0.9:
        reasons.append("render engine appears busy; avoid foreground CPU caps first")
    if avg_core_share is not None and avg_core_share >= 0.4:
        reasons.append("core package share is high; inspect background/helper work")

    return {
        "mode": "observe-only",
        "write_policy": "disabled",
        "preferred_latency_cpus": preferred_latency_cpus,
        "ranked_threads": ranked_threads,
        "role_candidates": role_candidates,
        "reasons": reasons,
    }


def build_background_shaping_advice(
    *,
    appid: str,
    process_cgroups: ProcessCgroupSummary | None,
    avg_core_share: float | None,
    avg_render_busy: float | None,
    fps_target: float | None,
    avg_fps: float | None,
) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    if process_cgroups is not None:
        candidates = [
            _background_shaping_candidate(candidate)
            for candidate in process_cgroups.background_candidates
        ]
    reasons = [
        "advisor output is observe-only until repeated A/B captures validate a policy",
        "foreground game cgroups are excluded from background shaping candidates",
    ]
    if candidates:
        reasons.append(
            "background/helper CPU time is visible outside the foreground app cgroup"
        )
    else:
        reasons.append("no background/helper CPU candidates observed")
    if avg_core_share is not None and avg_core_share >= 0.4:
        reasons.append("core package share is high; background shaping may free iGPU headroom")
    if avg_render_busy is not None and avg_render_busy >= 0.9:
        reasons.append(
            "render engine appears busy; shape background work before foreground caps"
        )
    if fps_target is not None and avg_fps is not None:
        if avg_fps < fps_target * FPS_TARGET_TOLERANCE:
            reasons.append("average FPS is below target tolerance")
        else:
            reasons.append("average FPS is within target tolerance")

    return {
        "mode": "observe-only",
        "write_policy": "disabled",
        "appid": appid,
        "observed_processes": (
            process_cgroups.observed_processes if process_cgroups is not None else 0
        ),
        "foreground_processes": (
            process_cgroups.foreground_processes if process_cgroups is not None else 0
        ),
        "candidates": candidates,
        "reasons": reasons,
    }


def merge_run_summary(
    *,
    appid: str,
    tdp_w: int,
    policy: str,
    fps: MangoHudFpsSummary,
    power: GamePowerLogSummary | None,
    pressure: dict[str, float] | None = None,
    thread_affinity: ThreadAffinitySummary | None = None,
    thread_schedstat: ThreadSchedstatSummary | None = None,
    epp: str | None = None,
    pcore_max_mhz: int | None = None,
    ecore_max_mhz: int | None = None,
    cpu_cap_enabled: bool | None = None,
    cpu_cap_core_share_threshold: float | None = None,
    fps_target: float | None = None,
    fps_target_source: str | None = None,
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
        fps_target=fps_target,
        fps_target_source=_normalize_fps_target_source(fps_target, fps_target_source),
        target_frame_ms=_target_frame_ms(fps_target),
        avg_fps_target_ratio=_ratio(fps.avg_fps, fps_target),
        fps_target_met=_fps_target_met(fps.avg_fps, fps_target),
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
        thread_schedstat_samples=thread_schedstat.samples if thread_schedstat else None,
        thread_schedstat_observed_threads=(
            thread_schedstat.observed_threads if thread_schedstat else None
        ),
        thread_schedstat_hot_threads=(
            thread_schedstat.hot_threads if thread_schedstat else None
        ),
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
    if baseline.fps_target != candidate.fps_target:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "baseline and candidate do not use the same FPS target",
        )

    low_gain = _percent_change(baseline.one_percent_low_fps, candidate.one_percent_low_fps)
    avg_gain = _percent_change(baseline.avg_fps, candidate.avg_fps)
    p99_gain = _lower_is_better_change(baseline.p99_frametime_ms, candidate.p99_frametime_ms)
    package_saving = _lower_is_better_change(
        baseline.avg_package_w,
        candidate.avg_package_w,
    )

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
    if (
        _run_target_sustained(baseline)
        and _run_target_sustained(candidate)
        and package_saving is not None
        and package_saving >= TARGET_POWER_SAVING_MIN_PCT
        and (low_gain is None or low_gain >= PACING_REGRESSION_REJECT_PCT)
        and (p99_gain is None or p99_gain >= PACING_REGRESSION_REJECT_PCT)
    ):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            (
                f"target sustained while package power reduced by "
                f"{package_saving:.1f}%"
            ),
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
    duration_s, warmup_s, poll_s, fps_target, fps_target_source = first_experiment
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
        fps_target=fps_target,
        fps_target_source=fps_target_source,
        target_frame_ms=_target_frame_ms(fps_target),
        avg_fps_target_ratio_median=_median(
            [_run_avg_fps_target_ratio(run) for run in runs]
        ),
        fps_target_met_count=sum(1 for run in runs if _run_fps_target_met(run) is True),
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
    if baseline.fps_target != candidate.fps_target:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "baseline and candidate do not use the same FPS target",
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
    package_saving = _lower_is_better_change(
        baseline.avg_package_w_median,
        candidate.avg_package_w_median,
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
    if (
        _aggregate_target_sustained(baseline)
        and _aggregate_target_sustained(candidate)
        and package_saving is not None
        and package_saving >= TARGET_POWER_SAVING_MIN_PCT
        and (low_gain is None or low_gain >= PACING_REGRESSION_REJECT_PCT)
        and (p99_gain is None or p99_gain >= PACING_REGRESSION_REJECT_PCT)
    ):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            (
                f"target sustained while median package power reduced by "
                f"{package_saving:.1f}%"
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
    summarize.add_argument("--thread-schedstat-jsonl")
    summarize.add_argument("--cpu-topology-json")
    summarize.add_argument("--process-cgroups-jsonl")
    summarize.add_argument("--epp")
    summarize.add_argument("--pcore-max-mhz", type=int)
    summarize.add_argument("--ecore-max-mhz", type=int)
    summarize.add_argument("--cpu-cap-enabled", choices=["true", "false"])
    summarize.add_argument("--cpu-cap-core-share-threshold", type=float)
    summarize.add_argument("--fps-target", type=float)
    summarize.add_argument("--fps-target-source")
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
    aggregate.add_argument("--fps-target", type=float)
    aggregate.add_argument("--fps-target-source")
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
    thread_schedstat = (
        summarize_thread_schedstat_jsonl(args.thread_schedstat_jsonl)
        if args.thread_schedstat_jsonl
        else None
    )
    cpu_topology = (
        summarize_cpu_topology(args.cpu_topology_json) if args.cpu_topology_json else None
    )
    process_cgroups = (
        summarize_process_cgroups_jsonl(args.process_cgroups_jsonl, appid=args.appid)
        if args.process_cgroups_jsonl
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
        "fps_target": args.fps_target,
        "fps_target_source": _normalize_fps_target_source(
            args.fps_target,
            args.fps_target_source,
        ),
        "target_frame_ms": _target_frame_ms(args.fps_target),
        "duration_s": args.duration_s,
        "warmup_s": args.warmup_s,
        "poll_s": args.poll_s,
        "thread_affinity_jsonl": bool(args.thread_affinity_jsonl),
        "thread_schedstat_jsonl": bool(args.thread_schedstat_jsonl),
        "cpu_topology_json": bool(args.cpu_topology_json),
        "affinity_advice_json": bool(cpu_topology and thread_affinity),
        "process_cgroups_jsonl": bool(args.process_cgroups_jsonl),
        "background_shaping_json": bool(process_cgroups),
    }
    summary = merge_run_summary(
        appid=args.appid,
        tdp_w=args.tdp_w,
        policy=args.policy,
        fps=fps,
        power=power,
        pressure=pressure,
        thread_affinity=thread_affinity,
        thread_schedstat=thread_schedstat,
        epp=args.epp,
        pcore_max_mhz=args.pcore_max_mhz,
        ecore_max_mhz=args.ecore_max_mhz,
        cpu_cap_enabled=_optional_bool(args.cpu_cap_enabled),
        cpu_cap_core_share_threshold=args.cpu_cap_core_share_threshold,
        fps_target=args.fps_target,
        fps_target_source=args.fps_target_source,
        duration_s=args.duration_s,
        warmup_s=args.warmup_s,
        poll_s=args.poll_s,
        restored=args.restored == "true",
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / "summary.json").write_text(
        json.dumps(_json_ready(asdict(summary)), indent=2, sort_keys=True) + "\n"
    )
    if cpu_topology is not None and thread_affinity is not None:
        advice = build_affinity_advice(
            topology=cpu_topology,
            thread_affinity=thread_affinity,
            fps_target=args.fps_target,
            avg_fps=summary.avg_fps,
            avg_core_share=summary.avg_core_share,
            avg_render_busy=summary.avg_render_busy,
            thread_schedstat=thread_schedstat,
        )
        (output / "affinity-advice.json").write_text(
            json.dumps(_json_ready(advice), indent=2, sort_keys=True) + "\n"
        )
    if process_cgroups is not None:
        background = build_background_shaping_advice(
            appid=args.appid,
            process_cgroups=process_cgroups,
            fps_target=args.fps_target,
            avg_fps=summary.avg_fps,
            avg_core_share=summary.avg_core_share,
            avg_render_busy=summary.avg_render_busy,
        )
        (output / "background-shaping.json").write_text(
            json.dumps(_json_ready(background), indent=2, sort_keys=True) + "\n"
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
        if args.fps_target is not None and summary.fps_target != args.fps_target:
            continue
        if (
            args.fps_target_source is not None
            and summary.fps_target_source != args.fps_target_source
        ):
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
    return (group_key[0], group_key[1], *group_key[3:8])


def _experiment_settings(
    run: RunSummary,
) -> tuple[float | None, float | None, float | None, float | None, str | None]:
    return (
        run.duration_s,
        run.warmup_s,
        run.poll_s,
        run.fps_target,
        run.fps_target_source,
    )


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


def _normalize_cpu_topology_item(item: object) -> dict[str, object]:
    if not isinstance(item, dict):
        item = {}
    cpu = _optional_int(item.get("cpu"))
    if cpu is None:
        cpu = -1
    online = _boolish(item.get("online"), default=True)
    core_type = _optional_str(item.get("core_type")) or "unknown"
    return {
        "cpu": cpu,
        "online": online,
        "policy": _optional_str(item.get("policy")),
        "core_type": core_type,
        "capacity": _optional_int(item.get("capacity")),
        "thread_siblings": _optional_str(item.get("thread_siblings")),
        "core_id": _optional_int(item.get("core_id")),
        "physical_package_id": _optional_int(item.get("physical_package_id")),
        "max_freq_khz": _optional_int(item.get("max_freq_khz")),
        "epp": _optional_str(item.get("epp")),
    }


def _boolish(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = _optional_str(value)
    if text is None:
        return default
    return text.lower() not in {"0", "false", "no", "offline"}


def _preferred_latency_cpus(topology: CpuTopologySummary | None) -> list[int]:
    if topology is None:
        return []
    online = [cpu for cpu in topology.cpus if cpu.get("online") is True]
    p_cores = [cpu for cpu in online if cpu.get("core_type") == "p-core"]
    candidates = p_cores or online
    candidates.sort(
        key=lambda item: (
            -(_optional_int(item.get("capacity")) or 0),
            _optional_int(item.get("cpu")) or 0,
        )
    )
    return [int(cpu["cpu"]) for cpu in candidates]


def _schedstat_by_tid(
    thread_schedstat: ThreadSchedstatSummary | None,
) -> dict[int, dict[str, object]]:
    if thread_schedstat is None:
        return {}
    indexed: dict[int, dict[str, object]] = {}
    for item in thread_schedstat.hot_threads:
        tid = _optional_int(item.get("tid"))
        if tid is not None:
            indexed[tid] = item
    return indexed


def _ranked_affinity_thread(
    item: dict[str, object],
    preferred_latency_cpus: list[int],
    schedstat_by_tid: dict[int, dict[str, object]] | None = None,
) -> dict[str, object]:
    cpu_delta = _float(item.get("cpu_time_s_delta")) or 0.0
    migration_delta = _optional_int(item.get("migration_delta")) or 0
    involuntary_delta = _optional_int(item.get("nonvoluntary_ctxt_switches_delta")) or 0
    tid = _optional_int(item.get("tid"))
    schedstat = (schedstat_by_tid or {}).get(tid or -1, {})
    runqueue_wait_ms = _float(schedstat.get("runqueue_wait_ms_delta")) or 0.0
    wait_per_slice_ms = _float(schedstat.get("runqueue_wait_per_slice_ms")) or 0.0
    cpus_seen = item.get("cpus_seen")
    if not isinstance(cpus_seen, list):
        cpus_seen = []
    migration_harm_score = round(
        migration_delta * max(cpu_delta, 0.001)
        + involuntary_delta * 0.25
        + runqueue_wait_ms * 0.05
        + wait_per_slice_ms,
        3,
    )
    classification = "throughput-worker"
    if (
        (migration_delta >= 3 and cpu_delta >= 1.0)
        or (runqueue_wait_ms >= 50.0 and wait_per_slice_ms >= 1.0)
    ):
        classification = "latency-hot"
    elif migration_delta >= 2 or involuntary_delta >= 3 or runqueue_wait_ms > 0.0:
        classification = "latency-light"
    suggested_action = (
        "observe"
        if classification == "throughput-worker"
        else "prefer-latency-cpus"
    )
    preferred_overlap = sorted(
        cpu for cpu in cpus_seen if isinstance(cpu, int) and cpu in preferred_latency_cpus
    )
    cgroup_role = _thread_cgroup_role(item.get("cgroup"))
    comm = item.get("comm")
    role_key = f"{cgroup_role}:{_normalize_role_part(comm)}"
    result = {
        "tid": item.get("tid"),
        "comm": comm,
        "role_key": role_key,
        "cgroup_role": cgroup_role,
        "classification": classification,
        "cpu_time_s_delta": round(cpu_delta, 3),
        "migration_delta": migration_delta,
        "migration_harm_score": migration_harm_score,
        "cpus_seen": cpus_seen,
        "preferred_cpu_overlap": preferred_overlap,
        "suggested_action": suggested_action,
    }
    if schedstat:
        result.update(
            {
                "run_time_s_delta": schedstat.get("run_time_s_delta"),
                "runqueue_wait_ms_delta": schedstat.get("runqueue_wait_ms_delta"),
                "timeslices_delta": schedstat.get("timeslices_delta"),
                "runqueue_wait_per_slice_ms": schedstat.get(
                    "runqueue_wait_per_slice_ms"
                ),
                "runqueue_wait_ratio": schedstat.get("runqueue_wait_ratio"),
            }
        )
    return result


def _affinity_role_candidates(
    ranked_threads: list[dict[str, object]],
) -> list[dict[str, object]]:
    roles: dict[str, dict[str, object]] = {}
    for thread in ranked_threads:
        role_key = _optional_str(thread.get("role_key"))
        if role_key is None:
            continue
        role = roles.setdefault(
            role_key,
            {
                "role_key": role_key,
                "comm": thread.get("comm"),
                "cgroup_role": thread.get("cgroup_role"),
                "classification": "throughput-worker",
                "thread_count": 0,
                "tids": set(),
                "cpu_time_s_delta": 0.0,
                "migration_delta": 0,
                "runqueue_wait_ms_delta": 0.0,
                "runqueue_wait_per_slice_ms_max": 0.0,
                "migration_harm_score_max": 0.0,
                "cpus_seen": set(),
                "preferred_cpu_overlap": set(),
                "suggested_action": "observe",
            },
        )
        role["thread_count"] = int(role["thread_count"]) + 1
        _set_add_optional_int(role["tids"], thread.get("tid"))
        role["cpu_time_s_delta"] = float(role["cpu_time_s_delta"]) + (
            _float(thread.get("cpu_time_s_delta")) or 0.0
        )
        role["migration_delta"] = int(role["migration_delta"]) + (
            _optional_int(thread.get("migration_delta")) or 0
        )
        role["runqueue_wait_ms_delta"] = float(role["runqueue_wait_ms_delta"]) + (
            _float(thread.get("runqueue_wait_ms_delta")) or 0.0
        )
        role["runqueue_wait_per_slice_ms_max"] = max(
            float(role["runqueue_wait_per_slice_ms_max"]),
            _float(thread.get("runqueue_wait_per_slice_ms")) or 0.0,
        )
        role["migration_harm_score_max"] = max(
            float(role["migration_harm_score_max"]),
            _float(thread.get("migration_harm_score")) or 0.0,
        )
        _extend_int_set(role["cpus_seen"], thread.get("cpus_seen"))
        _extend_int_set(role["preferred_cpu_overlap"], thread.get("preferred_cpu_overlap"))
        thread_classification = _optional_str(thread.get("classification"))
        if _classification_rank(thread_classification) > _classification_rank(
            _optional_str(role.get("classification"))
        ):
            role["classification"] = thread_classification
        if thread.get("suggested_action") == "prefer-latency-cpus":
            role["suggested_action"] = "prefer-latency-cpus"

    candidates = [_finalize_affinity_role(role) for role in roles.values()]
    candidates.sort(
        key=lambda item: (
            -float(item["migration_harm_score_max"]),
            -float(item["runqueue_wait_ms_delta"]),
            -float(item["cpu_time_s_delta"]),
            str(item["role_key"]),
        )
    )
    return candidates


def _finalize_affinity_role(role: dict[str, object]) -> dict[str, object]:
    tids = role.get("tids")
    cpus_seen = role.get("cpus_seen")
    preferred_overlap = role.get("preferred_cpu_overlap")
    return {
        "role_key": role.get("role_key"),
        "comm": role.get("comm"),
        "cgroup_role": role.get("cgroup_role"),
        "classification": role.get("classification"),
        "thread_count": role.get("thread_count"),
        "tids": sorted(tids) if isinstance(tids, set) else [],
        "cpu_time_s_delta": round(float(role["cpu_time_s_delta"]), 3),
        "migration_delta": role.get("migration_delta"),
        "runqueue_wait_ms_delta": round(float(role["runqueue_wait_ms_delta"]), 3),
        "runqueue_wait_per_slice_ms_max": round(
            float(role["runqueue_wait_per_slice_ms_max"]),
            3,
        ),
        "migration_harm_score_max": round(float(role["migration_harm_score_max"]), 3),
        "cpus_seen": sorted(cpus_seen) if isinstance(cpus_seen, set) else [],
        "preferred_cpu_overlap": (
            sorted(preferred_overlap) if isinstance(preferred_overlap, set) else []
        ),
        "suggested_action": role.get("suggested_action"),
    }


def _set_add_optional_int(values: object, value: object) -> None:
    parsed = _optional_int(value)
    if parsed is None or not isinstance(values, set):
        return
    values.add(parsed)


def _extend_int_set(values: object, items: object) -> None:
    if not isinstance(values, set) or not isinstance(items, list):
        return
    for item in items:
        parsed = _optional_int(item)
        if parsed is not None:
            values.add(parsed)


def _classification_rank(classification: str | None) -> int:
    return {
        "throughput-worker": 0,
        "latency-light": 1,
        "latency-hot": 2,
    }.get(classification or "", -1)


def _thread_cgroup_role(cgroup: object) -> str:
    text = (_optional_str(cgroup) or "").lower()
    if "app-steam-app" in text:
        return "foreground-game"
    if "gamescope" in text or "mangoapp" in text:
        return "gamescope-helper"
    if "steam" in text:
        return "steam-helper"
    return "other"


def _normalize_role_part(value: object) -> str:
    text = (_optional_str(value) or "unknown").lower()
    normalized = []
    previous_dash = False
    for char in text:
        if char.isalnum():
            normalized.append(char)
            previous_dash = False
        elif not previous_dash:
            normalized.append("-")
            previous_dash = True
    return "".join(normalized).strip("-") or "unknown"


def _classify_background_cgroup(cgroup: str, command: object) -> str:
    haystack = f"{cgroup} {_optional_str(command) or ''}".lower()
    if "gamescope" in haystack or "mangoapp" in haystack:
        return "gamescope-helper"
    if "steamwebhelper" in haystack or "steam" in haystack:
        return "steam-helper"
    if "/system.slice" in haystack:
        return "system-helper"
    if "/user.slice" in haystack:
        return "user-helper"
    return "other-background"


def _process_cgroup_candidate(state: dict[str, object]) -> dict[str, object]:
    processes = state.get("processes")
    if not isinstance(processes, dict):
        processes = {}
    cpu_delta = 0.0
    for process in processes.values():
        if not isinstance(process, dict):
            continue
        first = _float(process.get("first")) or 0.0
        last = _float(process.get("last")) or 0.0
        cpu_delta += max(0.0, last - first)
    commands = state.get("commands")
    return {
        "cgroup": state.get("cgroup"),
        "classification": state.get("classification"),
        "cpu_time_s_delta": round(cpu_delta, 3),
        "process_count": len(processes),
        "pids": sorted(pid for pid in processes if isinstance(pid, int)),
        "commands": sorted(commands) if isinstance(commands, set) else [],
    }


def _background_shaping_candidate(candidate: dict[str, object]) -> dict[str, object]:
    classification = _optional_str(candidate.get("classification")) or "other-background"
    cpu_delta = _float(candidate.get("cpu_time_s_delta")) or 0.0
    suggested_action = "observe"
    if classification == "gamescope-helper":
        suggested_action = "observe"
    elif cpu_delta >= 1.0:
        suggested_action = "future-cpu-weight-candidate"
    elif classification in {"system-helper", "user-helper", "other-background"}:
        suggested_action = "future-uclamp-max-candidate"
    return {
        **candidate,
        "suggested_action": suggested_action,
    }


def _update_first_last_int(
    state: dict[str, object],
    field: str,
    value: object,
    *,
    suffix: str = "",
) -> None:
    parsed = _optional_int(value)
    if parsed is None:
        return
    first_key = f"{field}_first{suffix}"
    last_key = f"{field}_last{suffix}"
    if state[first_key] is None:
        state[first_key] = parsed
    state[last_key] = parsed


def _schedstat_hotspot(state: dict[str, object]) -> dict[str, object]:
    run_first = _optional_int(state.get("run_time_first_ns")) or 0
    run_last = _optional_int(state.get("run_time_last_ns")) or 0
    wait_first = _optional_int(state.get("runqueue_wait_first_ns")) or 0
    wait_last = _optional_int(state.get("runqueue_wait_last_ns")) or 0
    slices_first = _optional_int(state.get("timeslices_first")) or 0
    slices_last = _optional_int(state.get("timeslices_last")) or 0
    run_delta_ns = max(0, run_last - run_first)
    wait_delta_ns = max(0, wait_last - wait_first)
    slices_delta = max(0, slices_last - slices_first)
    cpus_seen = state.get("cpus_seen")
    total_sched_ns = run_delta_ns + wait_delta_ns
    return {
        "tid": int(state["tid"]),
        "comm": state.get("comm"),
        "run_time_s_delta": round(run_delta_ns / 1_000_000_000, 3),
        "runqueue_wait_ms_delta": round(wait_delta_ns / 1_000_000, 3),
        "timeslices_delta": slices_delta,
        "runqueue_wait_per_slice_ms": (
            round(wait_delta_ns / slices_delta / 1_000_000, 3)
            if slices_delta > 0
            else 0.0
        ),
        "runqueue_wait_ratio": (
            round(wait_delta_ns / total_sched_ns, 3) if total_sched_ns > 0 else 0.0
        ),
        "cpus_seen": sorted(cpus_seen) if isinstance(cpus_seen, set) else [],
        "cgroup": state.get("cgroup"),
    }


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


def _target_frame_ms(fps_target: float | None) -> float | None:
    if fps_target is None or fps_target <= 0:
        return None
    return round(1000.0 / fps_target, 3)


def _gamescope_fps_target(value: object, raw: str) -> FpsTargetDiscovery:
    fps_target = _float(value)
    if fps_target is None:
        return FpsTargetDiscovery(None, "unknown", "low", raw)
    if fps_target <= 0:
        return FpsTargetDiscovery(None, "gamescope-cmdline-unlimited", "medium", raw)
    return FpsTargetDiscovery(
        round(fps_target, 3),
        "gamescope-cmdline",
        "medium",
        raw,
    )


def _normalize_fps_target_source(
    fps_target: float | None,
    fps_target_source: str | None,
) -> str | None:
    source = _optional_str(fps_target_source)
    if source is not None:
        return source
    if fps_target is not None:
        return "manual"
    return None


def _fps_target_met(avg_fps: float | None, fps_target: float | None) -> bool | None:
    if avg_fps is None or fps_target is None or fps_target <= 0:
        return None
    return avg_fps >= FPS_TARGET_TOLERANCE * fps_target


def _run_fps_target_met(run: RunSummary) -> bool | None:
    if run.fps_target_met is not None:
        return run.fps_target_met
    return _fps_target_met(run.avg_fps, run.fps_target)


def _run_avg_fps_target_ratio(run: RunSummary) -> float | None:
    if run.avg_fps_target_ratio is not None:
        return run.avg_fps_target_ratio
    return _ratio(run.avg_fps, run.fps_target)


def _run_target_sustained(run: RunSummary) -> bool:
    return _run_fps_target_met(run) is True


def _aggregate_target_sustained(aggregate: PolicyAggregate) -> bool:
    return (
        aggregate.fps_target is not None
        and aggregate.sample_count > 0
        and aggregate.fps_target_met_count == aggregate.sample_count
    )


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
