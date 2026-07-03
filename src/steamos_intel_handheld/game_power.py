#!/usr/bin/env python3
"""Game-aware CPU/iGPU shared-power governor for Intel SteamOS handhelds."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
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
