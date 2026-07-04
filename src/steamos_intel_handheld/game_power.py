#!/usr/bin/env python3
"""Game-aware CPU/iGPU shared-power governor for Intel SteamOS handhelds."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

MICROJOULES_PER_JOULE = 1_000_000


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
    frame_target: FrameTargetTelemetry | None = None


@dataclass(frozen=True)
class GamePowerSample:
    appid: str | None
    rapl: RaplPowerWindow | None
    pl1_w: int | None
    fdinfo_busy: dict[str, float] = field(default_factory=dict)
    frame_target: FrameTargetTelemetry | None = None
    pressure: PressureTelemetry | None = None


@dataclass(frozen=True)
class GamePowerDecision:
    action: GamePowerAction
    reason: str
    classification: GamePowerClassification | None = None


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
    if not controller_active and (
        core_share is None or core_share < config.core_share_threshold
    ):
        return GamePowerClassification(
            "insufficient-cpu-contention-evidence",
            confidence="medium",
            advisories=advisories,
            evidence=_compact_evidence(evidence),
        )
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
    def __init__(self, config: GamePowerConfig) -> None:
        self.config = config
        self._positive_samples = 0
        self._negative_samples = 0
        self._active = False

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

        positive = self._sample_supports_gpu_priority(sample, active=self._active)
        if positive:
            self._positive_samples += 1
            self._negative_samples = 0
        else:
            self._negative_samples += 1
            self._positive_samples = 0

        if self._active and self._negative_samples >= self.config.restore_samples:
            self._active = False
            return GamePowerDecision(
                GamePowerAction.RESTORE,
                "restore hysteresis reached",
                classification=classification,
            )

        if self._positive_samples < self.config.activate_samples:
            return GamePowerDecision(
                GamePowerAction.OBSERVE_ONLY,
                "waiting for activation hysteresis",
                classification=classification,
            )

        self._active = True
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
            "package limited with GPU activity",
            classification=classification,
        )

    def _sample_supports_gpu_priority(
        self,
        sample: GamePowerSample,
        *,
        active: bool = False,
    ) -> bool:
        if sample.appid is None:
            return False
        if self.config.target_appid is not None and sample.appid != self.config.target_appid:
            return False
        if sample.rapl is None or sample.pl1_w is None or sample.rapl.package_w is None:
            return False
        if sample.rapl.package_w < self.config.package_pressure_ratio * sample.pl1_w:
            return False
        core_share = sample.rapl.core_share
        if not active and (
            core_share is None or core_share < self.config.core_share_threshold
        ):
            return False
        uncore_share = sample.rapl.uncore_share
        render_busy = sample.fdinfo_busy.get("render")
        has_gpu_activity = (
            uncore_share is not None and uncore_share >= self.config.uncore_share_threshold
        ) or (render_busy is not None and render_busy >= self.config.render_busy_threshold)
        return has_gpu_activity


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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_config = config
        self.config = config
        self.observer = observer
        self.actuator = actuator
        self.output_format = output_format
        self.config_provider = config_provider
        self.sleep = sleep
        self.controller = GamePowerController(config)
        self._started_s = time.monotonic()
        self._snapshot: object | None = None
        self._write_failed = False

    async def run_iterations(self, count: int) -> None:
        for _ in range(count):
            await self.run_once()

    async def run_forever(self) -> None:
        try:
            while True:
                await self.run_once()
        finally:
            self.restore()

    async def run_once(self) -> GamePowerDecision:
        self._refresh_config()
        if self.config.mode == GamePowerMode.OFF:
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
            if self.output_format == "jsonl":
                print(format_decision_jsonl(sample, decision, elapsed_s=elapsed_s), flush=True)
            else:
                print(_format_decision(sample, decision), flush=True)
            return decision
        sample = await self.observer.sample()
        decision = self.controller.evaluate(sample)
        self._apply_decision(decision)
        elapsed_s = time.monotonic() - self._started_s
        if self.output_format == "jsonl":
            print(format_decision_jsonl(sample, decision, elapsed_s=elapsed_s), flush=True)
        else:
            print(_format_decision(sample, decision), flush=True)
        return decision

    def restore(self) -> None:
        if self._snapshot is not None:
            self.actuator.restore(self._snapshot)
            self._snapshot = None

    def _refresh_config(self) -> None:
        if self.config_provider is None:
            return
        next_config = self.config_provider(self.base_config)
        if next_config == self.config:
            return
        self.restore()
        self.config = next_config
        self.controller = GamePowerController(next_config)
        self._write_failed = False

    def _apply_decision(self, decision: GamePowerDecision) -> None:
        if self._write_failed:
            return
        if decision.action in {GamePowerAction.IDLE, GamePowerAction.OBSERVE_ONLY}:
            return
        if decision.action == GamePowerAction.RESTORE:
            self.restore()
            return
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
            self.restore()
            self._write_failed = True


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
        "classification": _classification_json(decision.classification),
        "pressure": _pressure_json(sample.pressure),
    }
    return json.dumps(payload, sort_keys=True)


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
    ) -> None:
        self.rapl = RaplObserver(sysfs_root=sysfs_root)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)
        self.poll_s = poll_s
        self.frame_target = frame_target
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
            pressure=self._read_pressure(process),
        )

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
    return parser


def config_from_args(args: argparse.Namespace) -> GamePowerConfig:
    frame_target = frame_target_from_args(args)
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
    )


def _positive_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a finite positive float") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive float")
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
    observer = SystemGamePowerObserver(
        sysfs_root=args.sysfs_root,
        proc_root=args.proc_root,
        cgroup_root=args.cgroup_root,
        poll_s=config.poll_s,
        frame_target=config.frame_target,
    )
    actuator = CpuPolicyActuator(discover_cpu_policies(args.sysfs_root))
    governor = GamePowerGovernor(
        config=config,
        observer=observer,
        actuator=actuator,
        output_format=args.output_format,
    )
    iterations = max(1, int(args.duration_s / config.poll_s))
    try:
        await governor.run_iterations(iterations)
    finally:
        governor.restore()


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
