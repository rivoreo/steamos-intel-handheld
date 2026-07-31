#!/usr/bin/env python3
"""Game-aware CPU/iGPU shared-power governor for Intel SteamOS handhelds."""

from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from .game_power_cgroup_writers import (
    ForegroundUclampMinWriter,
    apply_background_shaping_to_cgroups,
    is_background_shaping_write_target,
    restore_background_shaping_from_report,
)
from .game_power_coloring import (
    COLOR_LEDGER_TID_BUDGET,
    ColorLedgerEntry,
    aggregate_role_observations,
    build_color_ledger,
    cap_thread_samples,
    is_compositor_role,
    resolve_ledger_actuators,
)
from .game_power_frame_target import (
    AutoTargetEstimator,
    AutoTargetProposal,
    divisor_candidates,
    snap_down_to_candidate,
)
from .game_power_gpu import GpuFreqActuator, discover_gpu_gts

MICROJOULES_PER_JOULE = 1_000_000
RUNTIME_SNAPSHOT_SCHEMA_VERSION = "game-power-runtime-snapshot-v1"
DEFAULT_RUNTIME_SNAPSHOT_FILE = Path(
    "/run/steamos-intel-handheld/game-power-runtime.json"
)
DEFAULT_VERDICT_LEDGER_FILE = Path(
    "/var/lib/steamos-intel-handheld/game-power-verdicts.json"
)
DEFAULT_VERDICT_LEDGER_RUN_FALLBACK = Path(
    "/run/steamos-intel-handheld/game-power-verdicts.json"
)


class GamePowerMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    GPU_PRIORITY = "gpu-priority"
    TARGET_BALANCE = "target-balance"


class GamePowerPersona(str, Enum):
    """V10 persona (plan section 0). Resolved from power source + override.

    Default mapping: battery power -> ``battery``; AC power -> ``ac-performance``
    (conservative: AC behavior is unchanged until the user opts into quiet).
    """

    BATTERY = "battery"
    AC_QUIET = "ac-quiet"
    AC_PERFORMANCE = "ac-performance"


class GamePowerAction(str, Enum):
    IDLE = "idle"
    OBSERVE_ONLY = "observe-only"
    GPU_PRIORITY_EPP = "gpu-priority-epp"
    GPU_PRIORITY_CPU_CAP = "gpu-priority-cpu-cap"
    RESTORE = "restore"
    TARGET_BALANCE_TRIM = "target-balance-trim"
    TARGET_BALANCE_RELEASE = "target-balance-release"
    LOADING_BOOST = "loading-boost"


class GamePowerPhase(str, Enum):
    NO_GAME = "no-game"
    LOADING = "loading"
    BELOW_TARGET_CPU_BOUND = "below-target-cpu-bound"
    BELOW_TARGET_GPU_BOUND = "below-target-gpu-bound"
    AT_TARGET = "at-target"
    ABOVE_TARGET = "above-target"
    NO_TARGET = "no-target"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GamePowerActuation:
    """Desired absolute per-class CPU policy layered on top of the baseline snapshot.

    ``None`` fields mean "leave the baseline value"; a non-``None`` field is the
    target value for that policy class. All target-balance writes are expressed
    as absolute state so any phase/step transition is a restore-to-baseline plus
    apply, which makes step-down and phase changes correct by construction.
    """

    pcore_epp: str | None = None
    ecore_epp: str | None = None
    pcore_max_khz: int | None = None
    ecore_max_khz: int | None = None
    # --- V10 additive: GPU envelope + soft-PL1 overlay (contracts 1.2/1.3/1.4).
    # ``None`` means "leave the baseline value" exactly as the CPU fields do, so
    # gpu-priority (which never sets these) stays byte-identical. GPU frequencies
    # are expressed in MHz (native xe sysfs unit); soft-PL1 in whole watts. ---
    gpu_max_mhz: int | None = None
    gpu_min_mhz: int | None = None
    soft_pl1_w: int | None = None
    # D6: a G-rung expresses its GPU cap as a fraction of rp0, not an absolute
    # MHz, because the two GTs have different rp0 (render gt0 1950, media gt1
    # 1200) and each must be trimmed from its OWN rp0. The actuator derives the
    # per-GT absolute cap; ``gpu_max_mhz`` remains for any absolute-MHz caller
    # (e.g. a fixed profiler sweep). ``gpu_max_ratio`` wins over ``gpu_max_mhz``.
    gpu_max_ratio: float | None = None


@dataclass(frozen=True)
class EnergyReading:
    timestamp_s: float
    energy_uj: dict[str, int]


@dataclass(frozen=True)
class RaplPowerWindow:
    duration_s: float
    package_w: float | None = None
    core_w: float | None = None
    uncore_w: float | None = None
    dram_w: float | None = None
    psys_w: float | None = None

    @property
    def core_share(self) -> float | None:
        return _share(self.core_w, self.package_w)

    @property
    def uncore_share(self) -> float | None:
        return _share(self.uncore_w, self.package_w)


def _share(part_w: float | None, total_w: float | None) -> float | None:
    if part_w is None or total_w is None or total_w <= 0:
        return None
    return part_w / total_w


@dataclass(frozen=True)
class FrameTargetTelemetry:
    fps_target: float | None = None
    source: str | None = None
    confidence: str | None = None

    @property
    def target_frame_ms(self) -> float | None:
        if self.fps_target is None or not math.isfinite(self.fps_target):
            return None
        if self.fps_target <= 0:
            return None
        return round(1000.0 / self.fps_target, 3)


@dataclass(frozen=True)
class FramePerformanceTelemetry:
    avg_fps: float | None = None
    p95_frame_ms: float | None = None
    sample_count: int = 0
    window_s: float | None = None
    source: str | None = None
    confidence: str | None = None


@dataclass(frozen=True)
class GamePowerTargetState:
    status: str
    source: str
    confidence: str
    fps: float | None = None
    target_frame_ms: float | None = None
    raw: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "fps": self.fps,
            "target_frame_ms": self.target_frame_ms,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class GamePowerFrameSourceState:
    status: str
    source: str
    confidence: str
    avg_fps: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    sample_count: int | None = None
    window_s: float | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "avg_fps": self.avg_fps,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "sample_count": self.sample_count,
            "window_s": self.window_s,
        }


def target_state_from_telemetry(
    frame_target: FrameTargetTelemetry | None,
) -> GamePowerTargetState:
    if frame_target is None:
        return GamePowerTargetState(
            status="unknown",
            source="none",
            confidence="low",
        )

    source = frame_target.source or "none"
    confidence = frame_target.confidence or "low"
    if frame_target.fps_target is None:
        status = (
            "unlimited"
            if source in {"manual-unlimited", "gamescope-unlimited", "unlimited"}
            else "unknown"
        )
        return GamePowerTargetState(
            status=status,
            source=source,
            confidence=confidence,
        )
    if (
        not math.isfinite(frame_target.fps_target)
        or frame_target.fps_target <= 0
        or frame_target.target_frame_ms is None
    ):
        return GamePowerTargetState(
            status="unknown",
            source=source,
            confidence="low",
            raw=str(frame_target.fps_target),
        )

    return GamePowerTargetState(
        status="known",
        source=source,
        confidence=confidence,
        fps=_round_or_none(frame_target.fps_target),
        target_frame_ms=frame_target.target_frame_ms,
    )


def frame_source_state_from_telemetry(
    frame_performance: FramePerformanceTelemetry | None,
) -> GamePowerFrameSourceState:
    if frame_performance is None:
        return GamePowerFrameSourceState(
            status="missing",
            source="none",
            confidence="low",
        )

    source = frame_performance.source or "unknown"
    confidence = frame_performance.confidence or "low"
    malformed = (
        frame_performance.sample_count <= 0
        or frame_performance.avg_fps is None
        or frame_performance.p95_frame_ms is None
        or not math.isfinite(frame_performance.avg_fps)
        or not math.isfinite(frame_performance.p95_frame_ms)
    )
    return GamePowerFrameSourceState(
        status="malformed" if malformed else "live",
        source=source,
        confidence=confidence,
        avg_fps=_round_or_none(frame_performance.avg_fps),
        p95_ms=_round_or_none(frame_performance.p95_frame_ms),
        sample_count=frame_performance.sample_count,
        window_s=_round_or_none(frame_performance.window_s),
    )


def public_game_power_mode(mode: GamePowerMode) -> str:
    if mode in (GamePowerMode.GPU_PRIORITY, GamePowerMode.TARGET_BALANCE):
        return "automatic"
    return mode.value


class MangoHudCsvFramePerformanceReader:
    def __init__(
        self,
        path: str | Path,
        *,
        window_samples: int = 20,
        min_samples: int = 12,
    ) -> None:
        if window_samples <= 0:
            raise ValueError("window_samples must be positive")
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        if min_samples > window_samples:
            raise ValueError("min_samples cannot exceed window_samples")
        self.path = Path(path)
        self.window_samples = int(window_samples)
        self.min_samples = int(min_samples)

    def read(self) -> FramePerformanceTelemetry | None:
        try:
            rows = self._read_recent_rows()
        except OSError:
            return None
        if not rows:
            return None
        fps_values = [row[0] for row in rows]
        frame_values = [row[1] for row in rows]
        window_s = _elapsed_window_s([row[2] for row in rows])
        return FramePerformanceTelemetry(
            avg_fps=round(sum(fps_values) / len(fps_values), 3),
            p95_frame_ms=_percentile(frame_values, 0.95),
            sample_count=len(rows),
            window_s=window_s,
            source="mangohud-csv",
            confidence="high" if len(rows) >= self.min_samples else "low",
        )

    def _read_recent_rows(self) -> deque[tuple[float, float, float | None]]:
        rows: deque[tuple[float, float, float | None]] = deque(maxlen=self.window_samples)
        header: list[str] | None = None
        fps_index: int | None = None
        frametime_index: int | None = None
        elapsed_index: int | None = None
        with self.path.open(newline="") as handle:
            for raw_row in csv.reader(handle):
                if not raw_row:
                    continue
                if header is None:
                    normalized = [value.strip().lower() for value in raw_row]
                    if "fps" not in normalized or "frametime" not in normalized:
                        continue
                    header = normalized
                    fps_index = header.index("fps")
                    frametime_index = header.index("frametime")
                    elapsed_index = header.index("elapsed") if "elapsed" in header else None
                    continue
                assert fps_index is not None
                assert frametime_index is not None
                fps = _finite_positive_float_or_none(_row_value(raw_row, fps_index))
                frametime = _finite_positive_float_or_none(
                    _row_value(raw_row, frametime_index)
                )
                if fps is None or frametime is None:
                    continue
                elapsed = (
                    _float_or_none(_row_value(raw_row, elapsed_index))
                    if elapsed_index is not None
                    else None
                )
                rows.append((fps, frametime, elapsed))
        return rows


FRAME_FEED_SCHEMA = "steamos-intel-handheld-frame-feed-v1"


@dataclass(frozen=True)
class FrameFeedFast:
    """Cheap fast-lane view of the frame feed (contract 1.5)."""

    status: str  # "live" | "stale" | "absent"
    last_frame_ms: float | None = None
    spike_worst_ms: float | None = None
    avg_fps: float | None = None


class FrameFeedReader:
    """Daemon-side reader for the mangoapp frame feed (contract 1.1).

    Reads ``$XDG_RUNTIME_DIR/steamos-intel-handheld/frame-feed.json`` (path
    injected). A record is *stale* when ``updated_monotonic_s`` is older than
    ``stale_s`` against the injected CLOCK_MONOTONIC clock. Stale / missing /
    corrupt all resolve to feed *absent* so the observer falls back to exact V9
    behaviour (MangoHud CSV when configured, else NO_TARGET degradation). When
    the feed is present and fresh, :meth:`read` upgrades the frame telemetry to
    source ``mangoapp-feed`` with high confidence.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        stale_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self.stale_s = float(stale_s)
        self.clock = clock
        self._last_status = "absent"

    @property
    def last_status(self) -> str:
        return self._last_status

    def _load(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("schema") != FRAME_FEED_SCHEMA:
            return None
        return payload

    def _fresh_payload(self) -> dict[str, object] | None:
        """Return the payload only when present and fresh; set ``_last_status``."""

        payload = self._load()
        if payload is None:
            self._last_status = "absent"
            return None
        updated = _float_or_none(payload.get("updated_monotonic_s"))
        if updated is None:
            self._last_status = "absent"
            return None
        if float(self.clock()) - updated > self.stale_s:
            self._last_status = "stale"
            return None
        self._last_status = "live"
        return payload

    def status(self) -> str:
        self._fresh_payload()
        return self._last_status

    def read(self) -> FramePerformanceTelemetry | None:
        payload = self._fresh_payload()
        if payload is None:
            return None
        avg_fps = _finite_positive_float_or_none(payload.get("avg_fps"))
        p95 = _finite_positive_float_or_none(payload.get("p95_frame_ms"))
        if avg_fps is None or p95 is None:
            # Present but unusable numbers -> treat as absent (V9 fallback).
            self._last_status = "absent"
            return None
        frame_count = payload.get("frame_count")
        sample_count = int(frame_count) if isinstance(frame_count, int) else 0
        window_s = _float_or_none(payload.get("window_s"))
        return FramePerformanceTelemetry(
            avg_fps=round(avg_fps, 3),
            p95_frame_ms=round(p95, 3),
            sample_count=sample_count,
            window_s=round(window_s, 3) if window_s is not None else None,
            source="mangoapp-feed",
            confidence="high",
        )

    def read_fast(self) -> FrameFeedFast:
        payload = self._fresh_payload()
        if payload is None:
            return FrameFeedFast(status=self._last_status)
        spike = payload.get("spike")
        spike_worst = (
            _finite_positive_float_or_none(spike.get("worst_ms"))
            if isinstance(spike, dict)
            else None
        )
        return FrameFeedFast(
            status="live",
            last_frame_ms=_finite_positive_float_or_none(payload.get("last_frame_ms")),
            spike_worst_ms=spike_worst,
            avg_fps=_finite_positive_float_or_none(payload.get("avg_fps")),
        )


def _row_value(row: list[str], index: int | None) -> str | None:
    if index is None or index >= len(row):
        return None
    return row[index]


def _finite_positive_float_or_none(value: object) -> float | None:
    parsed = _float_or_none(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[min(index, len(ordered) - 1)], 3)


def _elapsed_window_s(values: list[float | None]) -> float | None:
    parsed = [value for value in values if value is not None]
    if len(parsed) < 2:
        return None
    delta = parsed[-1] - parsed[0]
    if delta <= 0:
        return None
    if delta > 1_000_000:
        delta /= 1_000_000_000
    return round(delta, 3)


@dataclass(frozen=True)
class PressureSignal:
    scope: str
    source_path: str | None
    supported: bool
    some_avg10: float | None = None
    full_avg10: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class PressureTelemetry:
    cpu: tuple[PressureSignal, ...] = ()
    memory: tuple[PressureSignal, ...] = ()
    io: tuple[PressureSignal, ...] = ()


@dataclass(frozen=True)
class GamePowerClassification:
    primary: str
    advisories: tuple[str, ...] = ()
    confidence: str = "low"
    evidence: dict[str, object] = field(default_factory=dict)


def compute_rapl_power_window(start: EnergyReading, end: EnergyReading) -> RaplPowerWindow:
    duration_s = float(end.timestamp_s) - float(start.timestamp_s)
    if duration_s <= 0:
        raise ValueError("RAPL power window requires positive duration")

    def watts(name: str) -> float | None:
        if name not in start.energy_uj or name not in end.energy_uj:
            return None
        delta_uj = int(end.energy_uj[name]) - int(start.energy_uj[name])
        if delta_uj < 0:
            return None
        return delta_uj / MICROJOULES_PER_JOULE / duration_s

    return RaplPowerWindow(
        duration_s=duration_s,
        package_w=watts("package"),
        core_w=watts("core"),
        uncore_w=watts("uncore"),
        dram_w=watts("dram"),
        psys_w=watts("psys"),
    )


class CpuPolicyClass(str, Enum):
    PCORE = "pcore"
    ECORE = "ecore"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CpuPolicy:
    name: str
    path: Path
    affected_cpus: tuple[int, ...]
    capacity: int | None
    policy_class: CpuPolicyClass
    available_epp: tuple[str, ...]
    current_epp: str | None
    scaling_min_freq: int | None
    scaling_max_freq: int | None
    # Immutable ceiling reported by the CPU/firmware. Used for the topology
    # fingerprint so a runtime scaling_max_freq write (the ladder itself, or a
    # user freq limit) cannot silently diverge the fingerprint (defect C15).
    cpuinfo_max_freq: int | None = None


@dataclass(frozen=True)
class CpuPolicySnapshot:
    values: dict[str, tuple[str | None, int | None]]


# F1: PCORE classification tolerance. Real Lunar Lake hardware reports slightly
# different capacities within the P-core class (cpu0/1 = 1005 vs cpu2/3 = 1024
# on the MSI Claw 8 AI+), so exact equality with the max misclassifies two real
# P-cores as ECORE (they then get the E-core ladder caps and EPP). Anything at
# or above 85% of the max capacity is a P-core; E-cores sit far below on every
# supported hybrid part (676/1024 = 0.66).
PCORE_CAPACITY_RATIO = 0.85


def discover_cpu_policies(sysfs_root: str | Path = "/sys") -> list[CpuPolicy]:
    sysfs_root = Path(sysfs_root)
    cpufreq = sysfs_root / "devices" / "system" / "cpu" / "cpufreq"
    paths = sorted(cpufreq.glob("policy*"), key=_policy_sort_key)
    capacities = {path.name: _policy_capacity(sysfs_root, path) for path in paths}
    known_capacities = [value for value in capacities.values() if value is not None]
    max_capacity = max(known_capacities) if known_capacities else None

    policies: list[CpuPolicy] = []
    for path in paths:
        capacity = capacities[path.name]
        if max_capacity is None or capacity is None:
            policy_class = CpuPolicyClass.UNKNOWN
        elif capacity >= PCORE_CAPACITY_RATIO * max_capacity:
            policy_class = CpuPolicyClass.PCORE
        else:
            policy_class = CpuPolicyClass.ECORE
        policies.append(
            CpuPolicy(
                name=path.name,
                path=path,
                affected_cpus=_read_cpu_list(path / "affected_cpus"),
                capacity=capacity,
                policy_class=policy_class,
                available_epp=tuple(
                    _read_text(path / "energy_performance_available_preferences").split()
                ),
                current_epp=_read_optional_text(path / "energy_performance_preference"),
                scaling_min_freq=_read_optional_int(path / "scaling_min_freq"),
                scaling_max_freq=_read_optional_int(path / "scaling_max_freq"),
                cpuinfo_max_freq=_read_optional_int(path / "cpuinfo_max_freq"),
            )
        )
    return policies


def _policy_sort_key(path: Path) -> tuple[str, int]:
    match = re.search(r"(\d+)$", path.name)
    return (path.name.rstrip("0123456789"), int(match.group(1)) if match else -1)


def _read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _read_optional_text(path: Path) -> str | None:
    value = _read_text(path)
    return value if value else None


def _read_optional_int(path: Path) -> int | None:
    value = _read_text(path)
    try:
        return int(value)
    except ValueError:
        return None


def _read_cpu_list(path: Path) -> tuple[int, ...]:
    text = _read_text(path)
    cpus: list[int] = []
    for part in text.split(","):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            cpus.extend(range(int(start), int(end) + 1))
        else:
            cpus.append(int(part))
    return tuple(cpus)


def _policy_capacity(sysfs_root: Path, policy_path: Path) -> int | None:
    capacities: list[int] = []
    for cpu in _read_cpu_list(policy_path / "affected_cpus"):
        capacity = _read_optional_int(
            sysfs_root / "devices" / "system" / "cpu" / f"cpu{cpu}" / "cpu_capacity"
        )
        if capacity is not None:
            capacities.append(capacity)
    if not capacities:
        return None
    return max(capacities)


class CpuPolicyActuator:
    def __init__(self, policies: Iterable[CpuPolicy]) -> None:
        self.policies = list(policies)

    def snapshot(self) -> CpuPolicySnapshot:
        values: dict[str, tuple[str | None, int | None]] = {}
        for policy in self.policies:
            values[policy.name] = (
                _read_optional_text(policy.path / "energy_performance_preference"),
                _read_optional_int(policy.path / "scaling_max_freq"),
            )
        return CpuPolicySnapshot(values=values)

    def apply(
        self,
        *,
        epp: str | None = None,
        pcore_epp: str | None = None,
        ecore_epp: str | None = None,
        pcore_max_khz: int | None = None,
        ecore_max_khz: int | None = None,
    ) -> None:
        for policy in self.policies:
            target_epp = _epp_for_policy(policy, epp, pcore_epp, ecore_epp)
            if target_epp and target_epp in policy.available_epp:
                _write_if_changed(
                    policy.path / "energy_performance_preference", target_epp
                )
            cap = _cap_for_policy(policy, pcore_max_khz, ecore_max_khz)
            if cap is not None:
                _write_if_changed(policy.path / "scaling_max_freq", str(cap))

    def restore(self, snapshot: CpuPolicySnapshot) -> list[str]:
        """Restore snapshot values with readback verification (F2).

        Every write is verified by readback (the write is skipped only when the
        pre-read already matches), retried once on mismatch, and failures are
        collected instead of silently ignored. Returns the list of control-file
        paths that could not be verified as restored (empty on success).
        """

        failed: list[str] = []
        for policy in self.policies:
            epp, max_freq = snapshot.values.get(policy.name, (None, None))
            # F2.3: restore scaling_max_freq BEFORE the EPP for each policy --
            # a leftover frequency cap is the harmful residue, a leftover EPP
            # is benign, so the cap gets the first (least interruptible) write.
            if max_freq is not None and not _restore_verified(
                policy.path / "scaling_max_freq", str(max_freq)
            ):
                failed.append(str(policy.path / "scaling_max_freq"))
            if epp is not None and not _restore_verified(
                policy.path / "energy_performance_preference", epp
            ):
                failed.append(str(policy.path / "energy_performance_preference"))
        return failed


def _epp_for_policy(
    policy: CpuPolicy,
    epp: str | None,
    pcore_epp: str | None,
    ecore_epp: str | None,
) -> str | None:
    if pcore_epp is None and ecore_epp is None:
        return epp
    if policy.policy_class == CpuPolicyClass.PCORE:
        return pcore_epp if pcore_epp is not None else epp
    if policy.policy_class == CpuPolicyClass.ECORE:
        return ecore_epp if ecore_epp is not None else epp
    if pcore_epp == ecore_epp:
        return pcore_epp
    return epp


def _cap_for_policy(
    policy: CpuPolicy,
    pcore_max_khz: int | None,
    ecore_max_khz: int | None,
) -> int | None:
    if policy.policy_class == CpuPolicyClass.PCORE:
        return pcore_max_khz
    if policy.policy_class == CpuPolicyClass.ECORE:
        return ecore_max_khz
    return pcore_max_khz if pcore_max_khz == ecore_max_khz else None


def _write_if_changed(path: Path, value: str) -> None:
    if _read_text(path) == value:
        return
    path.write_text(value)


def _restore_verified(path: Path, value: str, *, attempts: int = 2) -> bool:
    """Write ``value`` and verify by readback; retry once on mismatch (F2).

    The write is skipped only when the pre-read already matches. Any write
    error or persistent readback mismatch returns ``False`` so the caller can
    fail loudly instead of leaving a silent partial restore behind.
    """

    for _ in range(max(1, attempts)):
        try:
            if path.read_text().strip() == value:
                return True
            path.write_text(value)
            if path.read_text().strip() == value:
                return True
        except OSError:
            continue
    return False


RAPL_NAME_MAP = {
    "package-0": "package",
    "core": "core",
    "uncore": "uncore",
    "dram": "dram",
    "psys": "psys",
}


@dataclass(frozen=True)
class GameProcess:
    pid: int
    appid: str | None
    command: str
    cgroup_text: str = ""


class RaplObserver:
    def __init__(
        self,
        *,
        sysfs_root: str | Path = "/sys",
        clock: object = time.monotonic,
    ) -> None:
        self.sysfs_root = Path(sysfs_root)
        self.clock = clock

    def read(self) -> EnergyReading:
        energy: dict[str, int] = {}
        powercap = self.sysfs_root / "class" / "powercap"
        for domain in sorted(powercap.glob("intel-rapl*")):
            name = _read_text(domain / "name")
            mapped = RAPL_NAME_MAP.get(name)
            if mapped is None:
                continue
            value = _read_optional_int(domain / "energy_uj")
            if value is not None:
                energy[mapped] = value
        return EnergyReading(timestamp_s=float(self.clock()), energy_uj=energy)


def parse_fdinfo_engine_times(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"drm-(total-)?engine-([^:]+):\s+(\d+)\s+ns$", line)
        if not match:
            continue
        prefix = "total-" if match.group(1) else ""
        values[f"{prefix}{match.group(2)}"] = int(match.group(3))
    return values


def compute_fdinfo_busy(
    start: dict[str, int],
    end: dict[str, int],
    *,
    duration_s: float,
) -> dict[str, float]:
    if duration_s <= 0:
        raise ValueError("fdinfo busy calculation requires positive duration")
    busy: dict[str, float] = {}
    for engine, start_ns in start.items():
        if engine.startswith("total-") or engine not in end:
            continue
        delta_ns = end[engine] - start_ns
        if delta_ns < 0:
            continue
        busy[engine] = delta_ns / 1_000_000_000 / duration_s
    return busy


@dataclass(frozen=True)
class GamePowerConfig:
    mode: GamePowerMode = GamePowerMode.OFF
    poll_s: float = 2.0
    epp: str = "balance_power"
    pcore_max_khz: int = 3_200_000
    ecore_max_khz: int = 2_800_000
    cpu_cap_enabled: bool = False
    cpu_cap_core_share_threshold: float = 0.38
    target_appid: str | None = None
    package_pressure_ratio: float = 0.94
    core_share_threshold: float = 0.30
    uncore_share_threshold: float = 0.20
    render_busy_threshold: float = 0.70
    activate_samples: int = 2
    restore_samples: int = 3
    rolling_window_samples: int = 5
    hinted_activate_samples: int = 1
    session_hint_contradiction_samples: int = 2
    frame_target: FrameTargetTelemetry | None = None
    # Limiter-aware: capped games can report slightly below the target while
    # pacing is still healthy, so p95 carries the quality guard.
    # A game pinned at its own frame cap averages just under the target (2 s
    # window: 59.5-60.1 for a 60 cap), so a 0.98 headroom sits inside the noise
    # band and flags "below target" every few ticks -- which flaps the phase
    # machine and applies/removes trims every 2-4 s. p95 (baseline-relative,
    # above) is the real pacing guard; avg_fps only needs to catch genuine
    # misses, which on this device land at 43-52 FPS against a 60 target.
    fps_target_satisfied_headroom_ratio: float = 0.95
    fps_target_satisfied_p95_ratio: float = 1.15
    frame_performance_min_samples: int = 12
    runtime_control_health: dict[str, object] | None = None
    # --- V9 target-balance: phase machine (section 4) ---
    phase_stable_samples: int = 3
    above_target_fps_ratio: float = 1.25
    loading_launch_grace_s: float = 30.0
    loading_exit_samples: int = 5
    loading_boost_max_s: float = 180.0
    loading_frame_stall_s: float = 2.0
    loading_cpu_psi_avg10_threshold: float = 40.0
    loading_core_share_threshold: float = 0.50
    loading_low_fps_ratio: float = 0.50
    loading_render_busy_threshold: float = 0.30
    loading_exit_fps_ratio: float = 0.70
    below_target_cpu_core_share_threshold: float = 0.35
    below_target_cpu_runqueue_wait_ms_threshold: float = 50.0
    below_target_cpu_render_busy_threshold: float = 0.60
    below_target_cpu_core_share_high_threshold: float = 0.45
    # --- V9 target-balance: per-class EPP (section 5) ---
    loading_pcore_epp: str = "performance"
    loading_ecore_epp: str = "balance_performance"
    below_target_cpu_pcore_epp: str = "performance"
    below_target_cpu_ecore_epp: str = "balance_power"
    # --- V9 target-balance: convergence ladder (section 7) ---
    ladder_hold_samples: int = 15
    ladder_backoff_s: float = 300.0
    ladder_p95_guard_ratio: float = 1.10
    # The p95 guard is a *regression* guard, not an absolute-quality guard. It
    # allows the larger of (target frame time * ladder_p95_guard_ratio) and
    # (unconstrained baseline p95 * ladder_p95_regression_ratio), so a scene
    # whose natural p95 already exceeds the ideal frame time can still be
    # trimmed, while a trim that actually degrades pacing still releases.
    ladder_p95_baseline_samples: int = 5
    ladder_p95_regression_ratio: float = 1.08
    # Consecutive breaching samples before the ladder fast-releases. The frame
    # feed's 2 s avg_fps carries ~+/-0.7 FPS of noise against a 0.98 headroom
    # threshold, so a single-sample release resets the ladder (and burns a
    # backoff) on scene noise rather than on a real regression.
    ladder_release_samples: int = 2
    ladder_pcore_epp: str = "balance_power"
    ladder_ecore_epp: str = "balance_power"
    ladder_s3_pcore_max_khz: int = 4_000_000
    ladder_s4_pcore_max_khz: int = 3_000_000
    ladder_s4_ecore_max_khz: int = 2_400_000
    # S5+ deeper caps unlocked only by a BETTER verdict entry (section 7/8).
    ladder_s5_pcore_max_khz: int = 2_600_000
    ladder_s5_ecore_max_khz: int = 2_000_000
    # Profiler-only unlock for the target-balance-ladder5 candidate policy
    # (CLI --allow-ladder-step-5). The daemon service never sets this; it lets a
    # controlled run gather the evidence a ladder-step-5 verdict requires.
    allow_ladder_step_5: bool = False
    # --- V9 gated lanes (section 8) ---
    foreground_uclamp_min_floor: str = "25.00"
    background_shaping_variant: str = "uclamp-max-85"
    # --- V9 coloring cadence (section 6) ---
    colorize_interval_s: float = 10.0
    # --- V10 persona (plan section 0) ---
    persona: GamePowerPersona = GamePowerPersona.BATTERY
    # --- Input-idle frame cap (Radeon Chill / BatteryBoost shape) ---
    # A game left running with nobody touching it still renders at full rate.
    # Capping then costs nothing by definition: the player is not looking for
    # responsiveness they are not asking for. Released the moment input returns.
    # The grace period is deliberately long: this targets genuine
    # put-the-device-down idling, not "watching a cutscene for ten seconds".
    # Driven by the evdev monitor, which is verified to track in-game input.
    # Do NOT wire this to the compositor's input counter: that atom does not see
    # input Steam Input routes to the game, and an idle detector built on it caps
    # a player mid-game and never releases.
    idle_input_grace_s: float = 60.0
    idle_frame_cap_fps: int = 30
    # --- V10 frame feed (contract 1.1) ---
    frame_feed_file: str | None = None
    frame_feed_stale_s: float = 5.0
    # --- V10 GPU cap rungs G1/G2/G3 (contract 1.4): max_freq = rp0 * (1-ratio) ---
    # Defaults sized to the measured 17W/60fps pacing plateau: the cap holds to
    # rp0*0.69 (~1350 MHz, -31% depth) before the knee, so the battery rungs stop
    # at G3 -30%. The old -45% depth landed ~1072 MHz -> ~50 fps (breaks pacing)
    # and is now reachable only as the verdict-gated deep rung G4CAP below.
    gpu_cap_g1_ratio: float = 0.12
    gpu_cap_g2_ratio: float = 0.22
    gpu_cap_g3_ratio: float = 0.30
    # Verdict-gated deep GPU cap (G4CAP): the -45% depth, unlocked only by a
    # matching ``gpu-cap`` BETTER verdict (same mechanism as S3CAP/S4CAP).
    gpu_cap_g4_ratio: float = 0.45
    # --- V10 soft-PL1 rungs P1/P2/P3 (contracts 1.3/1.4) ---
    # P1 = min(user_slider - slider_margin, ceil(package median) + headroom) so it
    # always starts BELOW the user slider (the shipped ceil(median+headroom) sat
    # >= slider on a PL1-pinned scene and clamped to a no-op). P2 = P1 - p2 step;
    # P3 = P1 - p3 step. Effective soft-PL1 is floored at soft_pl1_floor_w. The
    # p95 guard stops the descent (knee at ~slider-2 on the probed scene); the
    # step depth is not hardcoded to that knee.
    soft_pl1_floor_w: int = 8
    soft_pl1_p1_headroom_w: float = 1.5
    soft_pl1_p1_slider_margin_w: float = 1.0
    soft_pl1_p2_step_w: float = 1.0
    soft_pl1_p3_step_w: float = 2.0
    # --- V10 CPU EPP rungs C1 (ecore)/C2 (pcore) (contract 1.4) ---
    trim_ecore_epp: str = "balance_power"
    trim_pcore_epp: str = "balance_power"
    # Profiler-only rung-subset filter (CLI --trim-rungs). ``None`` keeps the
    # full persona sequence (daemon default, byte-identical to V10 Slice A). When
    # set it is a tuple of allowed rung-id first letters (e.g. ("G",) keeps only
    # G-rungs) so the profiler can isolate a single lane for A/B evidence
    # (v10-gpu-cap / v10-soft-pl1). The daemon service never sets this.
    trim_rung_filter: tuple[str, ...] | None = None
    # --- V10 persona guard bands (contract 1.4): ac-quiet holds a wider p95
    # guard than battery so fan noise, not the wall, is the constraint. ---
    ac_quiet_p95_guard_ratio: float = 1.20
    # --- V10 fast boost lane (contract 1.5) ---
    fast_poll_s: float = 0.25
    spike_boost_ratio: float = 1.5
    psi_boost_delta: float = 15.0
    boost_hold_s: float = 3.0
    gpu_boost_floor_ratio: float = 1.0


@dataclass(frozen=True)
class GamePowerSample:
    appid: str | None
    rapl: RaplPowerWindow | None
    pl1_w: int | None
    fdinfo_busy: dict[str, float] = field(default_factory=dict)
    frame_target: FrameTargetTelemetry | None = None
    frame_performance: FramePerformanceTelemetry | None = None
    pressure: PressureTelemetry | None = None
    foreground_runqueue_wait_ms_per_s: float | None = None
    foreground_process_age_s: float | None = None
    frame_feed_stalled: bool | None = None
    # --- V9 coloring (section 6): raw per-role color observations from the
    # observer's colorize cadence, plus the writable allowlist cgroups the
    # background-shaping gated lane may target (section 8). ---
    color_ledger_entries: tuple[object, ...] | None = None
    color_ledger_truncated: bool = False
    allowlist_cgroups: tuple[dict[str, object], ...] = ()
    foreground_cgroup_path: str | None = None
    # --- V10 additive (contracts 1.1/1.2/1.4). All optional so gpu-priority and
    # V9 target-balance samples are unaffected. GPU bounds (rp0/rpe MHz) size the
    # G-rungs and the boost floor; package_median_w sizes the P-rungs; the frame
    # feed status feeds telemetry v3. ---
    gpu_rp0_mhz: int | None = None
    gpu_rpe_mhz: int | None = None
    package_median_w: float | None = None
    frame_feed_status: str | None = None


@dataclass(frozen=True)
class GamePowerDecision:
    action: GamePowerAction
    reason: str
    classification: GamePowerClassification | None = None
    phase: GamePowerPhase | None = None
    phase_reason_codes: tuple[str, ...] = ()
    ladder_step: int | None = None
    actuation: GamePowerActuation | None = None
    # --- V9 additive telemetry (target-balance only) ---
    color_ledger: dict[str, object] | None = None
    verdict_ledger_health: dict[str, object] | None = None
    gated_lanes: dict[str, object] | None = None
    # --- V10 additive telemetry v3 (target-balance only; contract 1.7). All
    # default None so gpu-priority JSONL/snapshots stay byte-identical (same
    # only-when-not-None pattern as the V9 additive fields). ---
    persona: str | None = None
    soft_pl1_w: int | None = None
    gpu_freq_caps: dict[str, object] | None = None
    boost_active: bool | None = None
    boost_reason: str | None = None
    trim_rungs_active: list[str] | None = None
    frame_feed_status: str | None = None
    limiter_state: str | None = None
    # Pacing guard observability: what the ladder learned as this scene's
    # unconstrained p95 and the budget it is actually holding against.
    p95_baseline_ms: float | None = None
    p95_budget_ms: float | None = None


# Mirrors the FPS-target override contract in game_power_control so Auto can
# never propose a target the control surface would reject.
AUTO_TARGET_MIN_FPS = 30
AUTO_TARGET_MAX_FPS = 120

# v2 (2026-07-31): learned hints from v1 are not comparable and are discarded on
# load. Three things changed underneath them at once: the pacing guards became
# baseline-relative, the trim ladder interleaved its lanes, and frame-target
# detection moved to the compositor's own limit atom. v1 entries also carry
# targets captured while the panel seeded a literal 40 FPS, so their context keys
# describe a target the user never chose.
DEFAULT_GAME_POWER_POLICY_VERSION = "game-power-sampling-v2"
GAME_POWER_HINT_SCHEMA_VERSION = 1
NON_REUSABLE_FPS_TARGETS = frozenset({"", "unknown", "none-configured", "unlimited"})


@dataclass(frozen=True)
class GamePowerHintPolicy:
    min_hint_sessions: int = 2
    min_hint_samples: int = 20
    min_hint_positive_ratio: float = 0.70
    hint_contradiction_limit: int = 3
    session_hint_contradiction_samples: int = 2
    policy_version: str = DEFAULT_GAME_POWER_POLICY_VERSION
    max_aggregate_records: int = 128
    max_hint_entries: int = 64
    max_hint_cache_bytes: int = 262_144
    max_aggregate_age_days: int = 14
    max_hint_age_days: int = 30
    max_runtime_unaware_hint_age_days: int = 7


@dataclass(frozen=True)
class GamePowerHintContext:
    appid: str
    pl1_w: int | None
    power_source: str
    fps_target: str
    topology_signature: str
    os_signature: str
    runtime_signature: str
    runtime_signature_known: bool
    policy_version: str = DEFAULT_GAME_POWER_POLICY_VERSION
    complete: bool = False


@dataclass
class GamePowerSessionSummary:
    context: GamePowerHintContext
    started_s: float
    samples: int = 0
    positive_samples: int = 0
    negative_samples: int = 0
    applied_samples: int = 0
    restored_samples: int = 0
    cpu_cap_samples: int = 0
    contradiction_samples: int = 0
    hint_was_used: bool = False
    hint_disabled: bool = False
    hint_disable_reason: str | None = None
    write_failed: bool = False
    restore_attempted: bool = False
    restore_succeeded: bool | None = None
    restore_error: str | None = None


@dataclass(frozen=True)
class GamePowerHintEntry:
    key: str
    context: GamePowerHintContext
    confidence: str
    observed_sessions: int
    total_samples: int
    positive_ratio: float
    cpu_cap_ratio: float
    last_validated_at: float
    contradiction_count: int = 0
    stale: bool = False
    runtime_unaware: bool = False


@dataclass(frozen=True)
class GamePowerHintStoreResult:
    aggregate_updated: bool = False
    hint_promoted: bool = False
    cache_write_result: str = "not_configured"
    promotion_skip_reason: str | None = None
    hint_contradiction_count_before: int = 0
    hint_contradiction_count_after: int = 0
    hint_repair_delta: int = 0


@dataclass(frozen=True)
class GamePowerActuatorOutcome:
    attempted: bool
    succeeded: bool
    reason: str


def pl1_bucket_w(value_w: float | int | None) -> int | None:
    if value_w is None or not math.isfinite(float(value_w)):
        return None
    if float(value_w) < 0.5:
        return None
    return math.floor(float(value_w) + 0.5)


def canonical_hint_key(context: GamePowerHintContext) -> str:
    payload = {
        "appid": context.appid,
        "fps_target": context.fps_target,
        "os_signature": context.os_signature,
        "pl1_w": context.pl1_w,
        "policy_version": context.policy_version,
        "power_source": context.power_source,
        "runtime_signature": context.runtime_signature,
        "topology_signature": context.topology_signature,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"game-power-context-v1:{hashlib.sha256(raw).hexdigest()}"


def _context_json(context: GamePowerHintContext) -> dict[str, object]:
    return {
        "appid": context.appid,
        "pl1_w": context.pl1_w,
        "power_source": context.power_source,
        "fps_target": context.fps_target,
        "topology_signature": context.topology_signature,
        "os_signature": context.os_signature,
        "runtime_signature": context.runtime_signature,
        "runtime_signature_known": context.runtime_signature_known,
        "policy_version": context.policy_version,
        "complete": context.complete,
    }


def _context_from_json(value: object) -> GamePowerHintContext | None:
    if not isinstance(value, dict):
        return None
    try:
        context = GamePowerHintContext(
            appid=str(value["appid"]),
            pl1_w=int(value["pl1_w"]) if value.get("pl1_w") is not None else None,
            power_source=str(value["power_source"]),
            fps_target=str(value["fps_target"]),
            topology_signature=str(value["topology_signature"]),
            os_signature=str(value["os_signature"]),
            runtime_signature=str(value["runtime_signature"]),
            runtime_signature_known=bool(value.get("runtime_signature_known", False)),
            policy_version=str(value.get("policy_version", DEFAULT_GAME_POWER_POLICY_VERSION)),
            complete=bool(value.get("complete", True)),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return _normalize_hint_context(context)


def _normalize_hint_context(context: GamePowerHintContext) -> GamePowerHintContext:
    if not _context_has_reusable_target(context):
        return replace(context, complete=False)
    return context


def _context_has_reusable_target(context: GamePowerHintContext) -> bool:
    return context.fps_target.strip().lower() not in NON_REUSABLE_FPS_TARGETS


def _session_eligible(summary: GamePowerSessionSummary) -> bool:
    if not summary.context.complete:
        return False
    if summary.samples <= 0:
        return False
    restore_required = summary.applied_samples > 0 or summary.restore_attempted
    return not (
        summary.write_failed
        or (restore_required and summary.restore_succeeded is not True)
        or summary.contradiction_samples > 0
        or summary.hint_disabled
    )


class GamePowerHintStore:
    def __init__(
        self,
        path: str | Path | None,
        *,
        policy: GamePowerHintPolicy | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.policy = policy or GamePowerHintPolicy()
        self.now = now
        self.load_error: str | None = None
        self._aggregates: dict[str, dict[str, object]] = {}
        self._entries: dict[str, GamePowerHintEntry] = {}
        self._load()

    def get_hint(self, context: GamePowerHintContext) -> GamePowerHintEntry | None:
        if not context.complete:
            return None
        if not _context_has_reusable_target(context):
            return None
        entry = self._entries.get(canonical_hint_key(context))
        if entry is None or entry.stale:
            return None
        if self._entry_expired(entry):
            return None
        if entry.contradiction_count >= self.policy.hint_contradiction_limit:
            return None
        return entry

    def record_session(self, summary: GamePowerSessionSummary) -> GamePowerHintStoreResult:
        if self.path is None:
            return GamePowerHintStoreResult(cache_write_result="not_configured")
        if not _session_eligible(summary):
            if summary.context.complete and (
                summary.contradiction_samples > 0 or summary.hint_disabled
            ):
                return self._record_contradiction(summary)
            return GamePowerHintStoreResult(
                cache_write_result="not_eligible",
                promotion_skip_reason=self._promotion_skip_reason(summary),
            )
        key = canonical_hint_key(summary.context)
        aggregate = self._aggregates.get(key) or {
            "context": _context_json(summary.context),
            "observed_sessions": 0,
            "total_samples": 0,
            "positive_samples": 0,
            "cpu_cap_samples": 0,
            "clean_restore_sessions": 0,
            "last_observed_at": 0.0,
        }
        aggregate["observed_sessions"] = int(aggregate["observed_sessions"]) + 1
        aggregate["total_samples"] = int(aggregate["total_samples"]) + summary.samples
        aggregate["positive_samples"] = (
            int(aggregate["positive_samples"]) + summary.positive_samples
        )
        aggregate["cpu_cap_samples"] = int(aggregate["cpu_cap_samples"]) + summary.cpu_cap_samples
        if summary.applied_samples == 0 or summary.restore_succeeded is True:
            aggregate["clean_restore_sessions"] = int(aggregate["clean_restore_sessions"]) + 1
        aggregate["last_observed_at"] = self.now()
        self._aggregates[key] = aggregate

        before_count = (
            self._entries[key].contradiction_count if key in self._entries else 0
        )
        promoted = self._maybe_promote(
            key,
            aggregate,
            previous_contradiction_count=before_count,
        )
        after_count = (
            self._entries[key].contradiction_count if key in self._entries else before_count
        )
        self._prune()
        write_result = self._write()
        return GamePowerHintStoreResult(
            aggregate_updated=True,
            hint_promoted=promoted,
            cache_write_result=write_result,
            promotion_skip_reason=None if promoted else self._aggregate_skip_reason(aggregate),
            hint_contradiction_count_before=before_count,
            hint_contradiction_count_after=after_count,
            hint_repair_delta=before_count - after_count,
        )

    def _record_contradiction(
        self,
        summary: GamePowerSessionSummary,
    ) -> GamePowerHintStoreResult:
        key = canonical_hint_key(summary.context)
        entry = self._entries.get(key)
        if entry is None:
            return GamePowerHintStoreResult(
                cache_write_result="not_eligible",
                promotion_skip_reason=self._promotion_skip_reason(summary),
            )
        before_count = entry.contradiction_count
        after_count = before_count + 1
        self._entries[key] = replace(entry, contradiction_count=after_count)
        self._prune()
        write_result = self._write()
        return GamePowerHintStoreResult(
            cache_write_result=write_result,
            promotion_skip_reason=self._promotion_skip_reason(summary),
            hint_contradiction_count_before=before_count,
            hint_contradiction_count_after=after_count,
        )

    def _maybe_promote(
        self,
        key: str,
        aggregate: dict[str, object],
        *,
        previous_contradiction_count: int = 0,
    ) -> bool:
        sessions = int(aggregate["observed_sessions"])
        samples = int(aggregate["total_samples"])
        positives = int(aggregate["positive_samples"])
        if sessions < self.policy.min_hint_sessions:
            return False
        if samples < self.policy.min_hint_samples:
            return False
        positive_ratio = positives / samples if samples else 0.0
        if positive_ratio < self.policy.min_hint_positive_ratio:
            return False
        context = _context_from_json(aggregate.get("context"))
        if context is None:
            return False
        self._entries[key] = GamePowerHintEntry(
            key=key,
            context=context,
            confidence="medium",
            observed_sessions=sessions,
            total_samples=samples,
            positive_ratio=positive_ratio,
            cpu_cap_ratio=(
                int(aggregate["cpu_cap_samples"]) / samples if samples else 0.0
            ),
            last_validated_at=float(aggregate["last_observed_at"]),
            contradiction_count=max(0, previous_contradiction_count - 1),
            stale=False,
            runtime_unaware=not context.runtime_signature_known,
        )
        return True

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            if self.path.stat().st_size > self.policy.max_hint_cache_bytes:
                self.load_error = "cache_over_budget"
                return
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            self.load_error = "invalid_json"
            return
        except OSError as exc:
            self.load_error = f"read_failed:{exc.strerror or exc}"
            return
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != GAME_POWER_HINT_SCHEMA_VERSION
        ):
            self.load_error = "schema_mismatch"
            return
        if data.get("policy_version") != self.policy.policy_version:
            self.load_error = "policy_mismatch"
            return
        self._aggregates = self._load_aggregates(data.get("aggregates"))
        self._entries = self._load_entries(data.get("entries"))

    def _load_aggregates(self, value: object) -> dict[str, dict[str, object]]:
        if not isinstance(value, dict):
            return {}
        loaded: dict[str, dict[str, object]] = {}
        for key, record in value.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            context = _context_from_json(record.get("context"))
            if context is None or canonical_hint_key(context) != key:
                continue
            try:
                loaded[key] = {
                    "context": _context_json(context),
                    "observed_sessions": int(record["observed_sessions"]),
                    "total_samples": int(record["total_samples"]),
                    "positive_samples": int(record["positive_samples"]),
                    "cpu_cap_samples": int(record.get("cpu_cap_samples", 0)),
                    "clean_restore_sessions": int(record.get("clean_restore_sessions", 0)),
                    "last_observed_at": float(record.get("last_observed_at", 0.0)),
                }
            except (KeyError, TypeError, ValueError):
                continue
        return loaded

    def _load_entries(self, value: object) -> dict[str, GamePowerHintEntry]:
        if not isinstance(value, dict):
            return {}
        loaded: dict[str, GamePowerHintEntry] = {}
        for key, record in value.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            context = _context_from_json(record.get("context"))
            if context is None or canonical_hint_key(context) != key:
                continue
            try:
                loaded[key] = GamePowerHintEntry(
                    key=key,
                    context=context,
                    confidence=str(record.get("confidence", "medium")),
                    observed_sessions=int(record["observed_sessions"]),
                    total_samples=int(record["total_samples"]),
                    positive_ratio=float(record["positive_ratio"]),
                    cpu_cap_ratio=float(record.get("cpu_cap_ratio", 0.0)),
                    last_validated_at=float(record.get("last_validated_at", 0.0)),
                    contradiction_count=int(record.get("contradiction_count", 0)),
                    stale=bool(record.get("stale", False)),
                    runtime_unaware=bool(record.get("runtime_unaware", False)),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return loaded

    def _prune(self) -> None:
        now = self.now()
        aggregate_max_age_s = self.policy.max_aggregate_age_days * 86_400
        hint_max_age_s = self.policy.max_hint_age_days * 86_400
        runtime_unaware_max_age_s = self.policy.max_runtime_unaware_hint_age_days * 86_400
        self._aggregates = {
            key: aggregate
            for key, aggregate in self._aggregates.items()
            if now - float(aggregate.get("last_observed_at", 0.0)) <= aggregate_max_age_s
        }
        self._entries = {
            key: entry
            for key, entry in self._entries.items()
            if not entry.stale
            and now - entry.last_validated_at
            <= (runtime_unaware_max_age_s if entry.runtime_unaware else hint_max_age_s)
        }
        self._aggregates = dict(
            sorted(
                self._aggregates.items(),
                key=lambda item: float(item[1].get("last_observed_at", 0.0)),
                reverse=True,
            )[: self.policy.max_aggregate_records]
        )
        self._entries = dict(
            sorted(
                self._entries.items(),
                key=lambda item: item[1].last_validated_at,
                reverse=True,
            )[: self.policy.max_hint_entries]
        )

    def _write(self) -> str:
        if self.path is None:
            return "not_configured"
        data = self._json()
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        if len(payload.encode()) > self.policy.max_hint_cache_bytes:
            return "cache_over_budget"
        lock_handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = self._acquire_lock()
            tmp = self.path.with_name(f"{self.path.name}.tmp")
            tmp.write_text(payload)
            tmp.replace(self.path)
        except BlockingIOError:
            return "lock_failed"
        except OSError:
            return "write_failed"
        finally:
            if lock_handle is not None:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)
                lock_handle.close()
        return "written"

    def _acquire_lock(self):
        if self.path is None:
            return None
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        handle = lock_path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise BlockingIOError(str(exc)) from exc
        return handle

    def _entry_expired(self, entry: GamePowerHintEntry) -> bool:
        max_age_s = (
            self.policy.max_runtime_unaware_hint_age_days * 86_400
            if entry.runtime_unaware
            else self.policy.max_hint_age_days * 86_400
        )
        return self.now() - entry.last_validated_at > max_age_s

    def _json(self) -> dict[str, object]:
        return {
            "schema_version": GAME_POWER_HINT_SCHEMA_VERSION,
            "policy_version": self.policy.policy_version,
            "aggregates": self._aggregates,
            "entries": {
                key: {
                    "context": _context_json(entry.context),
                    "preferred_mode": "gpu-priority",
                    "confidence": entry.confidence,
                    "observed_sessions": entry.observed_sessions,
                    "total_samples": entry.total_samples,
                    "positive_ratio": entry.positive_ratio,
                    "cpu_cap_ratio": entry.cpu_cap_ratio,
                    "last_validated_at": entry.last_validated_at,
                    "contradiction_count": entry.contradiction_count,
                    "stale": entry.stale,
                    "runtime_unaware": entry.runtime_unaware,
                }
                for key, entry in self._entries.items()
            },
        }

    def _aggregate_skip_reason(self, aggregate: dict[str, object]) -> str:
        if int(aggregate["observed_sessions"]) < self.policy.min_hint_sessions:
            return "not_enough_sessions"
        if int(aggregate["total_samples"]) < self.policy.min_hint_samples:
            return "not_enough_samples"
        samples = int(aggregate["total_samples"])
        positives = int(aggregate["positive_samples"])
        ratio = positives / samples if samples else 0.0
        if ratio < self.policy.min_hint_positive_ratio:
            return "positive_ratio_below_threshold"
        return "not_eligible"

    def _promotion_skip_reason(self, summary: GamePowerSessionSummary) -> str:
        if not summary.context.complete:
            return "context_incomplete"
        if summary.write_failed:
            return "write_failed"
        if summary.contradiction_samples > 0 or summary.hint_disabled:
            return "hint_contradicted"
        if summary.restore_succeeded is not True and (
            summary.applied_samples > 0 or summary.restore_attempted
        ):
            return "restore_not_clean"
        return "not_eligible"


def classify_game_power_sample(
    config: GamePowerConfig,
    sample: GamePowerSample,
    *,
    controller_active: bool = False,
    p95_budget_ms: float | None = None,
) -> GamePowerClassification:
    if config.mode == GamePowerMode.OFF:
        return GamePowerClassification("control-disabled", confidence="high")
    if config.mode == GamePowerMode.OBSERVE:
        return GamePowerClassification("observe-only", confidence="high")
    if sample.appid is None:
        return GamePowerClassification("no-foreground-game", confidence="high")
    if config.target_appid is not None and sample.appid != config.target_appid:
        return GamePowerClassification("non-target-game", confidence="high")
    if _sample_fps_target_satisfied(config, sample, p95_budget_ms=p95_budget_ms):
        return GamePowerClassification(
            "fps-target-satisfied",
            confidence="high",
            advisories=_pressure_advisories(sample.pressure),
            evidence=_compact_evidence(
                {
                    **_frame_target_evidence(sample),
                    "controller_active": controller_active,
                    "pressure_scopes": _pressure_scopes(sample.pressure),
                }
            ),
        )
    if sample.rapl is None or sample.pl1_w is None or sample.rapl.package_w is None:
        return GamePowerClassification("insufficient-power-evidence", confidence="low")

    rapl = sample.rapl
    package_pressure_ratio = _share(rapl.package_w, sample.pl1_w)
    evidence: dict[str, object] = {
        "package_pressure_ratio": _round_or_none(package_pressure_ratio),
        "package_pressure_threshold": config.package_pressure_ratio,
        "core_share": _round_or_none(rapl.core_share),
        "core_share_threshold": config.core_share_threshold,
        "uncore_share": _round_or_none(rapl.uncore_share),
        "uncore_share_threshold": config.uncore_share_threshold,
        "render_busy": _round_or_none(sample.fdinfo_busy.get("render")),
        "render_busy_threshold": config.render_busy_threshold,
        "controller_active": controller_active,
    }
    if sample.frame_target is not None:
        evidence["fps_target"] = sample.frame_target.fps_target
        evidence["target_frame_ms"] = sample.frame_target.target_frame_ms
    if sample.frame_performance is not None:
        evidence.update(_frame_target_evidence(sample))
    pressure_scopes = _pressure_scopes(sample.pressure)
    if pressure_scopes:
        evidence["pressure_scopes"] = pressure_scopes

    if package_pressure_ratio is None:
        return GamePowerClassification(
            "insufficient-power-evidence",
            confidence="low",
            evidence=_compact_evidence(evidence),
        )
    if package_pressure_ratio < config.package_pressure_ratio:
        return GamePowerClassification(
            "not-package-bound",
            confidence="medium",
            advisories=_pressure_advisories(sample.pressure),
            evidence=_compact_evidence(evidence),
        )

    uncore_share = rapl.uncore_share
    render_busy = sample.fdinfo_busy.get("render")
    has_gpu_activity = (
        uncore_share is not None and uncore_share >= config.uncore_share_threshold
    ) or (render_busy is not None and render_busy >= config.render_busy_threshold)
    advisories = _pressure_advisories(sample.pressure)
    if not has_gpu_activity:
        return GamePowerClassification(
            "unknown-package-pressure",
            confidence="medium",
            advisories=advisories,
            evidence=_compact_evidence(evidence),
        )

    core_share = rapl.core_share
    if core_share is not None and core_share >= config.cpu_cap_core_share_threshold:
        return GamePowerClassification(
            "gpu-package-bound-cpu-contention",
            confidence="high",
            advisories=advisories,
            evidence=_compact_evidence(evidence),
        )
    return GamePowerClassification(
        "gpu-package-bound",
        confidence="high",
        advisories=advisories,
        evidence=_compact_evidence(evidence),
    )


def _compact_evidence(evidence: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in evidence.items() if value is not None}


def _sample_fps_target_satisfied(
    config: GamePowerConfig,
    sample: GamePowerSample,
    *,
    p95_budget_ms: float | None = None,
) -> bool:
    """Is the frame target being met?

    ``p95_budget_ms`` overrides the absolute ``target_frame_ms * ratio`` pacing
    allowance. Callers that have learned the scene's unconstrained p95 pass the
    baseline-relative budget instead: a healthy 60 FPS scene commonly paces at
    p95 18-20 ms against a 16.67 ms target, and judging that "not satisfied"
    flaps the phase machine every tick.
    """
    target = sample.frame_target
    performance = sample.frame_performance
    if target is None or performance is None:
        return False
    fps_target = target.fps_target
    target_frame_ms = target.target_frame_ms
    if fps_target is None or target_frame_ms is None:
        return False
    if performance.confidence != "high":
        return False
    if performance.sample_count < config.frame_performance_min_samples:
        return False
    if performance.avg_fps is None or performance.p95_frame_ms is None:
        return False
    if not math.isfinite(performance.avg_fps) or not math.isfinite(
        performance.p95_frame_ms
    ):
        return False
    budget = (
        p95_budget_ms
        if p95_budget_ms is not None
        else target_frame_ms * config.fps_target_satisfied_p95_ratio
    )
    return (
        performance.avg_fps >= fps_target * config.fps_target_satisfied_headroom_ratio
        and performance.p95_frame_ms <= budget
    )


def _foreground_cpu_psi_avg10(pressure: PressureTelemetry | None) -> float | None:
    if pressure is None:
        return None
    for signal in pressure.cpu:
        if signal.scope == "foreground_cgroup" and signal.supported:
            return signal.some_avg10
    return None


def classify_game_power_phase(
    config: GamePowerConfig,
    sample: GamePowerSample,
    *,
    p95_budget_ms: float | None = None,
) -> tuple[GamePowerPhase, tuple[str, ...]]:
    """Instantaneous (pre-hysteresis) phase classification (design section 4).

    Returns the raw phase for this tick plus reason codes. Hysteresis, loading
    budget, and ladder control live in :class:`GamePowerController`.
    """

    if sample.appid is None:
        return GamePowerPhase.NO_GAME, ("no-foreground-game",)

    target = target_state_from_telemetry(sample.frame_target)
    if target.status != "known":
        return GamePowerPhase.NO_TARGET, ("target-unknown-or-unlimited",)

    fps_target = target.fps
    rapl = sample.rapl
    core_share = rapl.core_share if rapl is not None else None
    uncore_share = rapl.uncore_share if rapl is not None else None
    package_w = rapl.package_w if rapl is not None else None
    render_busy = sample.fdinfo_busy.get("render")
    avg_fps = (
        sample.frame_performance.avg_fps
        if sample.frame_performance is not None
        else None
    )
    cpu_psi = _foreground_cpu_psi_avg10(sample.pressure)

    # LOADING has highest priority when a target exists (design section 4/5, P1).
    loading_reasons: list[str] = []
    if (
        sample.foreground_process_age_s is not None
        and sample.foreground_process_age_s < config.loading_launch_grace_s
    ):
        loading_reasons.append("launch-grace")
    stalled = bool(sample.frame_feed_stalled)
    high_core = core_share is not None and core_share > config.loading_core_share_threshold
    high_psi = cpu_psi is not None and cpu_psi > config.loading_cpu_psi_avg10_threshold
    # C6: the stall trigger must not fire while the game is comfortably at target
    # with a slow-updating frame aggregate (launch-grace and low-fps unchanged).
    if (
        stalled
        and (high_psi or high_core)
        and not _sample_fps_target_satisfied(config, sample, p95_budget_ms=p95_budget_ms)
    ):
        loading_reasons.append("frame-feed-stalled")
    if (
        avg_fps is not None
        and fps_target is not None
        and avg_fps < config.loading_low_fps_ratio * fps_target
        and render_busy is not None
        and render_busy < config.loading_render_busy_threshold
        and core_share is not None
        and core_share > config.loading_core_share_threshold
    ):
        loading_reasons.append("asset-shader-burst")
    if loading_reasons:
        return GamePowerPhase.LOADING, tuple(loading_reasons)

    if _sample_fps_target_satisfied(config, sample, p95_budget_ms=p95_budget_ms):
        if (
            avg_fps is not None
            and fps_target is not None
            and avg_fps >= config.above_target_fps_ratio * fps_target
        ):
            return GamePowerPhase.ABOVE_TARGET, ("above-target-fps",)
        return GamePowerPhase.AT_TARGET, ("fps-target-satisfied",)

    # Not satisfied: identify the bound resource.
    gpu_bound = (
        render_busy is not None and render_busy >= config.render_busy_threshold
    ) or (
        uncore_share is not None
        and uncore_share >= config.uncore_share_threshold
        and package_w is not None
        and sample.pl1_w is not None
        and package_w >= config.package_pressure_ratio * sample.pl1_w
    )
    if gpu_bound:
        return GamePowerPhase.BELOW_TARGET_GPU_BOUND, ("gpu-bound",)

    runqueue_wait = sample.foreground_runqueue_wait_ms_per_s
    cpu_bound = (
        core_share is not None
        and core_share >= config.below_target_cpu_core_share_threshold
        and runqueue_wait is not None
        and runqueue_wait >= config.below_target_cpu_runqueue_wait_ms_threshold
    ) or (
        render_busy is not None
        and render_busy < config.below_target_cpu_render_busy_threshold
        and core_share is not None
        and core_share >= config.below_target_cpu_core_share_high_threshold
    )
    if cpu_bound:
        return GamePowerPhase.BELOW_TARGET_CPU_BOUND, ("cpu-bound",)

    return GamePowerPhase.UNKNOWN, ("no-bound-signal",)


def _frame_target_evidence(sample: GamePowerSample) -> dict[str, object]:
    target = sample.frame_target
    performance = sample.frame_performance
    fps_target = target.fps_target if target is not None else None
    target_frame_ms = target.target_frame_ms if target is not None else None
    avg_fps = performance.avg_fps if performance is not None else None
    p95_frame_ms = performance.p95_frame_ms if performance is not None else None
    return {
        "fps_target": _round_or_none(fps_target),
        "target_frame_ms": target_frame_ms,
        "frame_avg_fps": _round_or_none(avg_fps),
        "frame_p95_ms": _round_or_none(p95_frame_ms),
        "fps_target_ratio": _round_or_none(_share(avg_fps, fps_target)),
        "p95_frame_time_ratio": _round_or_none(_share(p95_frame_ms, target_frame_ms)),
        "frame_performance_sample_count": (
            performance.sample_count if performance is not None else None
        ),
        "frame_performance_window_s": _round_or_none(
            performance.window_s if performance is not None else None
        ),
        "frame_performance_source": performance.source if performance is not None else None,
        "frame_performance_confidence": (
            performance.confidence if performance is not None else None
        ),
    }


def _pressure_scopes(pressure: PressureTelemetry | None) -> list[str]:
    if pressure is None:
        return []
    scopes = {
        signal.scope
        for signals in (pressure.cpu, pressure.memory, pressure.io)
        for signal in signals
    }
    return sorted(scopes)


def _pressure_advisories(pressure: PressureTelemetry | None) -> tuple[str, ...]:
    if pressure is None:
        return ()
    advisories: set[str] = set()
    for resource, signals in (
        ("cpu", pressure.cpu),
        ("memory", pressure.memory),
        ("io", pressure.io),
    ):
        some_threshold, full_threshold = _pressure_thresholds(resource)
        for signal in signals:
            if not signal.supported:
                continue
            pressured = (
                signal.some_avg10 is not None
                and signal.some_avg10 >= some_threshold
            ) or (
                signal.full_avg10 is not None
                and signal.full_avg10 >= full_threshold
            )
            if not pressured:
                continue
            if signal.scope == "foreground_cgroup":
                advisories.add(f"foreground-{resource}-pressure")
            elif signal.scope == "system":
                advisories.add("system-pressure-advisory")
    return tuple(sorted(advisories))


def _pressure_thresholds(resource: str) -> tuple[float, float]:
    if resource == "cpu":
        return (2.0, 0.5)
    return (1.0, 0.2)


# V10 TrimLadder rung sequences (contract 1.4). Each rung is one demand-shaping
# step applied cumulatively; climbing to step k means rungs[:k] are all active.
#   G1/G2/G3  GPU max_freq capped to rp0 * (1 - ratio)
#   P1/P2/P3  soft-PL1 overlay = ceil(package median + headroom), stepped down
#   C1/C2     ecore then pcore EPP -> balance_power
# The V9 S3/S4 CPU-frequency caps are dropped from the battery sequence (S4 p95
# regression, direction 1b) and available only as verdict-gated deep rungs.
# Lanes are interleaved rather than grouped: the sequence is strictly
# cumulative, so a rung the scene cannot sustain also strands every rung behind
# it. Device evidence (2026-07-31, MSI Claw 8 AI+): G2 (~1521 MHz) breaks 60 FPS
# in a heavy scene, which under the old G,G,G,P,P,P order made soft-PL1
# unreachable -- even though a 20 W soft-PL1 held 60 FPS in the same session.
_BATTERY_RUNGS = ("G1", "P1", "G2", "P2", "G3", "P3", "C1", "C2")
_AC_QUIET_RUNGS = _BATTERY_RUNGS
_AC_PERFORMANCE_RUNGS = ("C1", "C2")
_DEEP_CPU_CAP_RUNGS = ("S3CAP", "S4CAP")
# Verdict actuator string (and profiler --allow-ladder-step-5 flag) that unlocks
# the deep CPU-frequency-cap rungs on battery / ac-quiet.
_DEEP_CPU_CAP_VERDICT = "ladder-step-5"
# Verdict-gated deep GPU cap rung: the -45% depth beyond the measured pacing
# plateau, unlocked only by a matching ``gpu-cap`` BETTER verdict (the daemon now
# consumes gpu-cap verdicts, mirroring the S3CAP/S4CAP mechanism above).
_DEEP_GPU_CAP_RUNGS = ("G4CAP",)
_DEEP_GPU_CAP_VERDICT = "gpu-cap"


def _persona_base_rungs(persona: GamePowerPersona) -> tuple[str, ...]:
    if persona == GamePowerPersona.AC_PERFORMANCE:
        return _AC_PERFORMANCE_RUNGS
    return _BATTERY_RUNGS


# Profiler-only rung-subset selection (CLI --trim-rungs). Maps a user-facing
# selector to the set of allowed rung-id first letters. ``all`` == no filter, so
# the daemon default path stays byte-identical. Each rung id begins with its
# kind letter (G1/P2/C1/S3CAP), so first-letter membership isolates a lane.
_TRIM_RUNG_FILTERS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "G": ("G",),
    "P": ("P",),
    "GPC1C2": ("G", "P", "C"),
}


def _filter_rungs(
    rungs: tuple[str, ...], allowed_first_letters: tuple[str, ...] | None
) -> tuple[str, ...]:
    if allowed_first_letters is None:
        return rungs
    return tuple(rung for rung in rungs if rung[:1] in allowed_first_letters)


def _actuation_for_gpu_priority_action(
    action: GamePowerAction,
    config: GamePowerConfig,
) -> GamePowerActuation | None:
    """Translate a V7 gpu-priority decision into the absolute actuation model.

    Used by the target-balance ``NO_TARGET``/``NO_GAME`` fallback so V7 behavior
    flows through the same restore/apply path as every other target-balance
    write.
    """

    if action == GamePowerAction.GPU_PRIORITY_EPP:
        return GamePowerActuation(pcore_epp=config.epp, ecore_epp=config.epp)
    if action == GamePowerAction.GPU_PRIORITY_CPU_CAP:
        return GamePowerActuation(
            pcore_epp=config.epp,
            ecore_epp=config.epp,
            pcore_max_khz=config.pcore_max_khz,
            ecore_max_khz=config.ecore_max_khz,
        )
    return None


# ---------------------------------------------------------------------------
# V9 verdict ledger (design section 8): the authoritative unlock for gated
# write lanes. Read-only for the daemon, fail-closed on any problem.
# ---------------------------------------------------------------------------
GAME_POWER_POLICY_VERSION_V9 = "game-power-target-balance-v9"
VERDICT_TDP_BUCKETS_W = (12, 17, 22, 30)
VERDICT_TDP_TOLERANCE_W = 2


def topology_fingerprint(policies: Iterable[CpuPolicy]) -> str:
    """Deterministic fingerprint from sorted (cpu, capacity, max_khz) + layout.

    Example shape ``4p4e-nosmt-<sha256[:8]>`` (design section 8).
    """

    cpu_tuples: list[tuple[int, int | None, int | None]] = []
    pcore = 0
    ecore = 0
    smt = False
    for policy in sorted(
        policies,
        key=lambda p: min(p.affected_cpus) if p.affected_cpus else -1,
    ):
        if len(policy.affected_cpus) > 1:
            smt = True
        # C15: hash the immutable ceiling; fall back to scaling_max_freq only
        # when cpuinfo_max_freq is unavailable (prefer consistency).
        max_freq = (
            policy.cpuinfo_max_freq
            if policy.cpuinfo_max_freq is not None
            else policy.scaling_max_freq
        )
        for cpu in sorted(policy.affected_cpus):
            cpu_tuples.append((cpu, policy.capacity, max_freq))
        if policy.policy_class == CpuPolicyClass.PCORE:
            pcore += 1
        elif policy.policy_class == CpuPolicyClass.ECORE:
            ecore += 1
    payload = json.dumps(
        {"cpus": cpu_tuples, "pcore": pcore, "ecore": ecore},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
    return f"{pcore}p{ecore}e-{'smt' if smt else 'nosmt'}-{digest}"


def verdict_tdp_bucket(pl1_w: int | float | None) -> int | None:
    """Nearest of 12/17/22/30 W within +-2 W, else ``None`` (design section 8)."""

    if pl1_w is None or not math.isfinite(pl1_w):
        return None
    nearest = min(VERDICT_TDP_BUCKETS_W, key=lambda bucket: abs(bucket - pl1_w))
    if abs(nearest - pl1_w) > VERDICT_TDP_TOLERANCE_W:
        return None
    return nearest


def read_kernel_release(proc_root: str | Path = "/proc") -> str:
    return _read_text(Path(proc_root) / "sys" / "kernel" / "osrelease")


@dataclass(frozen=True)
class GamePowerVerdictEnv:
    topology_fingerprint: str
    kernel: str
    policy_version: str = GAME_POWER_POLICY_VERSION_V9


class GamePowerVerdictLedger:
    """Read-only verdict ledger with mtime reload and fail-closed lookups."""

    def __init__(
        self,
        path: str | Path,
        *,
        fallback_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.fallback_path = Path(fallback_path) if fallback_path is not None else None
        self._entries: tuple[dict[str, object], ...] = ()
        self._status = "unavailable"
        self._reason = "not-loaded"
        self._loaded_path: Path | None = None
        self._mtime: float | None = None
        self._load()

    def _active_path(self) -> Path | None:
        if self.path.exists():
            return self.path
        if self.fallback_path is not None and self.fallback_path.exists():
            return self.fallback_path
        return None

    def _load(self) -> None:
        active = self._active_path()
        if active is None:
            self._entries = ()
            self._status = "unavailable"
            self._reason = "missing"
            self._loaded_path = None
            self._mtime = None
            return
        try:
            payload = json.loads(active.read_text())
            self._mtime = active.stat().st_mtime
        except (OSError, ValueError):
            self._entries = ()
            self._status = "corrupt"
            self._reason = "unreadable-or-invalid-json"
            self._loaded_path = active
            return
        entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            self._entries = ()
            self._status = "corrupt"
            self._reason = "entries-not-a-list"
            self._loaded_path = active
            return
        better = tuple(
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("verdict") == "BETTER"
        )
        self._entries = better
        self._status = "ready"
        self._reason = "loaded"
        self._loaded_path = active

    def _maybe_reload(self) -> None:
        active = self._active_path()
        if active is None:
            if self._loaded_path is not None or self._status == "ready":
                self._load()
            return
        try:
            mtime = active.stat().st_mtime
        except OSError:
            self._load()
            return
        if active != self._loaded_path or mtime != self._mtime:
            self._load()

    def health(self) -> dict[str, object]:
        self._maybe_reload()
        return {
            "status": self._status,
            "reason": self._reason,
            "entry_count": len(self._entries),
            "path": str(self._loaded_path) if self._loaded_path is not None else None,
        }

    def lookup(
        self,
        *,
        appid: str | None,
        fps_target: float | None,
        pl1_w: int | float | None,
        actuator: str,
        env: GamePowerVerdictEnv,
    ) -> bool:
        self._maybe_reload()
        if self._status != "ready" or appid is None or fps_target is None:
            return False
        bucket = verdict_tdp_bucket(pl1_w)
        if bucket is None:
            return False
        target = round(float(fps_target), 3)
        for entry in self._entries:
            if str(entry.get("appid")) != str(appid):
                continue
            if entry.get("actuator") != actuator:
                continue
            entry_fps = _float_or_none(entry.get("fps_target"))
            if entry_fps is None or round(entry_fps, 3) != target:
                continue
            entry_tdp = entry.get("tdp_w")
            if not isinstance(entry_tdp, (int, float)) or int(entry_tdp) != bucket:
                continue
            if entry.get("topology_fingerprint") != env.topology_fingerprint:
                continue
            if entry.get("kernel") != env.kernel:
                continue
            if entry.get("policy_version") != env.policy_version:
                continue
            return True
        return False


class GamePowerController:
    def __init__(
        self,
        config: GamePowerConfig,
        *,
        hint: GamePowerHintEntry | None = None,
        verdict_ledger: GamePowerVerdictLedger | None = None,
        verdict_env: GamePowerVerdictEnv | None = None,
    ) -> None:
        self.config = config
        self.hint = hint
        self.verdict_ledger = verdict_ledger
        self.verdict_env = verdict_env
        self._positive_samples = 0
        self._negative_samples = 0
        self._active = False
        self._hint_used = False
        self._hint_disabled = False
        self._hint_contradiction_samples = 0
        self._hint_contradiction_total = 0
        self.last_positive: bool | None = None
        self._recent_positive: deque[bool] = deque(
            maxlen=max(0, config.rolling_window_samples)
        )
        # --- V9 target-balance state (phase machine + ladder) ---
        self._tick = 0
        self._committed_phase = GamePowerPhase.NO_GAME
        self._pending_phase: GamePowerPhase | None = None
        self._pending_phase_count = 0
        self._loading_ticks = 0
        self._ladder_step = 0
        self._ladder_hold = 0
        self._ladder_backoff: dict[int, int] = {}
        # Unconstrained pacing baseline (p95 observed while the ladder is at
        # step 0). The absolute target*ratio guard alone is unusable: a healthy
        # 60 FPS scene routinely paces at p95 18-20 ms against a 16.67 ms
        # target, so the guard would breach before the ladder ever climbs.
        self._p95_baseline_window: deque[float] = deque(
            maxlen=max(1, config.ladder_p95_baseline_samples)
        )
        self._p95_baseline: float | None = None
        self._p95_baseline_appid: str | None = None
        self._ladder_breach = 0
        self._miss_confirmed = False
        self._deep_unlocked = False
        self._gpu_deep_unlocked = False
        self._last_actuation: GamePowerActuation | None = None
        self._current_sample: GamePowerSample | None = None
        # Previous tick's gated-lane state + active actuators so UNKNOWN can hold
        # them instead of flapping to blocked (design section 5; defect C4b).
        self._last_gated_lanes: dict[str, object] | None = None
        self._last_active_actuators: frozenset[str] = frozenset()

    def evaluate(self, sample: GamePowerSample) -> GamePowerDecision:
        classification = classify_game_power_sample(
            self.config,
            sample,
            controller_active=self._active,
            p95_budget_ms=self._satisfied_budget_ms(sample),
        )
        if self.config.mode == GamePowerMode.OFF:
            return GamePowerDecision(
                GamePowerAction.IDLE,
                "mode is off",
                classification=classification,
            )
        if self.config.mode == GamePowerMode.OBSERVE:
            return GamePowerDecision(
                GamePowerAction.OBSERVE_ONLY,
                "mode is observe",
                classification=classification,
            )
        if self.config.mode == GamePowerMode.TARGET_BALANCE:
            return self._evaluate_target_balance(sample, classification)
        return self._evaluate_gpu_priority(sample, classification)

    def _evaluate_gpu_priority(
        self,
        sample: GamePowerSample,
        classification: GamePowerClassification,
    ) -> GamePowerDecision:
        positive = self._sample_supports_gpu_priority(sample)
        self.last_positive = positive
        if self.config.rolling_window_samples > 0:
            self._recent_positive.append(positive)
        if positive:
            self._positive_samples += 1
            self._negative_samples = 0
        else:
            self._negative_samples += 1
            self._positive_samples = 0
        self._update_hint_contradiction(sample, positive)
        classification = self._with_rolling_evidence(classification)

        if self._active and self._negative_samples >= self.config.restore_samples:
            if not self._rolling_restore_ready():
                return GamePowerDecision(
                    GamePowerAction.OBSERVE_ONLY,
                    "waiting for rolling restore evidence",
                    classification=classification,
                )
            self._active = False
            return GamePowerDecision(
                GamePowerAction.RESTORE,
                "restore hysteresis reached",
                classification=classification,
            )

        if not positive and classification.primary == "fps-target-satisfied":
            return GamePowerDecision(
                GamePowerAction.OBSERVE_ONLY,
                "fps target satisfied",
                classification=classification,
            )

        activation_required = self._activation_required_samples()
        if self._positive_samples < activation_required:
            return GamePowerDecision(
                GamePowerAction.OBSERVE_ONLY,
                "waiting for activation hysteresis",
                classification=classification,
            )
        if not self._rolling_activation_ready(activation_required):
            return GamePowerDecision(
                GamePowerAction.OBSERVE_ONLY,
                "waiting for rolling activation evidence",
                classification=classification,
            )

        self._active = True
        if self._hint_available():
            self._hint_used = True
            classification = self._with_rolling_evidence(classification)
            reason = "validated hint reduced activation warmup"
        else:
            reason = "package limited with GPU activity"
        if self.config.cpu_cap_enabled and _sample_core_pressure_high(
            sample,
            self.config.cpu_cap_core_share_threshold,
        ):
            return GamePowerDecision(
                GamePowerAction.GPU_PRIORITY_CPU_CAP,
                "package limited with high core pressure",
                classification=classification,
            )
        return GamePowerDecision(
            GamePowerAction.GPU_PRIORITY_EPP,
            reason,
            classification=classification,
        )

    # ------------------------------------------------------------------
    # V9 target-balance mode
    # ------------------------------------------------------------------
    def _evaluate_target_balance(
        self,
        sample: GamePowerSample,
        classification: GamePowerClassification,
    ) -> GamePowerDecision:
        self._tick += 1
        self._current_sample = sample
        # Reset per-tick gated-lane state (populated by S4 phase dispatch).
        self._active_actuators = frozenset()
        self._gated_lanes = self._default_gated_lanes()
        self._verdict_health = self._verdict_ledger_health()
        raw_phase, reason_codes = classify_game_power_phase(
            self.config,
            sample,
            p95_budget_ms=self._satisfied_budget_ms(sample),
        )

        # C1: target-balance only governs the configured target AppID. A
        # different foreground game is NO_GAME posture: restore CPU actuation
        # and release the gated lanes so the ladder never runs on the wrong
        # game (gpu-priority enforces this predicate; target-balance must too).
        if (
            self.config.target_appid is not None
            and sample.appid is not None
            and sample.appid != self.config.target_appid
        ):
            self._committed_phase = GamePowerPhase.NO_GAME
            self._pending_phase = None
            self._pending_phase_count = 0
            self._loading_ticks = 0
            self._ladder_step = 0
            self._ladder_hold = 0
            self._active_actuators = frozenset()
            self._gated_lanes = self._released_gated_lanes("non-target-game")
            return self._finalize_target_balance(
                GamePowerAction.RESTORE,
                "non-target game restore",
                classification,
                GamePowerPhase.NO_GAME,
                ("non-target-game",),
                None,
            )

        phase = self._commit_phase(raw_phase)
        satisfied = self._target_satisfied(sample)
        # Shared miss streak over the same predicate the ladder releases on
        # (target miss OR pacing regression). Leaving the at-target band still
        # releases the ladder immediately (boost fast), but the anti-oscillation
        # backoff is only earned by a *confirmed* miss -- otherwise one noisy
        # 2 s window locks a rung out for the whole backoff window.
        self._update_p95_baseline(sample, self._sample_p95_ms(sample))
        if satisfied and self._sample_p95_ok(sample):
            self._ladder_breach = 0
        else:
            self._ladder_breach += 1
        miss_confirmed = self._ladder_breach >= max(
            1, self.config.ladder_release_samples
        )
        self._miss_confirmed = miss_confirmed

        if phase == GamePowerPhase.LOADING:
            self._loading_ticks += 1
        else:
            self._loading_ticks = 0
        if phase not in (
            GamePowerPhase.AT_TARGET,
            GamePowerPhase.ABOVE_TARGET,
            GamePowerPhase.UNKNOWN,
        ):
            # Leaving the at/above band abandons the ladder position; re-entry
            # always restarts from S0 (backoff timers persist for the session).
            # C3: on a real target miss (below-*), record backoff for the failed
            # step BEFORE resetting so the anti-oscillation lock is reachable.
            # The test is on current state, not on the exit transition: with
            # release hysteresis the miss is usually confirmed a tick or more
            # AFTER the band was left, by which point this is no longer the exit
            # tick. UNKNOWN is handled in its own branch (C4a).
            if (
                self._ladder_step > 0
                and phase
                in (
                    GamePowerPhase.BELOW_TARGET_CPU_BOUND,
                    GamePowerPhase.BELOW_TARGET_GPU_BOUND,
                )
                and miss_confirmed
            ):
                self._ladder_backoff[self._ladder_step] = (
                    self._tick + self._backoff_samples()
                )
                reason_codes = reason_codes + ("ladder-target-miss",)
            # An unconfirmed blip still hands power back this tick (the
            # below-target/unknown handlers own the actuation while we are out
            # of the band), but the ladder keeps its position so returning to
            # target resumes the rung instead of re-climbing from zero. A
            # confirmed miss drops everything.
            if miss_confirmed:
                self._ladder_step = 0
            self._ladder_hold = 0

        if phase == GamePowerPhase.UNKNOWN:
            return self._target_balance_unknown(
                sample, classification, phase, reason_codes, satisfied
            )

        if phase in (GamePowerPhase.NO_GAME, GamePowerPhase.NO_TARGET):
            base = self._evaluate_gpu_priority(sample, classification)
            actuation = _actuation_for_gpu_priority_action(base.action, self.config)
            return self._finalize_target_balance(
                base.action, base.reason, classification, phase, reason_codes, actuation
            )

        if phase == GamePowerPhase.LOADING:
            return self._target_balance_loading(classification, phase, reason_codes)

        if phase == GamePowerPhase.BELOW_TARGET_CPU_BOUND:
            actuation = GamePowerActuation(
                pcore_epp=self.config.below_target_cpu_pcore_epp,
                ecore_epp=self.config.below_target_cpu_ecore_epp,
            )
            # Gated lanes (a) foreground cpu.uclamp.min and (b) background
            # shaping are allowed here (design section 5); each activates only
            # with a matching BETTER verdict entry.
            self._apply_gated_lanes(
                sample,
                foreground_uclamp=True,
                background_shaping=True,
            )
            return self._finalize_target_balance(
                GamePowerAction.TARGET_BALANCE_TRIM,
                "below-target cpu-bound boost",
                classification,
                phase,
                reason_codes,
                actuation,
            )

        if phase == GamePowerPhase.BELOW_TARGET_GPU_BOUND:
            # Gated lane (b) background shaping is allowed while below-target;
            # foreground uclamp.min is not (design section 5).
            self._apply_gated_lanes(
                sample,
                foreground_uclamp=False,
                background_shaping=True,
            )
            if self.config.cpu_cap_enabled and _sample_core_pressure_high(
                sample, self.config.cpu_cap_core_share_threshold
            ):
                actuation = GamePowerActuation(
                    pcore_epp=self.config.epp,
                    ecore_epp=self.config.epp,
                    pcore_max_khz=self.config.pcore_max_khz,
                    ecore_max_khz=self.config.ecore_max_khz,
                )
                action = GamePowerAction.GPU_PRIORITY_CPU_CAP
            else:
                actuation = GamePowerActuation(
                    pcore_epp=self.config.epp, ecore_epp=self.config.epp
                )
                action = GamePowerAction.GPU_PRIORITY_EPP
            return self._finalize_target_balance(
                action,
                "below-target gpu-bound",
                classification,
                phase,
                reason_codes,
                actuation,
            )

        if phase in (GamePowerPhase.AT_TARGET, GamePowerPhase.ABOVE_TARGET):
            return self._target_balance_ladder(
                sample, classification, phase, reason_codes
            )

        # UNKNOWN handled above via _target_balance_unknown.
        raise AssertionError("unreachable target-balance phase dispatch")

    def _target_balance_unknown(
        self,
        sample: GamePowerSample,
        classification: GamePowerClassification,
        phase: GamePowerPhase,
        reason_codes: tuple[str, ...],
        satisfied: bool,
    ) -> GamePowerDecision:
        # C4b: preserve the previous tick's gated-lane state and active
        # actuators during UNKNOWN so one UNKNOWN tick cannot restore-then-reapply
        # the background-shaping cgroup writes (systemctl churn). Lanes release
        # only on NO_GAME/LOADING/non-target/mode-change/fail-closed.
        held_lanes = (
            self._last_gated_lanes
            if self._last_gated_lanes is not None
            else self._default_gated_lanes()
        )
        self._gated_lanes = held_lanes
        self._active_actuators = self._last_active_actuators
        target = sample.frame_target
        target_known = target is not None and target.fps_target is not None
        if target_known and not satisfied and self._miss_confirmed:
            # C4a: below target with no bound signature. Holding a deep trim
            # forever has no escape, so release the ladder (restore CPU state),
            # record backoff for the held step, and report the release honestly.
            held_step = self._ladder_step
            if held_step > 0:
                self._ladder_backoff[held_step] = self._tick + self._backoff_samples()
            self._ladder_step = 0
            self._ladder_hold = 0
            return self._finalize_target_balance(
                GamePowerAction.TARGET_BALANCE_RELEASE,
                "unknown below-target release",
                classification,
                phase,
                reason_codes + ("unknown-below-target-release",),
                None,
            )
        # No target (or satisfied): hold current writes, take no new action.
        return self._finalize_target_balance(
            GamePowerAction.OBSERVE_ONLY,
            "unknown phase hold",
            classification,
            phase,
            reason_codes,
            self._last_actuation,
        )

    def _commit_phase(self, raw_phase: GamePowerPhase) -> GamePowerPhase:
        committed = self._committed_phase
        if raw_phase == committed:
            self._pending_phase = None
            self._pending_phase_count = 0
            return committed
        # C7: exiting LOADING requires a stable cadence at >= ratio*target for
        # loading_exit_samples consecutive samples (design section 4). A sample
        # below the cadence ratio does not accumulate toward the exit count.
        if (
            committed == GamePowerPhase.LOADING
            and not self._loading_exit_sample_qualifies()
        ):
            self._pending_phase = raw_phase
            self._pending_phase_count = 0
            return committed
        if self._pending_phase == raw_phase:
            self._pending_phase_count += 1
        else:
            self._pending_phase = raw_phase
            self._pending_phase_count = 1
        required = self._phase_commit_samples(committed, raw_phase)
        if self._pending_phase_count >= required:
            self._committed_phase = raw_phase
            self._pending_phase = None
            self._pending_phase_count = 0
        return self._committed_phase

    def _loading_exit_sample_qualifies(self) -> bool:
        sample = self._current_sample
        if sample is None:
            return True
        target = sample.frame_target
        fps_target = target.fps_target if target is not None else None
        if fps_target is None:
            # No target: keep the plain consecutive-sample-count exit behavior.
            return True
        performance = sample.frame_performance
        avg_fps = performance.avg_fps if performance is not None else None
        if avg_fps is None:
            return False
        return avg_fps >= self.config.loading_exit_fps_ratio * fps_target

    def _phase_commit_samples(
        self, committed: GamePowerPhase, raw_phase: GamePowerPhase
    ) -> int:
        # Asymmetric hysteresis (design section 4): fast to give power back,
        # slow to take it away.
        if raw_phase == GamePowerPhase.LOADING:
            return 1
        if committed == GamePowerPhase.LOADING:
            return max(1, self.config.loading_exit_samples)
        if committed in (
            GamePowerPhase.AT_TARGET,
            GamePowerPhase.ABOVE_TARGET,
        ) and raw_phase not in (
            GamePowerPhase.AT_TARGET,
            GamePowerPhase.ABOVE_TARGET,
        ):
            return 1
        return max(1, self.config.phase_stable_samples)

    def _target_balance_loading(
        self,
        classification: GamePowerClassification,
        phase: GamePowerPhase,
        reason_codes: tuple[str, ...],
    ) -> GamePowerDecision:
        # LOADING releases ALL V9 constraints, including gated lanes (section 5).
        self._active_actuators = frozenset()
        self._gated_lanes = self._released_gated_lanes()
        elapsed_s = self._loading_ticks * max(0.0, self.config.poll_s)
        if elapsed_s > self.config.loading_boost_max_s:
            return self._finalize_target_balance(
                GamePowerAction.OBSERVE_ONLY,
                "loading boost budget exhausted",
                classification,
                phase,
                reason_codes + ("loading-budget-exhausted",),
                None,
            )
        actuation = GamePowerActuation(
            pcore_epp=self.config.loading_pcore_epp,
            ecore_epp=self.config.loading_ecore_epp,
        )
        return self._finalize_target_balance(
            GamePowerAction.LOADING_BOOST,
            "loading boost",
            classification,
            phase,
            reason_codes,
            actuation,
        )

    def _target_balance_ladder(
        self,
        sample: GamePowerSample,
        classification: GamePowerClassification,
        phase: GamePowerPhase,
        reason_codes: tuple[str, ...],
    ) -> GamePowerDecision:
        target = sample.frame_target
        performance = sample.frame_performance
        target_frame_ms = target.target_frame_ms if target is not None else None
        p95 = performance.p95_frame_ms if performance is not None else None
        # Baseline + streak are maintained once per tick by the dispatcher.
        p95_budget_ms = self._p95_budget_ms(target_frame_ms)
        p95_ok = p95_budget_ms is not None and p95 is not None and p95 <= p95_budget_ms
        satisfied = self._target_satisfied(sample)

        # Gated lane (b) background shaping is allowed at/above target; the deep
        # CPU-frequency-cap rungs need a matching verdict (or profiler flag).
        self._apply_gated_lanes(
            sample, foreground_uclamp=False, background_shaping=True
        )
        not_ac_perf = self.config.persona != GamePowerPersona.AC_PERFORMANCE
        deep_active = not_ac_perf and (
            self.config.allow_ladder_step_5
            or self._verdict_active(_DEEP_CPU_CAP_VERDICT, sample)
        )
        # Deep GPU cap rung (G4CAP) is unlocked by a matching gpu-cap verdict.
        gpu_deep_active = not_ac_perf and self._verdict_active(
            _DEEP_GPU_CAP_VERDICT, sample
        )
        self._deep_unlocked = deep_active
        self._gpu_deep_unlocked = gpu_deep_active
        base_len = len(self._filtered_base_rungs())
        deep_len = 0
        if gpu_deep_active:
            deep_len += len(
                _filter_rungs(_DEEP_GPU_CAP_RUNGS, self.config.trim_rung_filter)
            )
        if deep_active:
            deep_len += len(
                _filter_rungs(_DEEP_CPU_CAP_RUNGS, self.config.trim_rung_filter)
            )
        max_step = base_len + deep_len
        if deep_active or gpu_deep_active:
            lanes = dict(self._gated_lanes or {})
            lanes["ladder_deep_step"] = {"state": "active", "reason_codes": []}
            self._gated_lanes = lanes

        # C5: if a deeper step was reached under a verdict that no longer matches
        # (e.g. TDP bucket changed), clamp down to the currently-allowed max step
        # immediately instead of continuing to apply the now-unlocked actuation.
        if self._ladder_step > max_step:
            self._ladder_step = max_step
            self._ladder_hold = 0
            codes = reason_codes + ("ladder-verdict-lock-lost",)
            return self._finalize_target_balance(
                GamePowerAction.TARGET_BALANCE_TRIM
                if max_step > 0
                else GamePowerAction.TARGET_BALANCE_RELEASE,
                f"ladder verdict lock lost, clamp to step {max_step}",
                classification,
                phase,
                codes,
                self._ladder_actuation(max_step),
            )

        if not satisfied or not p95_ok:
            breach_code = "ladder-p95-breach" if satisfied else "ladder-target-miss"
            if not self._miss_confirmed:
                # Hold the current rungs while the breach is unconfirmed: do not
                # climb, do not release, do not burn a backoff on noise.
                self._ladder_hold = 0
                return self._finalize_target_balance(
                    GamePowerAction.TARGET_BALANCE_TRIM
                    if self._ladder_step > 0
                    else GamePowerAction.TARGET_BALANCE_RELEASE,
                    f"ladder breach {self._ladder_breach} unconfirmed, holding step "
                    f"{self._ladder_step}",
                    classification,
                    phase,
                    reason_codes + (breach_code, "ladder-breach-unconfirmed"),
                    self._ladder_actuation(self._ladder_step),
                )
            # Fast release (contract 1.4): drop ALL rungs at once, lock the failed
            # step for backoff, and re-climb per the hold rules.
            failed_step = self._ladder_step
            if failed_step > 0:
                self._ladder_backoff[failed_step] = self._tick + self._backoff_samples()
            self._ladder_step = 0
            self._ladder_hold = 0
            codes = reason_codes + (breach_code,)
            return self._finalize_target_balance(
                GamePowerAction.TARGET_BALANCE_RELEASE,
                "ladder fast release to step 0",
                classification,
                phase,
                codes,
                self._ladder_actuation(0),
            )

        # Qualifying tick.
        self._ladder_hold += 1
        hold_required = self._ladder_hold_required(phase)
        next_step = self._ladder_step + 1
        if self._ladder_hold >= hold_required and next_step <= max_step:
            if self._backoff_active(next_step):
                codes = reason_codes + ("ladder-backoff-active",)
                return self._finalize_target_balance(
                    GamePowerAction.TARGET_BALANCE_TRIM
                    if self._ladder_step > 0
                    else GamePowerAction.OBSERVE_ONLY,
                    f"ladder hold at step {self._ladder_step} (backoff)",
                    classification,
                    phase,
                    codes,
                    self._ladder_actuation(self._ladder_step),
                )
            self._ladder_step = next_step
            self._ladder_hold = 0
            return self._finalize_target_balance(
                GamePowerAction.TARGET_BALANCE_TRIM,
                f"ladder step up to step {next_step}",
                classification,
                phase,
                reason_codes,
                self._ladder_actuation(next_step),
            )

        if self._ladder_hold >= hold_required and next_step > max_step:
            # S5+ inert until a verdict-ledger entry exists (design section 7).
            codes = reason_codes + ("no-verdict-for-context",)
            return self._finalize_target_balance(
                GamePowerAction.TARGET_BALANCE_TRIM,
                f"ladder hold at step {self._ladder_step} (locked)",
                classification,
                phase,
                codes,
                self._ladder_actuation(self._ladder_step),
            )

        action = (
            GamePowerAction.TARGET_BALANCE_TRIM
            if self._ladder_step > 0
            else GamePowerAction.OBSERVE_ONLY
        )
        return self._finalize_target_balance(
            action,
            f"ladder hold at step {self._ladder_step}",
            classification,
            phase,
            reason_codes,
            self._ladder_actuation(self._ladder_step),
        )

    def _ladder_hold_required(self, phase: GamePowerPhase) -> int:
        base = max(1, self.config.ladder_hold_samples)
        if phase == GamePowerPhase.ABOVE_TARGET:
            return max(1, base // 2)
        return base

    def _backoff_samples(self) -> int:
        poll = max(0.0, self.config.poll_s)
        if poll <= 0:
            return 1
        return max(1, math.ceil(self.config.ladder_backoff_s / poll))

    def _backoff_active(self, step: int) -> bool:
        return self._ladder_backoff.get(step, 0) > self._tick

    def _update_p95_baseline(self, sample: GamePowerSample, p95: float | None) -> None:
        """Learn this scene's pacing while nothing of ours is constraining it.

        Only samples taken at ladder step 0 with no boost active count, so the
        baseline describes the game, not our own trims. It resets per appid.
        """
        if sample.appid != self._p95_baseline_appid:
            self._p95_baseline_appid = sample.appid
            self._p95_baseline_window.clear()
            self._p95_baseline = None
        if p95 is None or not math.isfinite(p95) or p95 <= 0:
            return
        # Step 0 only: anything above it is our own trim, not the scene. The
        # median over the window absorbs the odd spike sample.
        if self._ladder_step != 0:
            return
        self._p95_baseline_window.append(p95)
        if len(self._p95_baseline_window) < self._p95_baseline_window.maxlen:
            return
        ordered = sorted(self._p95_baseline_window)
        self._p95_baseline = ordered[len(ordered) // 2]

    def _pacing_budget_ms(
        self, target_frame_ms: float | None, ratio: float
    ) -> float | None:
        if target_frame_ms is None:
            return None
        budget = target_frame_ms * ratio
        if self._p95_baseline is not None:
            budget = max(
                budget, self._p95_baseline * self.config.ladder_p95_regression_ratio
            )
        return budget

    def _p95_budget_ms(self, target_frame_ms: float | None) -> float | None:
        return self._pacing_budget_ms(target_frame_ms, self._p95_guard_ratio())

    @staticmethod
    def _sample_p95_ms(sample: GamePowerSample) -> float | None:
        performance = sample.frame_performance
        return performance.p95_frame_ms if performance is not None else None

    def _sample_p95_ok(self, sample: GamePowerSample) -> bool:
        target = sample.frame_target
        target_frame_ms = target.target_frame_ms if target is not None else None
        budget = self._p95_budget_ms(target_frame_ms)
        p95 = self._sample_p95_ms(sample)
        return budget is not None and p95 is not None and p95 <= budget

    def _satisfied_budget_ms(self, sample: GamePowerSample) -> float | None:
        target = sample.frame_target
        target_frame_ms = target.target_frame_ms if target is not None else None
        return self._pacing_budget_ms(
            target_frame_ms, self.config.fps_target_satisfied_p95_ratio
        )

    def _target_satisfied(self, sample: GamePowerSample) -> bool:
        """``_sample_fps_target_satisfied`` against the baseline-aware budget."""
        return _sample_fps_target_satisfied(
            self.config, sample, p95_budget_ms=self._satisfied_budget_ms(sample)
        )

    def _p95_guard_ratio(self) -> float:
        if self.config.persona == GamePowerPersona.AC_QUIET:
            return self.config.ac_quiet_p95_guard_ratio
        return self.config.ladder_p95_guard_ratio

    def _filtered_base_rungs(self) -> tuple[str, ...]:
        return _filter_rungs(
            _persona_base_rungs(self.config.persona),
            self.config.trim_rung_filter,
        )

    def _rung_sequence(self) -> tuple[str, ...]:
        base = self._filtered_base_rungs()
        if self.config.persona == GamePowerPersona.AC_PERFORMANCE:
            return base
        suffix: tuple[str, ...] = ()
        if self._gpu_deep_unlocked:
            suffix += _filter_rungs(_DEEP_GPU_CAP_RUNGS, self.config.trim_rung_filter)
        if self._deep_unlocked:
            suffix += _filter_rungs(_DEEP_CPU_CAP_RUNGS, self.config.trim_rung_filter)
        return base + suffix

    def _active_rungs(self, step: int) -> tuple[str, ...]:
        if step <= 0:
            return ()
        return self._rung_sequence()[:step]

    def _ladder_actuation(self, step: int) -> GamePowerActuation | None:
        """Fold the persona rung sequence[:step] into one absolute actuation.

        The GPU cap, soft-PL1 overlay and EPP are each the *deepest* active rung
        of their kind, so a step change is realised as restore-to-baseline plus
        this absolute state (correct by construction, contract 1.4).
        """

        active = self._active_rungs(step)
        if not active:
            return None
        sample = self._current_sample
        slider_w = sample.pl1_w if sample is not None else None
        median = sample.package_median_w if sample is not None else None
        if median is None and sample is not None and sample.rapl is not None:
            median = sample.rapl.package_w

        gpu_max_ratio = self._rung_gpu_max_ratio(active)
        soft_pl1_w = self._rung_soft_pl1_w(active, median, slider_w)
        pcore_epp: str | None = None
        ecore_epp: str | None = None
        pcore_max_khz: int | None = None
        ecore_max_khz: int | None = None
        if "C1" in active:
            ecore_epp = self.config.trim_ecore_epp
        if "C2" in active:
            pcore_epp = self.config.trim_pcore_epp
            ecore_epp = self.config.trim_ecore_epp
        if "S3CAP" in active:
            pcore_epp = pcore_epp or self.config.ladder_pcore_epp
            ecore_epp = ecore_epp or self.config.ladder_ecore_epp
            pcore_max_khz = self.config.ladder_s3_pcore_max_khz
        if "S4CAP" in active:
            pcore_epp = pcore_epp or self.config.ladder_pcore_epp
            ecore_epp = ecore_epp or self.config.ladder_ecore_epp
            pcore_max_khz = self.config.ladder_s4_pcore_max_khz
            ecore_max_khz = self.config.ladder_s4_ecore_max_khz
        if (
            pcore_epp is None
            and ecore_epp is None
            and pcore_max_khz is None
            and ecore_max_khz is None
            and gpu_max_ratio is None
            and soft_pl1_w is None
        ):
            return None
        return GamePowerActuation(
            pcore_epp=pcore_epp,
            ecore_epp=ecore_epp,
            pcore_max_khz=pcore_max_khz,
            ecore_max_khz=ecore_max_khz,
            gpu_max_ratio=gpu_max_ratio,
            soft_pl1_w=soft_pl1_w,
        )

    def _rung_gpu_max_ratio(self, active: tuple[str, ...]) -> float | None:
        # D6: the deepest active G-rung yields a cap RATIO of rp0; the actuator
        # applies it per GT from each GT's own rp0 (render gt0 and media gt1 are
        # trimmed proportionally, not collapsed to the smaller GT's absolute cap).
        ratio: float | None = None
        if "G1" in active:
            ratio = self.config.gpu_cap_g1_ratio
        if "G2" in active:
            ratio = self.config.gpu_cap_g2_ratio
        if "G3" in active:
            ratio = self.config.gpu_cap_g3_ratio
        if "G4CAP" in active:
            ratio = self.config.gpu_cap_g4_ratio
        return ratio

    def _rung_soft_pl1_w(
        self,
        active: tuple[str, ...],
        median_w: float | None,
        slider_w: int | None,
    ) -> int | None:
        level = 0
        if "P1" in active:
            level = 1
        if "P2" in active:
            level = 2
        if "P3" in active:
            level = 3
        if level == 0 or median_w is None:
            return None
        # D2: P1 must start BELOW the user slider. The shipped
        # ceil(median + headroom) sat >= slider on a PL1-pinned scene, so the
        # min(user_slider, soft_pl1) overlay clamped to a no-op. Anchor P1 at
        # min(slider - slider_margin, ceil(median) + headroom) instead.
        demand = math.ceil(median_w) + self.config.soft_pl1_p1_headroom_w
        if slider_w is not None:
            p1 = min(float(slider_w) - self.config.soft_pl1_p1_slider_margin_w, demand)
        else:
            p1 = demand
        if level == 1:
            value = p1
        elif level == 2:
            value = p1 - self.config.soft_pl1_p2_step_w
        else:
            value = p1 - self.config.soft_pl1_p3_step_w
        return max(self.config.soft_pl1_floor_w, int(math.ceil(value)))

    def _finalize_target_balance(
        self,
        action: GamePowerAction,
        reason: str,
        classification: GamePowerClassification,
        phase: GamePowerPhase,
        reason_codes: tuple[str, ...],
        actuation: GamePowerActuation | None,
    ) -> GamePowerDecision:
        self.last_positive = actuation is not None
        self._last_actuation = actuation
        self._last_gated_lanes = self._gated_lanes
        self._last_active_actuators = self._active_actuators
        gpu_caps = None
        if actuation is not None and (
            actuation.gpu_max_mhz is not None
            or actuation.gpu_min_mhz is not None
            or actuation.gpu_max_ratio is not None
        ):
            # Controller-side (pre-apply) telemetry: report the render-GT (gt0)
            # cap the ratio implies against the render rp0. The paired min is
            # data-dependent (the actuator lowers a latched-high min per GT), so
            # it stays null here; the governor overrides this with the real
            # per-GT applied values (incl. the ``per_gt`` breakdown) after apply.
            render_max = actuation.gpu_max_mhz
            if actuation.gpu_max_ratio is not None:
                render_rp0 = (
                    self._current_sample.gpu_rp0_mhz
                    if self._current_sample is not None
                    else None
                )
                if render_rp0 is not None:
                    render_max = max(1, int(render_rp0 * (1.0 - actuation.gpu_max_ratio)))
            gpu_caps = {
                "min_mhz": actuation.gpu_min_mhz,
                "max_mhz": render_max,
            }
        return GamePowerDecision(
            action,
            reason,
            classification=classification,
            phase=phase,
            phase_reason_codes=tuple(reason_codes),
            ladder_step=self._ladder_step,
            actuation=actuation,
            color_ledger=self._build_color_ledger_json(),
            verdict_ledger_health=self._verdict_health,
            gated_lanes=self._gated_lanes,
            persona=self.config.persona.value,
            soft_pl1_w=actuation.soft_pl1_w if actuation is not None else None,
            gpu_freq_caps=gpu_caps,
            trim_rungs_active=list(self._active_rungs(self._ladder_step)),
            frame_feed_status=(
                self._current_sample.frame_feed_status
                if self._current_sample is not None
                else None
            ),
            limiter_state="unknown",
            p95_baseline_ms=_round_or_none(self._p95_baseline),
            p95_budget_ms=_round_or_none(
                self._p95_budget_ms(
                    self._current_sample.frame_target.target_frame_ms
                    if self._current_sample is not None
                    and self._current_sample.frame_target is not None
                    else None
                )
            ),
        )

    def _build_color_ledger_json(self) -> dict[str, object] | None:
        sample = self._current_sample
        if sample is None or sample.color_ledger_entries is None:
            return None
        resolved = resolve_ledger_actuators(
            sample.color_ledger_entries,
            active_actuators=self._active_actuators,
        )
        return {
            "truncated": bool(sample.color_ledger_truncated),
            "entries": [entry.to_json() for entry in resolved],
        }

    def _default_gated_lanes(self) -> dict[str, object]:
        return {
            "foreground_uclamp_min": {"state": "blocked", "reason_codes": []},
            "background_shaping": {"state": "blocked", "reason_codes": []},
            "ladder_deep_step": {"state": "blocked", "reason_codes": []},
        }

    def _verdict_ledger_health(self) -> dict[str, object]:
        if self.verdict_ledger is None:
            return {"status": "unavailable", "reason": "no-verdict-ledger"}
        return self.verdict_ledger.health()

    def _verdict_active(self, actuator: str, sample: GamePowerSample) -> bool:
        if self.verdict_ledger is None or self.verdict_env is None:
            return False
        fps_target = (
            sample.frame_target.fps_target if sample.frame_target is not None else None
        )
        return self.verdict_ledger.lookup(
            appid=sample.appid,
            fps_target=fps_target,
            pl1_w=sample.pl1_w,
            actuator=actuator,
            env=self.verdict_env,
        )

    _BG_VARIANTS = (("bg-weight", "cpu-weight-80"), ("bg-uclamp", "uclamp-max-85"))

    def _apply_gated_lanes(
        self,
        sample: GamePowerSample,
        *,
        foreground_uclamp: bool,
        background_shaping: bool,
    ) -> None:
        """Resolve the gated lanes for this phase from the verdict ledger.

        Populates ``self._active_actuators`` (for the color ledger) and
        ``self._gated_lanes`` (for the governor's cgroup writes). A lane stays
        ``blocked`` with ``no-verdict-for-context`` unless a BETTER verdict entry
        matches the full context key.
        """

        active: set[str] = set()
        lanes = self._default_gated_lanes()
        if foreground_uclamp and self._verdict_active("uclamp-min", sample):
            active.add("uclamp-min")
            lanes["foreground_uclamp_min"] = {"state": "active", "reason_codes": []}
        if background_shaping:
            # C11: the lane/color-ledger actuator labels must reflect exactly the
            # unlocked variant(s), not a hardcoded ``bg-weight`` whenever any
            # background verdict matched.
            matched = [
                (actuator, variant)
                for actuator, variant in self._BG_VARIANTS
                if self._verdict_active(actuator, sample)
            ]
            if matched:
                for actuator, _variant in matched:
                    active.add(actuator)
                lanes["background_shaping"] = {
                    "state": "active",
                    "reason_codes": [],
                    "variants": [variant for _actuator, variant in matched],
                }
        self._active_actuators = frozenset(active)
        self._gated_lanes = lanes

    def _released_gated_lanes(
        self, reason: str = "loading-release"
    ) -> dict[str, object]:
        released = {"state": "released", "reason_codes": [reason]}
        return {
            "foreground_uclamp_min": dict(released),
            "background_shaping": dict(released),
            "ladder_deep_step": dict(released),
        }

    @property
    def committed_phase(self) -> GamePowerPhase:
        return self._committed_phase

    @property
    def ladder_step(self) -> int:
        return self._ladder_step

    def _sample_supports_gpu_priority(self, sample: GamePowerSample) -> bool:
        if self._target_satisfied(sample):
            return False
        if sample.appid is None:
            return False
        if self.config.target_appid is not None and sample.appid != self.config.target_appid:
            return False
        if sample.rapl is None or sample.pl1_w is None or sample.rapl.package_w is None:
            return False
        if sample.rapl.package_w < self.config.package_pressure_ratio * sample.pl1_w:
            return False
        uncore_share = sample.rapl.uncore_share
        render_busy = sample.fdinfo_busy.get("render")
        has_gpu_activity = (
            uncore_share is not None and uncore_share >= self.config.uncore_share_threshold
        ) or (render_busy is not None and render_busy >= self.config.render_busy_threshold)
        return has_gpu_activity

    def _rolling_activation_ready(self, activation_required: int) -> bool:
        if self.config.rolling_window_samples <= 1:
            return True
        if len(self._recent_positive) < activation_required:
            return False
        positive = sum(1 for value in self._recent_positive if value)
        return positive > len(self._recent_positive) / 2

    def _rolling_restore_ready(self) -> bool:
        if self.config.rolling_window_samples <= 1:
            return True
        if len(self._recent_positive) < self.config.restore_samples:
            return False
        negative = sum(1 for value in self._recent_positive if not value)
        return negative > len(self._recent_positive) / 2

    def _with_rolling_evidence(
        self,
        classification: GamePowerClassification,
    ) -> GamePowerClassification:
        positive = sum(1 for value in self._recent_positive if value)
        total = len(self._recent_positive)
        negative = total - positive
        activation_required = self._activation_required_samples()
        evidence = {
            **classification.evidence,
            "rolling_window_samples": self.config.rolling_window_samples,
            "rolling_positive_samples": positive,
            "rolling_negative_samples": negative,
            "rolling_positive_ratio": _round_or_none(positive / total if total else None),
            "rolling_ready": (
                True
                if self.config.rolling_window_samples <= 1
                else total >= activation_required
            ),
            "activation_required_samples": activation_required,
            "hint_key": self.hint.key if self.hint is not None else None,
            "hint_confidence": self.hint.confidence if self.hint is not None else None,
            "hint_used": self._hint_used,
            "hint_disabled": self._hint_disabled,
            "hint_contradiction_samples": self._hint_contradiction_total,
            "hint_reason": (
                "validated hint reduced activation warmup"
                if self._hint_available()
                else None
            ),
            "runtime_unaware": self.hint.runtime_unaware if self.hint is not None else None,
        }
        return replace(classification, evidence=_compact_evidence(evidence))

    def _hint_available(self) -> bool:
        return self.hint is not None and not self._hint_disabled

    def _update_hint_contradiction(self, sample: GamePowerSample, positive: bool) -> None:
        if not self._hint_available() or sample.appid != self.hint.context.appid:
            return
        if positive:
            self._hint_contradiction_samples = 0
            return
        self._hint_contradiction_samples += 1
        self._hint_contradiction_total += 1
        threshold = max(1, self.config.session_hint_contradiction_samples)
        if self._hint_contradiction_samples >= threshold:
            self._hint_disabled = True

    def _activation_required_samples(self) -> int:
        if self._hint_available():
            return max(1, self.config.hinted_activate_samples)
        return max(1, self.config.activate_samples)

    @property
    def hint_was_used(self) -> bool:
        return self._hint_used

    @property
    def hint_disabled(self) -> bool:
        return self._hint_disabled

    @property
    def hint_contradiction_samples(self) -> int:
        return self._hint_contradiction_total


def _sample_core_pressure_high(sample: GamePowerSample, threshold: float) -> bool:
    return (
        sample.rapl is not None
        and sample.rapl.core_share is not None
        and sample.rapl.core_share >= threshold
    )


class FastBoostLane:
    """Fast boost lane state machine (contract 1.5).

    Boost is unconditional (no verdict gate) because it only removes our own
    reductions. It fires on a frame-time spike, a foreground CPU-PSI jump between
    fast samples, or the LOADING phase, and holds ``hold_s`` past the last
    trigger. Pure logic with an injected clock so the governor can drive it from
    the single-threaded sub-tick loop and tests can exercise it directly.
    """

    def __init__(
        self,
        hold_s: float,
        *,
        spike_boost_ratio: float,
        psi_boost_delta: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hold_s = float(hold_s)
        self.spike_boost_ratio = float(spike_boost_ratio)
        self.psi_boost_delta = float(psi_boost_delta)
        self.clock = clock
        self._last_trigger_s: float | None = None
        self._prev_psi_avg10: float | None = None
        self.active = False
        self.reason: str | None = None

    def reset(self) -> None:
        self._last_trigger_s = None
        self._prev_psi_avg10 = None
        self.active = False
        self.reason = None

    def evaluate(
        self,
        *,
        target_frame_ms: float | None = None,
        spike_worst_ms: float | None = None,
        last_frame_ms: float | None = None,
        psi_avg10: float | None = None,
        phase_is_loading: bool = False,
    ) -> tuple[bool, str | None]:
        now = float(self.clock())
        bar = (
            self.spike_boost_ratio * target_frame_ms
            if target_frame_ms is not None and target_frame_ms > 0
            else None
        )
        reason: str | None = None
        if phase_is_loading:
            reason = "loading"
        elif bar is not None and (
            (spike_worst_ms is not None and spike_worst_ms > bar)
            or (last_frame_ms is not None and last_frame_ms > bar)
        ):
            reason = "frame-spike"
        elif (
            psi_avg10 is not None
            and self._prev_psi_avg10 is not None
            and psi_avg10 - self._prev_psi_avg10 > self.psi_boost_delta
        ):
            reason = "psi-jump"
        if psi_avg10 is not None:
            self._prev_psi_avg10 = psi_avg10
        if reason is not None:
            self._last_trigger_s = now
            self.active = True
            self.reason = reason
            return True, reason
        if self._last_trigger_s is not None and now - self._last_trigger_s <= self.hold_s:
            self.active = True
            self.reason = "boost-hold"
            return True, "boost-hold"
        self.active = False
        self.reason = None
        return False, None


class _DaemonCgroupWriter:
    """In-process gated cgroup writer bundling the V9 guarded writers.

    Owns the foreground ``cpu.uclamp.min`` floor writer and the background
    shaping apply/restore reports so the daemon can apply lanes when the verdict
    ledger unlocks them and restore them on lane exit / failure / close.
    """

    def __init__(self, *, floor_value: str = "25.00") -> None:
        self._floor_value = floor_value
        self._uclamp = ForegroundUclampMinWriter(floor_value=floor_value)
        self._bg_reports: list[dict[str, object]] = []
        self._bg_variants: list[str] = []
        self._failed = False
        # Lanes whose restore failed, kept for gated-lane telemetry (C9/C10).
        self._unrestored: list[dict[str, object]] = []

    @property
    def failed(self) -> bool:
        return self._failed or self._uclamp.failed

    @property
    def background_reports(self) -> list[dict[str, object]]:
        return list(self._bg_reports)

    @property
    def unrestored(self) -> list[dict[str, object]]:
        return list(self._unrestored)

    def apply_foreground_uclamp(self, cgroup_path: str) -> bool:
        result = self._uclamp.apply(cgroup_path)
        if result.get("status") in {
            "write-failed",
            "write-mismatch",
            "write-unavailable",
            "disabled",
        }:
            self._failed = True
            return False
        return True

    def restore_foreground_uclamp(self) -> dict[str, object]:
        report = self._uclamp.restore()
        if report.get("status") in {"restore-failed", "restore-mismatch"} or (
            self._uclamp.failed
        ):
            # C9: the daemon must fail closed and surface the unrestored floor.
            self._failed = True
            self._unrestored.append(report)
        return report

    def apply_background(
        self, cgroups: list[dict[str, object]], *, appid: str, variants: list[str]
    ) -> bool:
        requested = list(variants)
        # C12: hold only when the applied variant set matches the request. A
        # changed variant set (verdict updated mid-session) means restore the
        # current lanes and apply the new set instead of silently holding.
        if self._bg_reports and self._bg_variants == requested:
            return True
        if self._bg_reports and not self.restore_background():
            return False
        for variant in requested:
            report = apply_background_shaping_to_cgroups(
                cgroups, appid=appid, variant=variant
            )
            self._bg_reports.append(report)
            for write in report.get("writes") or []:
                if write.get("status") not in {"written"}:
                    self._failed = True
                    return False
        self._bg_variants = requested
        return True

    def restore_background(self) -> bool:
        remaining: list[dict[str, object]] = []
        all_restored = True
        for report in self._bg_reports:
            restore_report = restore_background_shaping_from_report(report)
            if not restore_report.get("restored", False):
                # C10: keep the unrestored report, fail closed, and stop further
                # gated writes instead of dropping the report unconditionally.
                all_restored = False
                self._failed = True
                remaining.append(report)
                self._unrestored.append(restore_report)
        self._bg_reports = remaining
        if all_restored:
            self._bg_variants = []
        return all_restored

    def restore_all(self) -> None:
        self.restore_foreground_uclamp()
        self.restore_background()

    def reset(self) -> None:
        self.restore_all()
        self._uclamp = ForegroundUclampMinWriter(floor_value=self._floor_value)
        self._bg_reports = []
        self._bg_variants = []
        self._unrestored = []
        self._failed = False


class GamePowerGovernor:
    def __init__(
        self,
        *,
        config: GamePowerConfig,
        observer: object,
        actuator: object,
        output_format: str = "text",
        config_provider: Callable[[GamePowerConfig], GamePowerConfig] | None = None,
        hint_store: GamePowerHintStore | None = None,
        hint_context_provider: Callable[[GamePowerSample], GamePowerHintContext | None]
        | None = None,
        runtime_snapshot_path: str | Path | None = None,
        verdict_ledger: GamePowerVerdictLedger | None = None,
        verdict_env: GamePowerVerdictEnv | None = None,
        cgroup_writer: object | None = None,
        gpu_actuator: object | None = None,
        soft_pl1_actuator: object | None = None,
        frame_feed_reader: object | None = None,
        auto_target_estimator: AutoTargetEstimator | None = None,
        refresh_hz_provider: Callable[[], float | None] | None = None,
        limiter_writer: Callable[[int | None], bool] | None = None,
        input_idle_provider: Callable[[], float | None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_config = config
        self.config = config
        self.observer = observer
        self.actuator = actuator
        # V10 additive actuators (contracts 1.2/1.3/1.5). All optional so the
        # existing CPU-only tests and the gpu-priority path are unaffected.
        self.gpu_actuator = gpu_actuator
        self.soft_pl1_actuator = soft_pl1_actuator
        self.frame_feed_reader = frame_feed_reader
        # Auto frame-target estimation is observe-only until the cap writer lands;
        # publishing the proposal lets it be validated against real sessions.
        self.auto_target_estimator = auto_target_estimator
        self.refresh_hz_provider = refresh_hz_provider
        self.limiter_writer = limiter_writer
        self.input_idle_provider = input_idle_provider
        self._input_idle_s = 0.0
        self._auto_target_proposal: AutoTargetProposal | None = None
        # The frame cap we applied, so it can be cleared on every restore path.
        self._applied_limiter_fps: int | None = None
        self.output_format = output_format
        self.config_provider = config_provider
        self.sleep = sleep
        self.clock = clock
        self.hint_store = hint_store
        self.hint_context_provider = hint_context_provider
        self.runtime_snapshot_path = (
            Path(runtime_snapshot_path) if runtime_snapshot_path is not None else None
        )
        self.verdict_ledger = verdict_ledger
        self.verdict_env = verdict_env
        # ``cgroup_writer`` is an injectable seam for the gated cgroup lanes; the
        # default in-process writer touches real cgroup files. A verdict ledger
        # is still required before any lane can activate (fail-closed).
        self.cgroup_writer = cgroup_writer or _DaemonCgroupWriter(
            floor_value=config.foreground_uclamp_min_floor
        )
        self.controller = self._new_controller(config)
        self._started_s = time.monotonic()
        self._snapshot: object | None = None
        self._write_failed = False
        self._applied_actuation: GamePowerActuation | None = None
        self._active_context_key: str | None = None
        self._active_context: GamePowerHintContext | None = None
        self._session: GamePowerSessionSummary | None = None
        # V10 actuator state (GPU envelope + soft-PL1 overlay + fast boost lane).
        self._gpu_snapshot: object | None = None
        # ``_gpu_caps_applied`` is the intent key used for change detection; the
        # telemetry view (``_gpu_caps_telemetry``) additionally carries the min
        # the actuator actually wrote, which is data-dependent (D1: min is only
        # lowered when the GT's latched min sits above the cap).
        self._gpu_caps_applied: dict[str, object] | None = None
        self._gpu_caps_telemetry: dict[str, object] | None = None
        self._soft_pl1_applied: int | None = None
        self._boost = FastBoostLane(
            config.boost_hold_s,
            spike_boost_ratio=config.spike_boost_ratio,
            psi_boost_delta=config.psi_boost_delta,
            clock=clock,
        )
        self._boost_posture_active = False
        self._last_sample: GamePowerSample | None = None

    async def _observer_sample(self, sleep_between: bool) -> GamePowerSample:
        # Fake observers in tests define ``sample()`` without the keyword; the
        # real SystemGamePowerObserver accepts ``sleep_between`` so the governor
        # can own the fast-lane cadence.
        try:
            return await self.observer.sample(sleep_between=sleep_between)
        except TypeError:
            return await self.observer.sample()

    def _new_controller(
        self,
        config: GamePowerConfig,
        *,
        hint: GamePowerHintEntry | None = None,
    ) -> GamePowerController:
        return GamePowerController(
            config,
            hint=hint,
            verdict_ledger=self.verdict_ledger,
            verdict_env=self.verdict_env,
        )

    async def run_iterations(self, count: int) -> None:
        for _ in range(count):
            await self.run_once()

    def _fast_subticks(self) -> int:
        fast = self.config.fast_poll_s
        if fast <= 0:
            return 1
        return max(1, round(self.config.poll_s / fast))

    async def run_forever(self) -> None:
        """Production loop with the single-threaded fast boost lane (contract 1.5).

        The loop sleeps in ``fast_poll_s`` increments, runs the cheap fast-lane
        check each increment, and runs the full slow lane every
        ``poll_s / fast_poll_s``-th increment (the slow tick samples without its
        own sleep, so the governor owns the cadence).
        """

        subticks = self._fast_subticks()
        i = 0
        try:
            while True:
                if i % subticks == 0:
                    await self.run_once(sleep_between=False)
                await self.sleep(self.config.fast_poll_s)
                self._fast_lane_tick()
                i += 1
        finally:
            self.close()

    def _fast_lane_tick(self) -> None:
        """Cheap fast-lane boost check between slow ticks (frame-feed only).

        Applies the boost posture the instant boost activates and lifts it when
        the hold window ends, so boost latency is one fast tick, not one poll."""

        if self.config.mode != GamePowerMode.TARGET_BALANCE:
            return
        sample = self._last_sample
        if sample is None:
            return
        target_frame_ms = (
            sample.frame_target.target_frame_ms
            if sample.frame_target is not None
            else None
        )
        fast = self._fast_feed_read()
        active, reason = self._boost.evaluate(
            target_frame_ms=target_frame_ms,
            spike_worst_ms=fast.spike_worst_ms if fast is not None else None,
            last_frame_ms=fast.last_frame_ms if fast is not None else None,
        )
        if active and not self._boost_posture_active:
            self._boost_posture_active = True
            self._apply_boost_posture(sample, reason)
        elif not active and self._boost_posture_active:
            self._boost_posture_active = False
            # Slow lane resumes ownership on its next tick; drop our reductions'
            # boost floor now by restoring the GPU/soft-PL1 to the slow state.
            self.restore()

    def _apply_boost_posture(self, sample: GamePowerSample, reason: str | None) -> None:
        if self._write_failed:
            return
        rpe = sample.gpu_rpe_mhz
        gpu_min = (
            max(1, int(rpe * self.config.gpu_boost_floor_ratio))
            if rpe is not None
            else None
        )
        boost = GamePowerActuation(
            pcore_epp=self.config.loading_pcore_epp,
            gpu_min_mhz=gpu_min,
            soft_pl1_w=None,
        )
        decision = GamePowerDecision(
            GamePowerAction.LOADING_BOOST,
            f"fast-lane boost ({reason})",
            actuation=boost,
        )
        self._apply_cpu_intent(decision)
        if not self._write_failed:
            self._apply_gpu_pl1_intent(decision)

    async def run_once(self, *, sleep_between: bool = True) -> GamePowerDecision:
        self._refresh_config()
        if self.config.mode == GamePowerMode.OFF:
            self._close_current_session()
            self.restore()
            if sleep_between:
                await self.sleep(self.config.poll_s)
            sample = GamePowerSample(
                appid=None,
                rapl=None,
                pl1_w=None,
                frame_target=self.config.frame_target,
            )
            decision = GamePowerDecision(
                GamePowerAction.IDLE,
                "mode is off",
                classification=classify_game_power_sample(self.config, sample),
            )
            elapsed_s = time.monotonic() - self._started_s
            self._emit_decision(sample, decision, elapsed_s=elapsed_s)
            return decision
        sample = await self._observer_sample(sleep_between)
        self._last_sample = sample
        self._prepare_context(sample)
        decision = self.controller.evaluate(sample)
        decision = self._maybe_apply_boost(sample, decision)
        outcome = self._apply_decision(decision, sample)
        decision = self._with_applied_telemetry(decision)
        self._record_session_sample(decision, outcome)
        elapsed_s = time.monotonic() - self._started_s
        self._emit_decision(sample, decision, elapsed_s=elapsed_s)
        return decision

    def _maybe_apply_boost(
        self, sample: GamePowerSample, decision: GamePowerDecision
    ) -> GamePowerDecision:
        """Overlay the fast boost lane on a target-balance decision (contract 1.5).

        Boost is unconditional (removes only our own reductions): it releases the
        trim rungs, clears the soft-PL1 overlay, floors GPU ``min_freq`` at rpe,
        and sets pcore EPP performance. LOADING implies boost posture.
        """

        if self.config.mode != GamePowerMode.TARGET_BALANCE:
            return decision
        target_frame_ms = (
            sample.frame_target.target_frame_ms
            if sample.frame_target is not None
            else None
        )
        fast = self._fast_feed_read()
        psi = _foreground_cpu_psi_avg10(sample.pressure)
        active, reason = self._boost.evaluate(
            target_frame_ms=target_frame_ms,
            spike_worst_ms=fast.spike_worst_ms if fast is not None else None,
            last_frame_ms=fast.last_frame_ms if fast is not None else None,
            psi_avg10=psi,
            phase_is_loading=decision.phase == GamePowerPhase.LOADING,
        )
        self._boost_posture_active = active
        if not active:
            return replace(decision, boost_active=False, boost_reason=None)
        rpe = sample.gpu_rpe_mhz
        gpu_min = (
            max(1, int(rpe * self.config.gpu_boost_floor_ratio))
            if rpe is not None
            else None
        )
        if decision.phase == GamePowerPhase.LOADING:
            # LOADING already released the rungs and set the loading EPP; boost
            # only adds the GPU min floor and guarantees the soft-PL1 is clear.
            base = decision.actuation or GamePowerActuation()
            boost_actuation = replace(
                base,
                gpu_min_mhz=gpu_min,
                gpu_max_mhz=None,
                gpu_max_ratio=None,  # D6: boost lifts any G-rung ratio cap too.
                soft_pl1_w=None,
            )
        else:
            boost_actuation = GamePowerActuation(
                pcore_epp=self.config.loading_pcore_epp,
                gpu_min_mhz=gpu_min,
                soft_pl1_w=None,
            )
        gpu_caps = {"min_mhz": gpu_min, "max_mhz": None} if gpu_min is not None else None
        return replace(
            decision,
            actuation=boost_actuation,
            boost_active=True,
            boost_reason=reason,
            soft_pl1_w=None,
            gpu_freq_caps=gpu_caps,
            trim_rungs_active=[],
        )

    def _fast_feed_read(self) -> FrameFeedFast | None:
        if self.frame_feed_reader is None:
            return None
        reader = getattr(self.frame_feed_reader, "read_fast", None)
        if not callable(reader):
            return None
        try:
            return reader()
        except Exception:  # noqa: BLE001 - fast path must never raise
            return None

    def _with_applied_telemetry(self, decision: GamePowerDecision) -> GamePowerDecision:
        """Attach the *applied* GPU caps to the decision for telemetry v3."""

        if decision.persona is None:
            return decision
        caps = (
            self._gpu_caps_telemetry
            if self._gpu_caps_telemetry is not None
            else self._gpu_caps_applied
        )
        if caps is not None and decision.boost_active is not True:
            return replace(decision, gpu_freq_caps=dict(caps))
        return decision

    def _emit_decision(
        self,
        sample: GamePowerSample,
        decision: GamePowerDecision,
        *,
        elapsed_s: float,
    ) -> None:
        if self.output_format == "jsonl":
            print(format_decision_jsonl(sample, decision, elapsed_s=elapsed_s), flush=True)
        else:
            print(_format_decision(sample, decision), flush=True)
        if self.runtime_snapshot_path is None:
            return
        payload = runtime_snapshot_payload(
            self.config,
            sample,
            decision,
            elapsed_s=time.monotonic(),
            learning=self._runtime_learning_state(),
        )
        payload["auto_target"] = self._observe_auto_target(sample, decision)
        try:
            write_runtime_snapshot(self.runtime_snapshot_path, payload)
        except OSError as exc:
            print(f"game-power: runtime snapshot write failed: {exc}", file=sys.stderr)

    def _observe_auto_target(
        self, sample: GamePowerSample, decision: GamePowerDecision
    ) -> dict[str, object]:
        """Feed the Auto frame-target estimator and report its latest proposal.

        Observe-only for now: the proposal is published so it can be validated
        against real sessions before anything acts on it. Acting on it is gated
        on battery per the frame-cap authority boundary.
        """
        idle_s = self._update_input_idle()
        estimator = self.auto_target_estimator
        if estimator is None:
            return {"status": "disabled", "input_idle_s": idle_s}
        target = sample.frame_target
        performance = sample.frame_performance
        phase = decision.phase.value if decision.phase is not None else None
        refresh_hz = self.refresh_hz_provider() if self.refresh_hz_provider else None
        proposal = estimator.observe(
            appid=sample.appid,
            target_fps=target.fps_target if target is not None else None,
            avg_fps=performance.avg_fps if performance is not None else None,
            refresh_hz=refresh_hz,
            below_target=phase
            in ("below-target-cpu-bound", "below-target-gpu-bound"),
            trims_active=bool(decision.trim_rungs_active),
            min_fps=AUTO_TARGET_MIN_FPS,
            max_fps=AUTO_TARGET_MAX_FPS,
        )
        if proposal is not None:
            self._auto_target_proposal = proposal
        latest = self._auto_target_proposal
        applied, cap_reason = self._maybe_apply_frame_cap(sample, latest)
        return {
            "status": "observing",
            "cap_applied_fps": applied,
            "cap_reason": cap_reason,
            "refresh_hz": refresh_hz,
            "candidates": list(
                divisor_candidates(
                    refresh_hz,
                    min_fps=AUTO_TARGET_MIN_FPS,
                    max_fps=AUTO_TARGET_MAX_FPS,
                )
            ),
            "drops_this_session": estimator.drops_this_session,
            "input_idle_s": round(idle_s, 1),
            "proposal": (
                None
                if latest is None
                else {
                    "fps": latest.fps,
                    "reason": latest.reason,
                    "sustainable_fps": latest.sustainable_fps,
                    "samples": latest.samples,
                }
            ),
        }

    def _update_input_idle(self) -> float:
        """Seconds since the last real input event, from the evdev monitor."""
        if self.input_idle_provider is None:
            self._input_idle_s = 0.0
            return 0.0
        idle = self.input_idle_provider()
        # Signal unavailable: never claim idle on missing evidence.
        self._input_idle_s = 0.0 if idle is None else max(0.0, idle)
        return self._input_idle_s

    def _idle_cap_fps(self) -> int | None:
        """The idle floor, snapped to a divisor of the panel rate."""
        if self.config.idle_input_grace_s <= 0:
            return None
        if self._input_idle_s < self.config.idle_input_grace_s:
            return None
        refresh = self.refresh_hz_provider() if self.refresh_hz_provider else None
        candidates = divisor_candidates(
            refresh, min_fps=AUTO_TARGET_MIN_FPS, max_fps=AUTO_TARGET_MAX_FPS
        )
        if not candidates:
            return None
        return snap_down_to_candidate(self.config.idle_frame_cap_fps, candidates)

    # Personas allowed to write a real frame cap. Plugged-in performance release
    # never caps; quiet is an explicit opt-in to trading frames for calm.
    _CAP_PERSONAS = (GamePowerPersona.BATTERY, GamePowerPersona.AC_QUIET)

    def _maybe_apply_frame_cap(
        self, sample: GamePowerSample, proposal: AutoTargetProposal | None
    ) -> tuple[int | None, str]:
        """Write the Auto frame cap when the persona and user intent allow it.

        Reduction-only overlay: the user's own limit is the higher layer, and
        clearing ours returns to it. Never applied over a manual target - if the
        user picked a number, that is the answer.
        """
        if self.limiter_writer is None:
            return self._applied_limiter_fps, "no-writer"
        if self.config.persona not in self._CAP_PERSONAS:
            return self._clear_frame_cap("persona-performance")
        target = sample.frame_target
        if target is not None and target.source == "manual":
            return self._clear_frame_cap("manual-target")
        idle_fps = self._idle_cap_fps()
        if idle_fps is not None:
            current = target.fps_target if target is not None else None
            if current is not None and idle_fps < current:
                if idle_fps == self._applied_limiter_fps:
                    return self._applied_limiter_fps, "input-idle"
                if self.limiter_writer(idle_fps):
                    self._applied_limiter_fps = idle_fps
                    return self._applied_limiter_fps, "input-idle"
                return self._applied_limiter_fps, "write-failed"
        elif self._applied_limiter_fps is not None and proposal is None:
            # Input returned (or idle never qualified) and nothing else wants a
            # cap: hand the frames straight back.
            return self._clear_frame_cap("input-active")
        if proposal is None:
            return self._applied_limiter_fps, "no-proposal"
        if proposal.fps == self._applied_limiter_fps:
            return self._applied_limiter_fps, "already-applied"
        # Only ever downward: capping above the user's own limit would be an
        # increase we have no authority to make.
        current = target.fps_target if target is not None else None
        if current is not None and proposal.fps >= current:
            return self._applied_limiter_fps, "not-a-reduction"
        if not self.limiter_writer(proposal.fps):
            return self._applied_limiter_fps, "write-failed"
        self._applied_limiter_fps = proposal.fps
        return self._applied_limiter_fps, proposal.reason

    def _clear_frame_cap(self, reason: str) -> tuple[int | None, str]:
        if self._applied_limiter_fps is None or self.limiter_writer is None:
            return None, reason
        if self.limiter_writer(None):
            self._applied_limiter_fps = None
        return self._applied_limiter_fps, reason

    def restore(self) -> GamePowerActuatorOutcome:
        # Our frame cap is one of our own reductions, so every restore path has
        # to lift it too, not just the CPU/GPU/PL1 actuators.
        self._clear_frame_cap("restore")
        # V10: the GPU envelope and soft-PL1 overlay are our own reductions and
        # must be lifted on every restore path (mode change / close / RESTORE /
        # deactivation), readback-verified and fail-closed like the CPU one.
        gpu_pl1_detail = self._restore_gpu_and_pl1()
        if self._snapshot is not None:
            try:
                failed = self.actuator.restore(self._snapshot)
            except Exception as exc:
                print(f"game-power: restore failed: {exc}", file=sys.stderr)
                return GamePowerActuatorOutcome(True, False, str(exc))
            self._snapshot = None
            self._applied_actuation = None
            failed = list(failed or [])
            if failed:
                # F2: a partial restore must be loud and fail-closed, never a
                # silent exit-0 with residue left in sysfs.
                detail = self._report_restore_failures(failed)
                return GamePowerActuatorOutcome(True, False, detail)
            if gpu_pl1_detail is not None:
                return GamePowerActuatorOutcome(True, False, gpu_pl1_detail)
            return GamePowerActuatorOutcome(True, True, "restored")
        if gpu_pl1_detail is not None:
            return GamePowerActuatorOutcome(True, False, gpu_pl1_detail)
        return GamePowerActuatorOutcome(False, True, "no-snapshot")

    def _restore_gpu_and_pl1(self) -> str | None:
        """Lift the GPU cap and clear the soft-PL1 overlay. Returns a failure
        detail string when a GPU readback restore failed (fail-closed), else
        ``None``."""

        detail: str | None = None
        if self.gpu_actuator is not None and self._gpu_snapshot is not None:
            try:
                failed = list(self.gpu_actuator.restore(self._gpu_snapshot) or [])
            except Exception as exc:  # noqa: BLE001 - restore must never raise
                failed = [f"gpu:{exc}"]
            self._gpu_snapshot = None
            self._gpu_caps_applied = None
            self._gpu_caps_telemetry = None
            if failed:
                detail = "gpu-restore-mismatch: " + ", ".join(failed)
                print(f"game-power: {detail}", file=sys.stderr)
                self._write_failed = True
        if self.soft_pl1_actuator is not None and self._soft_pl1_applied is not None:
            try:
                self.soft_pl1_actuator.set_soft_pl1_w(None)
            except Exception as exc:  # noqa: BLE001
                print(f"game-power: soft-PL1 clear failed: {exc}", file=sys.stderr)
                self._write_failed = True
                detail = detail or f"soft-pl1-clear-failed: {exc}"
            self._soft_pl1_applied = None
        return detail

    def _report_restore_failures(self, failed: list[str]) -> str:
        detail = "restore-mismatch: " + ", ".join(failed)
        print(f"game-power: {detail}", file=sys.stderr)
        self._write_failed = True
        return detail

    def close(self) -> None:
        self._close_current_session()
        self._restore_gated_lanes()
        self.restore()

    def _restore_gated_lanes(self) -> None:
        try:
            self.cgroup_writer.restore_all()
        except Exception as exc:  # noqa: BLE001 - restore must never raise
            print(f"game-power: gated lane restore failed: {exc}", file=sys.stderr)

    def _refresh_config(self) -> None:
        if self.config_provider is None:
            return
        next_config = self.config_provider(self.base_config)
        if next_config == self.config:
            return
        self._close_current_session()
        # Mode/config change releases the gated cgroup lanes (design section 8).
        self._restore_gated_lanes()
        # C2: a runtime mode change must also restore the CPU actuation snapshot.
        # The new controller (fresh OFF/OBSERVE/GPU_PRIORITY) would never emit a
        # RESTORE for the old target-balance snapshot, so a ladder cap would
        # survive the switch. Restore here whenever the previous mode was
        # target-balance or any actuation is still applied.
        if (
            self.config.mode == GamePowerMode.TARGET_BALANCE
            or self._applied_actuation is not None
            or self._gpu_snapshot is not None
            or self._soft_pl1_applied is not None
        ):
            self.restore()
        self._boost.reset()
        self._boost_posture_active = False
        self.cgroup_writer.reset()
        self.config = next_config
        self.controller = self._new_controller(next_config)
        self._write_failed = False

    def _apply_decision(
        self, decision: GamePowerDecision, sample: GamePowerSample
    ) -> GamePowerActuatorOutcome:
        if self.config.mode == GamePowerMode.TARGET_BALANCE:
            return self._apply_decision_target_balance(decision, sample)
        # C2 (defense in depth): if a target-balance actuation is still applied
        # when a non-TB path takes over, restore it before proceeding so a stale
        # ladder cap / EPP / GPU cap / soft-PL1 cannot survive into the
        # gpu-priority or observe/off path.
        if (
            self._applied_actuation is not None
            or self._gpu_snapshot is not None
            or self._soft_pl1_applied is not None
        ):
            self.restore()
        if self._write_failed:
            return GamePowerActuatorOutcome(False, False, "writes-disabled")
        if decision.action in {GamePowerAction.IDLE, GamePowerAction.OBSERVE_ONLY}:
            return GamePowerActuatorOutcome(False, True, "no-write-action")
        if decision.action == GamePowerAction.RESTORE:
            return self.restore()
        try:
            if self._snapshot is None:
                self._snapshot = self.actuator.snapshot()
            if decision.action == GamePowerAction.GPU_PRIORITY_EPP:
                self.actuator.apply(epp=self.config.epp)
            elif decision.action == GamePowerAction.GPU_PRIORITY_CPU_CAP:
                self.actuator.apply(
                    epp=self.config.epp,
                    pcore_max_khz=self.config.pcore_max_khz,
                    ecore_max_khz=self.config.ecore_max_khz,
                )
        except Exception as exc:
            print(
                "game-power: active write failed; restoring and disabling writes: "
                f"{exc}",
                file=sys.stderr,
            )
            outcome = self.restore()
            self._write_failed = True
            return outcome
        return GamePowerActuatorOutcome(False, True, "applied")

    def _apply_decision_target_balance(
        self, decision: GamePowerDecision, sample: GamePowerSample
    ) -> GamePowerActuatorOutcome:
        """Apply target-balance decisions as absolute CPU state (design section 5).

        Every non-neutral intent is realized as restore-to-baseline plus apply,
        so phase changes and ladder step-downs are correct without tracking the
        deltas of prior writes. A write failure (CPU or gated cgroup lane)
        triggers a full restore and the existing fail-closed ``_write_failed``
        latch.
        """

        if self._write_failed:
            return GamePowerActuatorOutcome(False, False, "writes-disabled")
        cpu_outcome = self._apply_cpu_intent(decision)
        if self._write_failed:
            return cpu_outcome
        gpu_pl1_outcome = self._apply_gpu_pl1_intent(decision)
        if self._write_failed:
            return gpu_pl1_outcome
        gate_outcome = self._apply_gated_lane_writes(decision, sample)
        if self._write_failed:
            return gate_outcome
        return cpu_outcome

    def _apply_gpu_pl1_intent(
        self, decision: GamePowerDecision
    ) -> GamePowerActuatorOutcome:
        """Apply the GPU envelope + soft-PL1 overlay as absolute reduction-only
        state (contracts 1.2/1.3). ``None`` intent restores/clears both."""

        intent = decision.actuation
        gpu_min = intent.gpu_min_mhz if intent is not None else None
        gpu_max = intent.gpu_max_mhz if intent is not None else None
        gpu_max_ratio = intent.gpu_max_ratio if intent is not None else None
        if self.gpu_actuator is not None:
            if gpu_min is None and gpu_max is None and gpu_max_ratio is None:
                if self._gpu_snapshot is not None:
                    detail = self._restore_gpu_only()
                    if detail is not None:
                        return GamePowerActuatorOutcome(True, False, detail)
            else:
                target = {
                    "min_mhz": gpu_min,
                    "max_mhz": gpu_max,
                    "max_ratio": gpu_max_ratio,
                }
                if target != self._gpu_caps_applied:
                    try:
                        if self._gpu_snapshot is None:
                            self._gpu_snapshot = self.gpu_actuator.snapshot()
                        else:
                            # Reset to the baseline envelope before applying the
                            # new absolute one so a step-down or a boost (which
                            # floors min but lifts the max cap) cannot leave a
                            # stale cap/floor behind (mirrors the CPU discipline).
                            self.gpu_actuator.restore(self._gpu_snapshot)
                        self.gpu_actuator.apply(
                            min_mhz=gpu_min, max_mhz=gpu_max, max_ratio=gpu_max_ratio
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(
                            "game-power: GPU write failed; restoring and disabling "
                            f"writes: {exc}",
                            file=sys.stderr,
                        )
                        self.restore()
                        self._write_failed = True
                        return GamePowerActuatorOutcome(True, False, str(exc))
                    if getattr(self.gpu_actuator, "failed", False):
                        # Fail-closed latch tripped inside the actuator.
                        self.restore()
                        self._write_failed = True
                        return GamePowerActuatorOutcome(True, False, "gpu-write-latched")
                    self._gpu_caps_applied = target
                    self._gpu_caps_telemetry = self._gpu_applied_telemetry(gpu_min)
        if self.soft_pl1_actuator is not None:
            soft = intent.soft_pl1_w if intent is not None else None
            if soft != self._soft_pl1_applied:
                try:
                    self.soft_pl1_actuator.set_soft_pl1_w(soft)
                except Exception as exc:  # noqa: BLE001
                    print(
                        "game-power: soft-PL1 write failed; restoring and "
                        f"disabling writes: {exc}",
                        file=sys.stderr,
                    )
                    self.restore()
                    self._write_failed = True
                    return GamePowerActuatorOutcome(True, False, str(exc))
                self._soft_pl1_applied = soft
        return GamePowerActuatorOutcome(False, True, "gpu-pl1-applied")

    def _gpu_applied_telemetry(self, intent_min: int | None) -> dict[str, object]:
        """Build the telemetry v3 ``gpu_freq_caps`` from what the actuator wrote.

        D6: report a per-GT breakdown (``per_gt``) built from the actuator's
        recorded per-GT applied values, and keep the flat ``min_mhz``/``max_mhz``
        keys populated with the RENDER GT (gt0) values for backward compat. The
        min the cap forced is data-dependent (D1: a max cap lowers a latched-high
        min per GT), so the flat/render min reflects what was actually written.
        """

        last_applied = getattr(self.gpu_actuator, "last_applied", None) or {}
        per_gt: dict[str, object] = {
            name: {"min_mhz": mn, "max_mhz": mx}
            for name, (mn, mx) in last_applied.items()
        }
        render_min: int | None = None
        render_max: int | None = None
        gts = getattr(self.gpu_actuator, "gts", None) or []
        if gts:
            render_min, render_max = last_applied.get(gts[0].name, (None, None))
        if render_min is None:
            # Fall back to the explicit intent min, then the deepest floor the
            # cap forced across GTs (preserves the pre-D6 flat-min semantics).
            render_min = intent_min
            if render_min is None:
                render_min = getattr(self.gpu_actuator, "last_applied_min_mhz", None)
        return {"min_mhz": render_min, "max_mhz": render_max, "per_gt": per_gt}

    def _restore_gpu_only(self) -> str | None:
        if self.gpu_actuator is None or self._gpu_snapshot is None:
            return None
        try:
            failed = list(self.gpu_actuator.restore(self._gpu_snapshot) or [])
        except Exception as exc:  # noqa: BLE001
            failed = [f"gpu:{exc}"]
        self._gpu_snapshot = None
        self._gpu_caps_applied = None
        self._gpu_caps_telemetry = None
        if failed:
            detail = "gpu-restore-mismatch: " + ", ".join(failed)
            print(f"game-power: {detail}", file=sys.stderr)
            self._write_failed = True
            return detail
        return None

    def _apply_cpu_intent(
        self, decision: GamePowerDecision
    ) -> GamePowerActuatorOutcome:
        intent = decision.actuation
        if intent is None:
            if self._snapshot is not None and self._applied_actuation is not None:
                try:
                    failed = list(self.actuator.restore(self._snapshot) or [])
                except Exception as exc:  # noqa: BLE001
                    print(f"game-power: restore failed: {exc}", file=sys.stderr)
                    self._write_failed = True
                    return GamePowerActuatorOutcome(True, False, str(exc))
                self._applied_actuation = None
                if failed:
                    # F2: partial restore -> loud stderr + fail-closed latch.
                    detail = self._report_restore_failures(failed)
                    return GamePowerActuatorOutcome(True, False, detail)
            return GamePowerActuatorOutcome(False, True, "no-write-action")
        if intent == self._applied_actuation and self._snapshot is not None:
            return GamePowerActuatorOutcome(False, True, "held")
        try:
            if self._snapshot is None:
                self._snapshot = self.actuator.snapshot()
            elif self._applied_actuation is not None:
                # Reset to baseline before applying the new absolute state so a
                # step-down or phase change cannot leave a stale cap/EPP behind.
                failed = list(self.actuator.restore(self._snapshot) or [])
                if failed:
                    # F2: surface the partial reset before the fail-closed path.
                    self._report_restore_failures(failed)
                    raise OSError("cpu restore readback mismatch")
            self.actuator.apply(
                pcore_epp=intent.pcore_epp,
                ecore_epp=intent.ecore_epp,
                pcore_max_khz=intent.pcore_max_khz,
                ecore_max_khz=intent.ecore_max_khz,
            )
            self._applied_actuation = intent
        except Exception as exc:
            print(
                "game-power: active write failed; restoring and disabling writes: "
                f"{exc}",
                file=sys.stderr,
            )
            outcome = self.restore()
            self._restore_gated_lanes()
            self._write_failed = True
            return outcome
        return GamePowerActuatorOutcome(False, True, "applied")

    def _apply_gated_lane_writes(
        self, decision: GamePowerDecision, sample: GamePowerSample
    ) -> GamePowerActuatorOutcome:
        lanes = decision.gated_lanes or {}
        writer = self.cgroup_writer
        try:
            fg = lanes.get("foreground_uclamp_min") or {}
            if fg.get("state") == "active" and sample.foreground_cgroup_path:
                if not writer.apply_foreground_uclamp(sample.foreground_cgroup_path):
                    raise OSError("foreground uclamp.min write failed")
            else:
                writer.restore_foreground_uclamp()

            bg = lanes.get("background_shaping") or {}
            if (
                bg.get("state") == "active"
                and sample.allowlist_cgroups
                and sample.appid is not None
            ):
                variants = bg.get("variants") or [self.config.background_shaping_variant]
                if not writer.apply_background(
                    list(sample.allowlist_cgroups),
                    appid=sample.appid,
                    variants=list(variants),
                ):
                    raise OSError("background shaping write failed")
            else:
                writer.restore_background()
        except Exception as exc:  # noqa: BLE001
            print(
                "game-power: gated lane write failed; restoring and disabling "
                f"writes: {exc}",
                file=sys.stderr,
            )
            self._restore_gated_lanes()
            self.restore()
            self._write_failed = True
            return GamePowerActuatorOutcome(True, False, str(exc))
        # C9/C10: a restore that failed (unrestored floor / bg lane) must fail
        # the daemon closed even though no apply raised this tick.
        if getattr(writer, "failed", False):
            self._write_failed = True
            return GamePowerActuatorOutcome(False, True, "gated-lane-unrestored")
        return GamePowerActuatorOutcome(False, True, "gated-applied")

    def _prepare_context(self, sample: GamePowerSample) -> None:
        context = self._sample_context(sample)
        next_key = canonical_hint_key(context) if context is not None else None
        if next_key == self._active_context_key:
            return
        self._close_current_session()
        self._active_context = context
        self._active_context_key = next_key
        hint = (
            self.hint_store.get_hint(context)
            if self.hint_store is not None and context is not None and context.complete
            else None
        )
        # Foreground context change releases any active gated cgroup lanes so a
        # stale write cannot survive into a different game/context.
        self._restore_gated_lanes()
        self.controller = self._new_controller(self.config, hint=hint)
        if context is not None:
            self._session = GamePowerSessionSummary(
                context=context,
                started_s=time.monotonic() - self._started_s,
            )

    def _sample_context(self, sample: GamePowerSample) -> GamePowerHintContext | None:
        if sample.appid is None:
            return None
        if self.config.target_appid is not None and sample.appid != self.config.target_appid:
            return None
        if self.hint_context_provider is None:
            return None
        return self.hint_context_provider(sample)

    def _record_session_sample(
        self,
        decision: GamePowerDecision,
        outcome: GamePowerActuatorOutcome,
    ) -> None:
        if self._session is None:
            return
        self._session.samples += 1
        if self.controller.last_positive:
            self._session.positive_samples += 1
        else:
            self._session.negative_samples += 1
        if decision.action in {
            GamePowerAction.GPU_PRIORITY_EPP,
            GamePowerAction.GPU_PRIORITY_CPU_CAP,
        }:
            self._session.applied_samples += 1
        if decision.action == GamePowerAction.GPU_PRIORITY_CPU_CAP:
            self._session.cpu_cap_samples += 1
        if decision.action == GamePowerAction.RESTORE:
            self._session.restored_samples += 1
        if self.controller.hint_was_used:
            self._session.hint_was_used = True
        if self.controller.hint_disabled:
            self._session.hint_disabled = True
            self._session.hint_disable_reason = "current-session-contradiction"
        self._session.contradiction_samples = max(
            self._session.contradiction_samples,
            self.controller.hint_contradiction_samples,
        )
        if self._write_failed:
            self._session.write_failed = True
        if outcome.attempted:
            self._session.restore_attempted = True
            self._session.restore_succeeded = outcome.succeeded
            self._session.restore_error = None if outcome.succeeded else outcome.reason

    def _close_current_session(self) -> None:
        if self._session is None:
            self._active_context = None
            self._active_context_key = None
            return
        if self._snapshot is not None:
            outcome = self.restore()
            self._session.restore_attempted = True
            self._session.restore_succeeded = outcome.succeeded
            self._session.restore_error = None if outcome.succeeded else outcome.reason
        if self.config.mode == GamePowerMode.GPU_PRIORITY and self.hint_store is not None:
            result = self.hint_store.record_session(self._session)
        else:
            result = GamePowerHintStoreResult(
                cache_write_result=(
                    "not_eligible"
                    if self.config.mode != GamePowerMode.GPU_PRIORITY
                    else "not_configured"
                ),
                promotion_skip_reason=(
                    "mode_not_actuating"
                    if self.config.mode != GamePowerMode.GPU_PRIORITY
                    else "cache_not_configured"
                ),
            )
        if self.output_format == "jsonl":
            print(self._format_session_close_jsonl(self._session, result), flush=True)
        self._session = None
        self._active_context = None
        self._active_context_key = None
        self.controller = GamePowerController(self.config)

    def _format_session_close_jsonl(
        self,
        summary: GamePowerSessionSummary,
        result: GamePowerHintStoreResult,
    ) -> str:
        contradiction_limit = (
            self.hint_store.policy.hint_contradiction_limit
            if self.hint_store is not None
            else GamePowerHintPolicy().hint_contradiction_limit
        )
        payload = {
            "event": "game-power-session-close",
            "appid": summary.context.appid,
            "hint_key": canonical_hint_key(summary.context) if summary.context.complete else None,
            "samples": summary.samples,
            "positive_ratio": _round_or_none(
                summary.positive_samples / summary.samples if summary.samples else None
            ),
            "hint_was_used": summary.hint_was_used,
            "hint_disabled": summary.hint_disabled,
            "hint_disable_reason": summary.hint_disable_reason,
            "contradiction_samples": summary.contradiction_samples,
            "hint_contradiction_count_before": result.hint_contradiction_count_before,
            "hint_contradiction_count_after": result.hint_contradiction_count_after,
            "hint_repair_delta": result.hint_repair_delta,
            "hint_contradiction_limit_reached": (
                result.hint_contradiction_count_after >= contradiction_limit
                if result.hint_contradiction_count_after
                else False
            ),
            "aggregate_updated": result.aggregate_updated,
            "hint_promoted": result.hint_promoted,
            "promotion_skip_reason": result.promotion_skip_reason,
            "restore_attempted": summary.restore_attempted,
            "restore_succeeded": summary.restore_succeeded,
            "write_failed": summary.write_failed,
            "cache_write_result": result.cache_write_result,
        }
        compact = _compact_evidence(payload)
        if not summary.context.complete:
            compact["hint_key"] = None
        return json.dumps(compact, sort_keys=True)

    def _runtime_learning_state(self) -> dict[str, object]:
        policy = self.hint_store.policy if self.hint_store is not None else GamePowerHintPolicy()
        if self.config.mode == GamePowerMode.OFF:
            return {
                "status": "stopped",
                "session_samples": 0,
                "required_samples": policy.min_hint_samples,
                "required_sessions": policy.min_hint_sessions,
                "reusable_next_launch": False,
                "skip_reason": "mode_off",
            }
        if self.config.mode == GamePowerMode.OBSERVE:
            return {
                "status": "view-data-only",
                "session_samples": 0,
                "required_samples": policy.min_hint_samples,
                "required_sessions": policy.min_hint_sessions,
                "reusable_next_launch": False,
                "skip_reason": "mode_observe",
            }
        if self._session is None:
            return {
                "status": "waiting-for-game",
                "session_samples": 0,
                "required_samples": policy.min_hint_samples,
                "required_sessions": policy.min_hint_sessions,
                "reusable_next_launch": False,
                "skip_reason": "no_foreground_game",
            }
        context = self._session.context
        if not context.complete:
            skip_reason = (
                "fps_target_unknown"
                if not _context_has_reusable_target(context)
                else "context_incomplete"
            )
            return {
                "status": (
                    "waiting-for-fps-target"
                    if skip_reason == "fps_target_unknown"
                    else "waiting-for-context"
                ),
                "session_samples": self._session.samples,
                "positive_samples": self._session.positive_samples,
                "required_samples": policy.min_hint_samples,
                "required_sessions": policy.min_hint_sessions,
                "reusable_next_launch": False,
                "skip_reason": skip_reason,
                "hint_key": None,
            }
        hint = self.hint_store.get_hint(context) if self.hint_store is not None else None
        return {
            "status": "ready" if hint is not None else "learning",
            "session_samples": self._session.samples,
            "positive_samples": self._session.positive_samples,
            "required_samples": policy.min_hint_samples,
            "required_sessions": policy.min_hint_sessions,
            "reusable_next_launch": hint is not None,
            "skip_reason": None if hint is not None else "not_enough_samples",
            "hint_key": canonical_hint_key(context),
        }


def _format_decision(sample: GamePowerSample, decision: GamePowerDecision) -> str:
    package_w = sample.rapl.package_w if sample.rapl else None
    core_w = sample.rapl.core_w if sample.rapl else None
    uncore_w = sample.rapl.uncore_w if sample.rapl else None
    return (
        f"game-power appid={sample.appid or '-'} action={decision.action.value} "
        f"reason={decision.reason!r} package_w={_fmt_w(package_w)} "
        f"core_w={_fmt_w(core_w)} uncore_w={_fmt_w(uncore_w)}"
    )


def format_decision_jsonl(
    sample: GamePowerSample,
    decision: GamePowerDecision,
    *,
    elapsed_s: float,
) -> str:
    rapl = sample.rapl
    frame_target = sample.frame_target
    frame_performance = sample.frame_performance
    payload = {
        "elapsed_s": round(elapsed_s, 3),
        "appid": sample.appid,
        "action": decision.action.value,
        "reason": decision.reason,
        "package_w": _round_or_none(rapl.package_w if rapl else None),
        "core_w": _round_or_none(rapl.core_w if rapl else None),
        "uncore_w": _round_or_none(rapl.uncore_w if rapl else None),
        "dram_w": _round_or_none(rapl.dram_w if rapl else None),
        "psys_w": _round_or_none(rapl.psys_w if rapl else None),
        "pl1_w": sample.pl1_w,
        "render_busy": _round_or_none(sample.fdinfo_busy.get("render")),
        "fps_target": _round_or_none(frame_target.fps_target if frame_target else None),
        "fps_target_source": frame_target.source if frame_target else None,
        "fps_target_confidence": frame_target.confidence if frame_target else None,
        "target_frame_ms": (
            frame_target.target_frame_ms if frame_target is not None else None
        ),
        "frame_avg_fps": _round_or_none(
            frame_performance.avg_fps if frame_performance else None
        ),
        "frame_p95_ms": _round_or_none(
            frame_performance.p95_frame_ms if frame_performance else None
        ),
        "frame_performance_sample_count": (
            frame_performance.sample_count if frame_performance else None
        ),
        "frame_performance_window_s": _round_or_none(
            frame_performance.window_s if frame_performance else None
        ),
        "frame_performance_source": (
            frame_performance.source if frame_performance else None
        ),
        "frame_performance_confidence": (
            frame_performance.confidence if frame_performance else None
        ),
        "classification": _classification_json(decision.classification),
        "pressure": _pressure_json(sample.pressure),
    }
    if decision.phase is not None:
        # Additive V9 fields; only present for target-balance so gpu-priority
        # JSONL replay stays byte-identical.
        payload["phase"] = decision.phase.value
        payload["phase_reason_codes"] = list(decision.phase_reason_codes)
        payload["ladder_step"] = decision.ladder_step
    _apply_v9_additive_fields(payload, decision)
    return json.dumps(payload, sort_keys=True)


def _apply_v9_additive_fields(
    payload: dict[str, object], decision: GamePowerDecision
) -> None:
    """Attach color-ledger / verdict / gated-lane telemetry when present.

    These are populated only by target-balance decisions, so gpu-priority JSONL
    and snapshots stay byte-identical (same only-when-not-None pattern as
    ``phase``).
    """

    if decision.color_ledger is not None:
        payload["color_ledger"] = decision.color_ledger
    if decision.verdict_ledger_health is not None:
        payload["verdict_ledger_health"] = decision.verdict_ledger_health
    if decision.gated_lanes is not None:
        payload["gated_lanes"] = decision.gated_lanes
    _apply_v10_additive_fields(payload, decision)


def _apply_v10_additive_fields(
    payload: dict[str, object], decision: GamePowerDecision
) -> None:
    """Attach telemetry v3 fields when present (contract 1.7).

    Populated only by target-balance decisions, so gpu-priority JSONL/snapshots
    stay byte-identical (same only-when-not-None discipline). ``persona`` gates
    the whole block: it is set on every target-balance decision and never on the
    gpu-priority path.
    """

    if decision.persona is None:
        return
    payload["persona"] = decision.persona
    payload["soft_pl1_w"] = decision.soft_pl1_w
    payload["gpu_freq_caps"] = decision.gpu_freq_caps
    payload["boost_active"] = bool(decision.boost_active)
    payload["boost_reason"] = decision.boost_reason
    payload["trim_rungs_active"] = list(decision.trim_rungs_active or [])
    payload["frame_feed_status"] = decision.frame_feed_status
    payload["limiter_state"] = decision.limiter_state or "unknown"
    payload["p95_baseline_ms"] = decision.p95_baseline_ms
    payload["p95_budget_ms"] = decision.p95_budget_ms


def runtime_snapshot_payload(
    config: GamePowerConfig,
    sample: GamePowerSample,
    decision: GamePowerDecision,
    *,
    elapsed_s: float,
    source: str = "daemon",
    sample_source: str = "governor",
    stale: bool = False,
    error: str | None = None,
    learning: dict[str, object] | None = None,
) -> dict[str, object]:
    rapl = sample.rapl
    classification = decision.classification
    learning_state = learning or _default_learning_state()
    payload: dict[str, object] = {
        "schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
        "timestamp_monotonic_s": round(elapsed_s, 3),
        "source": source,
        "mode": public_game_power_mode(config.mode),
        "control_active": config.mode
        in (GamePowerMode.GPU_PRIORITY, GamePowerMode.TARGET_BALANCE),
        "sample_source": sample_source,
        "appid": sample.appid,
        "last_action": decision.action.value,
        "last_reason": decision.reason,
        "classification_primary": (
            classification.primary if classification is not None else None
        ),
        "classification_confidence": (
            classification.confidence if classification is not None else None
        ),
        "fps_target": target_state_from_telemetry(sample.frame_target).to_json(),
        "frame_source": frame_source_state_from_telemetry(
            sample.frame_performance
        ).to_json(),
        "package_w": _round_or_none(rapl.package_w if rapl else None),
        "core_w": _round_or_none(rapl.core_w if rapl else None),
        "uncore_w": _round_or_none(rapl.uncore_w if rapl else None),
        "pl1_w": sample.pl1_w,
        "render_busy": _round_or_none(sample.fdinfo_busy.get("render")),
        "learning": learning_state,
        "evidence_readiness": evidence_readiness_from_runtime(
            config,
            sample,
            stale=stale,
            error=error,
            learning=learning_state,
        ),
        "stale": stale,
        "error": error,
    }
    if decision.phase is not None:
        payload["phase"] = decision.phase.value
        payload["phase_reason_codes"] = list(decision.phase_reason_codes)
        payload["ladder_step"] = decision.ladder_step
    _apply_v9_additive_fields(payload, decision)
    return payload


def _default_learning_state() -> dict[str, object]:
    return {
        "status": "unknown",
        "session_samples": None,
        "required_samples": None,
        "required_sessions": None,
        "reusable_next_launch": False,
        "skip_reason": "unavailable",
    }


def evidence_readiness_from_runtime(
    config: GamePowerConfig,
    sample: GamePowerSample,
    *,
    stale: bool = False,
    error: str | None = None,
    learning: dict[str, object] | None = None,
) -> dict[str, object]:
    if stale or error:
        return _evidence_readiness(
            status="unavailable",
            target_ready=False,
            frame_ready=False,
            learning_ready=False,
            claim_ready=False,
            control_ready=False,
            write_policy="disabled",
            reasons=["runtime unavailable"],
        )

    control_health = config.runtime_control_health or {"status": "ready"}
    control_ready = control_health.get("status") != "invalid"
    if not control_ready:
        return _evidence_readiness(
            status="control-invalid",
            target_ready=False,
            frame_ready=False,
            learning_ready=False,
            claim_ready=False,
            control_ready=False,
            write_policy="disabled",
            reasons=[
                str(control_health.get("reason") or "runtime control invalid")
            ],
        )

    if config.mode == GamePowerMode.OFF:
        return _evidence_readiness(
            status="stopped",
            target_ready=False,
            frame_ready=False,
            learning_ready=False,
            claim_ready=False,
            control_ready=True,
            write_policy="disabled",
            reasons=["game power stopped"],
        )

    if config.mode == GamePowerMode.OBSERVE:
        return _evidence_readiness(
            status="view-data-only",
            target_ready=False,
            frame_ready=False,
            learning_ready=False,
            claim_ready=False,
            control_ready=True,
            write_policy="disabled",
            reasons=["view data only"],
        )

    target_state = target_state_from_telemetry(sample.frame_target)
    frame_state = frame_source_state_from_telemetry(sample.frame_performance)
    target_ready = _target_state_is_ready(target_state)
    frame_ready = _frame_source_state_is_ready(config, frame_state)
    learning_state = learning or _default_learning_state()
    learning_ready = (
        learning_state.get("status") == "ready"
        and learning_state.get("reusable_next_launch") is True
    )
    claim_ready = target_ready and frame_ready
    status = "target-aware-live" if claim_ready else "power-signals-only"
    write_policy = (
        "epp-plus-cpu-cap-explicit" if config.cpu_cap_enabled else "epp-only"
    )
    reasons = ["control ready"]
    reasons.append("fps target known" if target_ready else "fps target unknown")
    reasons.append("frame data ready" if frame_ready else _frame_not_ready_reason(frame_state))
    return _evidence_readiness(
        status=status,
        target_ready=target_ready,
        frame_ready=frame_ready,
        learning_ready=learning_ready,
        claim_ready=claim_ready,
        control_ready=True,
        write_policy=write_policy,
        reasons=reasons,
    )


def _evidence_readiness(
    *,
    status: str,
    target_ready: bool,
    frame_ready: bool,
    learning_ready: bool,
    claim_ready: bool,
    control_ready: bool,
    write_policy: str,
    reasons: list[str],
) -> dict[str, object]:
    return {
        "status": status,
        "target_ready": target_ready,
        "frame_ready": frame_ready,
        "learning_ready": learning_ready,
        "claim_ready": claim_ready,
        "control_ready": control_ready,
        "write_policy": write_policy,
        "reasons": reasons,
    }


def _target_state_is_ready(target: GamePowerTargetState) -> bool:
    return (
        target.status == "known"
        and target.confidence != "low"
        and target.fps is not None
        and target.fps > 0
        and target.target_frame_ms is not None
        and target.target_frame_ms > 0
    )


def _frame_source_state_is_ready(
    config: GamePowerConfig,
    frame: GamePowerFrameSourceState,
) -> bool:
    return (
        frame.status == "live"
        and frame.confidence == "high"
        and frame.avg_fps is not None
        and frame.p95_ms is not None
        and frame.sample_count is not None
        and frame.sample_count >= config.frame_performance_min_samples
    )


def _frame_not_ready_reason(frame: GamePowerFrameSourceState) -> str:
    if frame.status == "missing":
        return "frame data missing"
    if frame.status == "malformed":
        return "frame data invalid"
    return "frame data not ready"


def format_runtime_snapshot_json(
    config: GamePowerConfig,
    sample: GamePowerSample,
    decision: GamePowerDecision,
    *,
    elapsed_s: float,
    source: str = "daemon",
    sample_source: str = "governor",
    stale: bool = False,
    error: str | None = None,
    learning: dict[str, object] | None = None,
) -> str:
    return json.dumps(
        runtime_snapshot_payload(
            config,
            sample,
            decision,
            elapsed_s=elapsed_s,
            source=source,
            sample_source=sample_source,
            stale=stale,
            error=error,
            learning=learning,
        ),
        sort_keys=True,
    )


def write_runtime_snapshot(path: str | Path, payload: dict[str, object]) -> None:
    snapshot_path = Path(path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = snapshot_path.with_name(f".{snapshot_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp_path.replace(snapshot_path)


def _fmt_w(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(value, 3)


def _classification_json(
    classification: GamePowerClassification | None,
) -> dict[str, object] | None:
    if classification is None:
        return None
    return {
        "primary": classification.primary,
        "advisories": sorted(classification.advisories),
        "confidence": classification.confidence,
        "evidence": classification.evidence,
    }


def _pressure_json(pressure: PressureTelemetry | None) -> dict[str, list[dict[str, object]]] | None:
    if pressure is None:
        return None
    return {
        "cpu": [_pressure_signal_json(signal) for signal in pressure.cpu],
        "memory": [_pressure_signal_json(signal) for signal in pressure.memory],
        "io": [_pressure_signal_json(signal) for signal in pressure.io],
    }


def _pressure_signal_json(signal: PressureSignal) -> dict[str, object]:
    return {
        "scope": signal.scope,
        "source_path": signal.source_path,
        "supported": signal.supported,
        "some_avg10": _round_or_none(signal.some_avg10),
        "full_avg10": _round_or_none(signal.full_avg10),
        "error": signal.error,
    }


def parse_pressure_signal(
    resource: str,
    scope: str,
    source_path: str | None,
    text: str,
) -> PressureSignal:
    try:
        values: dict[str, float] = {}
        for line in text.splitlines():
            parts = line.split()
            if not parts or parts[0] not in {"some", "full"}:
                continue
            for item in parts[1:]:
                key, value = item.split("=", 1)
                if key == "avg10":
                    values[parts[0]] = float(value)
        if not values:
            raise ValueError("missing avg10")
    except (TypeError, ValueError) as exc:
        return PressureSignal(
            scope=scope,
            source_path=source_path,
            supported=False,
            error=f"invalid {resource} pressure: {exc}",
        )
    return PressureSignal(
        scope=scope,
        source_path=source_path,
        supported=True,
        some_avg10=values.get("some"),
        full_avg10=values.get("full"),
    )


def read_pressure_signal(resource: str, scope: str, path: Path) -> PressureSignal:
    try:
        return parse_pressure_signal(resource, scope, str(path), path.read_text())
    except OSError as exc:
        return PressureSignal(
            scope=scope,
            source_path=str(path),
            supported=False,
            error=f"unreadable {resource} pressure: {exc.strerror or exc}",
        )


def resolve_cgroup_v2_path(cgroup_root: Path, cgroup_text: str) -> Path | None:
    raw_path: str | None = None
    for line in cgroup_text.splitlines():
        if line.startswith("0::"):
            raw_path = line.removeprefix("0::")
            break
    if raw_path is None or not raw_path.startswith("/"):
        return None
    stripped = raw_path.removeprefix("/")
    if not stripped:
        return None
    parts = stripped.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    root = cgroup_root.resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


class SystemGamePowerObserver:
    def __init__(
        self,
        *,
        sysfs_root: str | Path = "/sys",
        proc_root: str | Path = "/proc",
        cgroup_root: str | Path = "/sys/fs/cgroup",
        poll_s: float = 2.0,
        frame_target: FrameTargetTelemetry | None = None,
        frame_target_provider: Callable[[], FrameTargetTelemetry | None] | None = None,
        frame_performance_reader: object | None = None,
        frame_feed_reader: object | None = None,
        gpu_rp0_mhz: int | None = None,
        gpu_rpe_mhz: int | None = None,
        package_median_window: int = 5,
        colorize_interval_s: float = 10.0,
        loading_frame_stall_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rapl = RaplObserver(sysfs_root=sysfs_root)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        self.poll_s = poll_s
        self.frame_target = frame_target
        self.frame_target_provider = frame_target_provider
        self.frame_performance_reader = frame_performance_reader
        # V10 additive: the mangoapp frame feed (contract 1.1) is preferred over
        # the MangoHud CSV; static GPU bounds size the rungs/boost floor.
        self.frame_feed_reader = frame_feed_reader
        self.gpu_rp0_mhz = gpu_rp0_mhz
        self.gpu_rpe_mhz = gpu_rpe_mhz
        self.colorize_interval_s = colorize_interval_s
        self.loading_frame_stall_s = loading_frame_stall_s
        self.clock = clock
        self._frame_feed_status: str | None = None
        self._package_w_window: deque[float] = deque(
            maxlen=max(1, int(package_median_window))
        )
        self._previous_fdinfo: dict[str, int] | None = None
        self._previous_rapl: EnergyReading | None = None
        self._last_runqueue_wait_ms_per_s: float | None = None
        self._frame_signature: tuple[object, ...] | None = None
        self._frame_last_change_s: float | None = None
        # --- V9 coloring cadence state (section 6). One /proc pass per colorize
        # tick feeds both the color ledger and the foreground runqueue-wait
        # aggregate (Q2: single pass, single cadence counter). ---
        self._color_tick = 0
        self._color_prev: dict[int, dict[str, object]] | None = None
        self._color_prev_s: float = 0.0
        self._fg_sched_prev: dict[int, tuple[int, int]] | None = None
        self._last_color_entries: tuple[ColorLedgerEntry, ...] | None = None
        self._last_color_truncated = False
        self._last_allowlist_cgroups: tuple[dict[str, object], ...] = ()

    def _colorize_period_ticks(self) -> int:
        if self.poll_s <= 0:
            return 1
        return max(1, round(self.colorize_interval_s / self.poll_s))

    async def sample(self, *, sleep_between: bool = True) -> GamePowerSample:
        # ``sleep_between`` False lets the governor own the poll cadence (the fast
        # boost lane, contract 1.5): the RAPL/fdinfo window then spans the wall
        # time between successive slow ticks via the carried-forward previous
        # readings instead of an internal sleep.
        start = self._previous_rapl or self.rapl.read()
        processes = find_steam_game_processes(self.proc_root)
        process = processes[0] if processes else None
        fdinfo_now = (
            read_process_fdinfo_engines(self.proc_root, process.pid) if process else {}
        )
        if sleep_between:
            fdinfo_start = fdinfo_now
            await asyncio.sleep(self.poll_s)
            end = self.rapl.read()
            fdinfo_end = (
                read_process_fdinfo_engines(self.proc_root, process.pid)
                if process
                else {}
            )
        else:
            fdinfo_start = (
                self._previous_fdinfo if self._previous_fdinfo is not None else fdinfo_now
            )
            end = self.rapl.read()
            fdinfo_end = fdinfo_now
        self._previous_rapl = end
        self._previous_fdinfo = fdinfo_end if process else None
        try:
            rapl = compute_rapl_power_window(start, end)
        except ValueError:
            rapl = None
        duration_s = (rapl.duration_s if rapl is not None else self.poll_s)
        busy = (
            compute_fdinfo_busy(fdinfo_start, fdinfo_end, duration_s=duration_s)
            if process
            else {}
        )
        frame_performance = self._read_frame_performance()
        runqueue_wait = self._read_colorize_signals(process)
        return GamePowerSample(
            appid=process.appid if process else None,
            rapl=rapl,
            pl1_w=_read_current_pl1_w(self.rapl.sysfs_root),
            fdinfo_busy=busy,
            frame_target=self._read_frame_target(),
            frame_performance=frame_performance,
            pressure=self._read_pressure(process),
            foreground_runqueue_wait_ms_per_s=runqueue_wait,
            foreground_process_age_s=(
                read_process_age_s(self.proc_root, process.pid)
                if process is not None
                else None
            ),
            frame_feed_stalled=self._frame_feed_stalled(frame_performance),
            color_ledger_entries=self._last_color_entries,
            color_ledger_truncated=self._last_color_truncated,
            allowlist_cgroups=self._last_allowlist_cgroups,
            foreground_cgroup_path=self._foreground_cgroup_path(process),
            gpu_rp0_mhz=self.gpu_rp0_mhz,
            gpu_rpe_mhz=self.gpu_rpe_mhz,
            package_median_w=self._package_median_w(rapl),
            frame_feed_status=self._frame_feed_status,
        )

    def _package_median_w(self, rapl: RaplPowerWindow | None) -> float | None:
        if rapl is None or rapl.package_w is None:
            return None
        self._package_w_window.append(rapl.package_w)
        ordered = sorted(self._package_w_window)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return round(ordered[mid], 3)
        return round((ordered[mid - 1] + ordered[mid]) / 2.0, 3)

    def _foreground_cgroup_path(self, process: GameProcess | None) -> str | None:
        if process is None:
            return None
        path = resolve_cgroup_v2_path(self.cgroup_root, process.cgroup_text)
        return str(path) if path is not None else None

    def _read_colorize_signals(self, process: GameProcess | None) -> float | None:
        """One /proc pass per colorize tick: color ledger + runqueue wait (Q2).

        Colorize cadence (design section 6): recompute both signals every
        ``colorize_interval_s``; carry the last values forward between samples.
        Returns the foreground runqueue-wait aggregate (ms/s).
        """

        self._color_tick += 1
        if process is None:
            self._color_prev = None
            self._fg_sched_prev = None
            self._last_color_entries = None
            self._last_color_truncated = False
            self._last_allowlist_cgroups = ()
            self._last_runqueue_wait_ms_per_s = None
            return None
        if (self._color_tick - 1) % self._colorize_period_ticks() != 0:
            return self._last_runqueue_wait_ms_per_s
        now = float(self.clock())
        fg_rows = read_colorize_thread_rows(
            self.proc_root, process.pid, process.cgroup_text, restore_covered=True
        )
        rows: dict[int, dict[str, object]] = dict(fg_rows)
        allowlist: list[dict[str, object]] = []
        for helper in find_colorize_helper_processes(self.proc_root, process.appid):
            rows.update(
                read_colorize_thread_rows(
                    self.proc_root,
                    helper.pid,
                    helper.cgroup_text,
                    restore_covered=True,
                )
            )
            if process.appid is not None and is_background_shaping_write_target(
                helper.cgroup_text, appid=process.appid
            ):
                path = resolve_cgroup_v2_path(self.cgroup_root, helper.cgroup_text)
                if path is not None:
                    entry = {"cgroup": helper.cgroup_text, "path": str(path)}
                    if entry not in allowlist:
                        allowlist.append(entry)
        self._last_allowlist_cgroups = tuple(allowlist)
        fg_sched = {
            tid: (int(row["cpu_ns"]), int(row["wait_ns"]))
            for tid, row in fg_rows.items()
        }
        if self._color_prev is not None:
            elapsed = now - self._color_prev_s
            if elapsed > 0:
                samples = _colorize_delta_samples(self._color_prev, rows)
                kept, truncated = cap_thread_samples(
                    samples, budget=COLOR_LEDGER_TID_BUDGET
                )
                observations = aggregate_role_observations(kept, window_s=elapsed)
                ledger = build_color_ledger(observations, appid=process.appid)
                self._last_color_entries = ledger.entries
                self._last_color_truncated = truncated
                value = compute_foreground_runqueue_wait_ms_per_s(
                    self._fg_sched_prev or {},
                    fg_sched,
                    elapsed_s=elapsed,
                )
                if value is not None:
                    self._last_runqueue_wait_ms_per_s = value
        self._color_prev = rows
        self._color_prev_s = now
        self._fg_sched_prev = fg_sched
        return self._last_runqueue_wait_ms_per_s

    def _frame_feed_stalled(
        self, frame_performance: FramePerformanceTelemetry | None
    ) -> bool | None:
        if frame_performance is None:
            self._frame_signature = None
            self._frame_last_change_s = None
            return None
        signature = (
            frame_performance.avg_fps,
            frame_performance.p95_frame_ms,
            frame_performance.sample_count,
            frame_performance.window_s,
        )
        now = float(self.clock())
        if signature != self._frame_signature:
            self._frame_signature = signature
            self._frame_last_change_s = now
            return False
        if self._frame_last_change_s is None:
            self._frame_last_change_s = now
            return False
        return (now - self._frame_last_change_s) >= self.loading_frame_stall_s

    def _read_frame_target(self) -> FrameTargetTelemetry | None:
        if self.frame_target_provider is not None:
            return self.frame_target_provider()
        return self.frame_target

    def _read_frame_performance(self) -> FramePerformanceTelemetry | None:
        # Contract 1.1: the mangoapp frame feed wins when present and fresh and
        # upgrades confidence to high (source mangoapp-feed). Absent / stale /
        # corrupt -> exact V9 behaviour (MangoHud CSV when configured).
        if self.frame_feed_reader is not None:
            read = getattr(self.frame_feed_reader, "read", None)
            feed = read() if callable(read) else None
            self._frame_feed_status = getattr(self.frame_feed_reader, "last_status", None)
            if feed is not None:
                return feed
        else:
            self._frame_feed_status = None
        if self.frame_performance_reader is None:
            return None
        read = getattr(self.frame_performance_reader, "read", None)
        if not callable(read):
            return None
        return read()

    def _read_pressure(self, process: GameProcess | None) -> PressureTelemetry:
        foreground = (
            _read_foreground_pressure(self.cgroup_root, process.cgroup_text)
            if process is not None
            else {"cpu": (), "memory": (), "io": ()}
        )
        system = _read_system_pressure(self.proc_root)
        return PressureTelemetry(
            cpu=tuple(foreground["cpu"]) + tuple(system["cpu"]),
            memory=tuple(foreground["memory"]) + tuple(system["memory"]),
            io=tuple(foreground["io"]) + tuple(system["io"]),
        )


def _read_foreground_pressure(
    cgroup_root: Path,
    cgroup_text: str,
) -> dict[str, tuple[PressureSignal, ...]]:
    cgroup_path = resolve_cgroup_v2_path(cgroup_root, cgroup_text)
    if cgroup_path is None:
        return {
            resource: (
                PressureSignal(
                    scope="foreground_cgroup",
                    source_path=None,
                    supported=False,
                    error="missing or unsafe cgroup v2 path",
                ),
            )
            for resource in ("cpu", "memory", "io")
        }
    return {
        resource: (
            read_pressure_signal(
                resource,
                "foreground_cgroup",
                cgroup_path / f"{resource}.pressure",
            ),
        )
        for resource in ("cpu", "memory", "io")
    }


def _read_system_pressure(proc_root: Path) -> dict[str, tuple[PressureSignal, ...]]:
    pressure_root = proc_root / "pressure"
    signals: dict[str, tuple[PressureSignal, ...]] = {}
    for resource in ("cpu", "memory", "io"):
        path = pressure_root / resource
        if path.exists():
            signals[resource] = (read_pressure_signal(resource, "system", path),)
        else:
            signals[resource] = ()
    return signals


def _read_current_pl1_w(sysfs_root: Path) -> int | None:
    domain = sysfs_root / "class" / "powercap" / "intel-rapl:0"
    for name_file in sorted(domain.glob("constraint_*_name")):
        if _read_text(name_file) != "long_term":
            continue
        power_file = domain / f"{name_file.name.removesuffix('_name')}_power_limit_uw"
        value = _read_optional_int(power_file)
        return value // MICROJOULES_PER_JOULE if value is not None else None
    value = _read_optional_int(domain / "constraint_0_power_limit_uw")
    return value // MICROJOULES_PER_JOULE if value is not None else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in GamePowerMode],
        default=GamePowerMode.OBSERVE.value,
    )
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--epp", default="balance_power")
    parser.add_argument("--pcore-max-mhz", type=int, default=3200)
    parser.add_argument("--ecore-max-mhz", type=int, default=2800)
    parser.add_argument("--cpu-cap", action="store_true")
    parser.add_argument("--cpu-cap-core-share-threshold", type=float, default=0.38)
    parser.add_argument("--target-appid")
    parser.add_argument("--output-format", choices=["text", "jsonl"], default="text")
    parser.add_argument("--sysfs-root", default="/sys")
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--cgroup-root", default="/sys/fs/cgroup")
    parser.add_argument("--fps-target", type=_positive_finite_float)
    parser.add_argument("--fps-target-source")
    parser.add_argument("--fps-target-confidence")
    parser.add_argument("--frame-performance-csv")
    parser.add_argument("--frame-performance-window-samples", type=_positive_int, default=20)
    parser.add_argument("--frame-performance-min-samples", type=_positive_int, default=12)
    parser.add_argument("--runtime-snapshot-file")
    parser.add_argument("--hint-cache")
    parser.add_argument("--verdict-ledger")
    # Profiler-only: unlock ladder S5 for a controlled run without a verdict
    # (target-balance-ladder5 candidate policy). The daemon service never sets
    # this flag (design section 7: S5+ requires a BETTER verdict at runtime).
    parser.add_argument("--allow-ladder-step-5", action="store_true")
    # V10 frame feed + persona (contracts 1.1 / plan section 0).
    parser.add_argument("--frame-feed-file")
    parser.add_argument("--frame-feed-stale-s", type=_positive_finite_float, default=5.0)
    parser.add_argument(
        "--persona",
        choices=[persona.value for persona in GamePowerPersona],
        default=GamePowerPersona.BATTERY.value,
    )
    # Profiler-only rung-subset selection (v10-gpu-cap / v10-soft-pl1 candidate
    # policies). ``all`` keeps the full persona ladder (daemon default). The
    # daemon service never sets this; a controlled run isolates one lane so its
    # A/B evidence attributes savings to that actuator alone.
    parser.add_argument(
        "--trim-rungs",
        choices=sorted(_TRIM_RUNG_FILTERS),
        default="all",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> GamePowerConfig:
    frame_target = frame_target_from_args(args)
    if args.frame_performance_min_samples > args.frame_performance_window_samples:
        raise ValueError(
            "--frame-performance-min-samples cannot exceed "
            "--frame-performance-window-samples"
        )
    return GamePowerConfig(
        mode=GamePowerMode(args.mode),
        poll_s=args.poll_s,
        epp=args.epp,
        pcore_max_khz=args.pcore_max_mhz * 1000,
        ecore_max_khz=args.ecore_max_mhz * 1000,
        cpu_cap_enabled=bool(args.cpu_cap),
        cpu_cap_core_share_threshold=args.cpu_cap_core_share_threshold,
        target_appid=args.target_appid,
        frame_target=frame_target,
        frame_performance_min_samples=args.frame_performance_min_samples,
        allow_ladder_step_5=bool(args.allow_ladder_step_5),
        persona=GamePowerPersona(args.persona),
        frame_feed_file=args.frame_feed_file,
        frame_feed_stale_s=args.frame_feed_stale_s,
        trim_rung_filter=_TRIM_RUNG_FILTERS[getattr(args, "trim_rungs", "all")],
    )


def gpu_freq_bounds(gts: Iterable[object]) -> tuple[int | None, int | None]:
    """Representative (rp0, rpe) MHz across discovered GTs.

    D6: the GTs do NOT share bounds on the target device -- the render GT (gt0)
    tops out at rp0 1950 while the media GT (gt1) tops out at 1200. The per-GT
    cap is derived in the actuator from each GT's own rp0, so this scalar is only
    a representative for telemetry/boost:

    - ``rp0`` = MAX across GTs = the render GT's ceiling (the flat/render value a
      controller-side cap-telemetry line reports).
    - ``rpe`` = MIN across GTs = the conservative efficient-frequency boost floor.
    """

    rp0 = [gt.rp0_mhz for gt in gts if getattr(gt, "rp0_mhz", None) is not None]
    rpe = [gt.rpe_mhz for gt in gts if getattr(gt, "rpe_mhz", None) is not None]
    return (max(rp0) if rp0 else None, min(rpe) if rpe else None)


def _positive_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a finite positive float") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive float")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def frame_target_from_args(args: argparse.Namespace) -> FrameTargetTelemetry | None:
    if args.fps_target is None:
        if args.fps_target_source is not None:
            raise ValueError("--fps-target-source requires --fps-target")
        if args.fps_target_confidence is not None:
            raise ValueError("--fps-target-confidence requires --fps-target")
        return None
    return FrameTargetTelemetry(
        fps_target=round(float(args.fps_target), 3),
        source=args.fps_target_source or "manual",
        confidence=args.fps_target_confidence or "medium",
    )


async def run_cli(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    frame_performance_reader = (
        MangoHudCsvFramePerformanceReader(
            args.frame_performance_csv,
            window_samples=args.frame_performance_window_samples,
            min_samples=args.frame_performance_min_samples,
        )
        if args.frame_performance_csv
        else None
    )
    frame_feed_reader = (
        FrameFeedReader(args.frame_feed_file, stale_s=config.frame_feed_stale_s)
        if args.frame_feed_file
        else None
    )
    gts = discover_gpu_gts(args.sysfs_root)
    gpu_rp0_mhz, gpu_rpe_mhz = gpu_freq_bounds(gts)
    gpu_actuator = GpuFreqActuator(gts) if gts else None
    observer = SystemGamePowerObserver(
        sysfs_root=args.sysfs_root,
        proc_root=args.proc_root,
        cgroup_root=args.cgroup_root,
        poll_s=config.poll_s,
        frame_target=config.frame_target,
        frame_performance_reader=frame_performance_reader,
        frame_feed_reader=frame_feed_reader,
        gpu_rp0_mhz=gpu_rp0_mhz,
        gpu_rpe_mhz=gpu_rpe_mhz,
        colorize_interval_s=config.colorize_interval_s,
        loading_frame_stall_s=config.loading_frame_stall_s,
    )
    policies = discover_cpu_policies(args.sysfs_root)
    actuator = CpuPolicyActuator(policies)
    verdict_ledger = None
    verdict_env = None
    if config.mode == GamePowerMode.TARGET_BALANCE:
        verdict_ledger = GamePowerVerdictLedger(
            args.verdict_ledger or DEFAULT_VERDICT_LEDGER_FILE,
            fallback_path=DEFAULT_VERDICT_LEDGER_RUN_FALLBACK,
        )
        verdict_env = GamePowerVerdictEnv(
            topology_fingerprint=topology_fingerprint(policies),
            kernel=read_kernel_release(args.proc_root),
        )
    governor = GamePowerGovernor(
        config=config,
        observer=observer,
        actuator=actuator,
        gpu_actuator=gpu_actuator,
        frame_feed_reader=frame_feed_reader,
        output_format=args.output_format,
        hint_store=GamePowerHintStore(args.hint_cache) if args.hint_cache else None,
        runtime_snapshot_path=args.runtime_snapshot_file,
        verdict_ledger=verdict_ledger,
        verdict_env=verdict_env,
    )
    iterations = max(1, int(args.duration_s / config.poll_s))
    try:
        await governor.run_iterations(iterations)
    finally:
        governor.close()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(run_cli(args))


STEAM_APP_RE = re.compile(r"app-steam-app(\d+)-")


def find_steam_game_processes(proc_root: str | Path = "/proc") -> list[GameProcess]:
    proc_root = Path(proc_root)
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return []

    processes: list[GameProcess] = []
    for entry in sorted(entries, key=_proc_sort_key):
        if not entry.name.isdigit():
            continue
        cgroup = _read_text(entry / "cgroup")
        match = STEAM_APP_RE.search(cgroup)
        if match is None:
            continue
        processes.append(
            GameProcess(
                pid=int(entry.name),
                appid=match.group(1),
                command=_read_cmdline(entry / "cmdline"),
                cgroup_text=cgroup,
            )
        )
    return processes


def _proc_sort_key(path: Path) -> int:
    return int(path.name) if path.name.isdigit() else -1


def _read_cmdline(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    parts = [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]
    return " ".join(parts)


def read_process_fdinfo_engines(proc_root: Path, pid: int) -> dict[str, int]:
    totals: dict[str, int] = {}
    fdinfo_root = proc_root / str(pid) / "fdinfo"
    for fdinfo in sorted(fdinfo_root.glob("*")):
        try:
            parsed = parse_fdinfo_engine_times(fdinfo.read_text())
        except OSError:
            continue
        for engine, value in parsed.items():
            totals[engine] = totals.get(engine, 0) + value
    return totals


def parse_thread_schedstat(text: str) -> tuple[int, int] | None:
    """Parse ``/proc/<pid>/task/<tid>/schedstat`` -> (cpu_time_ns, runqueue_wait_ns).

    Q3: thin view over :func:`parse_thread_schedstat_full` (one parser, two
    historical shapes).
    """

    parsed = parse_thread_schedstat_full(text)
    if parsed is None:
        return None
    return parsed[0], parsed[1]


def compute_foreground_runqueue_wait_ms_per_s(
    prev: dict[int, tuple[int, int]],
    curr: dict[int, tuple[int, int]],
    *,
    elapsed_s: float,
    top_n: int = 16,
) -> float | None:
    """Sum runqueue-wait deltas over the top-N threads by CPU-time delta (ms/s).

    Design section 4: N=16 threads ranked by CPU-time delta; carried forward
    between colorize samples by the observer.
    """

    if elapsed_s <= 0:
        return None
    deltas: list[tuple[int, int]] = []  # (cpu_delta_ns, wait_delta_ns)
    for tid, (cpu_ns, wait_ns) in curr.items():
        if tid not in prev:
            continue
        prev_cpu, prev_wait = prev[tid]
        cpu_delta = cpu_ns - prev_cpu
        wait_delta = wait_ns - prev_wait
        if cpu_delta < 0 or wait_delta < 0:
            continue
        deltas.append((cpu_delta, wait_delta))
    if not deltas:
        return None
    deltas.sort(key=lambda item: item[0], reverse=True)
    total_wait_ns = sum(wait for _cpu, wait in deltas[:top_n])
    return round(total_wait_ns / 1_000_000 / elapsed_s, 3)


def parse_thread_schedstat_full(text: str) -> tuple[int, int, int] | None:
    """Parse schedstat -> (cpu_time_ns, runqueue_wait_ns, timeslices)."""

    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _parse_proc_stat_tail_field(text: str, index: int) -> int | None:
    """Extract an integer field from the post-comm tail of ``/proc/<pid>/stat``.

    Q3: single parser for the comm-with-spaces-safe stat tail; fields after the
    closing paren start at field 3 (state), so field N sits at index N - 3.
    """

    rparen = text.rfind(")")
    if rparen == -1:
        return None
    rest = text[rparen + 2 :].split()
    if len(rest) <= index:
        return None
    try:
        return int(rest[index])
    except ValueError:
        return None


def parse_proc_stat_processor(text: str) -> int | None:
    """Extract field 39 (processor, last-run CPU) from ``/proc/<pid>/stat``."""

    return _parse_proc_stat_tail_field(text, 36)


def read_colorize_thread_rows(
    proc_root: Path, pid: int, cgroup_text: str, *, restore_covered: bool = True
) -> dict[int, dict[str, object]]:
    """Read per-thread coloring signals for one process (design section 6).

    Returns ``{tid: {comm, cgroup, cpu_ns, wait_ns, timeslices, current_cpu,
    restore_covered}}`` for every task under ``/proc/<pid>/task``.
    """

    task_root = Path(proc_root) / str(pid) / "task"
    rows: dict[int, dict[str, object]] = {}
    try:
        entries = list(task_root.iterdir())
    except OSError:
        return rows
    for entry in entries:
        if not entry.name.isdigit():
            continue
        parsed = parse_thread_schedstat_full(_read_text(entry / "schedstat"))
        if parsed is None:
            continue
        cpu_ns, wait_ns, timeslices = parsed
        current_cpu = parse_proc_stat_processor(_read_text(entry / "stat"))
        rows[int(entry.name)] = {
            "comm": _read_text(entry / "comm") or None,
            "cgroup": cgroup_text,
            "cpu_ns": cpu_ns,
            "wait_ns": wait_ns,
            "timeslices": timeslices,
            "current_cpu": current_cpu,
            "restore_covered": restore_covered,
        }
    return rows


def find_colorize_helper_processes(
    proc_root: str | Path, appid: str | None
) -> list[GameProcess]:
    """Find compositor/overlay and background-helper processes to colorize.

    These feed color C (compositor-overlay-sensitive) and color D (background-
    helper-shapable). The foreground game itself is handled separately.
    """

    proc_root = Path(proc_root)
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return []
    processes: list[GameProcess] = []
    for entry in sorted(entries, key=_proc_sort_key):
        if not entry.name.isdigit():
            continue
        cgroup = _read_text(entry / "cgroup")
        if not cgroup:
            continue
        match = STEAM_APP_RE.search(cgroup)
        if match is not None and (appid is None or match.group(1) == appid):
            continue  # the foreground game, colorized on its own path
        shapable = appid is not None and is_background_shaping_write_target(
            cgroup, appid=appid
        )
        if not (shapable or is_compositor_role(cgroup, None)):
            continue
        processes.append(
            GameProcess(
                pid=int(entry.name),
                appid=match.group(1) if match else None,
                command=_read_cmdline(entry / "cmdline"),
                cgroup_text=cgroup,
            )
        )
    return processes


def _colorize_delta_samples(
    prev: dict[int, dict[str, object]],
    curr: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for tid, row in curr.items():
        old = prev.get(tid)
        if old is None:
            continue
        cpu_delta = int(row["cpu_ns"]) - int(old["cpu_ns"])
        wait_delta = int(row["wait_ns"]) - int(old["wait_ns"])
        slice_delta = int(row["timeslices"]) - int(old["timeslices"])
        if cpu_delta < 0 or wait_delta < 0 or slice_delta < 0:
            continue
        cpus_seen = sorted(
            {
                cpu
                for cpu in (old.get("current_cpu"), row.get("current_cpu"))
                if isinstance(cpu, int)
            }
        )
        samples.append(
            {
                "tid": tid,
                "comm": row.get("comm"),
                "cgroup": row.get("cgroup"),
                "cpu_time_ms_delta": cpu_delta / 1_000_000,
                "runqueue_wait_ms_delta": wait_delta / 1_000_000,
                "timeslices_delta": slice_delta,
                "cpus_seen": cpus_seen,
                "restore_covered": bool(row.get("restore_covered", True)),
            }
        )
    return samples


def parse_proc_stat_starttime_ticks(text: str) -> int | None:
    """Extract field 22 (starttime, clock ticks) from ``/proc/<pid>/stat``."""

    return _parse_proc_stat_tail_field(text, 19)


def read_process_age_s(
    proc_root: str | Path,
    pid: int,
    *,
    clock_ticks_per_s: int | None = None,
) -> float | None:
    """Age in seconds of ``pid`` from ``/proc/uptime`` and ``/proc/<pid>/stat``."""

    proc_root = Path(proc_root)
    uptime = _float_or_none((_read_text(proc_root / "uptime").split() or [""])[0])
    if uptime is None:
        return None
    starttime_ticks = parse_proc_stat_starttime_ticks(
        _read_text(proc_root / str(pid) / "stat")
    )
    if starttime_ticks is None:
        return None
    hz = clock_ticks_per_s or os.sysconf("SC_CLK_TCK")
    if hz <= 0:
        return None
    age = uptime - starttime_ticks / hz
    return round(max(0.0, age), 3)


if __name__ == "__main__":
    main()
