# Game Power Governor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reversible game-power governor that detects foreground Steam games, observes package/core/uncore pressure, and applies GPU-priority CPU policy hints under shared TDP.

**Architecture:** Add a focused `game_power.py` module with pure sampling math, CPU policy actuation, decision hysteresis, a standalone validation CLI, and optional `power_control.py` service wiring. Keep the installed service default-off while exposing `observe` and `gpu-priority` modes for controlled device validation.

**Tech Stack:** Python 3.10+, pytest, Linux sysfs CPUFreq/intel_pstate, Linux powercap/RAPL, `/proc/*/fdinfo`, SteamOS gamescope process/cgroup conventions, systemd service arguments, shell device verifier.

---

## File Structure

- Create `src/steamos_intel_handheld/game_power.py`
  - Data models for energy readings, power windows, CPU policies, game samples, decisions, and runtime config.
  - Pure helpers for RAPL delta math and DRM fdinfo busy math.
  - Sysfs/proc observer classes.
  - CPU policy actuator with snapshot/restore.
  - Decision engine with hysteresis.
  - Async governor loop and standalone CLI.
- Create `tests/test_game_power.py`
  - Hardware-free tests using temporary fake `/sys` and `/proc` trees.
- Modify `src/steamos_intel_handheld/power_control.py`
  - Parse game-power options.
  - Build `GamePowerConfig`.
  - Start the governor task only when mode is not `off`.
  - Restore the governor on service cancellation.
- Modify `tests/test_power_control_cli.py`
  - Prove CLI defaults are off and explicit arguments build the expected config.
- Modify `tests/test_integration_assets.py`
  - Prove the installed service explicitly remains `--game-power-mode off`.
  - Prove the guarded device verifier exists and is registered in `harness.toml`.
- Modify `pyproject.toml`
  - Add `steamos-intel-handheld-game-power = "steamos_intel_handheld.game_power:main"`.
- Modify `scripts/install-on-device.sh`
  - Install `/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power`
    because the development installer uses explicit wrappers rather than
    installing console scripts from `pyproject.toml`.
- Modify `data/systemd/steamos-intel-handheld-power-control.service`
  - Add explicit `--game-power-mode off`.
- Create `scripts/verify-game-power-on-device.sh`
  - Guarded SSH verifier for observe and reversible gpu-priority A/B captures.
- Modify `harness.toml`
  - Add a guarded `game-power-device` check.
- Modify `README.md` and `docs/design.md`
  - Document default-off behavior, validation CLI, and upstream path.

## Task 1: Add Pure Power Math And Data Models

**Files:**
- Create: `src/steamos_intel_handheld/game_power.py`
- Create: `tests/test_game_power.py`

- [ ] **Step 1: Write failing tests for power delta math**

Add to `tests/test_game_power.py`:

```python
from pathlib import Path

from steamos_intel_handheld.game_power import (
    EnergyReading,
    GamePowerMode,
    RaplPowerWindow,
    compute_rapl_power_window,
)


def test_compute_rapl_power_window_converts_energy_delta_to_watts():
    start = EnergyReading(
        timestamp_s=10.0,
        energy_uj={
            "package": 100_000_000,
            "core": 40_000_000,
            "uncore": 30_000_000,
            "dram": 2_000_000,
            "psys": 140_000_000,
        },
    )
    end = EnergyReading(
        timestamp_s=20.0,
        energy_uj={
            "package": 319_111_133,
            "core": 125_587_122,
            "uncore": 104_545_525,
            "dram": 6_523_548,
            "psys": 450_515_380,
        },
    )

    window = compute_rapl_power_window(start, end)

    assert isinstance(window, RaplPowerWindow)
    assert window.duration_s == 10.0
    assert round(window.package_w, 2) == 21.91
    assert round(window.core_w, 2) == 8.56
    assert round(window.uncore_w, 2) == 7.45
    assert round(window.dram_w, 2) == 0.45
    assert round(window.psys_w, 2) == 31.05
    assert round(window.core_share, 2) == 0.39
    assert round(window.uncore_share, 2) == 0.34


def test_compute_rapl_power_window_rejects_non_positive_duration():
    start = EnergyReading(timestamp_s=10.0, energy_uj={"package": 1})
    end = EnergyReading(timestamp_s=10.0, energy_uj={"package": 2})

    try:
        compute_rapl_power_window(start, end)
    except ValueError as exc:
        assert "positive duration" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_game_power_mode_values_are_stable_for_cli_and_service_config():
    assert [mode.value for mode in GamePowerMode] == ["off", "observe", "gpu-priority"]
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_compute_rapl_power_window_converts_energy_delta_to_watts tests/test_game_power.py::test_game_power_mode_values_are_stable_for_cli_and_service_config -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'steamos_intel_handheld.game_power'`.

- [ ] **Step 3: Implement the minimal data models and math**

Create `src/steamos_intel_handheld/game_power.py`:

```python
#!/usr/bin/env python3
"""Game-aware CPU/iGPU shared-power governor for Intel SteamOS handhelds."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections.abc import Iterable
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
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_compute_rapl_power_window_converts_energy_delta_to_watts tests/test_game_power.py::test_compute_rapl_power_window_rejects_non_positive_duration tests/test_game_power.py::test_game_power_mode_values_are_stable_for_cli_and_service_config -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py
git commit -m "feat: add game power data model"
```

## Task 2: Add CPU Policy Discovery And Reversible Actuator

**Files:**
- Modify: `src/steamos_intel_handheld/game_power.py`
- Modify: `tests/test_game_power.py`

- [ ] **Step 1: Write failing tests for CPU policy discovery**

Append to `tests/test_game_power.py`:

```python
from steamos_intel_handheld.game_power import (
    CpuPolicyActuator,
    CpuPolicyClass,
    discover_cpu_policies,
)


def make_cpu_policy(
    sysfs_root: Path,
    index: int,
    *,
    cpu: int,
    capacity: int,
    epp: str = "balance_performance",
    max_freq: int = 4_800_000,
    min_freq: int = 400_000,
):
    policy = sysfs_root / "devices" / "system" / "cpu" / "cpufreq" / f"policy{index}"
    policy.mkdir(parents=True)
    (policy / "affected_cpus").write_text(str(cpu))
    (policy / "related_cpus").write_text(str(cpu))
    (policy / "energy_performance_preference").write_text(epp)
    (policy / "energy_performance_available_preferences").write_text(
        "default performance balance_performance balance_power power"
    )
    (policy / "scaling_max_freq").write_text(str(max_freq))
    (policy / "scaling_min_freq").write_text(str(min_freq))
    cpu_root = sysfs_root / "devices" / "system" / "cpu" / f"cpu{cpu}"
    cpu_root.mkdir(parents=True)
    (cpu_root / "cpu_capacity").write_text(str(capacity))
    return policy


def test_discover_cpu_policies_classifies_highest_capacity_as_pcore(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)

    policies = discover_cpu_policies(sysfs_root)

    assert [policy.name for policy in policies] == ["policy0", "policy1"]
    assert policies[0].policy_class == CpuPolicyClass.PCORE
    assert policies[1].policy_class == CpuPolicyClass.ECORE
    assert policies[0].current_epp == "balance_performance"
    assert policies[1].scaling_max_freq == 3_700_000


def test_cpu_policy_actuator_applies_and_restores_epp_and_frequency_caps(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024, max_freq=4_800_000)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)
    policies = discover_cpu_policies(sysfs_root)
    actuator = CpuPolicyActuator(policies)

    snapshot = actuator.snapshot()
    actuator.apply(epp="balance_power", pcore_max_khz=3_200_000, ecore_max_khz=2_800_000)

    assert (policies[0].path / "energy_performance_preference").read_text() == "balance_power"
    assert (policies[1].path / "energy_performance_preference").read_text() == "balance_power"
    assert (policies[0].path / "scaling_max_freq").read_text() == "3200000"
    assert (policies[1].path / "scaling_max_freq").read_text() == "2800000"

    actuator.restore(snapshot)

    assert (policies[0].path / "energy_performance_preference").read_text() == "balance_performance"
    assert (policies[1].path / "energy_performance_preference").read_text() == "balance_performance"
    assert (policies[0].path / "scaling_max_freq").read_text() == "4800000"
    assert (policies[1].path / "scaling_max_freq").read_text() == "3700000"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_discover_cpu_policies_classifies_highest_capacity_as_pcore tests/test_game_power.py::test_cpu_policy_actuator_applies_and_restores_epp_and_frequency_caps -q
```

Expected: FAIL with missing `discover_cpu_policies` or `CpuPolicyActuator`.

- [ ] **Step 3: Implement CPU policy discovery and actuator**

Append to `src/steamos_intel_handheld/game_power.py`:

```python
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
                available_epp=tuple(_read_text(path / "energy_performance_available_preferences").split()),
                current_epp=_read_optional_text(path / "energy_performance_preference"),
                scaling_min_freq=_read_optional_int(path / "scaling_min_freq"),
                scaling_max_freq=_read_optional_int(path / "scaling_max_freq"),
            )
        )
    return policies


def _policy_sort_key(path: Path) -> tuple[str, int]:
    match = re.search(r"(\\d+)$", path.name)
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
        capacity = _read_optional_int(sysfs_root / "devices" / "system" / "cpu" / f"cpu{cpu}" / "cpu_capacity")
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
```

- [ ] **Step 4: Run CPU policy tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_discover_cpu_policies_classifies_highest_capacity_as_pcore tests/test_game_power.py::test_cpu_policy_actuator_applies_and_restores_epp_and_frequency_caps -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py
git commit -m "feat: add reversible CPU policy actuator"
```

## Task 3: Add RAPL, fdinfo, And Foreground Game Observation

**Files:**
- Modify: `src/steamos_intel_handheld/game_power.py`
- Modify: `tests/test_game_power.py`

- [ ] **Step 1: Write failing tests for RAPL and fdinfo parsing**

Append to `tests/test_game_power.py`:

```python
from steamos_intel_handheld.game_power import (
    GameProcess,
    RaplObserver,
    compute_fdinfo_busy,
    parse_fdinfo_engine_times,
)


def make_rapl_domain(sysfs_root: Path, domain: str, name: str, energy_uj: int):
    path = sysfs_root / "class" / "powercap" / domain
    path.mkdir(parents=True)
    (path / "name").write_text(name)
    (path / "energy_uj").write_text(str(energy_uj))
    return path


def test_rapl_observer_reads_named_domains(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_rapl_domain(sysfs_root, "intel-rapl:0", "package-0", 100)
    make_rapl_domain(sysfs_root, "intel-rapl:0:0", "core", 40)
    make_rapl_domain(sysfs_root, "intel-rapl:0:1", "uncore", 30)
    make_rapl_domain(sysfs_root, "intel-rapl:1", "psys", 140)

    reading = RaplObserver(sysfs_root=sysfs_root, clock=lambda: 5.0).read()

    assert reading.timestamp_s == 5.0
    assert reading.energy_uj == {
        "package": 100,
        "core": 40,
        "uncore": 30,
        "psys": 140,
    }


def test_parse_fdinfo_engine_times_reads_drm_engine_keys():
    fdinfo = \"\"\"
drm-engine-render: 123456789 ns
drm-engine-copy: 999 ns
drm-engine-compute: 234000000 ns
drm-total-engine-render: 200000000 ns
\"\"\"

    parsed = parse_fdinfo_engine_times(fdinfo)

    assert parsed["render"] == 123456789
    assert parsed["compute"] == 234000000
    assert parsed["total-render"] == 200000000


def test_compute_fdinfo_busy_uses_nanosecond_delta_over_window():
    start = {"render": 1_000_000_000, "compute": 500_000_000}
    end = {"render": 2_500_000_000, "compute": 1_000_000_000}

    busy = compute_fdinfo_busy(start, end, duration_s=2.0)

    assert busy["render"] == 0.75
    assert busy["compute"] == 0.25
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_rapl_observer_reads_named_domains tests/test_game_power.py::test_parse_fdinfo_engine_times_reads_drm_engine_keys tests/test_game_power.py::test_compute_fdinfo_busy_uses_nanosecond_delta_over_window -q
```

Expected: FAIL with missing observer/parser symbols.

- [ ] **Step 3: Implement observers and parsers**

Append to `src/steamos_intel_handheld/game_power.py`:

```python
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
        match = re.match(r"drm-(total-)?engine-([^:]+):\\s+(\\d+)\\s+ns$", line)
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
```

- [ ] **Step 4: Run observer/parser tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_rapl_observer_reads_named_domains tests/test_game_power.py::test_parse_fdinfo_engine_times_reads_drm_engine_keys tests/test_game_power.py::test_compute_fdinfo_busy_uses_nanosecond_delta_over_window -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py
git commit -m "feat: add game power observers"
```

## Task 4: Add Decision Engine With Hysteresis

**Files:**
- Modify: `src/steamos_intel_handheld/game_power.py`
- Modify: `tests/test_game_power.py`

- [ ] **Step 1: Write failing tests for policy decisions**

Append to `tests/test_game_power.py`:

```python
from steamos_intel_handheld.game_power import (
    GamePowerAction,
    GamePowerConfig,
    GamePowerController,
    GamePowerSample,
)


def make_sample(
    *,
    appid: str | None = "1091500",
    package_w: float = 22.0,
    core_w: float = 8.8,
    uncore_w: float = 7.4,
    pl1_w: int = 22,
    render_busy: float | None = 0.75,
):
    return GamePowerSample(
        appid=appid,
        rapl=RaplPowerWindow(
            duration_s=2.0,
            package_w=package_w,
            core_w=core_w,
            uncore_w=uncore_w,
            dram_w=0.4,
            psys_w=31.0,
        ),
        pl1_w=pl1_w,
        fdinfo_busy={"render": render_busy} if render_busy is not None else {},
    )


def test_controller_waits_for_hysteresis_before_applying_gpu_priority():
    controller = GamePowerController(GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY))

    first = controller.evaluate(make_sample())
    second = controller.evaluate(make_sample())

    assert first.action == GamePowerAction.OBSERVE_ONLY
    assert second.action == GamePowerAction.GPU_PRIORITY_EPP


def test_controller_restores_after_consecutive_invalid_samples():
    controller = GamePowerController(GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY))
    controller.evaluate(make_sample())
    controller.evaluate(make_sample())

    assert controller.evaluate(make_sample(appid=None)).action == GamePowerAction.OBSERVE_ONLY
    assert controller.evaluate(make_sample(appid=None)).action == GamePowerAction.OBSERVE_ONLY
    assert controller.evaluate(make_sample(appid=None)).action == GamePowerAction.RESTORE


def test_controller_uses_cpu_cap_when_enabled_and_epp_is_not_enough():
    config = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, cpu_cap_enabled=True)
    controller = GamePowerController(config)

    controller.evaluate(make_sample(core_w=10.0, render_busy=0.90))
    decision = controller.evaluate(make_sample(core_w=10.0, render_busy=0.90))

    assert decision.action == GamePowerAction.GPU_PRIORITY_CPU_CAP
```

- [ ] **Step 2: Run policy tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_controller_waits_for_hysteresis_before_applying_gpu_priority tests/test_game_power.py::test_controller_restores_after_consecutive_invalid_samples tests/test_game_power.py::test_controller_uses_cpu_cap_when_enabled_and_epp_is_not_enough -q
```

Expected: FAIL with missing `GamePowerConfig`, `GamePowerSample`, or `GamePowerController`.

- [ ] **Step 3: Implement decision models and controller**

Append to `src/steamos_intel_handheld/game_power.py`:

```python
@dataclass(frozen=True)
class GamePowerConfig:
    mode: GamePowerMode = GamePowerMode.OFF
    poll_s: float = 2.0
    epp: str = "balance_power"
    pcore_max_khz: int = 3_200_000
    ecore_max_khz: int = 2_800_000
    cpu_cap_enabled: bool = False
    target_appid: str | None = None
    package_pressure_ratio: float = 0.94
    core_share_threshold: float = 0.30
    uncore_share_threshold: float = 0.20
    render_busy_threshold: float = 0.70
    activate_samples: int = 2
    restore_samples: int = 3


@dataclass(frozen=True)
class GamePowerSample:
    appid: str | None
    rapl: RaplPowerWindow | None
    pl1_w: int | None
    fdinfo_busy: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class GamePowerDecision:
    action: GamePowerAction
    reason: str


class GamePowerController:
    def __init__(self, config: GamePowerConfig) -> None:
        self.config = config
        self._positive_samples = 0
        self._negative_samples = 0
        self._active = False

    def evaluate(self, sample: GamePowerSample) -> GamePowerDecision:
        if self.config.mode == GamePowerMode.OFF:
            return GamePowerDecision(GamePowerAction.IDLE, "mode is off")
        if self.config.mode == GamePowerMode.OBSERVE:
            return GamePowerDecision(GamePowerAction.OBSERVE_ONLY, "mode is observe")

        positive = self._sample_supports_gpu_priority(sample)
        if positive:
            self._positive_samples += 1
            self._negative_samples = 0
        else:
            self._negative_samples += 1
            self._positive_samples = 0

        if self._active and self._negative_samples >= self.config.restore_samples:
            self._active = False
            return GamePowerDecision(GamePowerAction.RESTORE, "restore hysteresis reached")

        if self._positive_samples < self.config.activate_samples:
            return GamePowerDecision(GamePowerAction.OBSERVE_ONLY, "waiting for activation hysteresis")

        self._active = True
        if self.config.cpu_cap_enabled and _sample_core_pressure_high(sample):
            return GamePowerDecision(GamePowerAction.GPU_PRIORITY_CPU_CAP, "package limited with high core pressure")
        return GamePowerDecision(GamePowerAction.GPU_PRIORITY_EPP, "package limited with GPU activity")

    def _sample_supports_gpu_priority(self, sample: GamePowerSample) -> bool:
        if sample.appid is None:
            return False
        if self.config.target_appid is not None and sample.appid != self.config.target_appid:
            return False
        if sample.rapl is None or sample.pl1_w is None or sample.rapl.package_w is None:
            return False
        if sample.rapl.package_w < self.config.package_pressure_ratio * sample.pl1_w:
            return False
        core_share = sample.rapl.core_share
        if core_share is None or core_share < self.config.core_share_threshold:
            return False
        uncore_share = sample.rapl.uncore_share
        render_busy = sample.fdinfo_busy.get("render")
        has_gpu_activity = (
            (uncore_share is not None and uncore_share >= self.config.uncore_share_threshold)
            or (render_busy is not None and render_busy >= self.config.render_busy_threshold)
        )
        return has_gpu_activity


def _sample_core_pressure_high(sample: GamePowerSample) -> bool:
    return sample.rapl is not None and sample.rapl.core_share is not None and sample.rapl.core_share >= 0.38
```

- [ ] **Step 4: Run decision tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_controller_waits_for_hysteresis_before_applying_gpu_priority tests/test_game_power.py::test_controller_restores_after_consecutive_invalid_samples tests/test_game_power.py::test_controller_uses_cpu_cap_when_enabled_and_epp_is_not_enough -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py
git commit -m "feat: add game power decision engine"
```

## Task 5: Add Governor Runtime And Standalone CLI

**Files:**
- Modify: `src/steamos_intel_handheld/game_power.py`
- Modify: `tests/test_game_power.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing tests for CLI config and runtime actuation**

Append to `tests/test_game_power.py`:

```python
from steamos_intel_handheld import game_power
from steamos_intel_handheld.game_power import GamePowerGovernor


class FakeObserver:
    def __init__(self, samples):
        self.samples = list(samples)

    async def sample(self):
        return self.samples.pop(0)


class RecordingActuator:
    def __init__(self):
        self.events = []
        self.snapshot_value = object()

    def snapshot(self):
        self.events.append(("snapshot",))
        return self.snapshot_value

    def apply(self, *, epp, pcore_max_khz=None, ecore_max_khz=None):
        self.events.append(("apply", epp, pcore_max_khz, ecore_max_khz))

    def restore(self, snapshot):
        self.events.append(("restore", snapshot))


class FailingActuator(RecordingActuator):
    def apply(self, *, epp, pcore_max_khz=None, ecore_max_khz=None):
        self.events.append(("apply-failed", epp, pcore_max_khz, ecore_max_khz))
        raise OSError("simulated sysfs write failure")


def test_build_parser_defaults_game_power_cli_to_observe_for_standalone_probe():
    args = game_power.build_parser().parse_args([])
    config = game_power.config_from_args(args)

    assert config.mode == GamePowerMode.OBSERVE
    assert config.cpu_cap_enabled is False


def test_governor_applies_epp_and_restores_when_controller_requests_restore():
    config = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)
    observer = FakeObserver(
        [
            make_sample(),
            make_sample(),
            make_sample(appid=None),
            make_sample(appid=None),
            make_sample(appid=None),
        ]
    )
    actuator = RecordingActuator()
    governor = GamePowerGovernor(
        config=config,
        observer=observer,
        actuator=actuator,
    )

    import asyncio

    asyncio.run(governor.run_iterations(5))

    assert ("snapshot",) in actuator.events
    assert ("apply", "balance_power", None, None) in actuator.events
    assert ("restore", actuator.snapshot_value) in actuator.events


def test_governor_restores_snapshot_when_active_write_fails():
    config = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)
    observer = FakeObserver([make_sample(), make_sample()])
    actuator = FailingActuator()
    governor = GamePowerGovernor(config=config, observer=observer, actuator=actuator)

    import asyncio

    asyncio.run(governor.run_iterations(2))

    assert ("snapshot",) in actuator.events
    assert ("apply-failed", "balance_power", None, None) in actuator.events
    assert ("restore", actuator.snapshot_value) in actuator.events
```

- [ ] **Step 2: Run runtime tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_build_parser_defaults_game_power_cli_to_observe_for_standalone_probe tests/test_game_power.py::test_governor_applies_epp_and_restores_when_controller_requests_restore tests/test_game_power.py::test_governor_restores_snapshot_when_active_write_fails -q
```

Expected: FAIL with missing parser/governor.

- [ ] **Step 3: Implement governor runtime and CLI**

Append to `src/steamos_intel_handheld/game_power.py`:

```python
class GamePowerGovernor:
    def __init__(
        self,
        *,
        config: GamePowerConfig,
        observer: object,
        actuator: object,
    ) -> None:
        self.config = config
        self.observer = observer
        self.actuator = actuator
        self.controller = GamePowerController(config)
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
        sample = await self.observer.sample()
        decision = self.controller.evaluate(sample)
        self._apply_decision(decision)
        print(_format_decision(sample, decision), flush=True)
        return decision

    def restore(self) -> None:
        if self._snapshot is not None:
            self.actuator.restore(self._snapshot)
            self._snapshot = None

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
            print(f"game-power: active write failed; restoring and disabling writes: {exc}", file=sys.stderr)
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


def _fmt_w(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[mode.value for mode in GamePowerMode], default=GamePowerMode.OBSERVE.value)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--epp", default="balance_power")
    parser.add_argument("--pcore-max-mhz", type=int, default=3200)
    parser.add_argument("--ecore-max-mhz", type=int, default=2800)
    parser.add_argument("--cpu-cap", action="store_true")
    parser.add_argument("--target-appid")
    parser.add_argument("--sysfs-root", default="/sys")
    parser.add_argument("--proc-root", default="/proc")
    return parser


def config_from_args(args: argparse.Namespace) -> GamePowerConfig:
    return GamePowerConfig(
        mode=GamePowerMode(args.mode),
        poll_s=args.poll_s,
        epp=args.epp,
        pcore_max_khz=args.pcore_max_mhz * 1000,
        ecore_max_khz=args.ecore_max_mhz * 1000,
        cpu_cap_enabled=bool(args.cpu_cap),
        target_appid=args.target_appid,
    )


async def run_cli(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    observer = SystemGamePowerObserver(
        sysfs_root=args.sysfs_root,
        proc_root=args.proc_root,
        poll_s=config.poll_s,
    )
    actuator = CpuPolicyActuator(discover_cpu_policies(args.sysfs_root))
    governor = GamePowerGovernor(config=config, observer=observer, actuator=actuator)
    iterations = max(1, int(args.duration_s / config.poll_s))
    try:
        await governor.run_iterations(iterations)
    finally:
        governor.restore()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(run_cli(args))
```

Also add a temporary `SystemGamePowerObserver` implementation that returns
observe-only samples using RAPL deltas and no foreground app until Task 6 fills
process detection:

```python
class SystemGamePowerObserver:
    def __init__(
        self,
        *,
        sysfs_root: str | Path = "/sys",
        proc_root: str | Path = "/proc",
        poll_s: float = 2.0,
    ) -> None:
        self.rapl = RaplObserver(sysfs_root=sysfs_root)
        self.proc_root = Path(proc_root)
        self.poll_s = poll_s
        self._previous_rapl: EnergyReading | None = None

    async def sample(self) -> GamePowerSample:
        start = self._previous_rapl or self.rapl.read()
        await asyncio.sleep(self.poll_s)
        end = self.rapl.read()
        self._previous_rapl = end
        try:
            rapl = compute_rapl_power_window(start, end)
        except ValueError:
            rapl = None
        return GamePowerSample(
            appid=None,
            rapl=rapl,
            pl1_w=_read_current_pl1_w(self.rapl.sysfs_root),
            fdinfo_busy={},
        )


def _read_current_pl1_w(sysfs_root: Path) -> int | None:
    domain = sysfs_root / "class" / "powercap" / "intel-rapl:0"
    for name_file in sorted(domain.glob("constraint_*_name")):
        if _read_text(name_file) != "long_term":
            continue
        power_file = domain / f"{name_file.name.removesuffix('_name')}_power_limit_uw"
        value = _read_optional_int(power_file)
        return value // 1_000_000 if value is not None else None
    value = _read_optional_int(domain / "constraint_0_power_limit_uw")
    return value // 1_000_000 if value is not None else None
```

Modify `pyproject.toml`:

```toml
[project.scripts]
steamos-intel-handheld-power-control = "steamos_intel_handheld.power_control:main"
steamos-intel-handheld-ec-control = "steamos_intel_handheld.ec_charge_control:main"
steamos-intel-handheld-restore-etc = "steamos_intel_handheld.restore_etc:main"
steamos-intel-handheld-game-power = "steamos_intel_handheld.game_power:main"
```

- [ ] **Step 4: Run runtime tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_build_parser_defaults_game_power_cli_to_observe_for_standalone_probe tests/test_game_power.py::test_governor_applies_epp_and_restores_when_controller_requests_restore tests/test_game_power.py::test_governor_restores_snapshot_when_active_write_fails -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py pyproject.toml
git commit -m "feat: add game power governor runtime"
```

## Task 6: Add Foreground Steam Game And fdinfo Sampling

**Files:**
- Modify: `src/steamos_intel_handheld/game_power.py`
- Modify: `tests/test_game_power.py`

- [ ] **Step 1: Write failing tests for process detection**

Append to `tests/test_game_power.py`:

```python
from steamos_intel_handheld.game_power import find_steam_game_processes


def make_proc_game(proc_root: Path, pid: int, appid: str, command: str = "Cyberpunk2077.exe"):
    root = proc_root / str(pid)
    root.mkdir(parents=True)
    (root / "cmdline").write_bytes(command.encode() + b"\\0")
    (root / "cgroup").write_text(
        f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-steam-app{appid}-{pid}.scope\\n"
    )
    return root


def test_find_steam_game_processes_reads_appid_from_cgroup(tmp_path):
    proc_root = tmp_path / "proc"
    make_proc_game(proc_root, 1234, "1091500")

    processes = find_steam_game_processes(proc_root)

    assert processes == [GameProcess(pid=1234, appid="1091500", command="Cyberpunk2077.exe")]
```

- [ ] **Step 2: Run process detection test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_find_steam_game_processes_reads_appid_from_cgroup -q
```

Expected: FAIL with missing `find_steam_game_processes`.

- [ ] **Step 3: Implement process detection and fdinfo aggregation**

Append to `src/steamos_intel_handheld/game_power.py`:

```python
STEAM_APP_RE = re.compile(r"app-steam-app(\\d+)-")


def find_steam_game_processes(proc_root: str | Path = "/proc") -> list[GameProcess]:
    proc_root = Path(proc_root)
    processes: list[GameProcess] = []
    for entry in sorted(proc_root.iterdir(), key=lambda path: int(path.name) if path.name.isdigit() else -1):
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
            )
        )
    return processes


def _read_cmdline(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    parts = [part.decode("utf-8", errors="replace") for part in raw.split(b"\\0") if part]
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
```

Update `SystemGamePowerObserver.sample()` so it:

```python
processes = find_steam_game_processes(self.proc_root)
process = processes[0] if processes else None
fdinfo_start = read_process_fdinfo_engines(self.proc_root, process.pid) if process else {}
await asyncio.sleep(self.poll_s)
end = self.rapl.read()
fdinfo_end = read_process_fdinfo_engines(self.proc_root, process.pid) if process else {}
busy = compute_fdinfo_busy(fdinfo_start, fdinfo_end, duration_s=self.poll_s) if process else {}
...
appid=process.appid if process else None
fdinfo_busy=busy
```

- [ ] **Step 4: Run process detection test and full game_power tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py
git commit -m "feat: detect Steam game power samples"
```

## Task 7: Wire Governor Into Power Control Service

**Files:**
- Modify: `src/steamos_intel_handheld/power_control.py`
- Modify: `tests/test_power_control_cli.py`
- Modify: `tests/test_integration_assets.py`
- Modify: `data/systemd/steamos-intel-handheld-power-control.service`
- Modify: `scripts/install-on-device.sh`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_power_control_cli.py`:

```python
def test_parser_configures_game_power_defaults_off():
    args = power_control.build_parser().parse_args(["serve"])
    config = power_control.build_game_power_config(args)

    assert config.mode.value == "off"
    assert config.cpu_cap_enabled is False


def test_parser_configures_game_power_gpu_priority_options():
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--game-power-mode",
            "gpu-priority",
            "--game-power-poll-s",
            "1.5",
            "--game-power-epp",
            "balance_power",
            "--game-power-pcore-max-mhz",
            "3100",
            "--game-power-ecore-max-mhz",
            "2700",
            "--game-power-cpu-cap",
            "on",
            "--game-power-target-appid",
            "1091500",
        ]
    )
    config = power_control.build_game_power_config(args)

    assert config.mode.value == "gpu-priority"
    assert config.poll_s == 1.5
    assert config.epp == "balance_power"
    assert config.pcore_max_khz == 3_100_000
    assert config.ecore_max_khz == 2_700_000
    assert config.cpu_cap_enabled is True
    assert config.target_appid == "1091500"
```

Append to `tests/test_integration_assets.py`:

```python
def test_power_control_service_keeps_game_power_governor_off_by_default():
    unit = (ROOT / "data/systemd/steamos-intel-handheld-power-control.service").read_text()

    assert "--game-power-mode off" in unit


def test_installer_installs_game_power_cli_wrapper():
    script = (ROOT / "scripts" / "install-on-device.sh").read_text()

    assert "steamos-intel-handheld-game-power" in script
    assert "python3 -m steamos_intel_handheld.game_power" in script
```

- [ ] **Step 2: Run CLI and integration tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_power_control_cli.py::test_parser_configures_game_power_defaults_off tests/test_power_control_cli.py::test_parser_configures_game_power_gpu_priority_options tests/test_integration_assets.py::test_power_control_service_keeps_game_power_governor_off_by_default tests/test_integration_assets.py::test_installer_installs_game_power_cli_wrapper -q
```

Expected: FAIL with missing parser arguments and unit flag.

- [ ] **Step 3: Implement parser and config wiring**

Modify imports near the top of `src/steamos_intel_handheld/power_control.py`:

```python
import signal
```

Add the game-power imports:

```python
from steamos_intel_handheld.game_power import (
    CpuPolicyActuator,
    GamePowerConfig,
    GamePowerGovernor,
    GamePowerMode,
    SystemGamePowerObserver,
    discover_cpu_policies,
)
```

Add helper functions before `prepare_mangohud_sensors_from_args()`:

```python
def build_game_power_config(args: argparse.Namespace) -> GamePowerConfig:
    return GamePowerConfig(
        mode=GamePowerMode(args.game_power_mode),
        poll_s=args.game_power_poll_s,
        epp=args.game_power_epp,
        pcore_max_khz=args.game_power_pcore_max_mhz * 1000,
        ecore_max_khz=args.game_power_ecore_max_mhz * 1000,
        cpu_cap_enabled=args.game_power_cpu_cap == "on",
        target_appid=args.game_power_target_appid,
    )


def build_game_power_governor(args: argparse.Namespace) -> GamePowerGovernor | None:
    config = build_game_power_config(args)
    if config.mode == GamePowerMode.OFF:
        return None
    observer = SystemGamePowerObserver(
        sysfs_root=args.sysfs_root,
        proc_root="/proc",
        poll_s=config.poll_s,
    )
    actuator = CpuPolicyActuator(discover_cpu_policies(args.sysfs_root))
    return GamePowerGovernor(config=config, observer=observer, actuator=actuator)
```

In `serve()`, replace the final `await asyncio.Future()` with a managed stop
future. Keep task references so active CPU policy can be restored during
service shutdown:

```python
    stop_future: asyncio.Future[None] = asyncio.Future()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        if not stop_future.done():
            stop_future.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    power_source_task = asyncio.create_task(poll_power_source_changes(backend))
    game_power_governor = build_game_power_governor(args)
    game_power_task: asyncio.Task[None] | None = None
    if game_power_governor is not None:
        game_power_task = asyncio.create_task(game_power_governor.run_forever())

    try:
        await stop_future
    except asyncio.CancelledError:
        pass
    finally:
        power_source_task.cancel()
        if game_power_task is not None:
            game_power_task.cancel()
        if game_power_governor is not None:
            game_power_governor.restore()
        await asyncio.gather(
            *(task for task in (power_source_task, game_power_task) if task is not None),
            return_exceptions=True,
        )
```

Add parser arguments in `build_parser()`:

```python
    parser.add_argument(
        "--game-power-mode",
        choices=[mode.value for mode in GamePowerMode],
        default=GamePowerMode.OFF.value,
    )
    parser.add_argument("--game-power-poll-s", type=float, default=2.0)
    parser.add_argument("--game-power-epp", default="balance_power")
    parser.add_argument("--game-power-pcore-max-mhz", type=int, default=3200)
    parser.add_argument("--game-power-ecore-max-mhz", type=int, default=2800)
    parser.add_argument("--game-power-cpu-cap", choices=["on", "off"], default="off")
    parser.add_argument("--game-power-target-appid")
```

Modify `data/systemd/steamos-intel-handheld-power-control.service` `ExecStart=`
to include:

```text
--game-power-mode off
```

Modify `scripts/install-on-device.sh` after the restore wrapper block:

```bash
  cat >/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/opt/steamos-intel-handheld/src
exec /usr/bin/python3 -m steamos_intel_handheld.game_power "$@"
WRAPPER
  chmod 0755 /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power
```

- [ ] **Step 4: Run CLI and integration tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_power_control_cli.py::test_parser_configures_game_power_defaults_off tests/test_power_control_cli.py::test_parser_configures_game_power_gpu_priority_options tests/test_integration_assets.py::test_power_control_service_keeps_game_power_governor_off_by_default tests/test_integration_assets.py::test_installer_installs_game_power_cli_wrapper -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/power_control.py tests/test_power_control_cli.py tests/test_integration_assets.py data/systemd/steamos-intel-handheld-power-control.service scripts/install-on-device.sh
git commit -m "feat: wire game power governor into service"
```

## Task 8: Add Guarded Device Verification Harness

**Files:**
- Create: `scripts/verify-game-power-on-device.sh`
- Modify: `harness.toml`
- Modify: `tests/test_integration_assets.py`

- [ ] **Step 1: Write failing integration tests for verifier registration**

Append to `tests/test_integration_assets.py`:

```python
def test_game_power_device_verifier_is_registered_as_guarded_harness_check():
    harness = (ROOT / "harness.toml").read_text()
    script = ROOT / "scripts" / "verify-game-power-on-device.sh"

    assert script.exists()
    assert 'id = "game-power-device"' in harness
    assert "scripts/verify-game-power-on-device.sh root@10.100.0.19" in harness
    assert 'tier = "guarded"' in harness
    assert 'safe_for_agents = false' in harness


def test_game_power_device_verifier_restores_cpu_policy_snapshot():
    script = (ROOT / "scripts" / "verify-game-power-on-device.sh").read_text()

    assert "snapshot_cpu_policy" in script
    assert "restore_cpu_policy" in script
    assert "assert_cpu_policy_restored" in script
    assert "diff -u" in script
    assert "steamos-intel-handheld-game-power --mode observe" in script
    assert "steamos-intel-handheld-game-power --mode gpu-priority" in script
```

- [ ] **Step 2: Run verifier tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_game_power_device_verifier_is_registered_as_guarded_harness_check tests/test_integration_assets.py::test_game_power_device_verifier_restores_cpu_policy_snapshot -q
```

Expected: FAIL because the script and harness check do not exist.

- [ ] **Step 3: Add guarded verifier script**

Create `scripts/verify-game-power-on-device.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

target="${1:?usage: scripts/verify-game-power-on-device.sh root@host}"
duration_s="${VERIFY_GAME_POWER_DURATION_S:-30}"
appid="${VERIFY_GAME_POWER_APPID:-1091500}"

ssh "$target" "DURATION_S='$duration_s' APPID='$appid' bash -s" <<'REMOTE'
set -euo pipefail

DURATION_S="${DURATION_S:-30}"
APPID="${APPID:-1091500}"
snapshot="/tmp/steamos-intel-handheld-game-power-cpufreq.snapshot"
restored_snapshot="/tmp/steamos-intel-handheld-game-power-cpufreq.restored"

snapshot_cpu_policy() {
  local output="${1:-$snapshot}"
  : > "$output"
  for policy in /sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$policy" ] || continue
    name="$(basename "$policy")"
    epp="-"
    max="-"
    [ -r "$policy/energy_performance_preference" ] && epp="$(cat "$policy/energy_performance_preference")"
    [ -r "$policy/scaling_max_freq" ] && max="$(cat "$policy/scaling_max_freq")"
    printf '%s %s %s\n' "$name" "$epp" "$max" >> "$output"
  done
}

restore_cpu_policy() {
  [ -r "$snapshot" ] || return 0
  while read -r name epp max; do
    policy="/sys/devices/system/cpu/cpufreq/$name"
    [ -d "$policy" ] || continue
    if [ "$epp" != "-" ] && [ -w "$policy/energy_performance_preference" ]; then
      printf '%s' "$epp" > "$policy/energy_performance_preference"
    fi
    if [ "$max" != "-" ] && [ -w "$policy/scaling_max_freq" ]; then
      printf '%s' "$max" > "$policy/scaling_max_freq"
    fi
  done < "$snapshot"
}

assert_cpu_policy_restored() {
  snapshot_cpu_policy "$restored_snapshot"
  if ! diff -u "$snapshot" "$restored_snapshot"; then
    echo "CPU policy was not restored to the pre-test snapshot" >&2
    return 1
  fi
}

trap restore_cpu_policy EXIT

snapshot_cpu_policy

echo "== baseline CPU policy =="
cat "$snapshot"

echo "== SteamOS TDP =="
runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  steamosctl get-tdp-limit || true

echo "== observe mode =="
steamos-intel-handheld-game-power --mode observe --duration-s "$DURATION_S" --target-appid "$APPID"

echo "== gpu-priority EPP mode =="
steamos-intel-handheld-game-power \
  --mode gpu-priority \
  --duration-s "$DURATION_S" \
  --target-appid "$APPID"

echo "== restored CPU policy =="
restore_cpu_policy
assert_cpu_policy_restored
cat "$restored_snapshot"
REMOTE
```

Make it executable:

```bash
chmod +x scripts/verify-game-power-on-device.sh
```

- [ ] **Step 4: Register guarded harness check**

Append to `harness.toml`:

```toml
[[checks]]
id = "game-power-device"
description = "Real-game CPU/iGPU shared-power governor A/B check on the handheld."
command = "scripts/verify-game-power-on-device.sh root@10.100.0.19"
requires = ["root-ssh", "handheld", "foreground-game"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "about 1-3 minutes after Cyberpunk 2077 is in a repeatable scene"
evidence = "Full observe and gpu-priority output plus restored CPU policy snapshot."
```

- [ ] **Step 5: Run verifier registration tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_game_power_device_verifier_is_registered_as_guarded_harness_check tests/test_integration_assets.py::test_game_power_device_verifier_restores_cpu_policy_snapshot -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add scripts/verify-game-power-on-device.sh harness.toml tests/test_integration_assets.py
git commit -m "test: add guarded game power device verifier"
```

## Task 9: Document User-Facing Behavior

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `docs/references.md`
- Modify: `tests/test_pages_site.py` if generated public pages include feature text

- [ ] **Step 1: Write failing documentation asset test**

Append to `tests/test_integration_assets.py`:

```python
def test_docs_describe_game_power_governor_default_off_and_reversible():
    readme = (ROOT / "README.md").read_text()
    design = (ROOT / "docs" / "design.md").read_text()

    assert "Game power governor" in readme
    assert "--game-power-mode off" in readme
    assert "restores the previous CPU EPP and frequency limits" in readme
    assert "Game power governor" in design
    assert "observe" in design
    assert "gpu-priority" in design
```

- [ ] **Step 2: Run documentation test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_docs_describe_game_power_governor_default_off_and_reversible -q
```

Expected: FAIL until docs are updated.

- [ ] **Step 3: Update docs**

Add to `README.md` after the TDP integration section:

```markdown
## Game power governor

The optional game power governor helps Intel integrated graphics keep package
power headroom while a foreground Steam game is running under a shared RAPL
package TDP. It is installed default-off:

```text
--game-power-mode off
```

Validation can run the standalone probe:

```bash
steamos-intel-handheld-game-power --mode observe --duration-s 30
steamos-intel-handheld-game-power --mode gpu-priority --duration-s 30 --target-appid 1091500
```

`observe` only reads sensors. `gpu-priority` snapshots CPUFreq policy state,
then can apply a reversible EPP hint and optional frequency caps while the
foreground game remains active. The governor restores the previous CPU EPP and
frequency limits when the game disappears, the process loses eligibility, the
command exits, or an error occurs.
```

Add to `docs/design.md`:

```markdown
## Game power governor

The game power governor is a separate default-off control loop. It does not
replace SteamOS Manager's TDP slider and does not raise PL1 automatically.
Instead, it observes RAPL package/core/uncore power and foreground Steam game
activity. In `gpu-priority` mode it uses reversible CPU EPP hints, and only then
optional CPU max-frequency caps, to reduce CPU package pressure when the iGPU is
the limiting side of the shared package budget.
```

- [ ] **Step 4: Run documentation test**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_docs_describe_game_power_governor_default_off_and_reversible -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add README.md docs/design.md docs/references.md tests/test_integration_assets.py
git commit -m "docs: document game power governor"
```

## Task 10: Run Local Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused game-power tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py tests/test_power_control_cli.py tests/test_integration_assets.py -q
```

Expected: PASS.

- [ ] **Step 2: Run required harness sweep**

Run:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

Expected: PASS. Evidence must include the required local check command:

```text
PYTHON=.venv/bin/python scripts/check-local.sh
```

- [ ] **Step 3: Commit any verification-only fixes**

If the required sweep exposes formatting or lint errors, fix only those errors
and run:

```bash
git add <changed-files>
git commit -m "fix: satisfy game power local checks"
```

## Task 11: Deploy And Run Real-Device A/B Validation

**Files:**
- No repo edits expected before first run.
- Device target: `root@10.100.0.19`.

- [ ] **Step 1: Deploy current working tree to the handheld**

Run the existing project installer for the target:

```bash
scripts/install-on-device.sh root@10.100.0.19
```

Expected: installer exits 0 and places the new `steamos-intel-handheld-game-power`
entry point on the target.

- [ ] **Step 2: Verify baseline TDP service remains healthy**

Run:

```bash
scripts/verify-on-device.sh root@10.100.0.19
```

Expected: PASS and TDP restored by the verifier.

- [ ] **Step 3: Run observe/gpu-priority guarded game-power verifier**

With Cyberpunk 2077 in a repeatable scene, run:

```bash
VERIFY_GAME_POWER_APPID=1091500 VERIFY_GAME_POWER_DURATION_S=30 \
  scripts/verify-game-power-on-device.sh root@10.100.0.19
```

Expected:

- observe output contains power samples and does not alter CPU policy
- gpu-priority output applies EPP only
- final restored CPU policy matches the pre-test snapshot
- no service failures or game crash

- [ ] **Step 4: If EPP-only is insufficient, run a CPU-cap experiment manually**

Run on the target through SSH:

```bash
ssh -o BatchMode=yes root@10.100.0.19 \
  "steamos-intel-handheld-game-power --mode gpu-priority --duration-s 30 --target-appid 1091500 --cpu-cap --pcore-max-mhz 3200 --ecore-max-mhz 2800"
```

Expected:

- previous CPU policy is restored after the command exits
- uncore/GPU power or GPU frequency improves compared with baseline
- average FPS or frame pacing improves without visible CPU-bound stutter

- [ ] **Step 5: Record validation outcome in the final response**

Report:

- local commands and pass/fail
- device commands and pass/fail
- baseline package/core/uncore/psys power
- observe-mode result
- gpu-priority EPP-only result
- CPU-cap result if run
- whether installed default remains off
- whether the policy is ready to enable by default or should stay experimental

## Self-Review Checklist

- Spec coverage:
  - Observation model: Tasks 1, 3, 6.
  - Decision model: Task 4.
  - Actuation and restore: Task 2, Task 5.
  - Service default-off integration: Task 7.
  - Device A/B validation: Task 8, Task 11.
  - Documentation and upstream path: Task 9.
- Placeholder scan:
  - This plan contains no `TBD`, `TODO`, "implement later", or unspecified test steps.
- Type consistency:
  - `GamePowerMode`, `GamePowerAction`, `GamePowerConfig`, `GamePowerSample`,
    `GamePowerController`, `GamePowerGovernor`, and `CpuPolicyActuator` are
    introduced before later tasks use them.
- Harness compliance:
  - After code or policy edits, Task 10 runs the required harness sweep.
  - Device checks are guarded and not claimed unless Task 11 runs.
