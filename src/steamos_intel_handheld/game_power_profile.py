#!/usr/bin/env python3
"""Profiler and comparison helpers for game-power policy experiments."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from statistics import mean, median
from typing import Any

# Background-shaping guarded writers were relocated to game_power_cgroup_writers
# so the daemon and profiler use literally the same apply/restore code (V9 S4).
# Re-exported here to preserve the profiler's public import surface.
from .game_power_cgroup_writers import (  # noqa: F401
    BACKGROUND_SHAPING_WRITE_VARIANTS,
    FOREGROUND_UCLAMP_MIN_FLOOR,
    apply_background_shaping_writes,
    apply_foreground_uclamp_min_writes,
    restore_background_shaping_writes,
    restore_foreground_uclamp_min_writes,
)

# Role normalization + background classification are single-sourced in the
# coloring module so the daemon and profiler agree on role keys (V9 S3).
from .game_power_coloring import (  # noqa: F401
    build_color_ledger_from_artifacts,
    iter_jsonl_rows,
)
from .game_power_coloring import (
    classify_background_cgroup as _classify_background_cgroup,
)
from .game_power_coloring import (
    normalize_role_part as _normalize_role_part,
)
from .game_power_coloring import (
    thread_cgroup_role as _thread_cgroup_role,
)


class CaptureMode(str, Enum):
    CONTROLLED = "controlled"
    IMPORTED = "imported"


FPS_TARGET_TOLERANCE = 0.98
TARGET_POWER_SAVING_MIN_PCT = 5.0
PACING_REGRESSION_REJECT_PCT = -3.0
AFFINITY_ROLE_MIN_RUN_COVERAGE = 0.67
AFFINITY_ROLE_MIN_OBSERVED_RUNS = 2
AFFINITY_ROLE_MIN_HARM_SCORE = 5.0
AFFINITY_ROLE_MIN_RUNQUEUE_WAIT_MS = 25.0
BACKGROUND_SHAPING_MIN_RUN_COVERAGE = 0.67
BACKGROUND_SHAPING_MIN_OBSERVED_RUNS = 2
BACKGROUND_SHAPING_MIN_CPU_TIME_S = 1.0
CGROUP_CPU_CONTROLLER_RESTORE_FILES = frozenset(
    {"cpu.uclamp.max", "cpu.uclamp.min", "cpu.weight", "cpu.max"}
)
FOREGROUND_AFFINITY_WRITE_VARIANTS = {"foreground-role-compact"}
AB_EVIDENCE_INCOMPLETE_PREFIX = "A/B evidence incomplete:"
AB_EXPLORATORY_SUFFIX = "exploratory only; cannot support a BETTER claim"
BETTER_CLAIM_BOUNDARY = (
    "scene/profile-specific controlled result; not a general performance claim"
)
GUARDED_ARTIFACT_CAVEAT = (
    "guarded foreground-game artifacts are required for this captured profile only"
)
AB_THERMAL_DELTA_MAX_C = 5.0
FIXED_COOLDOWN_MIN_S = 60.0
FIXED_COOLDOWN_RUN_GAP_MAX_S = 5.0


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
    classification_primary: dict[str, int] | None = None
    classification_advisories: dict[str, int] | None = None
    classification_malformed: int = 0
    fps_target_source_counts: dict[str, int] | None = None
    fps_target_confidence_counts: dict[str, int] | None = None
    runtime_telemetry_counts: RuntimeTelemetryCounts | None = None
    classification_unknown_ratio: float | None = None
    pressure_supported_ratio: float | None = None
    pressure_unsupported_ratio: float | None = None


@dataclass(frozen=True)
class RuntimeTelemetryCounts:
    foreground_runtime_rows: int = 0
    unknown_foreground_rows: int = 0
    foreground_pressure_signals: int = 0
    supported_foreground_pressure_signals: int = 0
    unsupported_foreground_pressure_signals: int = 0
    frame_performance_rows: int = 0
    fps_target_satisfied_rows: int = 0


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
class RestoreAffinitySummary:
    thread_count: int
    cgroup_count: int
    cgroups: list[str]
    files: list[str]
    cgroup_files: dict[str, list[str]]
    cgroup_file_values: dict[str, dict[str, str]]


@dataclass(frozen=True)
class FpsTargetDiscovery:
    fps_target: float | None
    source: str
    confidence: str
    raw: str | None = None


@dataclass(frozen=True)
class AbEvidence:
    order_strategy: str | None = None
    run_order: str | None = None
    order_valid: bool = False
    candidate_policy: str | None = None
    invocation_id: str | None = None
    pair_id: str | None = None
    pair_position: str | None = None
    scene_evidence: str | None = None
    power_source_state: str | None = None
    power_source_start_state: str | None = None
    power_source_pre_run_state: str | None = None
    power_source_end_state: str | None = None
    power_source_samples: list[str] | None = None
    power_source_stable: bool = False
    thermal_start_c: float | None = None
    thermal_end_c: float | None = None
    thermal_unavailable: bool = False
    thermal_source_kind: str | None = None
    thermal_source_id: str | None = None
    thermal_source_label: str | None = None
    run_started_at_s: float | None = None
    run_ended_at_s: float | None = None
    cooldown_rule: str | None = None
    cooldown_enforced: bool = False
    cooldown_started_at_s: float | None = None
    cooldown_ended_at_s: float | None = None
    cooldown_elapsed_s: float | None = None


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
    fps_target_confidence: str | None = None
    target_frame_ms: float | None = None
    avg_fps_target_ratio: float | None = None
    fps_target_met: bool | None = None
    pacing_proof: bool | None = None
    post_run_classification: str | None = None
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
    restore_affinity_thread_count: int | None = None
    restore_affinity_cgroup_count: int | None = None
    restore_affinity_cgroups: list[str] | None = None
    restore_affinity_files: list[str] | None = None
    restore_affinity_cgroup_files: dict[str, list[str]] | None = None
    restore_affinity_cgroup_file_values: dict[str, dict[str, str]] | None = None
    foreground_affinity_write_count: int | None = None
    foreground_affinity_failed_count: int | None = None
    foreground_affinity_matched_thread_count: int | None = None
    foreground_affinity_role_key: str | None = None
    foreground_affinity_preferred_cpus: str | None = None
    foreground_affinity_restore_restored: bool | None = None
    foreground_affinity_valid_evidence: bool | None = None
    actions: dict[str, int] | None = None
    restored: bool = False
    ab_order_strategy: str | None = None
    ab_run_order: str | None = None
    ab_order_valid: bool = False
    ab_candidate_policy: str | None = None
    ab_invocation_id: str | None = None
    ab_pair_id: str | None = None
    ab_pair_position: str | None = None
    scene_evidence: str | None = None
    power_source_state: str | None = None
    power_source_start_state: str | None = None
    power_source_pre_run_state: str | None = None
    power_source_end_state: str | None = None
    power_source_samples: list[str] | None = None
    power_source_stable: bool = False
    thermal_start_c: float | None = None
    thermal_end_c: float | None = None
    thermal_unavailable: bool = False
    thermal_source_kind: str | None = None
    thermal_source_id: str | None = None
    thermal_source_label: str | None = None
    run_started_at_s: float | None = None
    run_ended_at_s: float | None = None
    cooldown_rule: str | None = None
    cooldown_enforced: bool = False
    cooldown_started_at_s: float | None = None
    cooldown_ended_at_s: float | None = None
    cooldown_elapsed_s: float | None = None
    classification_primary: dict[str, int] | None = None
    classification_advisories: dict[str, int] | None = None
    classification_malformed: int = 0
    fps_target_source_counts: dict[str, int] | None = None
    fps_target_confidence_counts: dict[str, int] | None = None
    runtime_telemetry_counts: RuntimeTelemetryCounts | dict[str, int] | None = None
    classification_unknown_ratio: float | None = None
    pressure_supported_ratio: float | None = None
    pressure_unsupported_ratio: float | None = None
    # V9 target-balance per-phase metrics (design section 9). ``None`` for
    # gpu-priority runs and any run whose JSONL carries no ``phase`` rows, so
    # existing summaries stay byte-identical.
    phase_metrics: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capture_mode, CaptureMode):
            object.__setattr__(self, "capture_mode", CaptureMode(self.capture_mode))
        if self.runtime_telemetry_counts is not None and not isinstance(
            self.runtime_telemetry_counts,
            RuntimeTelemetryCounts,
        ):
            object.__setattr__(
                self,
                "runtime_telemetry_counts",
                _runtime_telemetry_counts(self.runtime_telemetry_counts),
            )
        if self.target_frame_ms is None:
            object.__setattr__(self, "target_frame_ms", _target_frame_ms(self.fps_target))
        if self.avg_fps_target_ratio is None:
            object.__setattr__(
                self,
                "avg_fps_target_ratio",
                _ratio(self.avg_fps, self.fps_target),
            )
        if self.fps_target_met is None:
            object.__setattr__(
                self,
                "fps_target_met",
                _fps_target_met(self.avg_fps, self.fps_target),
            )
        if self.pacing_proof is None:
            object.__setattr__(
                self,
                "pacing_proof",
                _pacing_proof_for_values(
                    fps_target=self.fps_target,
                    target_frame_ms=self.target_frame_ms,
                    one_percent_low_fps=self.one_percent_low_fps,
                    p99_frametime_ms=self.p99_frametime_ms,
                ),
            )
        if self.post_run_classification is None:
            object.__setattr__(
                self,
                "post_run_classification",
                _post_run_classification_for_values(
                    fps_target_met=self.fps_target_met,
                    pacing_proof=self.pacing_proof,
                ),
            )


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
    fps_target_confidence: str | None = None
    target_frame_ms: float | None = None
    avg_fps_target_ratio_median: float | None = None
    fps_target_met_count: int = 0
    target_sustained_count: int = 0
    target_average_only_count: int = 0
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
    restore_affinity_snapshot_count: int = 0
    restore_affinity_thread_count_median: float | None = None
    restore_affinity_cgroup_count_median: float | None = None
    restore_affinity_cgroups: list[str] | None = None
    restore_affinity_files: list[str] | None = None
    restore_affinity_cgroup_files: dict[str, list[str]] | None = None
    restore_affinity_cgroup_file_values: dict[str, dict[str, list[str]]] | None = None
    foreground_affinity_valid_count: int = 0
    foreground_affinity_write_count_median: float | None = None
    foreground_affinity_failed_count_median: float | None = None
    foreground_affinity_matched_thread_count_median: float | None = None
    foreground_affinity_role_key: str | None = None
    foreground_affinity_preferred_cpus: str | None = None
    ab_order_strategy: str | None = None
    ab_run_orders: list[str] | None = None
    ab_order_valid_count: int = 0
    ab_candidate_policy: str | None = None
    ab_invocation_ids: list[str] | None = None
    ab_pair_ids: list[str] | None = None
    ab_pair_position_counts: dict[str, int] | None = None
    ab_pair_position_counts_by_id: dict[str, dict[str, int]] | None = None
    scene_evidence: str | None = None
    power_source_state: str | None = None
    power_source_start_state: str | None = None
    power_source_pre_run_state: str | None = None
    power_source_end_state: str | None = None
    power_source_sample_signatures: list[str] | None = None
    power_source_stable_count: int = 0
    thermal_start_c_median: float | None = None
    thermal_end_c_median: float | None = None
    thermal_unavailable_count: int = 0
    thermal_source_kind: str | None = None
    thermal_source_id: str | None = None
    thermal_source_label: str | None = None
    thermal_pair_readings_by_id: dict[str, dict[str, dict[str, float | None]]] | None = None
    thermal_pair_evidence_complete: bool = False
    run_interval_by_pair_id: dict[str, dict[str, dict[str, float | None]]] | None = None
    cooldown_interval_by_pair_id: dict[str, dict[str, dict[str, float | None]]] | None = None
    cooldown_interval_evidence_complete: bool = False
    cooldown_rule: str | None = None
    cooldown_enforced_count: int = 0
    cooldown_started_at_s_min: float | None = None
    cooldown_ended_at_s_max: float | None = None
    cooldown_elapsed_s_median: float | None = None
    cooldown_run_gap_s_max: float | None = None
    pair_run_order_valid: bool = False
    ab_evidence_complete: bool = False
    classification_primary: dict[str, int] | None = None
    classification_advisories: dict[str, int] | None = None
    classification_malformed: int = 0
    fps_target_source_counts: dict[str, int] | None = None
    fps_target_confidence_counts: dict[str, int] | None = None
    runtime_telemetry_counts: RuntimeTelemetryCounts | dict[str, int] | None = None
    classification_unknown_ratio: float | None = None
    pressure_supported_ratio: float | None = None
    pressure_unsupported_ratio: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capture_mode, CaptureMode):
            object.__setattr__(self, "capture_mode", CaptureMode(self.capture_mode))
        if self.runtime_telemetry_counts is not None and not isinstance(
            self.runtime_telemetry_counts,
            RuntimeTelemetryCounts,
        ):
            object.__setattr__(
                self,
                "runtime_telemetry_counts",
                _runtime_telemetry_counts(self.runtime_telemetry_counts),
            )


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
    thermal_pair_start_delta_max_c: float | None = None
    thermal_pair_end_delta_max_c: float | None = None
    thermal_pair_mismatch_count: int = 0
    # D7: aggregate END-temperature median delta is REPORTED context, never a
    # rejection reason. A power-saving candidate necessarily ends cooler, so
    # gating on end temps would make a BETTER power verdict structurally
    # unreachable. Start-temperature parity (the pre-run pairing confound
    # control) is the only thermal gate; its median delta is reported too.
    thermal_start_delta_c: float | None = None
    thermal_end_delta_c: float | None = None
    cooldown_run_gap_s_max: float | None = None
    cooldown_interval_reuse_count: int = 0
    claim_scope: dict[str, object] | None = None
    human_summary: str | None = None


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
        raw_rows = list(csv.reader(handle))
    # mangoapp/MangoHud per-frame logs prepend a two-line system-info banner
    # ("os,cpu,gpu,..." then its values) before the real "fps,frametime,..."
    # header. Skip any preamble by locating the header row whose first cell is
    # "fps" (case-insensitive). If no such row exists (e.g. a summary-style CSV
    # was passed here) fall back to treating the first row as the header so the
    # summary fallback below still applies.
    header_index = next(
        (
            index
            for index, cells in enumerate(raw_rows)
            if cells and cells[0].strip().lower() == "fps"
        ),
        0,
    )
    if raw_rows:
        header = raw_rows[header_index]
        rows = [
            dict(zip(header, cells, strict=False))
            for cells in raw_rows[header_index + 1 :]
            if cells
        ]
    else:
        rows = []
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
    classification_primary: dict[str, int] = {}
    classification_advisories: dict[str, int] = {}
    classification_malformed = 0
    fps_target_source_counts: dict[str, int] = {}
    fps_target_confidence_counts: dict[str, int] = {}
    runtime_counts = RuntimeTelemetryCounts()
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
            appid = _optional_str(row.get("appid"))
            foreground_row = appid is not None
            classification = row.get("classification")
            primary, advisories, malformed = _parse_runtime_classification(
                classification,
            )
            classification_primary[primary] = classification_primary.get(primary, 0) + 1
            for advisory in advisories:
                classification_advisories[advisory] = (
                    classification_advisories.get(advisory, 0) + 1
                )
            if malformed:
                classification_malformed += 1
            if foreground_row:
                runtime_counts = _add_runtime_counts(
                    runtime_counts,
                    RuntimeTelemetryCounts(
                        foreground_runtime_rows=1,
                        unknown_foreground_rows=1 if primary == "unknown" else 0,
                        frame_performance_rows=(
                            1 if _row_has_frame_performance(row) else 0
                        ),
                        fps_target_satisfied_rows=(
                            1 if primary == "fps-target-satisfied" else 0
                        ),
                    ),
                )
                runtime_counts = _add_runtime_counts(
                    runtime_counts,
                    _foreground_pressure_counts(row.get("pressure")),
                )
            fps_target = _finite_positive_float(row.get("fps_target"))
            if fps_target is not None:
                source = _optional_str(row.get("fps_target_source")) or "unknown"
                confidence = _optional_str(row.get("fps_target_confidence")) or "unknown"
                fps_target_source_counts[source] = fps_target_source_counts.get(source, 0) + 1
                fps_target_confidence_counts[confidence] = (
                    fps_target_confidence_counts.get(confidence, 0) + 1
                )
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
        classification_primary=dict(sorted(classification_primary.items())),
        classification_advisories=dict(sorted(classification_advisories.items())),
        classification_malformed=classification_malformed,
        fps_target_source_counts=dict(sorted(fps_target_source_counts.items())),
        fps_target_confidence_counts=dict(sorted(fps_target_confidence_counts.items())),
        runtime_telemetry_counts=runtime_counts,
        classification_unknown_ratio=_ratio(
            runtime_counts.unknown_foreground_rows,
            runtime_counts.foreground_runtime_rows,
        ),
        pressure_supported_ratio=_ratio(
            runtime_counts.supported_foreground_pressure_signals,
            runtime_counts.foreground_pressure_signals,
        ),
        pressure_unsupported_ratio=_ratio(
            runtime_counts.unsupported_foreground_pressure_signals,
            runtime_counts.foreground_pressure_signals,
        ),
    )


def summarize_phase_metrics(
    path: str | Path,
    *,
    poll_s: float | None = None,
) -> dict[str, object] | None:
    """Summarize per-phase metrics from a target-balance decision JSONL.

    Emits seconds-per-phase, phase sample counts, loading episode count/total
    duration, and a ladder-step histogram (design section 9). Returns ``None``
    when the JSONL carries no ``phase`` rows (gpu-priority runs), so gpu-priority
    ``summary.json`` stays byte-identical.

    Per-phase p99 frame time is intentionally omitted: governor rows carry only
    rolling-window frame aggregates (``frame_p95_ms``) whose window spans phase
    boundaries, and the raw per-frame samples never reach this JSONL, so a p99
    cannot be cleanly attributed to a phase. Faking that alignment would be
    dishonest, so ``per_phase_p99_frame_ms`` is reported ``None`` with a note.
    """

    rows = _read_jsonl_rows(path)
    phase_rows = [row for row in rows if _optional_str(row.get("phase")) is not None]
    if not phase_rows:
        return None

    elapsed: list[float | None] = [_float(row.get("elapsed_s")) for row in phase_rows]
    deltas: list[float] = []
    for index in range(len(phase_rows) - 1):
        left = elapsed[index]
        right = elapsed[index + 1]
        if left is not None and right is not None and right > left:
            deltas.append(right - left)
    positive_median = _median([value for value in deltas]) if deltas else None
    fallback = poll_s if (poll_s is not None and poll_s > 0) else (positive_median or 0.0)

    durations: list[float] = []
    for index in range(len(phase_rows)):
        left = elapsed[index]
        if index + 1 < len(phase_rows):
            right = elapsed[index + 1]
            if left is not None and right is not None and right > left:
                durations.append(right - left)
            else:
                durations.append(fallback)
        else:
            durations.append(fallback)

    seconds_per_phase: dict[str, float] = {}
    phase_sample_counts: dict[str, int] = {}
    ladder_histogram: dict[str, int] = {}
    loading_episode_count = 0
    loading_total_s = 0.0
    previous_loading = False
    for row, duration in zip(phase_rows, durations, strict=True):
        phase = str(row.get("phase"))
        seconds_per_phase[phase] = seconds_per_phase.get(phase, 0.0) + duration
        phase_sample_counts[phase] = phase_sample_counts.get(phase, 0) + 1
        ladder_step = row.get("ladder_step")
        if isinstance(ladder_step, int):
            key = str(ladder_step)
            ladder_histogram[key] = ladder_histogram.get(key, 0) + 1
        is_loading = phase == "loading"
        if is_loading:
            if not previous_loading:
                loading_episode_count += 1
            loading_total_s += duration
        previous_loading = is_loading

    return {
        "phase_rows": len(phase_rows),
        "seconds_per_phase": {
            key: round(value, 3) for key, value in sorted(seconds_per_phase.items())
        },
        "phase_sample_counts": dict(sorted(phase_sample_counts.items())),
        "loading_episode_count": loading_episode_count,
        "loading_total_s": round(loading_total_s, 3),
        "ladder_step_histogram": {
            key: ladder_histogram[key]
            for key in sorted(ladder_histogram, key=lambda item: int(item))
        },
        "per_phase_p99_frame_ms": None,
        "per_phase_p99_frame_ms_note": (
            "governor rows carry rolling-window frame aggregates that span phase "
            "boundaries and no per-frame samples reach this JSONL, so per-phase "
            "p99 is not cleanly alignable and is intentionally omitted "
            "(design section 9)"
        ),
    }


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


def summarize_restore_affinity_json(path: str | Path) -> RestoreAffinitySummary:
    payload = json.loads(Path(path).read_text())
    threads = payload.get("threads") if isinstance(payload, dict) else None
    cgroups = payload.get("cgroups") if isinstance(payload, dict) else None
    if not isinstance(threads, list):
        threads = []
    if not isinstance(cgroups, list):
        cgroups = []

    cgroup_paths: set[str] = set()
    files: set[str] = set()
    cgroup_files_by_path: dict[str, list[str]] = {}
    cgroup_file_values_by_path: dict[str, dict[str, str]] = {}
    for cgroup in cgroups:
        if not isinstance(cgroup, dict):
            continue
        cgroup_path = _optional_str(cgroup.get("cgroup"))
        if cgroup_path:
            cgroup_paths.add(cgroup_path)
        cgroup_files = cgroup.get("files")
        if isinstance(cgroup_files, dict):
            file_names = sorted(str(key) for key in cgroup_files)
            files.update(file_names)
            if cgroup_path:
                cgroup_files_by_path[cgroup_path] = file_names
                cgroup_file_values_by_path[cgroup_path] = {
                    str(key): "" if value is None else str(value)
                    for key, value in sorted(cgroup_files.items())
                }

    return RestoreAffinitySummary(
        thread_count=len(threads),
        cgroup_count=len(cgroups),
        cgroups=sorted(cgroup_paths),
        files=sorted(files),
        cgroup_files=dict(sorted(cgroup_files_by_path.items())),
        cgroup_file_values=dict(sorted(cgroup_file_values_by_path.items())),
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
    restore_affinity: RestoreAffinitySummary | None = None,
    foreground_affinity: dict[str, object] | None = None,
    epp: str | None = None,
    pcore_max_mhz: int | None = None,
    ecore_max_mhz: int | None = None,
    cpu_cap_enabled: bool | None = None,
    cpu_cap_core_share_threshold: float | None = None,
    fps_target: float | None = None,
    fps_target_source: str | None = None,
    fps_target_confidence: str | None = None,
    duration_s: float | None = None,
    warmup_s: float | None = None,
    poll_s: float | None = None,
    ab_evidence: AbEvidence | None = None,
    phase_metrics: dict[str, object] | None = None,
    restored: bool,
) -> RunSummary:
    pressure = pressure or {}
    foreground_affinity = foreground_affinity or {}
    ab_evidence = ab_evidence or AbEvidence()
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
        fps_target_confidence=(
            fps_target_confidence
            or _single_counter_key(power.fps_target_confidence_counts if power else None)
        ),
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
        restore_affinity_thread_count=(
            restore_affinity.thread_count if restore_affinity else None
        ),
        restore_affinity_cgroup_count=(
            restore_affinity.cgroup_count if restore_affinity else None
        ),
        restore_affinity_cgroups=restore_affinity.cgroups if restore_affinity else None,
        restore_affinity_files=restore_affinity.files if restore_affinity else None,
        restore_affinity_cgroup_files=(
            restore_affinity.cgroup_files if restore_affinity else None
        ),
        restore_affinity_cgroup_file_values=(
            restore_affinity.cgroup_file_values if restore_affinity else None
        ),
        foreground_affinity_write_count=_optional_int(
            foreground_affinity.get("written_count")
        ),
        foreground_affinity_failed_count=_optional_int(
            foreground_affinity.get("failed_count")
        ),
        foreground_affinity_matched_thread_count=_optional_int(
            foreground_affinity.get("matched_thread_count")
        ),
        foreground_affinity_role_key=_optional_str(foreground_affinity.get("role_key")),
        foreground_affinity_preferred_cpus=_optional_str(
            foreground_affinity.get("preferred_cpus")
        ),
        foreground_affinity_restore_restored=_optional_bool(
            foreground_affinity.get("restore_restored")
        ),
        foreground_affinity_valid_evidence=_optional_bool(
            foreground_affinity.get("valid_evidence")
        ),
        actions=power.actions if power else None,
        classification_primary=power.classification_primary if power else None,
        classification_advisories=power.classification_advisories if power else None,
        classification_malformed=power.classification_malformed if power else 0,
        fps_target_source_counts=power.fps_target_source_counts if power else None,
        fps_target_confidence_counts=(
            power.fps_target_confidence_counts if power else None
        ),
        runtime_telemetry_counts=power.runtime_telemetry_counts if power else None,
        classification_unknown_ratio=(
            power.classification_unknown_ratio if power else None
        ),
        pressure_supported_ratio=power.pressure_supported_ratio if power else None,
        pressure_unsupported_ratio=power.pressure_unsupported_ratio if power else None,
        phase_metrics=phase_metrics,
        restored=restored,
        ab_order_strategy=ab_evidence.order_strategy,
        ab_run_order=ab_evidence.run_order,
        ab_order_valid=ab_evidence.order_valid,
        ab_candidate_policy=ab_evidence.candidate_policy,
        ab_invocation_id=ab_evidence.invocation_id,
        ab_pair_id=ab_evidence.pair_id,
        ab_pair_position=ab_evidence.pair_position,
        scene_evidence=ab_evidence.scene_evidence,
        power_source_state=ab_evidence.power_source_state,
        power_source_start_state=ab_evidence.power_source_start_state,
        power_source_pre_run_state=ab_evidence.power_source_pre_run_state,
        power_source_end_state=ab_evidence.power_source_end_state,
        power_source_samples=ab_evidence.power_source_samples,
        power_source_stable=ab_evidence.power_source_stable,
        thermal_start_c=ab_evidence.thermal_start_c,
        thermal_end_c=ab_evidence.thermal_end_c,
        thermal_unavailable=ab_evidence.thermal_unavailable,
        thermal_source_kind=ab_evidence.thermal_source_kind,
        thermal_source_id=ab_evidence.thermal_source_id,
        thermal_source_label=ab_evidence.thermal_source_label,
        run_started_at_s=ab_evidence.run_started_at_s,
        run_ended_at_s=ab_evidence.run_ended_at_s,
        cooldown_rule=ab_evidence.cooldown_rule,
        cooldown_enforced=ab_evidence.cooldown_enforced,
        cooldown_started_at_s=ab_evidence.cooldown_started_at_s,
        cooldown_ended_at_s=ab_evidence.cooldown_ended_at_s,
        cooldown_elapsed_s=ab_evidence.cooldown_elapsed_s,
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
        return _single_run_exploratory_comparison(baseline.policy, candidate.policy)
    if p99_gain is not None and p99_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        return _single_run_exploratory_comparison(baseline.policy, candidate.policy)
    if (
        _run_target_sustained(baseline)
        and _run_target_sustained(candidate)
        and package_saving is not None
        and package_saving >= TARGET_POWER_SAVING_MIN_PCT
        and (low_gain is None or low_gain >= PACING_REGRESSION_REJECT_PCT)
        and (p99_gain is None or p99_gain >= PACING_REGRESSION_REJECT_PCT)
    ):
        return _single_run_exploratory_comparison(baseline.policy, candidate.policy)
    if avg_gain is not None and avg_gain >= 5.0 and (low_gain is None or low_gain >= -2.0):
        return _single_run_exploratory_comparison(baseline.policy, candidate.policy)
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


def _single_run_exploratory_comparison(
    baseline_policy: str,
    candidate_policy: str,
) -> PolicyComparison:
    return PolicyComparison(
        baseline_policy,
        candidate_policy,
        PolicyVerdict.INCONCLUSIVE,
        (
            f"{AB_EVIDENCE_INCOMPLETE_PREFIX} single-run compare is exploratory "
            "only; cannot support a BETTER claim"
        ),
    )


def _incomplete_reason(reason: str) -> str:
    return f"{AB_EVIDENCE_INCOMPLETE_PREFIX} {reason}; {AB_EXPLORATORY_SUFFIX}"


def _unique_present(values: list[str | None]) -> list[str]:
    return sorted({value for value in values if value})


def _single_present(values: list[str | None]) -> str | None:
    unique = _unique_present(values)
    return unique[0] if len(unique) == 1 else None


def _position_counts(runs: list[RunSummary]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        if not run.ab_pair_position:
            continue
        counts[run.ab_pair_position] = counts.get(run.ab_pair_position, 0) + 1
    return dict(sorted(counts.items()))


def _position_counts_by_pair_id(runs: list[RunSummary]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for run in runs:
        if not run.ab_pair_id or not run.ab_pair_position:
            continue
        pair_counts = counts.setdefault(run.ab_pair_id, {})
        pair_counts[run.ab_pair_position] = pair_counts.get(run.ab_pair_position, 0) + 1
    return {
        pair_id: dict(sorted(pair_counts.items()))
        for pair_id, pair_counts in sorted(counts.items())
    }


def _sample_signature(samples: list[str] | None) -> str | None:
    if samples is None:
        return None
    return ",".join(samples)


def _thermal_pair_readings(
    runs: list[RunSummary],
) -> dict[str, dict[str, dict[str, float | None]]]:
    readings: dict[str, dict[str, dict[str, float | None]]] = {}
    for run in runs:
        if not run.ab_pair_id or not run.ab_pair_position:
            continue
        readings.setdefault(run.ab_pair_id, {})[run.ab_pair_position] = {
            "thermal_start_c": run.thermal_start_c,
            "thermal_end_c": run.thermal_end_c,
        }
    return {
        pair_id: dict(sorted(positions.items()))
        for pair_id, positions in sorted(readings.items())
    }


def _run_intervals(
    runs: list[RunSummary],
) -> dict[str, dict[str, dict[str, float | None]]]:
    intervals: dict[str, dict[str, dict[str, float | None]]] = {}
    for run in runs:
        if not run.ab_pair_id or not run.ab_pair_position:
            continue
        intervals.setdefault(run.ab_pair_id, {})[run.ab_pair_position] = {
            "run_started_at_s": run.run_started_at_s,
            "run_ended_at_s": run.run_ended_at_s,
        }
    return {
        pair_id: dict(sorted(positions.items()))
        for pair_id, positions in sorted(intervals.items())
    }


def _cooldown_intervals(
    runs: list[RunSummary],
) -> dict[str, dict[str, dict[str, float | None]]]:
    intervals: dict[str, dict[str, dict[str, float | None]]] = {}
    for run in runs:
        if not run.ab_pair_id or not run.ab_pair_position:
            continue
        gap = None
        if run.run_started_at_s is not None and run.cooldown_ended_at_s is not None:
            gap = round(run.run_started_at_s - run.cooldown_ended_at_s, 3)
        intervals.setdefault(run.ab_pair_id, {})[run.ab_pair_position] = {
            "cooldown_started_at_s": run.cooldown_started_at_s,
            "cooldown_ended_at_s": run.cooldown_ended_at_s,
            "cooldown_elapsed_s": run.cooldown_elapsed_s,
            "cooldown_run_gap_s": gap,
        }
    return {
        pair_id: dict(sorted(positions.items()))
        for pair_id, positions in sorted(intervals.items())
    }


def _aggregate_ab_evidence_complete(runs: list[RunSummary]) -> bool:
    if not runs:
        return False
    strategies = [run.ab_order_strategy for run in runs]
    if not all(strategies) or _single_present(strategies) != "paired-baseline":
        return False
    if any(not run.ab_run_order or not run.ab_order_valid for run in runs):
        return False
    if len(_unique_present([run.ab_run_order for run in runs])) != 1:
        return False
    if any(
        not run.ab_candidate_policy
        or not run.ab_invocation_id
        or not run.ab_pair_id
        or not run.ab_pair_position
        for run in runs
    ):
        return False
    candidate_policy = _single_present([run.ab_candidate_policy for run in runs])
    if candidate_policy is None:
        return False
    valid_positions = {"baseline-before", "candidate", "baseline-after"}
    if any(run.ab_pair_position not in valid_positions for run in runs):
        return False
    policies = {run.policy for run in runs}
    positions = {run.ab_pair_position for run in runs}
    if policies == {"off"}:
        if not positions.issubset({"baseline-before", "baseline-after"}):
            return False
    elif len(policies) == 1 and next(iter(policies)) == candidate_policy:
        if positions != {"candidate"}:
            return False
    else:
        return False
    if not _single_present([run.scene_evidence for run in runs]):
        return False
    for attr in (
        "power_source_state",
        "power_source_start_state",
        "power_source_pre_run_state",
        "power_source_end_state",
    ):
        value = _single_present([getattr(run, attr) for run in runs])
        if value is None or value in {"mixed", "unknown"}:
            return False
    for run in runs:
        samples = run.power_source_samples
        if (
            not samples
            or len(samples) != 3
            or "mixed" in samples
            or "unknown" in samples
            or len(set(samples)) != 1
            or samples[0] != run.power_source_start_state
            or samples[1] != run.power_source_pre_run_state
            or samples[2] != run.power_source_end_state
            or not run.power_source_stable
        ):
            return False
    if any(run.thermal_unavailable for run in runs):
        return False
    if any(run.thermal_start_c is None or run.thermal_end_c is None for run in runs):
        return False
    for attr in ("thermal_source_kind", "thermal_source_id", "thermal_source_label"):
        if _single_present([getattr(run, attr) for run in runs]) is None:
            return False
    pair_positions = _position_counts_by_pair_id(runs)
    if not pair_positions:
        return False
    for run in runs:
        if run.run_started_at_s is None or run.run_ended_at_s is None:
            return False
        if run.run_ended_at_s <= run.run_started_at_s:
            return False
        if run.cooldown_rule != "fixed-60s" or not run.cooldown_enforced:
            return False
        if (
            run.cooldown_started_at_s is None
            or run.cooldown_ended_at_s is None
            or run.cooldown_elapsed_s is None
        ):
            return False
        if run.cooldown_ended_at_s < run.cooldown_started_at_s:
            return False
        elapsed = run.cooldown_ended_at_s - run.cooldown_started_at_s
        if abs(elapsed - run.cooldown_elapsed_s) > 1.0:
            return False
        if run.cooldown_elapsed_s < FIXED_COOLDOWN_MIN_S:
            return False
        gap = run.run_started_at_s - run.cooldown_ended_at_s
        if gap < 0.0 or gap > FIXED_COOLDOWN_RUN_GAP_MAX_S:
            return False
    return True


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
    (
        duration_s,
        warmup_s,
        poll_s,
        fps_target,
        fps_target_source,
        fps_target_confidence,
    ) = first_experiment
    epp, pcore_max_mhz, ecore_max_mhz, cpu_cap_enabled, threshold = first_tunables
    ab_run_orders = _unique_present([run.ab_run_order for run in runs])
    ab_invocation_ids = _unique_present([run.ab_invocation_id for run in runs])
    ab_pair_ids = _unique_present([run.ab_pair_id for run in runs])
    ab_position_counts = _position_counts(runs)
    ab_position_counts_by_id = _position_counts_by_pair_id(runs)
    thermal_pair_readings = _thermal_pair_readings(runs)
    run_intervals = _run_intervals(runs)
    cooldown_intervals = _cooldown_intervals(runs)
    cooldown_run_gaps = [
        item["cooldown_run_gap_s"]
        for positions in cooldown_intervals.values()
        for item in positions.values()
        if item.get("cooldown_run_gap_s") is not None
    ]
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
        fps_target_confidence=fps_target_confidence,
        target_frame_ms=_target_frame_ms(fps_target),
        avg_fps_target_ratio_median=_median(
            [_run_avg_fps_target_ratio(run) for run in runs]
        ),
        fps_target_met_count=sum(1 for run in runs if _run_fps_target_met(run) is True),
        target_sustained_count=sum(1 for run in runs if _run_target_sustained(run)),
        target_average_only_count=sum(
            1 for run in runs if _run_post_classification(run) == "target-average-only"
        ),
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
        restore_affinity_snapshot_count=sum(
            1 for run in runs if _run_has_restore_affinity_snapshot(run)
        ),
        restore_affinity_thread_count_median=_median(
            [run.restore_affinity_thread_count for run in runs]
        ),
        restore_affinity_cgroup_count_median=_median(
            [run.restore_affinity_cgroup_count for run in runs]
        ),
        restore_affinity_cgroups=_aggregate_restore_affinity_cgroups(runs),
        restore_affinity_files=_aggregate_restore_affinity_files(runs),
        restore_affinity_cgroup_files=_aggregate_restore_affinity_cgroup_files(runs),
        restore_affinity_cgroup_file_values=_aggregate_restore_affinity_cgroup_file_values(
            runs
        ),
        foreground_affinity_valid_count=sum(
            1 for run in runs if run.foreground_affinity_valid_evidence is True
        ),
        foreground_affinity_write_count_median=_median(
            [run.foreground_affinity_write_count for run in runs]
        ),
        foreground_affinity_failed_count_median=_median(
            [run.foreground_affinity_failed_count for run in runs]
        ),
        foreground_affinity_matched_thread_count_median=_median(
            [run.foreground_affinity_matched_thread_count for run in runs]
        ),
        foreground_affinity_role_key=_single_present(
            [run.foreground_affinity_role_key for run in runs]
        ),
        foreground_affinity_preferred_cpus=_single_present(
            [run.foreground_affinity_preferred_cpus for run in runs]
        ),
        ab_order_strategy=_single_present([run.ab_order_strategy for run in runs]),
        ab_run_orders=ab_run_orders,
        ab_order_valid_count=sum(1 for run in runs if run.ab_order_valid),
        ab_candidate_policy=_single_present([run.ab_candidate_policy for run in runs]),
        ab_invocation_ids=ab_invocation_ids,
        ab_pair_ids=ab_pair_ids,
        ab_pair_position_counts=ab_position_counts,
        ab_pair_position_counts_by_id=ab_position_counts_by_id,
        scene_evidence=_single_present([run.scene_evidence for run in runs]),
        power_source_state=_single_present([run.power_source_state for run in runs]),
        power_source_start_state=_single_present(
            [run.power_source_start_state for run in runs]
        ),
        power_source_pre_run_state=_single_present(
            [run.power_source_pre_run_state for run in runs]
        ),
        power_source_end_state=_single_present([run.power_source_end_state for run in runs]),
        power_source_sample_signatures=_unique_present(
            [_sample_signature(run.power_source_samples) for run in runs]
        ),
        power_source_stable_count=sum(1 for run in runs if run.power_source_stable),
        thermal_start_c_median=_median([run.thermal_start_c for run in runs]),
        thermal_end_c_median=_median([run.thermal_end_c for run in runs]),
        thermal_unavailable_count=sum(1 for run in runs if run.thermal_unavailable),
        thermal_source_kind=_single_present([run.thermal_source_kind for run in runs]),
        thermal_source_id=_single_present([run.thermal_source_id for run in runs]),
        thermal_source_label=_single_present([run.thermal_source_label for run in runs]),
        thermal_pair_readings_by_id=thermal_pair_readings,
        thermal_pair_evidence_complete=all(
            reading.get("thermal_start_c") is not None
            and reading.get("thermal_end_c") is not None
            for positions in thermal_pair_readings.values()
            for reading in positions.values()
        )
        and bool(thermal_pair_readings),
        run_interval_by_pair_id=run_intervals,
        cooldown_interval_by_pair_id=cooldown_intervals,
        cooldown_interval_evidence_complete=all(
            item.get("cooldown_started_at_s") is not None
            and item.get("cooldown_ended_at_s") is not None
            and item.get("cooldown_elapsed_s") is not None
            and item.get("cooldown_run_gap_s") is not None
            for positions in cooldown_intervals.values()
            for item in positions.values()
        )
        and bool(cooldown_intervals),
        cooldown_rule=_single_present([run.cooldown_rule for run in runs]),
        cooldown_enforced_count=sum(1 for run in runs if run.cooldown_enforced),
        cooldown_started_at_s_min=_value_min([run.cooldown_started_at_s for run in runs]),
        cooldown_ended_at_s_max=_value_max([run.cooldown_ended_at_s for run in runs]),
        cooldown_elapsed_s_median=_median([run.cooldown_elapsed_s for run in runs]),
        cooldown_run_gap_s_max=max(cooldown_run_gaps) if cooldown_run_gaps else None,
        pair_run_order_valid=bool(ab_position_counts_by_id),
        ab_evidence_complete=_aggregate_ab_evidence_complete(runs),
        classification_primary=_sum_counter_dicts(
            [run.classification_primary for run in runs]
        ),
        classification_advisories=_sum_counter_dicts(
            [run.classification_advisories for run in runs]
        ),
        classification_malformed=sum(run.classification_malformed for run in runs),
        fps_target_source_counts=_sum_counter_dicts(
            [run.fps_target_source_counts for run in runs]
        ),
        fps_target_confidence_counts=_sum_counter_dicts(
            [run.fps_target_confidence_counts for run in runs]
        ),
        runtime_telemetry_counts=_sum_runtime_counts(
            [run.runtime_telemetry_counts for run in runs]
        ),
        classification_unknown_ratio=_ratio(
            _sum_runtime_counts(
                [run.runtime_telemetry_counts for run in runs]
            ).unknown_foreground_rows,
            _sum_runtime_counts(
                [run.runtime_telemetry_counts for run in runs]
            ).foreground_runtime_rows,
        ),
        pressure_supported_ratio=_ratio(
            _sum_runtime_counts(
                [run.runtime_telemetry_counts for run in runs]
            ).supported_foreground_pressure_signals,
            _sum_runtime_counts(
                [run.runtime_telemetry_counts for run in runs]
            ).foreground_pressure_signals,
        ),
        pressure_unsupported_ratio=_ratio(
            _sum_runtime_counts(
                [run.runtime_telemetry_counts for run in runs]
            ).unsupported_foreground_pressure_signals,
            _sum_runtime_counts(
                [run.runtime_telemetry_counts for run in runs]
            ).foreground_pressure_signals,
        ),
    )


def _ab_pairwise_gate(
    baseline: PolicyAggregate,
    candidate: PolicyAggregate,
) -> tuple[str | None, dict[str, object]]:
    diagnostics: dict[str, object] = {
        "thermal_pair_start_delta_max_c": None,
        "thermal_pair_end_delta_max_c": None,
        "thermal_pair_mismatch_count": 0,
        "thermal_start_delta_c": None,
        "thermal_end_delta_c": None,
        "cooldown_run_gap_s_max": None,
        "cooldown_interval_reuse_count": 0,
    }
    if not baseline.ab_evidence_complete or not candidate.ab_evidence_complete:
        return "aggregate-local evidence is incomplete", diagnostics
    if baseline.policy != "off":
        return "baseline policy must be off", diagnostics
    baseline_orders = baseline.ab_run_orders or []
    candidate_orders = candidate.ab_run_orders or []
    if len(baseline_orders) != 1 or len(candidate_orders) != 1:
        return "each aggregate must have exactly one A/B run order", diagnostics
    if baseline_orders[0] != candidate_orders[0]:
        return "baseline and candidate A/B run orders differ", diagnostics
    expected_order = f"off,{candidate.policy},off"
    if baseline_orders[0] != expected_order:
        return "paired-baseline run order does not match candidate policy", diagnostics
    if (
        baseline.ab_candidate_policy != candidate.policy
        or candidate.ab_candidate_policy != candidate.policy
    ):
        return "A/B candidate policy identity does not match candidate aggregate", diagnostics
    baseline_pair_ids = set(baseline.ab_pair_ids or [])
    candidate_pair_ids = set(candidate.ab_pair_ids or [])
    if baseline_pair_ids != candidate_pair_ids:
        return "baseline and candidate pair id sets differ", diagnostics
    if baseline.sample_count != candidate.sample_count * 2:
        return "baseline sample count must be exactly twice candidate sample count", diagnostics
    baseline_counts = baseline.ab_pair_position_counts_by_id or {}
    candidate_counts = candidate.ab_pair_position_counts_by_id or {}
    for pair_id in sorted(candidate_pair_ids):
        if baseline_counts.get(pair_id, {}).get("baseline-before") != 1:
            return "paired-baseline is missing baseline-before", diagnostics
        if baseline_counts.get(pair_id, {}).get("baseline-after") != 1:
            return "paired-baseline is missing baseline-after", diagnostics
        if candidate_counts.get(pair_id, {}).get("candidate") != 1:
            return "paired-baseline is missing candidate", diagnostics
        if any(count != 1 for count in baseline_counts.get(pair_id, {}).values()):
            return "paired-baseline has duplicate baseline pair positions", diagnostics
        if any(count != 1 for count in candidate_counts.get(pair_id, {}).values()):
            return "paired-baseline has duplicate candidate pair positions", diagnostics
    if (
        baseline.thermal_source_kind,
        baseline.thermal_source_id,
        baseline.thermal_source_label,
    ) != (
        candidate.thermal_source_kind,
        candidate.thermal_source_id,
        candidate.thermal_source_label,
    ):
        return "thermal source identity differs between baseline and candidate", diagnostics
    if baseline.ab_order_strategy != candidate.ab_order_strategy:
        return "A/B order strategy differs between baseline and candidate", diagnostics
    if baseline.scene_evidence != candidate.scene_evidence:
        return "scene evidence differs between baseline and candidate", diagnostics
    if baseline.power_source_state != candidate.power_source_state:
        return "power source state differs between baseline and candidate", diagnostics
    start_delta = _abs_delta(baseline.thermal_start_c_median, candidate.thermal_start_c_median)
    if start_delta is None:
        return "aggregate start thermal median is missing", diagnostics
    diagnostics["thermal_start_delta_c"] = round(start_delta, 3)
    # D7: END temps are reported, not gated. A -7 W candidate ends ~7 C cooler;
    # gating on it would reject every genuine power win. Compute the end median
    # delta for claim_scope but never reject on it.
    end_delta = _abs_delta(baseline.thermal_end_c_median, candidate.thermal_end_c_median)
    if end_delta is not None:
        diagnostics["thermal_end_delta_c"] = round(end_delta, 3)
    if start_delta > AB_THERMAL_DELTA_MAX_C:
        return "aggregate start thermal medians differ too much", diagnostics

    baseline_thermal = baseline.thermal_pair_readings_by_id or {}
    candidate_thermal = candidate.thermal_pair_readings_by_id or {}
    baseline_runs = baseline.run_interval_by_pair_id or {}
    candidate_runs = candidate.run_interval_by_pair_id or {}
    baseline_cooldowns = baseline.cooldown_interval_by_pair_id or {}
    candidate_cooldowns = candidate.cooldown_interval_by_pair_id or {}
    start_deltas: list[float] = []
    end_deltas: list[float] = []
    cooldown_gaps: list[float] = []
    reused_cooldowns = 0
    thermal_mismatches = 0
    for pair_id in sorted(candidate_pair_ids):
        before_thermal = (baseline_thermal.get(pair_id) or {}).get("baseline-before")
        candidate_thermal_item = (candidate_thermal.get(pair_id) or {}).get("candidate")
        after_thermal = (baseline_thermal.get(pair_id) or {}).get("baseline-after")
        if not before_thermal or not candidate_thermal_item or not after_thermal:
            return "pair-scoped thermal readings are incomplete", diagnostics
        # D7: START-temperature parity is the pairing confound control and is
        # the only thermal PARITY gate; a start mismatch still rejects. END
        # deltas are collected for reporting (claim_scope) but never counted as
        # a mismatch -- a power-saving candidate is expected to end cooler.
        for key, delta_list, gated in (
            ("thermal_start_c", start_deltas, True),
            ("thermal_end_c", end_deltas, False),
        ):
            candidate_value = candidate_thermal_item.get(key)
            before_delta = _abs_delta(before_thermal.get(key), candidate_value)
            after_delta = _abs_delta(after_thermal.get(key), candidate_value)
            if before_delta is None or after_delta is None:
                return "pair-scoped thermal readings are incomplete", diagnostics
            delta_list.extend([before_delta, after_delta])
            if gated and (
                before_delta > AB_THERMAL_DELTA_MAX_C
                or after_delta > AB_THERMAL_DELTA_MAX_C
            ):
                thermal_mismatches += 1

        before_run = (baseline_runs.get(pair_id) or {}).get("baseline-before")
        candidate_run = (candidate_runs.get(pair_id) or {}).get("candidate")
        after_run = (baseline_runs.get(pair_id) or {}).get("baseline-after")
        if not before_run or not candidate_run or not after_run:
            return "pair-scoped run intervals are incomplete", diagnostics
        if not _run_interval_is_real(before_run) or not _run_interval_is_real(
            candidate_run
        ) or not _run_interval_is_real(after_run):
            return "pair-scoped run intervals are incomplete", diagnostics
        if not (
            before_run["run_ended_at_s"]
            <= candidate_run["run_started_at_s"]
            <= candidate_run["run_ended_at_s"]
            <= after_run["run_started_at_s"]
        ):
            return "paired-baseline run intervals are not monotonic", diagnostics

        before_cooldown = (baseline_cooldowns.get(pair_id) or {}).get("baseline-before")
        candidate_cooldown = (candidate_cooldowns.get(pair_id) or {}).get("candidate")
        after_cooldown = (baseline_cooldowns.get(pair_id) or {}).get("baseline-after")
        if not before_cooldown or not candidate_cooldown or not after_cooldown:
            return "pair-scoped cooldown intervals are incomplete", diagnostics
        cooldowns = [before_cooldown, candidate_cooldown, after_cooldown]
        cooldown_keys = {
            (
                item.get("cooldown_started_at_s"),
                item.get("cooldown_ended_at_s"),
            )
            for item in cooldowns
        }
        reused_cooldowns += len(cooldowns) - len(cooldown_keys)
        for cooldown, run in (
            (before_cooldown, before_run),
            (candidate_cooldown, candidate_run),
            (after_cooldown, after_run),
        ):
            if not _cooldown_interval_is_real(cooldown):
                return "pair-scoped cooldown intervals are incomplete", diagnostics
            gap = cooldown.get("cooldown_run_gap_s")
            if isinstance(gap, float | int):
                cooldown_gaps.append(float(gap))
            if cooldown["cooldown_ended_at_s"] > run["run_started_at_s"]:
                return "cooldown interval overlaps measured run", diagnostics
            if (
                gap is None
                or gap < 0.0
                or gap > FIXED_COOLDOWN_RUN_GAP_MAX_S
            ):
                return "measured run is not adjacent to cooldown", diagnostics
        if candidate_cooldown["cooldown_started_at_s"] < before_run["run_ended_at_s"]:
            return "candidate cooldown starts before baseline-before run ends", diagnostics
        if after_cooldown["cooldown_started_at_s"] < candidate_run["run_ended_at_s"]:
            return "baseline-after cooldown starts before candidate run ends", diagnostics
    diagnostics["thermal_pair_start_delta_max_c"] = (
        round(max(start_deltas), 3) if start_deltas else None
    )
    diagnostics["thermal_pair_end_delta_max_c"] = (
        round(max(end_deltas), 3) if end_deltas else None
    )
    diagnostics["thermal_pair_mismatch_count"] = thermal_mismatches
    diagnostics["cooldown_run_gap_s_max"] = (
        round(max(cooldown_gaps), 3) if cooldown_gaps else None
    )
    diagnostics["cooldown_interval_reuse_count"] = reused_cooldowns
    if thermal_mismatches:
        return "pair-scoped start thermal readings differ too much", diagnostics
    if reused_cooldowns:
        return "pair-scoped cooldown interval was reused", diagnostics
    return None, diagnostics


def _run_interval_is_real(item: dict[str, float | None]) -> bool:
    start = item.get("run_started_at_s")
    end = item.get("run_ended_at_s")
    return start is not None and end is not None and end > start


def _cooldown_interval_is_real(item: dict[str, float | None]) -> bool:
    started = item.get("cooldown_started_at_s")
    ended = item.get("cooldown_ended_at_s")
    elapsed = item.get("cooldown_elapsed_s")
    gap = item.get("cooldown_run_gap_s")
    return (
        started is not None
        and ended is not None
        and elapsed is not None
        and gap is not None
        and ended >= started
        and elapsed >= FIXED_COOLDOWN_MIN_S
    )


def _abs_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return abs(left - right)


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
    if (
        candidate.policy == "gpu-priority-affinity"
        and candidate.foreground_affinity_valid_count != candidate.sample_count
    ):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "foreground affinity evidence did not pass for every candidate run",
        )

    ab_reason, ab_diagnostics = _ab_pairwise_gate(baseline, candidate)
    if ab_reason is not None:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.INCONCLUSIVE,
            _incomplete_reason(ab_reason),
            **ab_diagnostics,
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
        reason = (
            f"median 1% low improved by {low_gain:.1f}% "
            f"with median average FPS change {avg_gain or 0:.1f}%"
        )
        return _better_policy_comparison(
            baseline.policy,
            candidate.policy,
            reason,
            baseline=baseline,
            candidate=candidate,
            diagnostics=ab_diagnostics,
        )
    if p99_gain is not None and p99_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        reason = (
            f"median p99 frametime improved by {p99_gain:.1f}% "
            f"with median average FPS change {avg_gain or 0:.1f}%"
        )
        return _better_policy_comparison(
            baseline.policy,
            candidate.policy,
            reason,
            baseline=baseline,
            candidate=candidate,
            diagnostics=ab_diagnostics,
        )
    if (
        _aggregate_target_sustained(baseline)
        and _aggregate_target_sustained(candidate)
        and package_saving is not None
        and package_saving >= TARGET_POWER_SAVING_MIN_PCT
        and (low_gain is None or low_gain >= PACING_REGRESSION_REJECT_PCT)
        and (p99_gain is None or p99_gain >= PACING_REGRESSION_REJECT_PCT)
    ):
        reason = (
            f"target sustained while median package power reduced by "
            f"{package_saving:.1f}%"
        )
        return _better_policy_comparison(
            baseline.policy,
            candidate.policy,
            reason,
            baseline=baseline,
            candidate=candidate,
            diagnostics=ab_diagnostics,
        )
    if avg_gain is not None and avg_gain >= 5.0 and (low_gain is None or low_gain >= -2.0):
        reason = (
            f"median average FPS improved by {avg_gain:.1f}% "
            f"without low-percentile regression"
        )
        return _better_policy_comparison(
            baseline.policy,
            candidate.policy,
            reason,
            baseline=baseline,
            candidate=candidate,
            diagnostics=ab_diagnostics,
        )
    if low_gain is not None and low_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"median 1% low worsened by {abs(low_gain):.1f}%",
            **ab_diagnostics,
        )
    if p99_gain is not None and p99_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"median p99 frametime worsened by {abs(p99_gain):.1f}%",
            **ab_diagnostics,
        )
    return PolicyComparison(
        baseline.policy,
        candidate.policy,
        PolicyVerdict.INCONCLUSIVE,
        "candidate medians did not meet improvement or rejection thresholds",
        **ab_diagnostics,
    )


def _better_policy_comparison(
    baseline_policy: str,
    candidate_policy: str,
    reason: str,
    *,
    baseline: PolicyAggregate,
    candidate: PolicyAggregate,
    diagnostics: dict[str, object],
) -> PolicyComparison:
    return PolicyComparison(
        baseline_policy,
        candidate_policy,
        PolicyVerdict.BETTER,
        reason,
        **diagnostics,
        claim_scope=_claim_scope(baseline, candidate, diagnostics),
        human_summary=(
            f"BETTER ({BETTER_CLAIM_BOUNDARY}): {reason}; "
            f"{GUARDED_ARTIFACT_CAVEAT}"
        ),
    )


def _claim_scope(
    baseline: PolicyAggregate,
    candidate: PolicyAggregate,
    diagnostics: dict[str, object],
) -> dict[str, object]:
    return {
        "appid": baseline.appid,
        "scene_evidence": baseline.scene_evidence,
        "baseline_policy": baseline.policy,
        "candidate_policy": candidate.policy,
        "tdp_w": baseline.tdp_w,
        "duration_s": baseline.duration_s,
        "warmup_s": baseline.warmup_s,
        "poll_s": baseline.poll_s,
        "fps_target": baseline.fps_target,
        "fps_target_source": baseline.fps_target_source,
        "pair_count": len(candidate.ab_pair_ids or []),
        "ab_order_strategy": baseline.ab_order_strategy,
        "ab_run_order": (baseline.ab_run_orders or [None])[0],
        "power_source_state": baseline.power_source_state,
        "thermal_source_kind": baseline.thermal_source_kind,
        "thermal_source_id": baseline.thermal_source_id,
        "thermal_pair_start_delta_max_c": diagnostics.get(
            "thermal_pair_start_delta_max_c"
        ),
        "thermal_pair_end_delta_max_c": diagnostics.get("thermal_pair_end_delta_max_c"),
        # D7: START delta is the gated parity metric; END delta is reported
        # context only (a BETTER power candidate is expected to end cooler).
        "thermal_start_delta_c": diagnostics.get("thermal_start_delta_c"),
        "thermal_end_delta_c": diagnostics.get("thermal_end_delta_c"),
        "cooldown_rule": baseline.cooldown_rule,
        "cooldown_elapsed_s_median": candidate.cooldown_elapsed_s_median,
        "evidence_boundary": BETTER_CLAIM_BOUNDARY,
        "hardware_claim_requires": (
            f"{GUARDED_ARTIFACT_CAVEAT}; not sufficient for hardware-wide, "
            "game-wide, release-note, or default-policy performance claims "
            "without a separate claim plan"
        ),
    }


def _run_has_restore_affinity_snapshot(run: RunSummary) -> bool:
    return (
        (run.restore_affinity_thread_count or 0) > 0
        and (run.restore_affinity_cgroup_count or 0) > 0
        and bool(run.restore_affinity_files)
    )


def _aggregate_restore_affinity_files(runs: list[RunSummary]) -> list[str]:
    files: set[str] = set()
    for run in runs:
        if run.restore_affinity_files:
            files.update(run.restore_affinity_files)
    return sorted(files)


def _aggregate_restore_affinity_cgroups(runs: list[RunSummary]) -> list[str]:
    cgroups: set[str] = set()
    for run in runs:
        if run.restore_affinity_cgroups:
            cgroups.update(run.restore_affinity_cgroups)
    return sorted(cgroups)


def _aggregate_restore_affinity_cgroup_files(
    runs: list[RunSummary],
) -> dict[str, list[str]]:
    files_by_cgroup: dict[str, set[str]] = {}
    for run in runs:
        if not run.restore_affinity_cgroup_files:
            continue
        for cgroup, files in run.restore_affinity_cgroup_files.items():
            if not isinstance(files, list):
                continue
            state = files_by_cgroup.setdefault(cgroup, set())
            state.update(str(item) for item in files)
    return {
        cgroup: sorted(files)
        for cgroup, files in sorted(files_by_cgroup.items())
    }


def _aggregate_restore_affinity_cgroup_file_values(
    runs: list[RunSummary],
) -> dict[str, dict[str, list[str]]]:
    values_by_cgroup: dict[str, dict[str, set[str]]] = {}
    for run in runs:
        if not run.restore_affinity_cgroup_file_values:
            continue
        for cgroup, file_values in run.restore_affinity_cgroup_file_values.items():
            if not isinstance(file_values, dict):
                continue
            state = values_by_cgroup.setdefault(cgroup, {})
            for filename, value in file_values.items():
                values = state.setdefault(str(filename), set())
                values.add(str(value))
    return {
        cgroup: {
            filename: sorted(values)
            for filename, values in sorted(file_values.items())
        }
        for cgroup, file_values in sorted(values_by_cgroup.items())
    }


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
    summarize.add_argument("--restore-affinity-json")
    summarize.add_argument("--foreground-affinity-writes-json")
    summarize.add_argument("--foreground-affinity-restore-json")
    summarize.add_argument("--epp")
    summarize.add_argument("--pcore-max-mhz", type=int)
    summarize.add_argument("--ecore-max-mhz", type=int)
    summarize.add_argument("--cpu-cap-enabled", choices=["true", "false"])
    summarize.add_argument("--cpu-cap-core-share-threshold", type=float)
    summarize.add_argument("--fps-target", type=float)
    summarize.add_argument("--fps-target-source")
    summarize.add_argument("--fps-target-confidence")
    summarize.add_argument("--duration-s", type=float)
    summarize.add_argument("--warmup-s", type=float)
    summarize.add_argument("--poll-s", type=float)
    summarize.add_argument("--ab-order-strategy")
    summarize.add_argument("--ab-run-order")
    summarize.add_argument("--ab-order-valid", choices=["true", "false"])
    summarize.add_argument("--ab-candidate-policy")
    summarize.add_argument("--ab-invocation-id")
    summarize.add_argument("--ab-pair-id")
    summarize.add_argument(
        "--ab-pair-position",
        choices=["baseline-before", "candidate", "baseline-after"],
    )
    summarize.add_argument("--scene-evidence")
    summarize.add_argument(
        "--power-source-state",
        choices=["ac", "battery", "mixed", "unknown"],
    )
    summarize.add_argument(
        "--power-source-start-state",
        choices=["ac", "battery", "unknown"],
    )
    summarize.add_argument(
        "--power-source-pre-run-state",
        choices=["ac", "battery", "unknown"],
    )
    summarize.add_argument(
        "--power-source-end-state",
        choices=["ac", "battery", "unknown"],
    )
    summarize.add_argument("--power-source-samples")
    summarize.add_argument("--power-source-stable", choices=["true", "false"])
    summarize.add_argument("--thermal-start-c", type=float)
    summarize.add_argument("--thermal-end-c", type=float)
    summarize.add_argument("--thermal-unavailable", choices=["true", "false"])
    summarize.add_argument(
        "--thermal-source-kind",
        choices=["cpu-package", "platform", "other", "unknown"],
    )
    summarize.add_argument("--thermal-source-id")
    summarize.add_argument("--thermal-source-label")
    summarize.add_argument("--run-started-at-s", type=float)
    summarize.add_argument("--run-ended-at-s", type=float)
    summarize.add_argument("--cooldown-rule")
    summarize.add_argument("--cooldown-enforced", choices=["true", "false"])
    summarize.add_argument("--cooldown-started-at-s", type=float)
    summarize.add_argument("--cooldown-ended-at-s", type=float)
    summarize.add_argument("--cooldown-elapsed-s", type=float)
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

    apply_background = subcommands.add_parser("apply-background-shaping")
    apply_background.add_argument("--restore-affinity-json", required=True)
    apply_background.add_argument("--output", required=True)
    apply_background.add_argument("--appid", required=True)
    apply_background.add_argument(
        "--variant",
        choices=sorted(BACKGROUND_SHAPING_WRITE_VARIANTS),
        required=True,
    )

    restore_background = subcommands.add_parser("restore-background-shaping")
    restore_background.add_argument("--writes-json", required=True)
    restore_background.add_argument("--output", required=True)

    apply_foreground = subcommands.add_parser("apply-foreground-affinity")
    apply_foreground.add_argument("--restore-affinity-json", required=True)
    apply_foreground.add_argument("--output", required=True)
    apply_foreground.add_argument("--role-key", required=True)
    apply_foreground.add_argument("--preferred-cpus", required=True)
    apply_foreground.add_argument(
        "--variant",
        choices=sorted(FOREGROUND_AFFINITY_WRITE_VARIANTS),
        required=True,
    )

    restore_foreground = subcommands.add_parser("restore-foreground-affinity")
    restore_foreground.add_argument("--writes-json", required=True)
    restore_foreground.add_argument("--output", required=True)

    apply_fg_uclamp = subcommands.add_parser("apply-foreground-uclamp")
    apply_fg_uclamp.add_argument("--restore-affinity-json", required=True)
    apply_fg_uclamp.add_argument("--output", required=True)
    apply_fg_uclamp.add_argument("--appid", required=True)
    apply_fg_uclamp.add_argument(
        "--floor-value", default=FOREGROUND_UCLAMP_MIN_FLOOR
    )

    restore_fg_uclamp = subcommands.add_parser("restore-foreground-uclamp")
    restore_fg_uclamp.add_argument("--writes-json", required=True)
    restore_fg_uclamp.add_argument("--output", required=True)

    resolve_foreground = subcommands.add_parser("resolve-foreground-affinity")
    resolve_foreground.add_argument("--plan-json", required=True)

    validate_runtime = subcommands.add_parser("validate-runtime-telemetry")
    validate_runtime.add_argument("--game-power-jsonl", required=True)
    validate_runtime.add_argument("--summary-json")
    validate_runtime.add_argument("--action-replay-json")
    validate_runtime.add_argument("--require-classification", action="store_true")
    validate_runtime.add_argument("--require-pressure", action="store_true")
    validate_runtime.add_argument("--require-cpu-cap-action", action="store_true")
    validate_runtime.add_argument("--require-frame-performance", action="store_true")
    validate_runtime.add_argument("--require-fps-target-satisfied", action="store_true")
    validate_runtime.add_argument(
        "--require-target-balance-contract", action="store_true"
    )
    validate_runtime.add_argument("--require-v10-contract", action="store_true")
    validate_runtime.add_argument("--expect-fps-target", type=float)
    validate_runtime.add_argument("--expect-fps-target-source")
    validate_runtime.add_argument("--expect-fps-target-confidence")
    validate_runtime.add_argument("--expect-target-frame-ms", type=float)
    validate_runtime.add_argument("--output")

    replay_actions = subcommands.add_parser("replay-action-equivalence")
    replay_actions.add_argument("--output")

    export_verdicts_parser = subcommands.add_parser("export-verdicts")
    export_verdicts_parser.add_argument("--aggregate", action="append")
    export_verdicts_parser.add_argument("--root", action="append")
    export_verdicts_parser.add_argument("--baseline-policy", default="off")
    export_verdicts_parser.add_argument("--candidate-policy", action="append")
    export_verdicts_parser.add_argument("--appid")
    export_verdicts_parser.add_argument("--tdp-w", type=int)
    export_verdicts_parser.add_argument("--fps-target", type=float)
    export_verdicts_parser.add_argument(
        "--capture-mode",
        choices=[mode.value for mode in CaptureMode],
        default=CaptureMode.CONTROLLED.value,
    )
    export_verdicts_parser.add_argument("--min-runs", type=int, default=3)
    export_verdicts_parser.add_argument("--cpu-topology-json")
    export_verdicts_parser.add_argument("--topology-fingerprint")
    export_verdicts_parser.add_argument("--kernel", required=True)
    export_verdicts_parser.add_argument("--policy-version")
    export_verdicts_parser.add_argument("--out")
    return parser


def run_summarize(args: argparse.Namespace) -> Path:
    capture_mode = CaptureMode(args.capture_mode)
    if args.mangohud_summary_csv:
        fps = parse_mangohud_summary_csv(args.mangohud_summary_csv, capture_mode=capture_mode)
        # The mangoapp summary CSV carries lows/97th but no p95/p99 frametime.
        # When the raw per-frame CSV is also present, backfill p95/p99 (and any
        # missing avg frametime) from it while keeping the summary's lows as-is.
        if args.mangohud_csv and (
            fps.p95_frametime_ms is None or fps.p99_frametime_ms is None
        ):
            try:
                raw = parse_mangohud_fps_csv(args.mangohud_csv, capture_mode=capture_mode)
            except (OSError, ValueError):
                raw = None
            if raw is not None:
                fps = replace(
                    fps,
                    p95_frametime_ms=(
                        fps.p95_frametime_ms
                        if fps.p95_frametime_ms is not None
                        else raw.p95_frametime_ms
                    ),
                    p99_frametime_ms=(
                        fps.p99_frametime_ms
                        if fps.p99_frametime_ms is not None
                        else raw.p99_frametime_ms
                    ),
                    avg_frametime_ms=(
                        fps.avg_frametime_ms
                        if fps.avg_frametime_ms is not None
                        else raw.avg_frametime_ms
                    ),
                )
    elif args.mangohud_csv:
        fps = parse_mangohud_fps_csv(args.mangohud_csv, capture_mode=capture_mode)
    else:
        raise SystemExit("summarize requires --mangohud-csv or --mangohud-summary-csv")

    power = parse_game_power_jsonl(args.game_power_jsonl) if args.game_power_jsonl else None
    phase_metrics = (
        summarize_phase_metrics(args.game_power_jsonl, poll_s=args.poll_s)
        if args.game_power_jsonl
        else None
    )
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
    restore_affinity = (
        summarize_restore_affinity_json(args.restore_affinity_json)
        if args.restore_affinity_json
        else None
    )
    foreground_affinity = summarize_foreground_affinity_artifacts(
        args.foreground_affinity_writes_json,
        args.foreground_affinity_restore_json,
    )
    ab_evidence = AbEvidence(
        order_strategy=args.ab_order_strategy,
        run_order=args.ab_run_order,
        order_valid=_optional_bool(args.ab_order_valid) is True,
        candidate_policy=args.ab_candidate_policy,
        invocation_id=args.ab_invocation_id,
        pair_id=args.ab_pair_id,
        pair_position=args.ab_pair_position,
        scene_evidence=args.scene_evidence,
        power_source_state=args.power_source_state,
        power_source_start_state=args.power_source_start_state,
        power_source_pre_run_state=args.power_source_pre_run_state,
        power_source_end_state=args.power_source_end_state,
        power_source_samples=_csv_values(args.power_source_samples),
        power_source_stable=_optional_bool(args.power_source_stable) is True,
        thermal_start_c=args.thermal_start_c,
        thermal_end_c=args.thermal_end_c,
        thermal_unavailable=_optional_bool(args.thermal_unavailable) is True,
        thermal_source_kind=args.thermal_source_kind,
        thermal_source_id=args.thermal_source_id,
        thermal_source_label=args.thermal_source_label,
        run_started_at_s=args.run_started_at_s,
        run_ended_at_s=args.run_ended_at_s,
        cooldown_rule=args.cooldown_rule,
        cooldown_enforced=_optional_bool(args.cooldown_enforced) is True,
        cooldown_started_at_s=args.cooldown_started_at_s,
        cooldown_ended_at_s=args.cooldown_ended_at_s,
        cooldown_elapsed_s=args.cooldown_elapsed_s,
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
        "fps_target_confidence": args.fps_target_confidence,
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
        "color_ledger_json": bool(
            args.thread_schedstat_jsonl or args.process_cgroups_jsonl
        ),
        "restore_affinity_json": bool(args.restore_affinity_json),
        "foreground_affinity_writes_json": bool(args.foreground_affinity_writes_json),
        "foreground_affinity_restore_json": bool(args.foreground_affinity_restore_json),
        "ab_order_strategy": ab_evidence.order_strategy,
        "ab_run_order": ab_evidence.run_order,
        "ab_order_valid": ab_evidence.order_valid,
        "ab_candidate_policy": ab_evidence.candidate_policy,
        "ab_invocation_id": ab_evidence.invocation_id,
        "ab_pair_id": ab_evidence.pair_id,
        "ab_pair_position": ab_evidence.pair_position,
        "scene_evidence": ab_evidence.scene_evidence,
        "power_source_state": ab_evidence.power_source_state,
        "power_source_start_state": ab_evidence.power_source_start_state,
        "power_source_pre_run_state": ab_evidence.power_source_pre_run_state,
        "power_source_end_state": ab_evidence.power_source_end_state,
        "power_source_samples": ab_evidence.power_source_samples,
        "power_source_stable": ab_evidence.power_source_stable,
        "thermal_start_c": ab_evidence.thermal_start_c,
        "thermal_end_c": ab_evidence.thermal_end_c,
        "thermal_unavailable": ab_evidence.thermal_unavailable,
        "thermal_source_kind": ab_evidence.thermal_source_kind,
        "thermal_source_id": ab_evidence.thermal_source_id,
        "thermal_source_label": ab_evidence.thermal_source_label,
        "run_started_at_s": ab_evidence.run_started_at_s,
        "run_ended_at_s": ab_evidence.run_ended_at_s,
        "cooldown_rule": ab_evidence.cooldown_rule,
        "cooldown_enforced": ab_evidence.cooldown_enforced,
        "cooldown_started_at_s": ab_evidence.cooldown_started_at_s,
        "cooldown_ended_at_s": ab_evidence.cooldown_ended_at_s,
        "cooldown_elapsed_s": ab_evidence.cooldown_elapsed_s,
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
        restore_affinity=restore_affinity,
        foreground_affinity=foreground_affinity,
        epp=args.epp,
        pcore_max_mhz=args.pcore_max_mhz,
        ecore_max_mhz=args.ecore_max_mhz,
        cpu_cap_enabled=_optional_bool(args.cpu_cap_enabled),
        cpu_cap_core_share_threshold=args.cpu_cap_core_share_threshold,
        fps_target=args.fps_target,
        fps_target_source=args.fps_target_source,
        fps_target_confidence=args.fps_target_confidence,
        duration_s=args.duration_s,
        warmup_s=args.warmup_s,
        poll_s=args.poll_s,
        ab_evidence=ab_evidence,
        phase_metrics=phase_metrics,
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
    if args.thread_schedstat_jsonl or args.process_cgroups_jsonl:
        # Wire restore-snapshot coverage so roles whose cgroup lacks a restore
        # snapshot color E (design section 6). ``None`` (no restore-affinity
        # artifact) means "assume covered" and keeps prior behavior.
        restore_covered_cgroups = (
            set(restore_affinity.cgroups)
            if restore_affinity is not None and restore_affinity.cgroups
            else None
        )
        color_ledger = build_color_ledger_from_artifacts(
            appid=args.appid,
            window_s=args.duration_s or 1.0,
            thread_schedstat_jsonl=args.thread_schedstat_jsonl,
            process_cgroups_jsonl=args.process_cgroups_jsonl,
            restore_covered_cgroups=restore_covered_cgroups,
        )
        (output / "color-ledger.json").write_text(
            json.dumps(_json_ready(color_ledger.to_json()), indent=2, sort_keys=True) + "\n"
        )
    return output / "summary.json"


def run_compare(args: argparse.Namespace) -> PolicyComparison:
    baseline = _load_run_summary(args.baseline)
    candidate = _load_run_summary(args.candidate)
    return compare_run_summaries(baseline, candidate)


def run_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    capture_mode = CaptureMode(args.capture_mode)
    requested_candidates = set(args.candidate_policy)
    records: list[tuple[Path, RunSummary]] = []
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
        if (
            summary.policy == args.baseline_policy
            and summary.ab_candidate_policy
            and summary.ab_candidate_policy not in requested_candidates
        ):
            continue
        records.append((path, summary))

    groups: dict[tuple[object, ...], list[RunSummary]] = defaultdict(list)
    paths_by_group: dict[tuple[object, ...], list[Path]] = defaultdict(list)
    for path, summary in records:
        groups[_profile_group_key(summary)].append(summary)
        paths_by_group[_profile_group_key(summary)].append(path)

    baseline_keys_by_context: dict[tuple[object, ...], list[tuple[object, ...]]] = defaultdict(
        list
    )
    for key in groups:
        _appid, _tdp_w, policy = key[:3]
        if policy == args.baseline_policy:
            baseline_keys_by_context[_comparison_context_key(key)].append(key)

    comparisons = []
    incomplete_groups = []
    matched_baseline_keys: set[tuple[object, ...]] = set()
    candidate_keys = sorted(
        (key for key in groups if key[2] in args.candidate_policy),
        key=_sortable_group_key,
    )
    for candidate_key in candidate_keys:
        appid, tdp_w, _candidate_policy = candidate_key[:3]
        baseline_keys = baseline_keys_by_context.get(_comparison_context_key(candidate_key), [])
        if not baseline_keys:
            candidate = aggregate_run_summaries(groups[candidate_key])
            incomplete_groups.append(
                _incomplete_group(
                    baseline_policy=args.baseline_policy,
                    candidate_policy=str(_candidate_policy),
                    aggregate=candidate,
                    missing_side="baseline",
                )
            )
            continue
        # Q4: candidate-side rollups depend only on the candidate group; compute
        # them once here instead of per matched baseline (identical output).
        candidate_runs = groups[candidate_key]
        candidate = aggregate_run_summaries(candidate_runs)
        candidate_sched_ext = aggregate_sched_ext_evidence(
            paths_by_group[candidate_key]
        )
        candidate_gpu_floor = aggregate_gpu_floor_evidence(
            paths_by_group[candidate_key]
        )
        candidate_foreground_uclamp = aggregate_foreground_uclamp_evidence(
            paths_by_group[candidate_key]
        )
        candidate_roles = aggregate_affinity_roles(paths_by_group[candidate_key])
        candidate_background = aggregate_background_shaping_candidates(
            paths_by_group[candidate_key]
        )
        candidate_color_ledger = aggregate_color_ledger(paths_by_group[candidate_key])
        for baseline_key in sorted(baseline_keys, key=_sortable_group_key):
            matched_baseline_keys.add(baseline_key)
            baseline_runs = groups[baseline_key]
            baseline = aggregate_run_summaries(baseline_runs)
            comparison = compare_policy_aggregates(
                baseline,
                candidate,
                min_runs=args.min_runs,
            )
            # Validity gating for the new V9 profiler lanes: scx-lavd requires
            # sched-ext state evidence, target-balance-gpufloor requires gpu-freq
            # restore evidence, and target-balance-uclampmin requires foreground
            # uclamp.min restore evidence in every candidate run (same
            # all-runs-covered idiom as foreground affinity).
            comparison = _gate_new_lane_evidence(
                comparison,
                candidate_policy=str(_candidate_policy),
                sched_ext_evidence=candidate_sched_ext,
                gpu_floor_evidence=candidate_gpu_floor,
                foreground_uclamp_evidence=candidate_foreground_uclamp,
            )
            baseline_roles = aggregate_affinity_roles(paths_by_group[baseline_key])
            baseline_background = aggregate_background_shaping_candidates(
                paths_by_group[baseline_key]
            )
            comparisons.append(
                {
                    "appid": appid,
                    "tdp_w": tdp_w,
                    "baseline": asdict(baseline),
                    "candidate": asdict(candidate),
                    "baseline_affinity_roles": baseline_roles,
                    "candidate_affinity_roles": candidate_roles,
                    "baseline_background_shaping_candidates": baseline_background,
                    "candidate_background_shaping_candidates": candidate_background,
                    "baseline_color_ledger": aggregate_color_ledger(
                        paths_by_group[baseline_key]
                    ),
                    "candidate_color_ledger": candidate_color_ledger,
                    "candidate_sched_ext_evidence": candidate_sched_ext,
                    "candidate_gpu_floor_evidence": candidate_gpu_floor,
                    "candidate_foreground_uclamp_evidence": candidate_foreground_uclamp,
                    "affinity_experiment_plan": build_affinity_experiment_plan(
                        baseline=baseline,
                        candidate=candidate,
                        comparison=comparison,
                        baseline_roles=baseline_roles,
                        candidate_roles=candidate_roles,
                        min_runs=args.min_runs,
                    ),
                    "background_shaping_experiment_plan": (
                        build_background_shaping_experiment_plan(
                            baseline=baseline,
                            candidate=candidate,
                            comparison=comparison,
                            baseline_candidates=baseline_background,
                            candidate_candidates=candidate_background,
                            min_runs=args.min_runs,
                        )
                    ),
                    "comparison": asdict(comparison),
                }
            )

    candidate_contexts = {_comparison_context_key(key) for key in candidate_keys}
    for baseline_keys in baseline_keys_by_context.values():
        for baseline_key in sorted(baseline_keys, key=_sortable_group_key):
            if baseline_key in matched_baseline_keys:
                continue
            if _comparison_context_key(baseline_key) in candidate_contexts:
                continue
            baseline = aggregate_run_summaries(groups[baseline_key])
            incomplete_groups.append(
                _incomplete_group(
                    baseline_policy=args.baseline_policy,
                    candidate_policy=baseline.ab_candidate_policy or "",
                    aggregate=baseline,
                    missing_side="candidate",
                )
            )

    return {
        "baseline_policy": args.baseline_policy,
        "candidate_policies": args.candidate_policy,
        "capture_mode": capture_mode,
        "min_runs": args.min_runs,
        "comparisons": comparisons,
        "incomplete_groups": incomplete_groups,
    }


# Explicit, tested candidate-policy -> daemon-actuator mapping (design section 8).
# The daemon keys the verdict ledger on these actuator strings. The three
# gpu-priority guarded lanes map deterministically; target-balance ladder/uclamp
# verdicts are only exported when the aggregate context explicitly carries a
# ``candidate_policy_actuators`` list, so a plain ``target-balance`` mode
# aggregate never fabricates a single-lane claim it did not isolate.
VERDICT_ACTUATOR_BY_CANDIDATE_POLICY: dict[str, str] = {
    "gpu-priority-bg-weight": "bg-weight",
    "gpu-priority-bg-uclamp": "bg-uclamp",
    "gpu-priority-affinity": "compact-affinity",
    # C16: one-control-per-run target-balance lane probes (design section 9).
    "target-balance-uclampmin": "uclamp-min",
    "target-balance-ladder5": "ladder-step-5",
    # V10 single-lane candidates (Slice C). Each isolates one actuator via
    # --trim-rungs so its BETTER claim maps to exactly one daemon actuator.
    # The G1-G3 / P1-P3 base rungs are persona-defaults (always on for
    # battery/ac-quiet), so a ``soft-pl1`` verdict still only accumulates
    # evidence. A ``gpu-cap`` verdict, however, NOW unlocks runtime behavior:
    # the daemon consumes it to enable the deep GPU cap rung G4CAP (the -45%
    # depth beyond the measured pacing plateau), mirroring how ``ladder-step-5``
    # unlocks S3CAP/S4CAP. ``v10-battery`` is deliberately absent: a whole-ladder
    # BETTER cannot claim a single lane, so it is skipped unless the aggregate
    # explicitly declares candidate_policy_actuators (same rule as a plain
    # ``target-balance`` mode aggregate).
    "v10-gpu-cap": "gpu-cap",
    "v10-soft-pl1": "soft-pl1",
}
GAME_POWER_VERDICTS_SCHEMA_VERSION = "game-power-verdicts-v1"


def _cpu_policies_from_topology_json(path: str | Path) -> list[object]:
    """Rebuild ``CpuPolicy`` objects from a collected ``cpu-topology.json``.

    Used only to feed the imported ``topology_fingerprint`` so the exported key
    is byte-identical to what the daemon computes at runtime (never re-hashed
    here).
    """

    from steamos_intel_handheld.game_power import (
        PCORE_CAPACITY_RATIO,
        CpuPolicy,
        CpuPolicyClass,
    )

    payload = json.loads(Path(path).read_text())
    cpus = payload.get("cpus") if isinstance(payload, dict) else None
    groups: dict[str, dict[str, object]] = {}
    for item in cpus or []:
        if not isinstance(item, dict):
            continue
        cpu = item.get("cpu")
        if not isinstance(cpu, int):
            continue
        name = _optional_str(item.get("policy")) or f"policy-cpu{cpu}"
        group = groups.setdefault(
            name,
            {"cpus": set(), "capacity": None, "max_freq": None, "cpuinfo_max_freq": None},
        )
        group["cpus"].add(cpu)  # type: ignore[union-attr]
        capacity = item.get("capacity")
        if isinstance(capacity, int):
            group["capacity"] = capacity
        max_freq = item.get("max_freq_khz")
        if isinstance(max_freq, int):
            group["max_freq"] = max_freq
        # C15: prefer the immutable ceiling so the exported fingerprint matches
        # the daemon's (which hashes cpuinfo_max_freq).
        cpuinfo_max_freq = item.get("cpuinfo_max_freq_khz")
        if isinstance(cpuinfo_max_freq, int):
            group["cpuinfo_max_freq"] = cpuinfo_max_freq

    capacities = [
        group["capacity"] for group in groups.values() if group["capacity"] is not None
    ]
    max_capacity = max(capacities) if capacities else None  # type: ignore[type-var]
    policies: list[object] = []
    for name, group in groups.items():
        capacity = group["capacity"]
        if max_capacity is None or capacity is None:
            policy_class = CpuPolicyClass.UNKNOWN
        elif capacity >= PCORE_CAPACITY_RATIO * max_capacity:
            # F1: same tolerance as the daemon's discover_cpu_policies so the
            # exported fingerprint's pcore/ecore label matches at runtime.
            policy_class = CpuPolicyClass.PCORE
        else:
            policy_class = CpuPolicyClass.ECORE
        policies.append(
            CpuPolicy(
                name=name,
                path=Path(name),
                affected_cpus=tuple(sorted(group["cpus"])),  # type: ignore[arg-type]
                capacity=capacity,  # type: ignore[arg-type]
                policy_class=policy_class,
                available_epp=(),
                current_epp=None,
                scaling_min_freq=None,
                scaling_max_freq=group["max_freq"],  # type: ignore[arg-type]
                cpuinfo_max_freq=group["cpuinfo_max_freq"],  # type: ignore[arg-type]
            )
        )
    return policies


def resolve_topology_fingerprint(cpu_topology_json: str | Path) -> str:
    from steamos_intel_handheld.game_power import topology_fingerprint

    return topology_fingerprint(_cpu_policies_from_topology_json(cpu_topology_json))


def _comparison_actuators(comparison: dict[str, object]) -> list[str]:
    declared = comparison.get("candidate_policy_actuators")
    if isinstance(declared, list):
        explicit = [item for item in declared if isinstance(item, str) and item]
        if explicit:
            return explicit
    inner = comparison.get("comparison")
    candidate_policy = None
    if isinstance(inner, dict):
        candidate_policy = _optional_str(inner.get("candidate_policy"))
    if candidate_policy is None:
        candidate = comparison.get("candidate")
        if isinstance(candidate, dict):
            candidate_policy = _optional_str(candidate.get("policy"))
    actuator = VERDICT_ACTUATOR_BY_CANDIDATE_POLICY.get(candidate_policy or "")
    return [actuator] if actuator else []


def export_verdicts(
    reports: list[dict[str, object]],
    *,
    topology_fingerprint: str,
    kernel: str,
    policy_version: str | None = None,
) -> dict[str, object]:
    """Emit daemon-compatible verdict-ledger entries from aggregate reports.

    Only ``BETTER`` comparisons are exported (which already implies controlled
    capture, >= 3 pairs, exact restore, and the pairwise gate). ``claim_scope``
    is copied verbatim from the comparison so no claim exceeds its evidence.
    """

    from steamos_intel_handheld.game_power import (
        GAME_POWER_POLICY_VERSION_V9,
        verdict_tdp_bucket,
    )

    resolved_policy_version = policy_version or GAME_POWER_POLICY_VERSION_V9
    entries: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for report in reports:
        comparisons = report.get("comparisons") if isinstance(report, dict) else None
        for comparison in comparisons or []:
            if not isinstance(comparison, dict):
                continue
            inner = comparison.get("comparison")
            if not isinstance(inner, dict):
                continue
            if inner.get("verdict") != PolicyVerdict.BETTER.value:
                continue
            candidate_policy = _optional_str(inner.get("candidate_policy"))
            claim_scope = inner.get("claim_scope")
            fps_target = None
            if isinstance(claim_scope, dict):
                fps_target = claim_scope.get("fps_target")
            actuators = _comparison_actuators(comparison)
            if not actuators:
                skipped.append(
                    {
                        "appid": comparison.get("appid"),
                        "tdp_w": comparison.get("tdp_w"),
                        "candidate_policy": candidate_policy,
                        "reason": "no-actuator-mapping-for-candidate-policy",
                    }
                )
                continue
            # C13: the daemon lookup matches exact bucket values (12/17/22/30),
            # so raw tdp_w must be bucketed here; an off-bucket measurement is
            # skipped explicitly instead of exported as a dead entry.
            raw_tdp = comparison.get("tdp_w")
            tdp_bucket = verdict_tdp_bucket(
                raw_tdp if isinstance(raw_tdp, (int, float)) else None
            )
            if tdp_bucket is None:
                skipped.append(
                    {
                        "appid": comparison.get("appid"),
                        "tdp_w": raw_tdp,
                        "candidate_policy": candidate_policy,
                        "reason": "tdp-outside-verdict-buckets",
                    }
                )
                continue
            # C14: the daemon requires a non-None fps_target; entries without
            # one are dead weight that inflate verdict_ledger_health entry_count.
            if fps_target is None:
                skipped.append(
                    {
                        "appid": comparison.get("appid"),
                        "tdp_w": raw_tdp,
                        "candidate_policy": candidate_policy,
                        "reason": "missing-fps-target-in-claim-scope",
                    }
                )
                continue
            for actuator in actuators:
                entries.append(
                    {
                        "appid": comparison.get("appid"),
                        "tdp_w": tdp_bucket,
                        "fps_target": fps_target,
                        "topology_fingerprint": topology_fingerprint,
                        "kernel": kernel,
                        "policy_version": resolved_policy_version,
                        "actuator": actuator,
                        "verdict": "BETTER",
                        "claim_scope": claim_scope,
                    }
                )
    entries.sort(
        key=lambda entry: (
            str(entry.get("appid")),
            str(entry.get("tdp_w")),
            str(entry.get("actuator")),
        )
    )
    return {
        "schema_version": GAME_POWER_VERDICTS_SCHEMA_VERSION,
        "policy_version": resolved_policy_version,
        "topology_fingerprint": topology_fingerprint,
        "kernel": kernel,
        "entries": entries,
        "skipped": skipped,
    }


def run_export_verdicts(args: argparse.Namespace) -> dict[str, object]:
    if args.topology_fingerprint:
        fingerprint = args.topology_fingerprint
    elif args.cpu_topology_json:
        fingerprint = resolve_topology_fingerprint(args.cpu_topology_json)
    else:
        raise SystemExit(
            "export-verdicts requires --topology-fingerprint or --cpu-topology-json"
        )

    reports: list[dict[str, object]] = []
    for aggregate_path in args.aggregate or []:
        reports.append(json.loads(Path(aggregate_path).read_text()))
    if args.root:
        if not args.candidate_policy:
            raise SystemExit("export-verdicts --root requires --candidate-policy")
        aggregate_namespace = argparse.Namespace(
            root=args.root,
            baseline_policy=args.baseline_policy,
            candidate_policy=args.candidate_policy,
            appid=args.appid,
            tdp_w=args.tdp_w,
            duration_s=None,
            warmup_s=None,
            poll_s=None,
            fps_target=args.fps_target,
            fps_target_source=None,
            capture_mode=args.capture_mode,
            min_runs=args.min_runs,
        )
        reports.append(_json_ready(run_aggregate(aggregate_namespace)))
    if not reports:
        raise SystemExit("export-verdicts requires --aggregate or --root")

    verdicts = export_verdicts(
        reports,
        topology_fingerprint=fingerprint,
        kernel=args.kernel,
        policy_version=args.policy_version,
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(_json_ready(verdicts), indent=2, sort_keys=True) + "\n"
        )
    return verdicts


def aggregate_color_ledger(summary_paths: list[Path]) -> dict[str, object]:
    """Aggregate per-run ``color-ledger.json`` artifacts across included runs.

    Reuses the shared coloring module's per-run ledger (emitted by ``summarize``)
    and rolls it up into per-color role stability, median cpu-time/wait, actuator
    recommendation, and blocking reason codes (design section 9).
    """

    roles: dict[str, dict[str, object]] = {}
    runs_with_ledger = 0
    for summary_path in summary_paths:
        ledger_path = Path(summary_path).with_name("color-ledger.json")
        if not ledger_path.is_file():
            continue
        try:
            payload = json.loads(ledger_path.read_text())
        except json.JSONDecodeError:
            continue
        runs_with_ledger += 1
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            role_key = _optional_str(entry.get("role_key"))
            color = _optional_str(entry.get("color"))
            if role_key is None or color is None:
                continue
            state = roles.setdefault(
                f"{color}:{role_key}",
                {
                    "color": color,
                    "role_key": role_key,
                    "observed_run_count": 0,
                    "cpu_time_ms_per_s": [],
                    "runqueue_wait_ms_per_s": [],
                    "actuator": _optional_str(entry.get("actuator")),
                    "actuator_states": set(),
                    "blocking_reason_codes": set(),
                },
            )
            state["observed_run_count"] = int(state["observed_run_count"]) + 1
            _append_float(state["cpu_time_ms_per_s"], entry.get("cpu_time_ms_per_s"))
            _append_float(
                state["runqueue_wait_ms_per_s"], entry.get("runqueue_wait_ms_per_s")
            )
            actuator = _optional_str(entry.get("actuator"))
            if actuator is not None:
                state["actuator"] = actuator
            actuator_state = _optional_str(entry.get("actuator_state"))
            if actuator_state is not None:
                state["actuator_states"].add(actuator_state)  # type: ignore[union-attr]
            for code in entry.get("blocking_reason_codes") or []:
                if isinstance(code, str):
                    state["blocking_reason_codes"].add(code)  # type: ignore[union-attr]

    color_counts: dict[str, int] = {}
    finalized: list[dict[str, object]] = []
    for state in roles.values():
        color = str(state["color"])
        color_counts[color] = color_counts.get(color, 0) + 1
        finalized.append(
            {
                "color": color,
                "role_key": state["role_key"],
                "observed_run_count": state["observed_run_count"],
                "role_stability_ratio": _ratio(
                    float(state["observed_run_count"]), float(runs_with_ledger)
                ),
                "cpu_time_ms_per_s_median": _median(state["cpu_time_ms_per_s"]),
                "runqueue_wait_ms_per_s_median": _median(
                    state["runqueue_wait_ms_per_s"]
                ),
                "actuator_recommendation": state["actuator"],
                "actuator_states": sorted(state["actuator_states"]),  # type: ignore[arg-type]
                "blocking_reason_codes": sorted(state["blocking_reason_codes"]),  # type: ignore[arg-type]
            }
        )
    finalized.sort(
        key=lambda item: (
            str(item["color"]),
            -int(item["observed_run_count"]),
            str(item["role_key"]),
        )
    )
    return {
        "run_count": len(summary_paths),
        "run_count_with_ledger": runs_with_ledger,
        "color_counts": dict(sorted(color_counts.items())),
        "roles": finalized,
    }


def aggregate_sched_ext_evidence(summary_paths: list[Path]) -> dict[str, object]:
    """Roll up per-run ``sched-ext-state.json`` (scx-lavd lane, design section 9)."""

    runs_total = len(summary_paths)
    runs_with_evidence = 0
    valid_count = 0
    root_ops: set[str] = set()
    for summary_path in summary_paths:
        evidence_path = Path(summary_path).with_name("sched-ext-state.json")
        if not evidence_path.is_file():
            continue
        try:
            payload = json.loads(evidence_path.read_text())
        except json.JSONDecodeError:
            continue
        runs_with_evidence += 1
        if payload.get("valid") is True:
            valid_count += 1
        during = payload.get("during")
        if isinstance(during, dict):
            ops = _optional_str(during.get("root_ops"))
            if ops is not None:
                root_ops.add(ops)
    return {
        "runs_total": runs_total,
        "runs_with_evidence": runs_with_evidence,
        "valid_evidence_count": valid_count,
        "evidence_complete": (
            runs_total > 0
            and runs_with_evidence == runs_total
            and valid_count == runs_total
        ),
        "root_ops": sorted(root_ops),
    }


def aggregate_gpu_floor_evidence(summary_paths: list[Path]) -> dict[str, object]:
    """Roll up per-run ``gpu-freq-restore.json`` (gpufloor lane, design section 9)."""

    runs_total = len(summary_paths)
    runs_with_evidence = 0
    valid_count = 0
    restored_count = 0
    floor_mhz: set[int] = set()
    scopes: set[str] = set()
    for summary_path in summary_paths:
        evidence_path = Path(summary_path).with_name("gpu-freq-restore.json")
        if not evidence_path.is_file():
            continue
        try:
            payload = json.loads(evidence_path.read_text())
        except json.JSONDecodeError:
            continue
        runs_with_evidence += 1
        if payload.get("valid") is True:
            valid_count += 1
        if payload.get("restored") is True:
            restored_count += 1
        value = payload.get("floor_mhz")
        if isinstance(value, int):
            floor_mhz.add(value)
        scope = _optional_str(payload.get("gpu_floor_scope"))
        if scope is not None:
            scopes.add(scope)
    return {
        "runs_total": runs_total,
        "runs_with_evidence": runs_with_evidence,
        "valid_evidence_count": valid_count,
        "restored_count": restored_count,
        "evidence_complete": (
            runs_total > 0
            and runs_with_evidence == runs_total
            and valid_count == runs_total
            and restored_count == runs_total
        ),
        "floor_mhz": sorted(floor_mhz),
        "gpu_floor_scope": sorted(scopes),
    }


def aggregate_foreground_uclamp_evidence(
    summary_paths: list[Path],
) -> dict[str, object]:
    """Roll up per-run ``foreground-uclamp-restore.json`` (uclampmin lane, C16)."""

    runs_total = len(summary_paths)
    runs_with_evidence = 0
    valid_count = 0
    restored_count = 0
    floor_values: set[str] = set()
    for summary_path in summary_paths:
        evidence_path = Path(summary_path).with_name("foreground-uclamp-restore.json")
        if not evidence_path.is_file():
            continue
        try:
            payload = json.loads(evidence_path.read_text())
        except json.JSONDecodeError:
            continue
        runs_with_evidence += 1
        if payload.get("valid") is True:
            valid_count += 1
        if payload.get("restored") is True:
            restored_count += 1
        value = _optional_str(payload.get("floor_value"))
        if value is not None:
            floor_values.add(value)
    return {
        "runs_total": runs_total,
        "runs_with_evidence": runs_with_evidence,
        "valid_evidence_count": valid_count,
        "restored_count": restored_count,
        "evidence_complete": (
            runs_total > 0
            and runs_with_evidence == runs_total
            and valid_count == runs_total
            and restored_count == runs_total
        ),
        "floor_value": sorted(floor_values),
    }


def _gate_new_lane_evidence(
    comparison: PolicyComparison,
    *,
    candidate_policy: str,
    sched_ext_evidence: dict[str, object],
    gpu_floor_evidence: dict[str, object],
    foreground_uclamp_evidence: dict[str, object] | None = None,
) -> PolicyComparison:
    """Reject a candidate that lacks complete new-lane evidence in every run.

    Keeps ``export-verdicts`` honest: a scx-lavd, target-balance-gpufloor, or
    target-balance-uclampmin aggregate cannot reach ``BETTER`` unless its guard
    evidence covered every candidate run (mirrors the foreground-affinity
    coverage gate).
    """

    if candidate_policy == "scx-lavd" and not sched_ext_evidence.get(
        "evidence_complete"
    ):
        return PolicyComparison(
            comparison.baseline_policy,
            comparison.candidate_policy,
            PolicyVerdict.REJECTED,
            "sched_ext state evidence did not pass for every candidate run",
        )
    if candidate_policy == "target-balance-gpufloor" and not gpu_floor_evidence.get(
        "evidence_complete"
    ):
        return PolicyComparison(
            comparison.baseline_policy,
            comparison.candidate_policy,
            PolicyVerdict.REJECTED,
            "gpu-freq restore evidence did not pass for every candidate run",
        )
    if candidate_policy == "target-balance-uclampmin" and not (
        foreground_uclamp_evidence or {}
    ).get("evidence_complete"):
        return PolicyComparison(
            comparison.baseline_policy,
            comparison.candidate_policy,
            PolicyVerdict.REJECTED,
            "foreground uclamp.min restore evidence did not pass for every "
            "candidate run",
        )
    return comparison


def _incomplete_group(
    *,
    baseline_policy: str,
    candidate_policy: str,
    aggregate: PolicyAggregate,
    missing_side: str,
) -> dict[str, object]:
    return {
        "baseline_policy": baseline_policy,
        "candidate_policy": candidate_policy,
        "ab_candidate_policy": aggregate.ab_candidate_policy,
        "ab_run_order": (aggregate.ab_run_orders or [None])[0],
        "missing_side": missing_side,
        "verdict": PolicyVerdict.INCONCLUSIVE.value,
        "reason": _incomplete_reason(f"missing matching {missing_side} group"),
    }


def build_affinity_experiment_plan(
    *,
    baseline: PolicyAggregate,
    candidate: PolicyAggregate,
    comparison: PolicyComparison,
    baseline_roles: list[dict[str, object]],
    candidate_roles: list[dict[str, object]],
    min_runs: int,
) -> dict[str, object]:
    reasons = [
        "hard per-TID affinity remains profiler-only",
        "plan output is advisory and does not write affinity state",
    ]
    ready = True

    if comparison.verdict == PolicyVerdict.BETTER:
        reasons.append("candidate policy comparison is better")
    else:
        ready = False
        reasons.append(f"candidate policy comparison is {comparison.verdict.value}")

    if baseline.sample_count < min_runs or candidate.sample_count < min_runs:
        ready = False
        reasons.append("controlled repeated min-runs gate is not met")

    if baseline.capture_mode != CaptureMode.CONTROLLED:
        ready = False
        reasons.append("baseline capture is not controlled")
    if candidate.capture_mode != CaptureMode.CONTROLLED:
        ready = False
        reasons.append("candidate capture is not controlled")

    if baseline.restored_count != baseline.sample_count:
        ready = False
        reasons.append("baseline restore verification is incomplete")
    if candidate.restored_count != candidate.sample_count:
        ready = False
        reasons.append("candidate restore verification is incomplete")
    if (
        baseline.restore_affinity_snapshot_count == baseline.sample_count
        and candidate.restore_affinity_snapshot_count == candidate.sample_count
    ):
        reasons.append("restore-affinity snapshots are available for every aggregated run")
    else:
        ready = False
        reasons.append("restore-affinity snapshots are missing for aggregated runs")

    if not candidate_roles:
        ready = False
        reasons.append("candidate run has no stable affinity role evidence")
    if baseline_roles:
        reasons.append("baseline affinity role evidence is available for comparison")

    role_candidates = [
        _guarded_affinity_role_candidate(role)
        for role in candidate_roles
        if _role_is_ready_for_guarded_affinity_experiment(role)
    ]
    if not role_candidates:
        ready = False
        reasons.append("no foreground latency-hot role passed guarded-affinity gates")

    return {
        "mode": "ready-for-guarded-experiment" if ready else "observe-only",
        "write_policy": "disabled",
        "strategy": "adaptive-compact-preferred-set",
        "candidates": role_candidates,
        "reasons": reasons,
    }


def _role_is_ready_for_guarded_affinity_experiment(role: dict[str, object]) -> bool:
    if role.get("cgroup_role") != "foreground-game":
        return False
    if role.get("suggested_action") != "prefer-latency-cpus":
        return False
    if _classification_rank(_optional_str(role.get("classification"))) < _classification_rank(
        "latency-hot"
    ):
        return False
    if (_optional_int(role.get("observed_run_count")) or 0) < AFFINITY_ROLE_MIN_OBSERVED_RUNS:
        return False
    if (_float(role.get("run_coverage")) or 0.0) < AFFINITY_ROLE_MIN_RUN_COVERAGE:
        return False
    preferred_cpus = role.get("preferred_cpu_overlap")
    if not isinstance(preferred_cpus, list) or not preferred_cpus:
        return False
    harm = _float(role.get("migration_harm_score_max_median")) or 0.0
    wait = _float(role.get("runqueue_wait_ms_delta_median")) or 0.0
    return (
        harm >= AFFINITY_ROLE_MIN_HARM_SCORE
        or wait >= AFFINITY_ROLE_MIN_RUNQUEUE_WAIT_MS
    )


def _guarded_affinity_role_candidate(role: dict[str, object]) -> dict[str, object]:
    return {
        "role_key": role.get("role_key"),
        "comm": role.get("comm"),
        "control_scope": "foreground-game-role",
        "candidate_control": "guarded-hard-compact-affinity",
        "guarded_variant": "foreground-role-compact",
        "preferred_cpus": role.get("preferred_cpu_overlap") or [],
        "fallback": "restore-original-affinity-and-cgroup-state",
        "observed_run_count": role.get("observed_run_count"),
        "run_coverage": role.get("run_coverage"),
        "thread_count_median": role.get("thread_count_median"),
        "classification": role.get("classification"),
        "runqueue_wait_ms_delta_median": role.get("runqueue_wait_ms_delta_median"),
        "runqueue_wait_per_slice_ms_max_median": role.get(
            "runqueue_wait_per_slice_ms_max_median"
        ),
        "migration_harm_score_max_median": role.get(
            "migration_harm_score_max_median"
        ),
    }


def build_background_shaping_experiment_plan(
    *,
    baseline: PolicyAggregate,
    candidate: PolicyAggregate,
    comparison: PolicyComparison,
    baseline_candidates: list[dict[str, object]],
    candidate_candidates: list[dict[str, object]],
    min_runs: int,
) -> dict[str, object]:
    reasons = [
        "background shaping remains advisory until a guarded writer is implemented",
        "plan output is advisory and does not write cgroup controller state",
    ]
    ready = True
    comparison_better = comparison.verdict == PolicyVerdict.BETTER
    controlled_repeats = (
        baseline.sample_count >= min_runs and candidate.sample_count >= min_runs
    )
    baseline_controlled = baseline.capture_mode == CaptureMode.CONTROLLED
    candidate_controlled = candidate.capture_mode == CaptureMode.CONTROLLED
    baseline_restored = baseline.restored_count == baseline.sample_count
    candidate_restored = candidate.restored_count == candidate.sample_count
    restore_coverage = (
        baseline.restore_affinity_snapshot_count == baseline.sample_count
        and candidate.restore_affinity_snapshot_count == candidate.sample_count
        and _aggregate_has_cgroup_cpu_controller_restore(baseline)
        and _aggregate_has_cgroup_cpu_controller_restore(candidate)
    )

    if comparison_better:
        reasons.append("candidate policy comparison is better")
    else:
        ready = False
        reasons.append(f"candidate policy comparison is {comparison.verdict.value}")

    if not controlled_repeats:
        ready = False
        reasons.append("controlled repeated min-runs gate is not met")
    if not baseline_controlled:
        ready = False
        reasons.append("baseline capture is not controlled")
    if not candidate_controlled:
        ready = False
        reasons.append("candidate capture is not controlled")
    if not baseline_restored:
        ready = False
        reasons.append("baseline restore verification is incomplete")
    if not candidate_restored:
        ready = False
        reasons.append("candidate restore verification is incomplete")

    if restore_coverage:
        reasons.append("cgroup CPU controller restore snapshots are available")
    else:
        ready = False
        reasons.append("cgroup CPU controller restore snapshots are missing")

    if baseline_candidates:
        reasons.append("baseline background/helper evidence is available for comparison")

    guarded_candidates = [
        _guarded_background_shaping_candidate(item, aggregate=candidate)
        for item in candidate_candidates
        if _background_candidate_is_ready_for_guarded_experiment(item)
    ]
    candidate_restore_coverage = any(
        _background_candidate_has_restore_coverage(item)
        for item in candidate_candidates
    )
    candidate_stability = any(
        (_optional_int(item.get("observed_run_count")) or 0)
        >= BACKGROUND_SHAPING_MIN_OBSERVED_RUNS
        and (_float(item.get("run_coverage")) or 0.0)
        >= BACKGROUND_SHAPING_MIN_RUN_COVERAGE
        and (_float(item.get("cpu_time_s_delta_median")) or 0.0)
        >= BACKGROUND_SHAPING_MIN_CPU_TIME_S
        and item.get("suggested_action")
        in {"future-cpu-weight-candidate", "future-uclamp-max-candidate"}
        for item in candidate_candidates
    )
    candidate_guarded = bool(guarded_candidates)
    if guarded_candidates:
        reasons.append(
            "background/helper cgroup candidate is stable across candidate runs"
        )
    else:
        ready = False
        if any(
            not _background_candidate_has_restore_coverage(item)
            for item in candidate_candidates
        ):
            reasons.append(
                "candidate background cgroups are missing from restore-affinity snapshots"
            )
        reasons.append("no background/helper cgroup passed guarded-shaping gates")
    blocking_reason_codes = []
    if not comparison_better:
        blocking_reason_codes.append("candidate_policy_not_better")
    if not controlled_repeats:
        blocking_reason_codes.append("controlled_repeats_missing")
    if not baseline_controlled:
        blocking_reason_codes.append("baseline_capture_not_controlled")
    if not candidate_controlled:
        blocking_reason_codes.append("candidate_capture_not_controlled")
    if not baseline_restored:
        blocking_reason_codes.append("baseline_restore_incomplete")
    if not candidate_restored:
        blocking_reason_codes.append("candidate_restore_incomplete")
    if not restore_coverage:
        blocking_reason_codes.append("cgroup_restore_snapshot_missing")
    if not candidate_restore_coverage:
        blocking_reason_codes.append("candidate_restore_coverage_missing")
    if not candidate_guarded:
        blocking_reason_codes.append("no_guarded_background_candidate")

    return {
        "mode": "ready-for-guarded-experiment" if ready else "observe-only",
        "write_policy": "disabled",
        "strategy": "background-helper-soft-cap",
        "candidates": guarded_candidates,
        "readiness": {
            "comparison_better": comparison_better,
            "controlled_repeats": controlled_repeats,
            "baseline_controlled": baseline_controlled,
            "candidate_controlled": candidate_controlled,
            "baseline_restored": baseline_restored,
            "candidate_restored": candidate_restored,
            "restore_coverage": restore_coverage,
            "candidate_restore_coverage": candidate_restore_coverage,
            "candidate_stability": candidate_stability,
            "candidate_guarded": candidate_guarded,
            "write_policy_disabled": True,
            "ready_for_guarded_experiment": ready,
            "blocking_reason_codes": blocking_reason_codes,
        },
        "reasons": reasons,
    }


def _aggregate_has_cgroup_cpu_controller_restore(aggregate: PolicyAggregate) -> bool:
    files = set(aggregate.restore_affinity_files or [])
    return bool(files.intersection(CGROUP_CPU_CONTROLLER_RESTORE_FILES))


def _background_candidate_is_ready_for_guarded_experiment(
    candidate: dict[str, object],
) -> bool:
    if (_optional_int(candidate.get("observed_run_count")) or 0) < (
        BACKGROUND_SHAPING_MIN_OBSERVED_RUNS
    ):
        return False
    if (_float(candidate.get("run_coverage")) or 0.0) < (
        BACKGROUND_SHAPING_MIN_RUN_COVERAGE
    ):
        return False
    if (_float(candidate.get("cpu_time_s_delta_median")) or 0.0) < (
        BACKGROUND_SHAPING_MIN_CPU_TIME_S
    ):
        return False
    if not _background_candidate_has_restore_coverage(candidate):
        return False
    return candidate.get("suggested_action") in {
        "future-cpu-weight-candidate",
        "future-uclamp-max-candidate",
    }


def _background_candidate_has_restore_coverage(candidate: dict[str, object]) -> bool:
    observed = _optional_int(candidate.get("observed_run_count")) or 0
    restore_observed = _optional_int(
        candidate.get("restore_snapshot_observed_run_count")
    ) or 0
    return observed > 0 and restore_observed == observed


def _guarded_background_shaping_candidate(
    candidate: dict[str, object],
    *,
    aggregate: PolicyAggregate,
) -> dict[str, object]:
    cgroup = _optional_str(candidate.get("cgroup")) or ""
    restore_files = _restore_files_for_cgroup(aggregate, cgroup)
    restore_values = _restore_values_for_cgroup(aggregate, cgroup)
    return {
        "candidate_key": candidate.get("candidate_key"),
        "cgroup": candidate.get("cgroup"),
        "classification": candidate.get("classification"),
        "control_scope": "background-helper-cgroup",
        "candidate_control": "cpu.weight-or-uclamp-max-soft-cap",
        "guarded_variant": "background-helper-soft-cap",
        "fallback": "restore-original-cgroup-cpu-controller-state",
        "observed_run_count": candidate.get("observed_run_count"),
        "run_coverage": candidate.get("run_coverage"),
        "restore_snapshot_observed_run_count": candidate.get(
            "restore_snapshot_observed_run_count"
        ),
        "restore_snapshot_run_coverage": candidate.get(
            "restore_snapshot_run_coverage"
        ),
        "cpu_time_s_delta_median": candidate.get("cpu_time_s_delta_median"),
        "restore_files": restore_files,
        "restore_values": restore_values,
        "dry_run_writes": _background_shaping_dry_run_writes(
            restore_files,
            restore_values,
        ),
        "acceptance_thresholds": {
            "avg_fps_regression_max_pct": -2.0,
            "one_percent_low_regression_max_pct": PACING_REGRESSION_REJECT_PCT,
            "p99_frametime_regression_max_pct": PACING_REGRESSION_REJECT_PCT,
            "target_power_saving_min_pct": TARGET_POWER_SAVING_MIN_PCT,
        },
    }


def _restore_files_for_cgroup(
    aggregate: PolicyAggregate,
    cgroup: str,
) -> list[str]:
    files_by_cgroup = aggregate.restore_affinity_cgroup_files or {}
    return sorted(str(item) for item in files_by_cgroup.get(cgroup, []))


def _restore_values_for_cgroup(
    aggregate: PolicyAggregate,
    cgroup: str,
) -> dict[str, list[str]]:
    values_by_cgroup = aggregate.restore_affinity_cgroup_file_values or {}
    file_values = values_by_cgroup.get(cgroup, {})
    if not isinstance(file_values, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for filename, values in file_values.items():
        if isinstance(values, list):
            normalized[str(filename)] = sorted(str(value) for value in values)
        else:
            normalized[str(filename)] = [str(values)]
    return dict(sorted(normalized.items()))


def _background_shaping_dry_run_writes(
    restore_files: list[str],
    restore_values: dict[str, list[str]],
) -> list[dict[str, object]]:
    writes: list[dict[str, object]] = []
    if "cpu.weight" in restore_files:
        writes.append(
            {
                "variant": "background-helper-cpu-weight-80",
                "control_file": "cpu.weight",
                "proposed_value": "80",
                "value_policy": "lower-only-min-current-or-80",
                "restore_values_observed": restore_values.get("cpu.weight", []),
                "write_mode": "one-control-per-ab-run",
            }
        )
    if "cpu.uclamp.max" in restore_files:
        writes.append(
            {
                "variant": "background-helper-uclamp-max-85",
                "control_file": "cpu.uclamp.max",
                "proposed_value": "85.00",
                "value_policy": "lower-only-max-85-percent",
                "restore_values_observed": restore_values.get("cpu.uclamp.max", []),
                "write_mode": "one-control-per-ab-run",
            }
        )
    return writes


def validate_runtime_telemetry(
    *,
    game_power_jsonl: str | Path,
    summary_json: str | Path | None = None,
    action_replay_json: str | Path | None = None,
    require_classification: bool = False,
    require_pressure: bool = False,
    require_cpu_cap_action: bool = False,
    require_frame_performance: bool = False,
    require_fps_target_satisfied: bool = False,
    require_target_balance_contract: bool = False,
    require_v10_contract: bool = False,
    expect_fps_target: float | None = None,
    expect_fps_target_source: str | None = None,
    expect_fps_target_confidence: str | None = None,
    expect_target_frame_ms: float | None = None,
) -> dict[str, object]:
    rows = _read_jsonl_rows(game_power_jsonl)
    summary = parse_game_power_jsonl(game_power_jsonl)
    classification_samples = 0
    pressure_samples = 0
    cpu_cap_action_reached = False
    frame_performance_samples = 0
    fps_target_satisfied_samples = 0
    target_rows = 0
    target_mismatches: list[str] = []
    # V9 target-balance additive telemetry (contract v2). Presence is counted
    # per run (>= 1 row), the same "at least one" idiom v1 uses for
    # classification/pressure, because the daemon emits color_ledger only at the
    # colorize cadence, not on every governor tick.
    phase_samples = 0
    ladder_step_samples = 0
    color_ledger_samples = 0
    verdict_ledger_health_samples = 0
    # V10 target-balance additive telemetry (contract v3). ``persona`` must be on
    # EVERY emitted row (it is only ever set by target-balance decisions, so it
    # proves the run is a v10 run); trim_rungs_active/frame_feed_status need >= 1
    # row; soft_pl1_w/gpu_freq_caps are presence-if-active (required only when the
    # corresponding P/G rungs engaged).
    persona_samples = 0
    persona_missing_rows = 0
    trim_rungs_active_samples = 0
    frame_feed_status_samples = 0
    g_rung_active = False
    p_rung_active = False
    gpu_freq_caps_present = False
    soft_pl1_w_present = False

    for index, row in enumerate(rows, start=1):
        if require_v10_contract:
            persona_value = _optional_str(row.get("persona"))
            if persona_value is not None:
                persona_samples += 1
            else:
                persona_missing_rows += 1
            trim = row.get("trim_rungs_active")
            if isinstance(trim, list):
                trim_rungs_active_samples += 1
                for rung in trim:
                    if isinstance(rung, str) and rung[:1] == "G":
                        g_rung_active = True
                    elif isinstance(rung, str) and rung[:1] == "P":
                        p_rung_active = True
            if "frame_feed_status" in row:
                frame_feed_status_samples += 1
            if isinstance(row.get("gpu_freq_caps"), dict):
                gpu_freq_caps_present = True
            if row.get("soft_pl1_w") is not None:
                soft_pl1_w_present = True
        if _optional_str(row.get("phase")) is not None:
            phase_samples += 1
        if isinstance(row.get("ladder_step"), int):
            ladder_step_samples += 1
        if isinstance(row.get("color_ledger"), dict):
            color_ledger_samples += 1
        if isinstance(row.get("verdict_ledger_health"), dict):
            verdict_ledger_health_samples += 1
        primary, _advisories, malformed = _parse_runtime_classification(
            row.get("classification")
        )
        if primary != "unknown" and not malformed:
            classification_samples += 1
        pressure_count = _foreground_pressure_counts(row.get("pressure"))
        if pressure_count.foreground_pressure_signals > 0:
            pressure_samples += 1
        if row.get("action") == "gpu-priority-cpu-cap":
            cpu_cap_action_reached = True
        if _row_has_frame_performance(row):
            frame_performance_samples += 1
        if primary == "fps-target-satisfied" and not malformed:
            fps_target_satisfied_samples += 1
        if _finite_positive_float(row.get("fps_target")) is not None:
            target_rows += 1
            _check_target_expectation(
                row,
                index,
                target_mismatches,
                expect_fps_target=expect_fps_target,
                expect_fps_target_source=expect_fps_target_source,
                expect_fps_target_confidence=expect_fps_target_confidence,
                expect_target_frame_ms=expect_target_frame_ms,
            )

    failures = []
    if require_classification and classification_samples == 0:
        failures.append("runtime classification samples are missing")
    if require_pressure and pressure_samples == 0:
        failures.append("foreground pressure samples are missing")
    if require_cpu_cap_action and not cpu_cap_action_reached:
        failures.append("gpu-priority-cpu-cap action was not reached")
    if require_frame_performance and frame_performance_samples == 0:
        failures.append("frame-performance telemetry rows are missing")
    if require_fps_target_satisfied and fps_target_satisfied_samples == 0:
        failures.append("fps-target-satisfied classification was not reached")
    # A v10 run is a target-balance run, so the v2 field presence checks also
    # apply; keeping them together means --require-v10-contract is a single flag.
    if require_target_balance_contract or require_v10_contract:
        if phase_samples == 0:
            failures.append("target-balance phase telemetry rows are missing")
        if ladder_step_samples == 0:
            failures.append("target-balance ladder_step telemetry rows are missing")
        if color_ledger_samples == 0:
            failures.append("target-balance color_ledger telemetry rows are missing")
        if verdict_ledger_health_samples == 0:
            failures.append(
                "target-balance verdict_ledger_health telemetry rows are missing"
            )
    if require_v10_contract:
        if persona_samples == 0:
            failures.append("v10 persona telemetry rows are missing")
        elif persona_missing_rows > 0:
            failures.append(
                f"v10 persona is missing on {persona_missing_rows} emitted row(s)"
            )
        if trim_rungs_active_samples == 0:
            failures.append("v10 trim_rungs_active telemetry rows are missing")
        if frame_feed_status_samples == 0:
            failures.append("v10 frame_feed_status telemetry rows are missing")
        if g_rung_active and not gpu_freq_caps_present:
            failures.append(
                "v10 gpu_freq_caps telemetry is missing while a G rung was active"
            )
        if p_rung_active and not soft_pl1_w_present:
            failures.append(
                "v10 soft_pl1_w telemetry is missing while a P rung was active"
            )
    if (
        expect_fps_target is not None
        or expect_fps_target_source is not None
        or expect_fps_target_confidence is not None
        or expect_target_frame_ms is not None
    ) and target_rows == 0:
        failures.append("FPS target metadata rows are missing")
    failures.extend(target_mismatches)

    if summary_json is not None:
        summary_payload = json.loads(Path(summary_json).read_text())
        if require_classification and not summary_payload.get("classification_primary"):
            failures.append("summary.json classification_primary is missing")
        if require_pressure and not summary_payload.get("runtime_telemetry_counts"):
            failures.append("summary.json runtime_telemetry_counts is missing")

    action_replay_status = None
    if action_replay_json is not None:
        replay = json.loads(Path(action_replay_json).read_text())
        action_replay_status = "pass"
        # C17: action/reason delta keys are the v1 contract; an artifact missing
        # them must fail closed, not pass as if the deltas were zero.
        required_deltas = (
            replay.get("action_delta_count"),
            replay.get("reason_delta_count"),
        )
        optional_deltas = (
            # Target-balance replay adds phase/ladder deltas and v10 replay adds
            # rung/boost deltas (default 0/absent for older artifacts, so v1/v2
            # artifacts stay valid).
            replay.get("phase_delta_count", 0),
            replay.get("ladder_delta_count", 0),
            replay.get("rung_delta_count", 0),
            replay.get("boost_delta_count", 0),
        )
        if any(delta != 0 for delta in required_deltas) or any(
            delta != 0 for delta in optional_deltas
        ):
            action_replay_status = "fail"
            failures.append("action replay equivalence failed")

    if failures:
        raise ValueError("; ".join(failures))

    if require_v10_contract:
        schema_version = "game-power-runtime-telemetry-contract-v3"
    elif require_target_balance_contract:
        schema_version = "game-power-runtime-telemetry-contract-v2"
    else:
        schema_version = "game-power-runtime-telemetry-contract-v1"
    result: dict[str, object] = {
        "schema_version": schema_version,
        "status": "pass",
        "game_power_jsonl": str(game_power_jsonl),
        "samples": summary.samples,
        "classification_samples": classification_samples,
        "pressure_samples": pressure_samples,
        "target_metadata_samples": target_rows,
        "cpu_cap_action_reached": cpu_cap_action_reached,
        "frame_performance_samples": frame_performance_samples,
        "fps_target_satisfied_samples": fps_target_satisfied_samples,
        "runtime_telemetry_counts": asdict(summary.runtime_telemetry_counts)
        if summary.runtime_telemetry_counts
        else None,
        "classification_unknown_ratio": summary.classification_unknown_ratio,
        "pressure_supported_ratio": summary.pressure_supported_ratio,
        "pressure_unsupported_ratio": summary.pressure_unsupported_ratio,
        "expect_fps_target": expect_fps_target,
        "expect_fps_target_source": expect_fps_target_source,
        "expect_fps_target_confidence": expect_fps_target_confidence,
        "expect_target_frame_ms": expect_target_frame_ms,
        "action_replay_status": action_replay_status,
    }
    if require_target_balance_contract or require_v10_contract:
        result["phase_samples"] = phase_samples
        result["ladder_step_samples"] = ladder_step_samples
        result["color_ledger_samples"] = color_ledger_samples
        result["verdict_ledger_health_samples"] = verdict_ledger_health_samples
    if require_v10_contract:
        result["persona_samples"] = persona_samples
        result["persona_missing_rows"] = persona_missing_rows
        result["trim_rungs_active_samples"] = trim_rungs_active_samples
        result["frame_feed_status_samples"] = frame_feed_status_samples
        result["gpu_freq_caps_present"] = gpu_freq_caps_present
        result["soft_pl1_w_present"] = soft_pl1_w_present
        result["g_rung_active"] = g_rung_active
        result["p_rung_active"] = p_rung_active
    return result


def replay_action_equivalence(output: str | Path | None = None) -> dict[str, object]:
    from steamos_intel_handheld.game_power import (
        FramePerformanceTelemetry,
        FrameTargetTelemetry,
        GamePowerAction,
        GamePowerConfig,
        GamePowerController,
        GamePowerMode,
        GamePowerPersona,
        GamePowerSample,
        RaplPowerWindow,
    )

    def sample(
        *,
        package_w: float,
        core_w: float,
        uncore_w: float,
        render_busy: float = 0.8,
    ) -> GamePowerSample:
        return GamePowerSample(
            appid="1091500",
            rapl=RaplPowerWindow(
                duration_s=2.0,
                package_w=package_w,
                core_w=core_w,
                uncore_w=uncore_w,
            ),
            pl1_w=20,
            fdinfo_busy={"render": render_busy},
        )

    scenarios = [
        {
            "name": "off",
            "config": GamePowerConfig(mode=GamePowerMode.OFF),
            "samples": [sample(package_w=19.0, core_w=7.0, uncore_w=9.0)],
            "expected_actions": [GamePowerAction.IDLE.value],
            "expected_reasons": ["mode is off"],
        },
        {
            "name": "observe",
            "config": GamePowerConfig(mode=GamePowerMode.OBSERVE),
            "samples": [sample(package_w=19.0, core_w=7.0, uncore_w=9.0)],
            "expected_actions": [GamePowerAction.OBSERVE_ONLY.value],
            "expected_reasons": ["mode is observe"],
        },
        {
            "name": "activation-hysteresis",
            "config": GamePowerConfig(
                mode=GamePowerMode.GPU_PRIORITY,
                activate_samples=2,
            ),
            "samples": [sample(package_w=19.0, core_w=7.0, uncore_w=9.0)],
            "expected_actions": [GamePowerAction.OBSERVE_ONLY.value],
            "expected_reasons": ["waiting for activation hysteresis"],
        },
        {
            "name": "gpu-priority-epp",
            "config": GamePowerConfig(
                mode=GamePowerMode.GPU_PRIORITY,
                activate_samples=1,
            ),
            "samples": [sample(package_w=19.0, core_w=7.0, uncore_w=9.0)],
            "expected_actions": [GamePowerAction.GPU_PRIORITY_EPP.value],
            "expected_reasons": ["package limited with GPU activity"],
        },
        {
            "name": "gpu-priority-cpu-cap",
            "config": GamePowerConfig(
                mode=GamePowerMode.GPU_PRIORITY,
                activate_samples=1,
                cpu_cap_enabled=True,
            ),
            "samples": [sample(package_w=19.0, core_w=8.5, uncore_w=9.0)],
            "expected_actions": [GamePowerAction.GPU_PRIORITY_CPU_CAP.value],
            "expected_reasons": ["package limited with high core pressure"],
        },
        {
            "name": "restore",
            "config": GamePowerConfig(
                mode=GamePowerMode.GPU_PRIORITY,
                activate_samples=1,
                restore_samples=1,
            ),
            "samples": [
                sample(package_w=19.0, core_w=7.0, uncore_w=9.0),
                sample(package_w=10.0, core_w=2.0, uncore_w=3.0),
            ],
            "expected_actions": [
                GamePowerAction.GPU_PRIORITY_EPP.value,
                GamePowerAction.OBSERVE_ONLY.value,
            ],
            "expected_reasons": [
                "package limited with GPU activity",
                "waiting for rolling restore evidence",
            ],
        },
    ]

    results = []
    action_delta_count = 0
    reason_delta_count = 0
    for scenario in scenarios:
        controller = GamePowerController(scenario["config"])
        decisions = [controller.evaluate(item) for item in scenario["samples"]]
        actions = [decision.action.value for decision in decisions]
        reasons = [decision.reason for decision in decisions]
        expected_actions = scenario["expected_actions"]
        expected_reasons = scenario["expected_reasons"]
        action_delta = _sequence_delta_count(actions, expected_actions)
        reason_delta = _sequence_delta_count(reasons, expected_reasons)
        action_delta_count += action_delta
        reason_delta_count += reason_delta
        results.append(
            {
                "name": scenario["name"],
                "actions": actions,
                "expected_actions": expected_actions,
                "action_delta_count": action_delta,
                "reasons": reasons,
                "expected_reasons": expected_reasons,
                "reason_delta_count": reason_delta,
            }
        )

    # --- V9 target-balance replay (phase + ladder determinism) ---
    # gpu-priority scenarios above stay byte-identical; these additive scenarios
    # drive the controller in TARGET_BALANCE mode through two independent fresh
    # controllers and require the committed phase and ladder-step sequences to
    # match between runs (zero delta = deterministic replay, design section 9).
    def tb_sample(
        *,
        appid: str | None,
        package_w: float,
        core_w: float,
        uncore_w: float,
        render_busy: float = 0.8,
    ) -> GamePowerSample:
        return GamePowerSample(
            appid=appid,
            rapl=RaplPowerWindow(
                duration_s=2.0,
                package_w=package_w,
                core_w=core_w,
                uncore_w=uncore_w,
            ),
            pl1_w=20,
            fdinfo_busy={"render": render_busy},
        )

    target_balance_scenarios = [
        {
            "name": "target-balance-no-game",
            "config": GamePowerConfig(
                mode=GamePowerMode.TARGET_BALANCE, activate_samples=1
            ),
            "samples": [tb_sample(appid=None, package_w=10.0, core_w=2.0, uncore_w=3.0)],
        },
        {
            "name": "target-balance-no-target",
            "config": GamePowerConfig(
                mode=GamePowerMode.TARGET_BALANCE, activate_samples=1
            ),
            "samples": [
                tb_sample(appid="1091500", package_w=19.0, core_w=7.0, uncore_w=9.0)
                for _ in range(4)
            ],
        },
    ]

    target_balance_results = []
    phase_delta_count = 0
    ladder_delta_count = 0
    for scenario in target_balance_scenarios:
        phases_a, ladders_a, actions_a = _replay_target_balance_run(
            GamePowerController(scenario["config"]), scenario["samples"]
        )
        phases_b, ladders_b, _actions_b = _replay_target_balance_run(
            GamePowerController(scenario["config"]), scenario["samples"]
        )
        phase_delta = _sequence_delta_count(phases_a, phases_b)
        ladder_delta = _sequence_delta_count(
            [str(step) for step in ladders_a],
            [str(step) for step in ladders_b],
        )
        phase_delta_count += phase_delta
        ladder_delta_count += ladder_delta
        target_balance_results.append(
            {
                "name": scenario["name"],
                "phases": phases_a,
                "ladder_steps": ladders_a,
                "actions": actions_a,
                "phase_delta_count": phase_delta,
                "ladder_delta_count": ladder_delta,
            }
        )

    # --- V10 replay (trim-rung + boost determinism) ---
    # Two independent fresh controllers driven through the same at-target sample
    # sequence must reproduce identical trim_rungs_active and boost_active
    # sequences (zero delta = deterministic replay, contract 1.7). The pure
    # controller never engages the fast boost lane (that overlay lives in the
    # observer's run_once), so boost_active is captured as the controller-emitted
    # value; its determinism is still asserted. gpu-priority replay above stays
    # byte-identical.
    def v10_sample() -> GamePowerSample:
        return GamePowerSample(
            appid="1091500",
            rapl=RaplPowerWindow(
                duration_s=2.0, package_w=22.0, core_w=8.8, uncore_w=7.4
            ),
            pl1_w=22,
            fdinfo_busy={"render": 0.75},
            frame_target=FrameTargetTelemetry(
                fps_target=60.0, source="manual", confidence="high"
            ),
            frame_performance=FramePerformanceTelemetry(
                avg_fps=63.0,
                p95_frame_ms=15.0,
                sample_count=20,
                window_s=2.0,
                source="mangoapp-feed",
                confidence="high",
            ),
            foreground_process_age_s=100.0,
            gpu_rp0_mhz=1950,
            gpu_rpe_mhz=800,
            package_median_w=22.0,
            frame_feed_status="live",
        )

    def v10_config(**over: object) -> GamePowerConfig:
        base: dict[str, object] = dict(
            mode=GamePowerMode.TARGET_BALANCE,
            activate_samples=1,
            phase_stable_samples=1,
            ladder_hold_samples=1,
            persona=GamePowerPersona.BATTERY,
        )
        base.update(over)
        return GamePowerConfig(**base)

    v10_scenarios = [
        {"name": "v10-battery-full-ladder", "config": v10_config(), "ticks": 8},
        {
            "name": "v10-gpu-cap-only",
            "config": v10_config(trim_rung_filter=("G",)),
            "ticks": 5,
        },
        {
            "name": "v10-soft-pl1-only",
            "config": v10_config(trim_rung_filter=("P",)),
            "ticks": 5,
        },
    ]
    v10_results = []
    rung_delta_count = 0
    boost_delta_count = 0
    for scenario in v10_scenarios:
        samples = [v10_sample() for _ in range(scenario["ticks"])]
        rungs_a, boosts_a = _replay_v10_run(
            GamePowerController(scenario["config"]), samples
        )
        rungs_b, boosts_b = _replay_v10_run(
            GamePowerController(scenario["config"]), samples
        )
        rung_delta = _sequence_delta_count(
            [",".join(step) for step in rungs_a],
            [",".join(step) for step in rungs_b],
        )
        boost_delta = _sequence_delta_count(
            [str(flag) for flag in boosts_a],
            [str(flag) for flag in boosts_b],
        )
        rung_delta_count += rung_delta
        boost_delta_count += boost_delta
        v10_results.append(
            {
                "name": scenario["name"],
                "trim_rung_sequences": rungs_a,
                "boost_sequence": boosts_a,
                "rung_delta_count": rung_delta,
                "boost_delta_count": boost_delta,
            }
        )

    verdict = {
        "schema_version": "game-power-action-equivalence-v1",
        "status": (
            "pass"
            if action_delta_count == 0
            and reason_delta_count == 0
            and phase_delta_count == 0
            and ladder_delta_count == 0
            and rung_delta_count == 0
            and boost_delta_count == 0
            else "fail"
        ),
        "action_delta_count": action_delta_count,
        "reason_delta_count": reason_delta_count,
        "phase_delta_count": phase_delta_count,
        "ladder_delta_count": ladder_delta_count,
        "rung_delta_count": rung_delta_count,
        "boost_delta_count": boost_delta_count,
        "scenarios": results,
        "target_balance_scenarios": target_balance_results,
        "v10_scenarios": v10_results,
    }
    if output is not None:
        Path(output).write_text(json.dumps(_json_ready(verdict), indent=2, sort_keys=True) + "\n")
    return verdict


def _replay_target_balance_run(
    controller: object,
    samples: list[object],
) -> tuple[list[str], list[int | None], list[str]]:
    """Evaluate a target-balance sample sequence, returning phase/ladder/action.

    Kept helper-local so the gpu-priority replay path stays untouched.
    """

    phases: list[str] = []
    ladders: list[int | None] = []
    actions: list[str] = []
    for item in samples:
        decision = controller.evaluate(item)  # type: ignore[attr-defined]
        phases.append(decision.phase.value if decision.phase is not None else "none")
        ladders.append(decision.ladder_step)
        actions.append(decision.action.value)
    return phases, ladders, actions


def _replay_v10_run(
    controller: object,
    samples: list[object],
) -> tuple[list[list[str]], list[bool]]:
    """Evaluate a v10 sample sequence, returning trim-rung and boost sequences.

    Kept helper-local so the gpu-priority and v2 replay paths stay untouched.
    """

    rungs: list[list[str]] = []
    boosts: list[bool] = []
    for item in samples:
        decision = controller.evaluate(item)  # type: ignore[attr-defined]
        rungs.append(list(decision.trim_rungs_active or []))
        boosts.append(bool(decision.boost_active))
    return rungs, boosts


def _read_jsonl_rows(path: str | Path) -> list[dict[str, object]]:
    # Q3: single hardened JSONL reader shared with the coloring module
    # (tolerates a truncated final line, rejects mid-file corruption).
    return list(iter_jsonl_rows(path))


def _check_target_expectation(
    row: dict[str, object],
    index: int,
    mismatches: list[str],
    *,
    expect_fps_target: float | None,
    expect_fps_target_source: str | None,
    expect_fps_target_confidence: str | None,
    expect_target_frame_ms: float | None,
) -> None:
    if expect_fps_target is not None and not _float_near(
        _finite_positive_float(row.get("fps_target")),
        expect_fps_target,
    ):
        mismatches.append(f"row {index} fps_target does not match expected target")
    if (
        expect_fps_target_source is not None
        and row.get("fps_target_source") != expect_fps_target_source
    ):
        mismatches.append(f"row {index} fps_target_source does not match expected source")
    if (
        expect_fps_target_confidence is not None
        and row.get("fps_target_confidence") != expect_fps_target_confidence
    ):
        mismatches.append(
            f"row {index} fps_target_confidence does not match expected confidence"
        )
    if expect_target_frame_ms is not None and not _float_near(
        _float(row.get("target_frame_ms")),
        expect_target_frame_ms,
    ):
        mismatches.append(
            f"row {index} target_frame_ms does not match expected frame time"
        )


def _float_near(left: float | None, right: float | None, *, epsilon: float = 0.001) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= epsilon


def _sequence_delta_count(left: list[str], right: list[str]) -> int:
    count = abs(len(left) - len(right))
    count += sum(
        1
        for left_item, right_item in zip(left, right, strict=False)
        if left_item != right_item
    )
    return count


def resolve_foreground_affinity_candidate(plan_json: str | Path) -> dict[str, object]:
    payload = json.loads(Path(plan_json).read_text())
    plans: list[dict[str, object]] = []
    if isinstance(payload, dict) and isinstance(payload.get("comparisons"), list):
        for comparison in payload["comparisons"]:
            if not isinstance(comparison, dict):
                continue
            plan = comparison.get("affinity_experiment_plan")
            if isinstance(plan, dict):
                plans.append(plan)
    elif isinstance(payload, dict):
        plans.append(payload)

    for plan in plans:
        if plan.get("mode") != "ready-for-guarded-experiment":
            continue
        for item in plan.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            if item.get("guarded_variant") != "foreground-role-compact":
                continue
            role_key = _optional_str(item.get("role_key"))
            if not role_key or not role_key.startswith("foreground-game:"):
                continue
            return {
                "role_key": role_key,
                "preferred_cpus": _plan_compact_affinity_mask(item),
                "guarded_variant": "foreground-role-compact",
            }
    raise ValueError("no ready foreground affinity candidate found")


def apply_foreground_affinity_writes(
    restore_affinity_json: str | Path,
    output: str | Path,
    *,
    role_key: str,
    preferred_cpus: str,
    variant: str,
    command_runner: Any | None = None,
    proc_root: str | Path | None = Path("/proc"),
) -> dict[str, object]:
    if variant not in FOREGROUND_AFFINITY_WRITE_VARIANTS:
        raise ValueError(f"unsupported foreground affinity variant: {variant}")
    if not role_key.startswith("foreground-game:"):
        raise ValueError("foreground affinity writes only support foreground-game roles")
    cpu_list = _format_cpu_list(_parse_cpu_list(preferred_cpus))
    payload = json.loads(Path(restore_affinity_json).read_text())
    writes: list[dict[str, object]] = []
    matched = 0
    written = 0
    failed = 0
    partial_failure = False

    for item in payload.get("threads") or []:
        if not isinstance(item, dict):
            continue
        current_role_key = _thread_role_key_from_restore_item(item)
        if current_role_key != role_key:
            continue
        matched += 1
        write = _foreground_affinity_write_base(item, role_key=role_key, proposed_cpus=cpu_list)
        stale_reason = _foreground_task_stale_reason(item, proc_root=proc_root)
        if stale_reason is not None:
            failed += 1
            write["status"] = stale_reason
            writes.append(write)
            continue
        tid = _optional_int(item.get("tid"))
        if tid is None:
            failed += 1
            write["status"] = "missing-tid"
            writes.append(write)
            continue
        result = _run_completed_command(
            ["taskset", "-pc", cpu_list, str(tid)],
            command_runner=command_runner,
        )
        write.update(
            {
                "returncode": result["returncode"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
        if result["status"] != "ok":
            failed += 1
            partial_failure = partial_failure or written > 0
            write["status"] = result["status"]
            writes.append(write)
            continue
        written += 1
        write["status"] = "written"
        writes.append(write)

    report = {
        "mode": "foreground-affinity-writes",
        "write_policy": "guarded-foreground-affinity",
        "role_key": role_key,
        "variant": variant,
        "preferred_cpus": cpu_list,
        "matched_thread_count": matched,
        "written_count": written,
        "failed_count": failed,
        "partial_failure": partial_failure,
        "valid": matched > 0 and written > 0 and failed == 0,
        "writes": writes,
    }
    Path(output).write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n")
    return report


def restore_foreground_affinity_writes(
    writes_json: str | Path,
    output: str | Path,
    *,
    command_runner: Any | None = None,
    proc_root: str | Path | None = Path("/proc"),
) -> dict[str, object]:
    payload = json.loads(Path(writes_json).read_text())
    restores: list[dict[str, object]] = []
    restored = True
    for item in payload.get("writes") or []:
        if not isinstance(item, dict) or item.get("status") != "written":
            continue
        tid = _optional_int(item.get("tid"))
        original = _optional_str(item.get("original_cpus_allowed_list"))
        restore_item = {
            "tid": tid,
            "pid": item.get("pid"),
            "comm": item.get("comm"),
            "role_key": item.get("role_key"),
            "restored_cpus": original,
        }
        if tid is None:
            restored = False
            restore_item["status"] = "missing-tid"
            restores.append(restore_item)
            continue
        if not original:
            restored = False
            restore_item["status"] = "restore-missing-original-mask"
            restores.append(restore_item)
            continue
        stale_reason = _foreground_task_stale_reason(item, proc_root=proc_root)
        if stale_reason is not None:
            restored = False
            restore_item["status"] = stale_reason
            restores.append(restore_item)
            continue
        result = _run_completed_command(
            ["taskset", "-pc", original, str(tid)],
            command_runner=command_runner,
        )
        restore_item.update(
            {
                "returncode": result["returncode"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
        if result["status"] != "ok":
            restored = False
            restore_item["status"] = (
                "restore-taskset-missing"
                if result["status"] == "taskset-missing"
                else "restore-failed"
            )
            restores.append(restore_item)
            continue
        current_mask = _taskset_stdout_affinity(_optional_str(result["stdout"]) or "")
        if current_mask is not None and _parse_cpu_list(current_mask) != _parse_cpu_list(original):
            restored = False
            restore_item["status"] = "restore-mismatch"
            restore_item["current_cpus"] = current_mask
            restores.append(restore_item)
            continue
        restore_item["status"] = "restored"
        restore_item["current_cpus"] = current_mask or original
        restores.append(restore_item)

    report = {
        "mode": "foreground-affinity-restore",
        "write_policy": "restore-foreground-affinity",
        "restored": restored,
        "restores": restores,
    }
    Path(output).write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n")
    return report


def summarize_foreground_affinity_artifacts(
    writes_json: str | Path | None,
    restore_json: str | Path | None,
) -> dict[str, object] | None:
    if not writes_json:
        return None
    writes = json.loads(Path(writes_json).read_text())
    restore = json.loads(Path(restore_json).read_text()) if restore_json else None
    restore_restored = restore.get("restored") if isinstance(restore, dict) else None
    written = _optional_int(writes.get("written_count")) or 0
    failed = _optional_int(writes.get("failed_count")) or 0
    matched = _optional_int(writes.get("matched_thread_count")) or 0
    return {
        "role_key": _optional_str(writes.get("role_key")),
        "preferred_cpus": _optional_str(writes.get("preferred_cpus")),
        "matched_thread_count": matched,
        "written_count": written,
        "failed_count": failed,
        "restore_restored": restore_restored,
        "valid_evidence": written > 0 and failed == 0 and restore_restored is True,
    }


def _parse_cpu_list(value: str) -> list[int]:
    cpus: set[int] = set()
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            bounds = item.split("-", 1)
            if len(bounds) != 2 or not bounds[0].isdigit() or not bounds[1].isdigit():
                raise ValueError(f"invalid CPU list: {value!r}")
            start = int(bounds[0])
            end = int(bounds[1])
            if end < start:
                raise ValueError(f"invalid CPU range: {item!r}")
            cpus.update(range(start, end + 1))
        else:
            if not item.isdigit():
                raise ValueError(f"invalid CPU list: {value!r}")
            cpus.add(int(item))
    if not cpus:
        raise ValueError("CPU list must not be empty")
    return sorted(cpus)


def _format_cpu_list(cpus: list[int]) -> str:
    if not cpus:
        raise ValueError("CPU list must not be empty")
    return ",".join(str(cpu) for cpu in sorted(set(cpus)))


def _plan_compact_affinity_mask(candidate: dict[str, object]) -> str:
    raw_cpus = candidate.get("preferred_cpus")
    if raw_cpus is None:
        raw_cpus = candidate.get("preferred_cpu_overlap")
    if isinstance(raw_cpus, str):
        cpus = _parse_cpu_list(raw_cpus)
    elif isinstance(raw_cpus, list):
        cpus = sorted(
            {
                cpu
                for item in raw_cpus
                if (cpu := _optional_int(item)) is not None and cpu >= 0
            }
        )
        if not cpus:
            raise ValueError("foreground affinity candidate has no preferred CPUs")
    else:
        raise ValueError("foreground affinity candidate has no preferred CPUs")
    thread_count = (
        _float(candidate.get("thread_count_median"))
        or _float(candidate.get("thread_count"))
        or 1.0
    )
    if thread_count > 1.0 and len(cpus) < 2:
        raise ValueError("foreground affinity mask is too narrow for a multi-thread role")
    return _format_cpu_list(cpus)


def _thread_role_key_from_restore_item(item: dict[str, object]) -> str | None:
    cgroup_role = _thread_cgroup_role(item.get("cgroup"))
    comm = item.get("comm")
    if cgroup_role != "foreground-game":
        return None
    return f"{cgroup_role}:{_normalize_role_part(comm)}"


def _foreground_affinity_write_base(
    item: dict[str, object],
    *,
    role_key: str,
    proposed_cpus: str,
) -> dict[str, object]:
    return {
        "tid": item.get("tid"),
        "pid": item.get("pid"),
        "comm": item.get("comm"),
        "role_key": role_key,
        "original_cpus_allowed_list": item.get("cpus_allowed_list"),
        "proposed_cpus": proposed_cpus,
    }


def _foreground_task_stale_reason(
    item: dict[str, object],
    *,
    proc_root: str | Path | None,
) -> str | None:
    if proc_root is None:
        return None
    pid = _optional_int(item.get("pid"))
    tid = _optional_int(item.get("tid"))
    if pid is None or tid is None:
        return "missing-task-identity"
    task_dir = Path(proc_root) / str(pid) / "task" / str(tid)
    status = _parse_proc_status(_read_optional_text(task_dir / "status") or "")
    current_comm = status.get("Name")
    expected_comm = _optional_str(item.get("comm"))
    if expected_comm and current_comm and current_comm != expected_comm:
        return "stale-thread"
    current_cgroup = _normalize_proc_cgroup(
        _read_optional_text(Path(proc_root) / str(pid) / "cgroup") or ""
    )
    expected_cgroup = _optional_str(item.get("cgroup"))
    if expected_cgroup and current_cgroup and current_cgroup != expected_cgroup:
        return "stale-thread"
    expected_start = _optional_int(item.get("start_time_ticks"))
    if expected_start is not None:
        current_start = _parse_proc_stat_start_time(_read_optional_text(task_dir / "stat") or "")
        if current_start != expected_start:
            return "stale-thread"
    if not task_dir.exists():
        return "stale-thread"
    return None


def _run_completed_command(
    command: list[str],
    *,
    command_runner: Any | None,
) -> dict[str, object]:
    try:
        if command_runner is None:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            result = command_runner(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        return {
            "status": "taskset-missing",
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }
    if isinstance(result, str):
        return {"status": "ok", "returncode": 0, "stdout": result, "stderr": ""}
    returncode = int(getattr(result, "returncode", 1))
    return {
        "status": "ok" if returncode == 0 else "write-failed",
        "returncode": returncode,
        "stdout": getattr(result, "stdout", "") or "",
        "stderr": getattr(result, "stderr", "") or "",
    }


def _taskset_stdout_affinity(stdout: str) -> str | None:
    new_matches = re.findall(r"new affinity list:\s*([0-9,\\-]+)", stdout)
    if new_matches:
        return new_matches[-1]
    matches = re.findall(r"affinity list:\s*([0-9,\\-]+)", stdout)
    return matches[-1] if matches else None


def _parse_proc_status(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key] = value.strip()
    return parsed


def _normalize_proc_cgroup(text: str) -> str:
    return text.strip().replace("\n", ";")


def _parse_proc_stat_start_time(text: str) -> int | None:
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2 :].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _read_optional_text(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value or None


def _load_run_summary(path: str | Path) -> RunSummary:
    payload = json.loads(Path(path).read_text())
    if "capture_mode" in payload:
        payload["capture_mode"] = CaptureMode(payload["capture_mode"])
    return RunSummary(**payload)


def aggregate_affinity_roles(summary_paths: list[Path]) -> list[dict[str, object]]:
    if not summary_paths:
        return []
    roles: dict[str, dict[str, object]] = {}
    for summary_path in summary_paths:
        advice_path = summary_path.with_name("affinity-advice.json")
        if not advice_path.is_file():
            continue
        try:
            payload = json.loads(advice_path.read_text())
        except json.JSONDecodeError:
            continue
        for item in payload.get("role_candidates") or []:
            if not isinstance(item, dict):
                continue
            role_key = _optional_str(item.get("role_key"))
            if role_key is None:
                continue
            role = roles.setdefault(
                role_key,
                {
                    "role_key": role_key,
                    "comm": item.get("comm"),
                    "cgroup_role": item.get("cgroup_role"),
                    "classification": "throughput-worker",
                    "suggested_action": "observe",
                    "observed_run_count": 0,
                    "thread_count": [],
                    "cpu_time_s_delta": [],
                    "migration_delta": [],
                    "runqueue_wait_ms_delta": [],
                    "runqueue_wait_per_slice_ms_max": [],
                    "migration_harm_score_max": [],
                    "cpus_seen": set(),
                    "preferred_cpu_overlap": set(),
                },
            )
            role["observed_run_count"] = int(role["observed_run_count"]) + 1
            _append_float(role["thread_count"], item.get("thread_count"))
            _append_float(role["cpu_time_s_delta"], item.get("cpu_time_s_delta"))
            _append_float(role["migration_delta"], item.get("migration_delta"))
            _append_float(
                role["runqueue_wait_ms_delta"],
                item.get("runqueue_wait_ms_delta"),
            )
            _append_float(
                role["runqueue_wait_per_slice_ms_max"],
                item.get("runqueue_wait_per_slice_ms_max"),
            )
            _append_float(
                role["migration_harm_score_max"],
                item.get("migration_harm_score_max"),
            )
            _extend_int_set(role["cpus_seen"], item.get("cpus_seen"))
            _extend_int_set(role["preferred_cpu_overlap"], item.get("preferred_cpu_overlap"))
            classification = _optional_str(item.get("classification"))
            if _classification_rank(classification) > _classification_rank(
                _optional_str(role.get("classification"))
            ):
                role["classification"] = classification
            if item.get("suggested_action") == "prefer-latency-cpus":
                role["suggested_action"] = "prefer-latency-cpus"

    candidates = [
        _finalize_aggregate_affinity_role(role, total_runs=len(summary_paths))
        for role in roles.values()
    ]
    candidates.sort(
        key=lambda item: (
            -int(item["observed_run_count"]),
            -float(item["migration_harm_score_max_median"] or 0.0),
            -float(item["runqueue_wait_ms_delta_median"] or 0.0),
            str(item["role_key"]),
        )
    )
    return candidates


def aggregate_background_shaping_candidates(
    summary_paths: list[Path],
) -> list[dict[str, object]]:
    if not summary_paths:
        return []
    candidates: dict[str, dict[str, object]] = {}
    for summary_path in summary_paths:
        run = _load_run_summary(summary_path)
        advice_path = summary_path.with_name("background-shaping.json")
        if not advice_path.is_file():
            continue
        try:
            payload = json.loads(advice_path.read_text())
        except json.JSONDecodeError:
            continue
        for item in payload.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            cgroup = _optional_str(item.get("cgroup"))
            if cgroup is None:
                continue
            classification = _optional_str(item.get("classification")) or (
                "other-background"
            )
            key = f"{classification}:{cgroup}"
            state = candidates.setdefault(
                key,
                {
                    "candidate_key": key,
                    "cgroup": cgroup,
                    "classification": classification,
                    "suggested_action": "observe",
                    "observed_run_count": 0,
                    "restore_snapshot_observed_run_count": 0,
                    "cpu_time_s_delta": [],
                    "process_count": [],
                    "commands": set(),
                },
            )
            state["observed_run_count"] = int(state["observed_run_count"]) + 1
            if _run_has_cgroup_cpu_controller_restore(run, cgroup):
                state["restore_snapshot_observed_run_count"] = (
                    int(state["restore_snapshot_observed_run_count"]) + 1
                )
            _append_float(state["cpu_time_s_delta"], item.get("cpu_time_s_delta"))
            _append_float(state["process_count"], item.get("process_count"))
            commands = state["commands"]
            if isinstance(commands, set):
                for command in item.get("commands") or []:
                    text = _optional_str(command)
                    if text:
                        commands.add(text)
            if _background_action_rank(
                _optional_str(item.get("suggested_action"))
            ) > _background_action_rank(_optional_str(state.get("suggested_action"))):
                state["suggested_action"] = item.get("suggested_action")

    results = [
        _finalize_aggregate_background_shaping_candidate(
            candidate,
            total_runs=len(summary_paths),
        )
        for candidate in candidates.values()
    ]
    results.sort(
        key=lambda item: (
            -int(item["observed_run_count"]),
            -float(item["cpu_time_s_delta_median"] or 0.0),
            str(item["candidate_key"]),
        )
    )
    return results


def _background_action_rank(action: str | None) -> int:
    return {
        "observe": 0,
        "future-uclamp-max-candidate": 1,
        "future-cpu-weight-candidate": 2,
    }.get(action or "", -1)


def _finalize_aggregate_background_shaping_candidate(
    candidate: dict[str, object],
    *,
    total_runs: int,
) -> dict[str, object]:
    observed = int(candidate["observed_run_count"])
    restore_observed = int(candidate["restore_snapshot_observed_run_count"])
    commands = candidate.get("commands")
    return {
        "candidate_key": candidate.get("candidate_key"),
        "cgroup": candidate.get("cgroup"),
        "classification": candidate.get("classification"),
        "suggested_action": candidate.get("suggested_action"),
        "observed_run_count": observed,
        "run_coverage": round(observed / total_runs, 3) if total_runs > 0 else 0.0,
        "restore_snapshot_observed_run_count": restore_observed,
        "restore_snapshot_run_coverage": (
            round(restore_observed / observed, 3) if observed > 0 else 0.0
        ),
        "cpu_time_s_delta_median": _median(candidate["cpu_time_s_delta"]),
        "process_count_median": _median(candidate["process_count"]),
        "commands": sorted(commands) if isinstance(commands, set) else [],
    }


def _run_has_cgroup_cpu_controller_restore(run: RunSummary, cgroup: str) -> bool:
    files_by_cgroup = run.restore_affinity_cgroup_files or {}
    files = files_by_cgroup.get(cgroup) or []
    return bool(set(files).intersection(CGROUP_CPU_CONTROLLER_RESTORE_FILES))


def _finalize_aggregate_affinity_role(
    role: dict[str, object],
    *,
    total_runs: int,
) -> dict[str, object]:
    cpus_seen = role.get("cpus_seen")
    preferred_overlap = role.get("preferred_cpu_overlap")
    observed = int(role["observed_run_count"])
    return {
        "role_key": role.get("role_key"),
        "comm": role.get("comm"),
        "cgroup_role": role.get("cgroup_role"),
        "classification": role.get("classification"),
        "suggested_action": role.get("suggested_action"),
        "observed_run_count": observed,
        "run_coverage": round(observed / total_runs, 3) if total_runs > 0 else 0.0,
        "thread_count_median": _median(role["thread_count"]),
        "cpu_time_s_delta_median": _median(role["cpu_time_s_delta"]),
        "migration_delta_median": _median(role["migration_delta"]),
        "runqueue_wait_ms_delta_median": _median(role["runqueue_wait_ms_delta"]),
        "runqueue_wait_per_slice_ms_max_median": _median(
            role["runqueue_wait_per_slice_ms_max"]
        ),
        "migration_harm_score_max_median": _median(
            role["migration_harm_score_max"]
        ),
        "cpus_seen": sorted(cpus_seen) if isinstance(cpus_seen, set) else [],
        "preferred_cpu_overlap": (
            sorted(preferred_overlap) if isinstance(preferred_overlap, set) else []
        ),
    }


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
        run.ab_order_strategy,
        run.ab_candidate_policy,
        run.ab_run_order,
    )


def _comparison_context_key(group_key: tuple[object, ...]) -> tuple[object, ...]:
    return (group_key[0], group_key[1], *group_key[3:9], *group_key[14:17])


def _experiment_settings(
    run: RunSummary,
) -> tuple[
    float | None,
    float | None,
    float | None,
    float | None,
    str | None,
    str | None,
]:
    return (
        run.duration_s,
        run.warmup_s,
        run.poll_s,
        run.fps_target,
        run.fps_target_source,
        run.fps_target_confidence,
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


def _runtime_telemetry_counts(value: object) -> RuntimeTelemetryCounts:
    if isinstance(value, RuntimeTelemetryCounts):
        return value
    if not isinstance(value, dict):
        return RuntimeTelemetryCounts()
    return RuntimeTelemetryCounts(
        foreground_runtime_rows=_optional_int(value.get("foreground_runtime_rows")) or 0,
        unknown_foreground_rows=_optional_int(value.get("unknown_foreground_rows")) or 0,
        foreground_pressure_signals=_optional_int(value.get("foreground_pressure_signals"))
        or 0,
        supported_foreground_pressure_signals=_optional_int(
            value.get("supported_foreground_pressure_signals")
        )
        or 0,
        unsupported_foreground_pressure_signals=_optional_int(
            value.get("unsupported_foreground_pressure_signals")
        )
        or 0,
        frame_performance_rows=_optional_int(value.get("frame_performance_rows")) or 0,
        fps_target_satisfied_rows=_optional_int(value.get("fps_target_satisfied_rows"))
        or 0,
    )


def _add_runtime_counts(
    left: RuntimeTelemetryCounts,
    right: RuntimeTelemetryCounts,
) -> RuntimeTelemetryCounts:
    return RuntimeTelemetryCounts(
        foreground_runtime_rows=(
            left.foreground_runtime_rows + right.foreground_runtime_rows
        ),
        unknown_foreground_rows=(
            left.unknown_foreground_rows + right.unknown_foreground_rows
        ),
        foreground_pressure_signals=(
            left.foreground_pressure_signals + right.foreground_pressure_signals
        ),
        supported_foreground_pressure_signals=(
            left.supported_foreground_pressure_signals
            + right.supported_foreground_pressure_signals
        ),
        unsupported_foreground_pressure_signals=(
            left.unsupported_foreground_pressure_signals
            + right.unsupported_foreground_pressure_signals
        ),
        frame_performance_rows=(
            left.frame_performance_rows + right.frame_performance_rows
        ),
        fps_target_satisfied_rows=(
            left.fps_target_satisfied_rows + right.fps_target_satisfied_rows
        ),
    )


def _sum_runtime_counts(
    values: list[RuntimeTelemetryCounts | dict[str, int] | None],
) -> RuntimeTelemetryCounts:
    total = RuntimeTelemetryCounts()
    for value in values:
        if value is None:
            continue
        total = _add_runtime_counts(total, _runtime_telemetry_counts(value))
    return total


def _parse_runtime_classification(
    value: object,
) -> tuple[str, list[str], bool]:
    if value is None:
        return ("unknown", [], False)
    if not isinstance(value, dict):
        return ("unknown", [], True)
    primary = _optional_str(value.get("primary"))
    if primary is None:
        return ("unknown", [], True)
    advisories = []
    raw_advisories = value.get("advisories")
    if isinstance(raw_advisories, list):
        advisories = [
            item
            for item in (_optional_str(raw) for raw in raw_advisories)
            if item is not None
        ]
    elif raw_advisories is not None:
        return ("unknown", [], True)
    return (primary, sorted(advisories), False)


def _row_has_frame_performance(row: dict[str, object]) -> bool:
    return (
        _finite_positive_float(row.get("frame_avg_fps")) is not None
        and _finite_positive_float(row.get("frame_p95_ms")) is not None
        and _optional_int(row.get("frame_performance_sample_count")) is not None
        and _optional_str(row.get("frame_performance_confidence")) == "high"
        and _optional_str(row.get("frame_performance_source")) == "mangohud-csv"
    )


def _foreground_pressure_counts(value: object) -> RuntimeTelemetryCounts:
    if not isinstance(value, dict):
        return RuntimeTelemetryCounts()
    total = 0
    supported = 0
    unsupported = 0
    for signals in value.values():
        if not isinstance(signals, list):
            continue
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            if _optional_str(signal.get("scope")) != "foreground_cgroup":
                continue
            total += 1
            if signal.get("supported") is True:
                supported += 1
            else:
                unsupported += 1
    return RuntimeTelemetryCounts(
        foreground_pressure_signals=total,
        supported_foreground_pressure_signals=supported,
        unsupported_foreground_pressure_signals=unsupported,
    )


def _finite_positive_float(value: object) -> float | None:
    parsed = _float(value)
    if parsed is None or not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _sum_counter_dicts(values: list[dict[str, int] | None]) -> dict[str, int]:
    total: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            parsed = _optional_int(count)
            if parsed is None:
                continue
            total[str(key)] = total.get(str(key), 0) + parsed
    return dict(sorted(total.items()))


def _single_counter_key(value: dict[str, int] | None) -> str | None:
    if not isinstance(value, dict) or len(value) != 1:
        return None
    return next(iter(value))


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value == "true"
    return None


def _csv_values(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",")]


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
    return _run_post_classification(run) == "target-sustained"


def _aggregate_target_sustained(aggregate: PolicyAggregate) -> bool:
    return (
        aggregate.fps_target is not None
        and aggregate.sample_count > 0
        and aggregate.target_sustained_count == aggregate.sample_count
    )


def _run_post_classification(run: RunSummary) -> str | None:
    if run.post_run_classification:
        return run.post_run_classification
    return _post_run_classification_for_values(
        fps_target_met=_run_fps_target_met(run),
        pacing_proof=_run_pacing_proof(run),
    )


def _run_pacing_proof(run: RunSummary) -> bool:
    if run.pacing_proof is not None:
        return run.pacing_proof
    return _pacing_proof_for_values(
        fps_target=run.fps_target,
        target_frame_ms=run.target_frame_ms,
        one_percent_low_fps=run.one_percent_low_fps,
        p99_frametime_ms=run.p99_frametime_ms,
    )


def _post_run_classification_for_values(
    *,
    fps_target_met: bool | None,
    pacing_proof: bool | None,
) -> str | None:
    if fps_target_met is None:
        return None
    if fps_target_met and pacing_proof is True:
        return "target-sustained"
    if fps_target_met:
        return "target-average-only"
    return "below-target"


def _pacing_proof_for_values(
    *,
    fps_target: float | None,
    target_frame_ms: float | None,
    one_percent_low_fps: float | None,
    p99_frametime_ms: float | None,
) -> bool:
    if fps_target is None or fps_target <= 0:
        return False
    if target_frame_ms is None:
        target_frame_ms = _target_frame_ms(fps_target)
    if target_frame_ms is None:
        return False
    if one_percent_low_fps is None or p99_frametime_ms is None:
        return False
    return (
        one_percent_low_fps >= fps_target * 0.8
        and p99_frametime_ms <= target_frame_ms * 1.5
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
    if args.command == "apply-background-shaping":
        report = apply_background_shaping_writes(
            args.restore_affinity_json,
            args.output,
            appid=args.appid,
            variant=args.variant,
        )
        print(json.dumps(_json_ready(report), sort_keys=True))
        return
    if args.command == "restore-background-shaping":
        report = restore_background_shaping_writes(args.writes_json, args.output)
        print(json.dumps(_json_ready(report), sort_keys=True))
        return
    if args.command == "apply-foreground-affinity":
        report = apply_foreground_affinity_writes(
            args.restore_affinity_json,
            args.output,
            role_key=args.role_key,
            preferred_cpus=args.preferred_cpus,
            variant=args.variant,
        )
        print(json.dumps(_json_ready(report), sort_keys=True))
        if not report.get("valid"):
            raise SystemExit(1)
        return
    if args.command == "restore-foreground-affinity":
        report = restore_foreground_affinity_writes(args.writes_json, args.output)
        print(json.dumps(_json_ready(report), sort_keys=True))
        if report.get("restored") is not True:
            raise SystemExit(1)
        return
    if args.command == "apply-foreground-uclamp":
        report = apply_foreground_uclamp_min_writes(
            args.restore_affinity_json,
            args.output,
            appid=args.appid,
            floor_value=args.floor_value,
        )
        print(json.dumps(_json_ready(report), sort_keys=True))
        if not report.get("valid"):
            raise SystemExit(1)
        return
    if args.command == "restore-foreground-uclamp":
        report = restore_foreground_uclamp_min_writes(args.writes_json, args.output)
        print(json.dumps(_json_ready(report), sort_keys=True))
        if report.get("restored") is not True:
            raise SystemExit(1)
        return
    if args.command == "resolve-foreground-affinity":
        report = resolve_foreground_affinity_candidate(args.plan_json)
        print(json.dumps(_json_ready(report), sort_keys=True))
        return
    if args.command == "validate-runtime-telemetry":
        report = validate_runtime_telemetry(
            game_power_jsonl=args.game_power_jsonl,
            summary_json=args.summary_json,
            action_replay_json=args.action_replay_json,
            require_classification=args.require_classification,
            require_pressure=args.require_pressure,
            require_cpu_cap_action=args.require_cpu_cap_action,
            require_frame_performance=args.require_frame_performance,
            require_fps_target_satisfied=args.require_fps_target_satisfied,
            require_target_balance_contract=args.require_target_balance_contract,
            require_v10_contract=args.require_v10_contract,
            expect_fps_target=args.expect_fps_target,
            expect_fps_target_source=args.expect_fps_target_source,
            expect_fps_target_confidence=args.expect_fps_target_confidence,
            expect_target_frame_ms=args.expect_target_frame_ms,
        )
        if args.output:
            Path(args.output).write_text(
                json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n"
            )
        print(json.dumps(_json_ready(report), sort_keys=True))
        return
    if args.command == "replay-action-equivalence":
        report = replay_action_equivalence(args.output)
        print(json.dumps(_json_ready(report), sort_keys=True))
        return
    if args.command == "export-verdicts":
        report = run_export_verdicts(args)
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


def _value_min(values: list[float | None]) -> float | None:
    parsed = [value for value in values if value is not None]
    return min(parsed) if parsed else None


def _value_max(values: list[float | None]) -> float | None:
    parsed = [value for value in values if value is not None]
    return max(parsed) if parsed else None


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
