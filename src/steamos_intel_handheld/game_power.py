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
import re
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

MICROJOULES_PER_JOULE = 1_000_000
RUNTIME_SNAPSHOT_SCHEMA_VERSION = "game-power-runtime-snapshot-v1"
DEFAULT_RUNTIME_SNAPSHOT_FILE = Path(
    "/run/steamos-intel-handheld/game-power-runtime.json"
)


class GamePowerMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    GPU_PRIORITY = "gpu-priority"


class GamePowerAction(str, Enum):
    IDLE = "idle"
    OBSERVE_ONLY = "observe-only"
    GPU_PRIORITY_EPP = "gpu-priority-epp"
    GPU_PRIORITY_CPU_CAP = "gpu-priority-cpu-cap"
    RESTORE = "restore"


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
    if mode == GamePowerMode.GPU_PRIORITY:
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


@dataclass(frozen=True)
class CpuPolicySnapshot:
    values: dict[str, tuple[str | None, int | None]]


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
        elif capacity == max_capacity:
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
        epp: str,
        pcore_max_khz: int | None = None,
        ecore_max_khz: int | None = None,
    ) -> None:
        for policy in self.policies:
            if epp and epp in policy.available_epp:
                _write_if_changed(policy.path / "energy_performance_preference", epp)
            cap = _cap_for_policy(policy, pcore_max_khz, ecore_max_khz)
            if cap is not None:
                _write_if_changed(policy.path / "scaling_max_freq", str(cap))

    def restore(self, snapshot: CpuPolicySnapshot) -> None:
        for policy in self.policies:
            epp, max_freq = snapshot.values.get(policy.name, (None, None))
            if epp is not None:
                _write_if_changed(policy.path / "energy_performance_preference", epp)
            if max_freq is not None:
                _write_if_changed(policy.path / "scaling_max_freq", str(max_freq))


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
    fps_target_satisfied_headroom_ratio: float = 1.05
    fps_target_satisfied_p95_ratio: float = 1.15
    frame_performance_min_samples: int = 12


@dataclass(frozen=True)
class GamePowerSample:
    appid: str | None
    rapl: RaplPowerWindow | None
    pl1_w: int | None
    fdinfo_busy: dict[str, float] = field(default_factory=dict)
    frame_target: FrameTargetTelemetry | None = None
    frame_performance: FramePerformanceTelemetry | None = None
    pressure: PressureTelemetry | None = None


@dataclass(frozen=True)
class GamePowerDecision:
    action: GamePowerAction
    reason: str
    classification: GamePowerClassification | None = None


DEFAULT_GAME_POWER_POLICY_VERSION = "game-power-sampling-v1"
GAME_POWER_HINT_SCHEMA_VERSION = 1


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
    return context


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
) -> GamePowerClassification:
    if config.mode == GamePowerMode.OFF:
        return GamePowerClassification("control-disabled", confidence="high")
    if config.mode == GamePowerMode.OBSERVE:
        return GamePowerClassification("observe-only", confidence="high")
    if sample.appid is None:
        return GamePowerClassification("no-foreground-game", confidence="high")
    if config.target_appid is not None and sample.appid != config.target_appid:
        return GamePowerClassification("non-target-game", confidence="high")
    if _sample_fps_target_satisfied(config, sample):
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


def _sample_fps_target_satisfied(config: GamePowerConfig, sample: GamePowerSample) -> bool:
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
    return (
        performance.avg_fps >= fps_target * config.fps_target_satisfied_headroom_ratio
        and performance.p95_frame_ms
        <= target_frame_ms * config.fps_target_satisfied_p95_ratio
    )


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


class GamePowerController:
    def __init__(
        self,
        config: GamePowerConfig,
        *,
        hint: GamePowerHintEntry | None = None,
    ) -> None:
        self.config = config
        self.hint = hint
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

    def evaluate(self, sample: GamePowerSample) -> GamePowerDecision:
        classification = classify_game_power_sample(
            self.config,
            sample,
            controller_active=self._active,
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

    def _sample_supports_gpu_priority(self, sample: GamePowerSample) -> bool:
        if _sample_fps_target_satisfied(self.config, sample):
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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_config = config
        self.config = config
        self.observer = observer
        self.actuator = actuator
        self.output_format = output_format
        self.config_provider = config_provider
        self.sleep = sleep
        self.hint_store = hint_store
        self.hint_context_provider = hint_context_provider
        self.runtime_snapshot_path = (
            Path(runtime_snapshot_path) if runtime_snapshot_path is not None else None
        )
        self.controller = GamePowerController(config)
        self._started_s = time.monotonic()
        self._snapshot: object | None = None
        self._write_failed = False
        self._active_context_key: str | None = None
        self._active_context: GamePowerHintContext | None = None
        self._session: GamePowerSessionSummary | None = None

    async def run_iterations(self, count: int) -> None:
        for _ in range(count):
            await self.run_once()

    async def run_forever(self) -> None:
        try:
            while True:
                await self.run_once()
        finally:
            self.close()

    async def run_once(self) -> GamePowerDecision:
        self._refresh_config()
        if self.config.mode == GamePowerMode.OFF:
            self._close_current_session()
            self.restore()
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
        sample = await self.observer.sample()
        self._prepare_context(sample)
        decision = self.controller.evaluate(sample)
        outcome = self._apply_decision(decision)
        self._record_session_sample(decision, outcome)
        elapsed_s = time.monotonic() - self._started_s
        self._emit_decision(sample, decision, elapsed_s=elapsed_s)
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
        )
        try:
            write_runtime_snapshot(self.runtime_snapshot_path, payload)
        except OSError as exc:
            print(f"game-power: runtime snapshot write failed: {exc}", file=sys.stderr)

    def restore(self) -> GamePowerActuatorOutcome:
        if self._snapshot is not None:
            try:
                self.actuator.restore(self._snapshot)
            except Exception as exc:
                print(f"game-power: restore failed: {exc}", file=sys.stderr)
                return GamePowerActuatorOutcome(True, False, str(exc))
            self._snapshot = None
            return GamePowerActuatorOutcome(True, True, "restored")
        return GamePowerActuatorOutcome(False, True, "no-snapshot")

    def close(self) -> None:
        self._close_current_session()
        self.restore()

    def _refresh_config(self) -> None:
        if self.config_provider is None:
            return
        next_config = self.config_provider(self.base_config)
        if next_config == self.config:
            return
        self._close_current_session()
        self.config = next_config
        self.controller = GamePowerController(next_config)
        self._write_failed = False

    def _apply_decision(self, decision: GamePowerDecision) -> GamePowerActuatorOutcome:
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

    def _prepare_context(self, sample: GamePowerSample) -> None:
        context = self._sample_context(sample)
        next_key = canonical_hint_key(context) if context is not None and context.complete else None
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
        self.controller = GamePowerController(self.config, hint=hint)
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
        return json.dumps(_compact_evidence(payload), sort_keys=True)


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
    return json.dumps(payload, sort_keys=True)


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
) -> dict[str, object]:
    rapl = sample.rapl
    classification = decision.classification
    return {
        "schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
        "timestamp_monotonic_s": round(elapsed_s, 3),
        "source": source,
        "mode": public_game_power_mode(config.mode),
        "control_active": config.mode == GamePowerMode.GPU_PRIORITY,
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
        "stale": stale,
        "error": error,
    }


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
    return round(value, 3) if value is not None else None


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
        frame_performance_reader: object | None = None,
    ) -> None:
        self.rapl = RaplObserver(sysfs_root=sysfs_root)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        self.poll_s = poll_s
        self.frame_target = frame_target
        self.frame_performance_reader = frame_performance_reader
        self._previous_rapl: EnergyReading | None = None

    async def sample(self) -> GamePowerSample:
        start = self._previous_rapl or self.rapl.read()
        processes = find_steam_game_processes(self.proc_root)
        process = processes[0] if processes else None
        fdinfo_start = read_process_fdinfo_engines(self.proc_root, process.pid) if process else {}
        await asyncio.sleep(self.poll_s)
        end = self.rapl.read()
        fdinfo_end = read_process_fdinfo_engines(self.proc_root, process.pid) if process else {}
        self._previous_rapl = end
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
        return GamePowerSample(
            appid=process.appid if process else None,
            rapl=rapl,
            pl1_w=_read_current_pl1_w(self.rapl.sysfs_root),
            fdinfo_busy=busy,
            frame_target=self.frame_target,
            frame_performance=self._read_frame_performance(),
            pressure=self._read_pressure(process),
        )

    def _read_frame_performance(self) -> FramePerformanceTelemetry | None:
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
    )


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
    observer = SystemGamePowerObserver(
        sysfs_root=args.sysfs_root,
        proc_root=args.proc_root,
        cgroup_root=args.cgroup_root,
        poll_s=config.poll_s,
        frame_target=config.frame_target,
        frame_performance_reader=frame_performance_reader,
    )
    actuator = CpuPolicyActuator(discover_cpu_policies(args.sysfs_root))
    governor = GamePowerGovernor(
        config=config,
        observer=observer,
        actuator=actuator,
        output_format=args.output_format,
        hint_store=GamePowerHintStore(args.hint_cache) if args.hint_cache else None,
        runtime_snapshot_path=args.runtime_snapshot_file,
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


if __name__ == "__main__":
    main()
