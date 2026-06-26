# Profile-Aware TDP Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current fixed `PL2 = min(PL1 + 2W, 32W)` policy with a profile-aware PL2 and Tau policy for the MSI Claw 8 AI+ / Intel Core Ultra 7 258V while keeping the SteamOS slider semantics as PL1.

**Architecture:** Keep SteamOS Manager's single `TdpLimit` value as the sustained package power contract (`PL1`). Add a pure policy layer that maps `(PL1, power source, policy mode)` to `PL1`, `PL2`, optional RAPL time windows, and MSI EC shift-mode intent. `TdpBackend` consumes that policy object when writing RAPL and guarded MSI EC bytes, while CLI/systemd configuration controls the default policy mode until SteamOS exposes a reliable profile signal.

**Tech Stack:** Python 3.10+, pytest, Linux powercap Intel RAPL sysfs, SteamOS Manager D-Bus remote, systemd, guarded MSI Claw EC writes through debugfs.

---

## Review Surface

This plan covers only the next implementation pass for profile-aware PL2/Tau behavior. It does not implement a new SteamOS UI, fan control, CPU/GPU frequency governor changes, battery charge-limit UI, or per-game profile storage.

Context references:
- Current backend: `src/steamos_intel_handheld/power_control.py`
- Current backend tests: `tests/test_power_control_backend.py`
- Current CLI tests: `tests/test_power_control_cli.py`
- Current verifier: `scripts/verify-on-device.sh`
- Current systemd unit: `data/systemd/steamos-intel-handheld-power-control.service`
- Current docs: `README.md`, `docs/design.md`, `docs/hardware/msi-claw-8-ai-plus.md`

External facts used as planning inputs:
- Intel Core Ultra 7 258V public product page lists 8W Minimum Assured Power, 17W Processor Base Power, and 37W Maximum Turbo Power.
- Linux powercap exposes `constraint_X_power_limit_uw` and `constraint_X_time_window_us`; both are writable when the platform driver allows writes.
- The public Intel datasheet index found during research did not expose a directly citable Core Ultra 200V table proving a 17W/25W/28s tuple. Treat 17W/25W as a design anchor to validate on the target device, not as an unverified hard Intel requirement.

## Policy Decision

The SteamOS slider remains PL1. "Frequency point" in this plan means the SteamOS TDP slider wattage point, not CPU/GPU clock frequency. CPU and GPU frequency governors stay outside this implementation.

Policy modes:

```python
class TdpPolicyMode(str, Enum):
    AUTO = "auto"
    BATTERY_LOW_POWER = "battery-low-power"
    BATTERY_MAXQ = "battery-maxq"
    AC_QUIET = "ac-quiet"
    AC_PERFORMANCE = "ac-performance"
```

Power source values:

```python
class PowerSource(str, Enum):
    AC = "ac"
    BATTERY = "battery"
    UNKNOWN = "unknown"
```

`AUTO` resolves to:
- `BATTERY_MAXQ` when the current power source is battery.
- `AC_PERFORMANCE` when the current power source is AC.
- `BATTERY_MAXQ` when the power source is unknown, because the safe fallback should not apply 37W bursts.

Default target table:

| PL1 | Battery Low Power | Battery Max-Q | AC Quiet | AC Performance |
| ---: | ---: | ---: | ---: | ---: |
| 8W | PL2 10W / Tau 1s | PL2 10W / Tau 2s | PL2 12W / Tau 5s | PL2 18W / Tau 8s |
| 12W | PL2 15W / Tau 2s | PL2 15W / Tau 3s | PL2 18W / Tau 5s | PL2 25W / Tau 10s |
| 17W | PL2 23W / Tau 3s | PL2 25W / Tau 5s | PL2 25W / Tau 8s | PL2 37W / Tau 28s |
| 18W | PL2 24W / Tau 3s | PL2 25W / Tau 5s | PL2 25W / Tau 8s | PL2 37W / Tau 28s |
| 20W | PL2 25W / Tau 3s | PL2 25W / Tau 5s | PL2 30W / Tau 8s | PL2 37W / Tau 28s |
| 25W | PL2 28W / Tau 3s | PL2 30W / Tau 8s | PL2 30W / Tau 10s | PL2 37W / Tau 28s |
| 30W | PL2 33W / Tau 3s | PL2 35W / Tau 8s | PL2 35W / Tau 10s | PL2 37W / Tau 28s |

Policy formulas behind the table:

```python
def battery_low_power_pl2(pl1_w: int) -> int:
    if pl1_w <= 8:
        return pl1_w + 2
    if pl1_w <= 12:
        return min(15, pl1_w + 3)
    if pl1_w <= 18:
        return min(24, pl1_w + 6)
    if pl1_w <= 25:
        return min(28, max(25, pl1_w + 3))
    return min(33, pl1_w + 3)


def battery_maxq_pl2(pl1_w: int) -> int:
    if pl1_w <= 12:
        return min(15, max(pl1_w + 1, round(pl1_w * 1.25)))
    if pl1_w <= 18:
        return min(25, max(pl1_w + 1, round(pl1_w * 1.45)))
    if pl1_w <= 25:
        return min(30, max(25, pl1_w + 5))
    return min(35, pl1_w + 5)


def ac_quiet_pl2(pl1_w: int) -> int:
    if pl1_w <= 8:
        return 12
    if pl1_w <= 12:
        return 18
    if pl1_w <= 18:
        return 25
    if pl1_w <= 25:
        return 30
    return 35


def ac_performance_pl2(pl1_w: int) -> int:
    if pl1_w >= 17:
        return 37
    if pl1_w <= 8:
        return 18
    if pl1_w <= 12:
        return 25
    return min(37, max(pl1_w + 1, round(pl1_w * 1.45)))
```

All PL2 values must be clamped to:

```python
pl2_w = min(short_limit_max_w, max(pl1_w + 1, computed_pl2_w))
```

When a RAPL short-term `constraint_X_max_power_uw` is present and positive, keep the existing safety behavior and cap the written PL2 to that max. Do not cap PL1 to `constraint_X_max_power_uw`, because live device evidence already showed the long-term max-power field can be advisory on this platform.

## MSI EC Shift-Mode Decision

The plan must not silently rely on `0xd2=0xc1` comfort mode when the goal is short PL2 boost at 17W/18W. Existing hardware notes show `0xd2=0xc1` can hold package power near 17W even when EC PL1/PL2 are 30W/32W, while `0xd2=0xc4` allows package power to rise into the 25W-30W range.

Implement the code so EC shift mode can be driven by policy, but stage the systemd default carefully:

```python
class MsiClawEcShiftPolicy(str, Enum):
    TDP_THRESHOLD = "tdp-threshold"
    PROFILE = "profile"
```

Default CLI value in code: `tdp-threshold`, preserving current behavior for tests and installed service until on-device validation passes.

Validation target for a later service flip:
- Battery Low Power: comfort.
- Battery Max-Q: turbo when `PL1 >= 17W`, so 17W/25W and 18W/25W can actually burst.
- AC Quiet: comfort for `PL1 <= 17W`, turbo above 17W only if RAPL Tau proves sustained power stays near PL1.
- AC Performance: turbo.

The implementation should add tests for both shift policies, but should not change the installed systemd unit to `--msi-claw-ec-shift-policy profile` until the on-device verifier records acceptable sustained power behavior.

## Task 1: Add Pure TDP Policy Model

**Files:**
- Modify: `src/steamos_intel_handheld/power_control.py`
- Test: `tests/test_power_control_backend.py`

- [ ] **Step 1: Write failing tests for the policy table**

Add imports:

```python
from steamos_intel_handheld.power_control import (
    PowerSource,
    TdpBackend,
    TdpPolicyMode,
    TdpRangeError,
    compute_tdp_limits,
    compute_tdp_policy,
)
```

Add tests:

```python
def test_compute_tdp_policy_uses_battery_maxq_curve():
    expected = {
        8: (8, 10, 2_000_000),
        12: (12, 15, 3_000_000),
        17: (17, 25, 5_000_000),
        18: (18, 25, 5_000_000),
        20: (20, 25, 5_000_000),
        25: (25, 30, 8_000_000),
        30: (30, 35, 8_000_000),
    }

    for requested_watts, (pl1_w, pl2_w, pl2_tau_us) in expected.items():
        policy = compute_tdp_policy(
            requested_watts,
            mode=TdpPolicyMode.BATTERY_MAXQ,
            power_source=PowerSource.BATTERY,
            short_limit_max_w=37,
        )
        assert policy.pl1_w == pl1_w
        assert policy.pl2_w == pl2_w
        assert policy.pl2_tau_us == pl2_tau_us


def test_compute_tdp_policy_uses_ac_performance_curve():
    expected = {
        8: (8, 18, 8_000_000),
        12: (12, 25, 10_000_000),
        17: (17, 37, 28_000_000),
        18: (18, 37, 28_000_000),
        30: (30, 37, 28_000_000),
    }

    for requested_watts, (pl1_w, pl2_w, pl2_tau_us) in expected.items():
        policy = compute_tdp_policy(
            requested_watts,
            mode=TdpPolicyMode.AC_PERFORMANCE,
            power_source=PowerSource.AC,
            short_limit_max_w=37,
        )
        assert policy.pl1_w == pl1_w
        assert policy.pl2_w == pl2_w
        assert policy.pl2_tau_us == pl2_tau_us


def test_compute_tdp_policy_auto_resolves_unknown_to_battery_maxq():
    policy = compute_tdp_policy(
        17,
        mode=TdpPolicyMode.AUTO,
        power_source=PowerSource.UNKNOWN,
        short_limit_max_w=37,
    )

    assert policy.resolved_mode == TdpPolicyMode.BATTERY_MAXQ
    assert policy.pl1_w == 17
    assert policy.pl2_w == 25
    assert policy.pl2_tau_us == 5_000_000


def test_compute_tdp_policy_keeps_pl2_above_pl1_and_respects_short_limit_max():
    policy = compute_tdp_policy(
        30,
        mode=TdpPolicyMode.AC_PERFORMANCE,
        power_source=PowerSource.AC,
        short_limit_max_w=31,
    )

    assert policy.pl1_w == 30
    assert policy.pl2_w == 31
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py::test_compute_tdp_policy_uses_battery_maxq_curve tests/test_power_control_backend.py::test_compute_tdp_policy_uses_ac_performance_curve tests/test_power_control_backend.py::test_compute_tdp_policy_auto_resolves_unknown_to_battery_maxq tests/test_power_control_backend.py::test_compute_tdp_policy_keeps_pl2_above_pl1_and_respects_short_limit_max -q
```

Expected: fail because `PowerSource`, `TdpPolicyMode`, and `compute_tdp_policy` do not exist.

- [ ] **Step 3: Implement the pure policy model**

Add imports:

```python
from enum import Enum
```

Add dataclass and enums near `TdpLimits`:

```python
@dataclass(frozen=True)
class TdpPolicy:
    pl1_w: int
    pl2_w: int
    pl1_tau_us: int | None
    pl2_tau_us: int | None
    requested_mode: "TdpPolicyMode"
    resolved_mode: "TdpPolicyMode"
    power_source: "PowerSource"


class PowerSource(str, Enum):
    AC = "ac"
    BATTERY = "battery"
    UNKNOWN = "unknown"


class TdpPolicyMode(str, Enum):
    AUTO = "auto"
    BATTERY_LOW_POWER = "battery-low-power"
    BATTERY_MAXQ = "battery-maxq"
    AC_QUIET = "ac-quiet"
    AC_PERFORMANCE = "ac-performance"
```

Add helpers:

```python
def resolve_tdp_policy_mode(mode: TdpPolicyMode, power_source: PowerSource) -> TdpPolicyMode:
    if mode != TdpPolicyMode.AUTO:
        return mode
    if power_source == PowerSource.AC:
        return TdpPolicyMode.AC_PERFORMANCE
    return TdpPolicyMode.BATTERY_MAXQ
```

Add PL2 and Tau functions using the formulas in the Policy Decision section.

Implement:

```python
def compute_tdp_policy(
    watts: int,
    *,
    mode: TdpPolicyMode = TdpPolicyMode.AUTO,
    power_source: PowerSource = PowerSource.UNKNOWN,
    short_limit_max_w: int = DEFAULT_SHORT_LIMIT_MAX_W,
    min_w: int = DEFAULT_MIN_W,
    max_w: int = DEFAULT_MAX_W,
) -> TdpPolicy:
    pl1_w = max(int(min_w), min(int(max_w), int(watts)))
    resolved_mode = resolve_tdp_policy_mode(mode, power_source)
    computed_pl2_w = _compute_policy_pl2_w(pl1_w, resolved_mode)
    pl2_w = min(int(short_limit_max_w), max(pl1_w + 1, computed_pl2_w))
    return TdpPolicy(
        pl1_w=pl1_w,
        pl2_w=pl2_w,
        pl1_tau_us=None,
        pl2_tau_us=_compute_policy_pl2_tau_us(pl1_w, resolved_mode),
        requested_mode=mode,
        resolved_mode=resolved_mode,
        power_source=power_source,
    )
```

- [ ] **Step 4: Keep the legacy override path working**

Update `compute_tdp_limits()` so `pl2_w is None` uses `compute_tdp_policy(..., mode=TdpPolicyMode.BATTERY_LOW_POWER)` only if the implementation deliberately wants to replace the fixed curve globally. Preferred migration: keep `compute_tdp_limits()` compatible for existing tests and add a new `compute_tdp_limits_from_policy(policy)`. This avoids breaking callers before the backend is wired in Task 4.

Add:

```python
def compute_tdp_limits_from_policy(policy: TdpPolicy) -> TdpLimits:
    return TdpLimits(
        pl1_uw=policy.pl1_w * MICROWATTS_PER_WATT,
        pl2_uw=policy.pl2_w * MICROWATTS_PER_WATT,
    )
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py -q
```

Expected: pass, with old fixed-curve tests still passing because backend wiring has not changed yet.

## Task 2: Add RAPL Time Window Support

**Files:**
- Modify: `src/steamos_intel_handheld/power_control.py`
- Modify: `tests/test_power_control_backend.py`

- [ ] **Step 1: Extend the test RAPL fixture**

Modify `make_rapl_domain()`:

```python
def make_rapl_domain(
    sysfs_root: Path,
    name: str = "intel-rapl:0",
    pl1: int = 30,
    pl2: int = 37,
    pl1_max: int = 37,
    pl2_max: int = 37,
    pl1_tau_us: int | None = None,
    pl2_tau_us: int | None = None,
    pl1_min_tau_us: int | None = None,
    pl1_max_tau_us: int | None = None,
    pl2_min_tau_us: int | None = None,
    pl2_max_tau_us: int | None = None,
    *,
    swap_constraints: bool = False,
):
```

Inside the loop, write optional `constraint_{index}_time_window_us`, `constraint_{index}_min_time_window_us`, and `constraint_{index}_max_time_window_us` based on the matching constraint.

- [ ] **Step 2: Add time-window lookup and clamp tests**

Add `RaplConstraint` to the backend test imports:

```python
from steamos_intel_handheld.power_control import (
    PowerSource,
    RaplConstraint,
    TdpBackend,
    TdpPolicyMode,
    TdpRangeError,
    compute_tdp_limits,
    compute_tdp_policy,
)
```

Replace `test_write_limit_does_not_change_tau_windows()` with helper-level tests that can pass before full backend policy wiring:

```python
def test_constraint_by_name_includes_time_window_files(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(
        sysfs_root,
        pl1=30,
        pl2=37,
        pl1_tau_us=1_000_000,
        pl2_tau_us=28_000_000,
        pl2_min_tau_us=1_000_000,
        pl2_max_tau_us=10_000_000,
    )

    backend = TdpBackend(state_file=tmp_path / "state" / "tdp_w", sysfs_root=sysfs_root)
    short_term = backend._constraint_by_name(domain, "short_term", fallback_index=1)

    assert short_term is not None
    assert short_term.time_window_file == domain / "constraint_1_time_window_us"
    assert short_term.min_time_window_file == domain / "constraint_1_min_time_window_us"
    assert short_term.max_time_window_file == domain / "constraint_1_max_time_window_us"


def test_limit_time_window_for_constraint_clamps_to_range(tmp_path):
    time_window_file = tmp_path / "constraint_1_time_window_us"
    min_file = tmp_path / "constraint_1_min_time_window_us"
    max_file = tmp_path / "constraint_1_max_time_window_us"
    time_window_file.write_text("28000000")
    min_file.write_text("8000000")
    max_file.write_text("10000000")
    constraint = RaplConstraint(
        power_limit_file=tmp_path / "constraint_1_power_limit_uw",
        time_window_file=time_window_file,
        min_time_window_file=min_file,
        max_time_window_file=max_file,
    )

    backend = TdpBackend(state_file=tmp_path / "state" / "tdp_w", sysfs_root=tmp_path / "sys")

    assert backend._limit_time_window_for_constraint(5_000_000, constraint) == 8_000_000
    assert backend._limit_time_window_for_constraint(9_000_000, constraint) == 9_000_000
    assert backend._limit_time_window_for_constraint(28_000_000, constraint) == 10_000_000


def test_limit_time_window_for_constraint_ignores_missing_range(tmp_path):
    constraint = RaplConstraint(
        power_limit_file=tmp_path / "constraint_1_power_limit_uw",
        time_window_file=None,
    )
    backend = TdpBackend(state_file=tmp_path / "state" / "tdp_w", sysfs_root=tmp_path / "sys")

    assert backend._limit_time_window_for_constraint(5_000_000, constraint) == 5_000_000
```

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py::test_constraint_by_name_includes_time_window_files tests/test_power_control_backend.py::test_limit_time_window_for_constraint_clamps_to_range tests/test_power_control_backend.py::test_limit_time_window_for_constraint_ignores_missing_range -q
```

Expected: fail because `RaplConstraint` does not track time-window files and `_limit_time_window_for_constraint()` does not exist.

- [ ] **Step 4: Extend `RaplConstraint`**

Change:

```python
@dataclass(frozen=True)
class RaplConstraint:
    power_limit_file: Path
    max_power_file: Path | None = None
```

to:

```python
@dataclass(frozen=True)
class RaplConstraint:
    power_limit_file: Path
    max_power_file: Path | None = None
    time_window_file: Path | None = None
    min_time_window_file: Path | None = None
    max_time_window_file: Path | None = None
```

Update `_constraint_by_name()` fallback and named returns to populate those optional paths.

- [ ] **Step 5: Add Tau clamp helper**

Add:

```python
def _read_positive_int(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        value = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None
```

Add backend method:

```python
def _limit_time_window_for_constraint(self, requested_us: int, constraint: RaplConstraint) -> int:
    min_us = _read_positive_int(constraint.min_time_window_file)
    max_us = _read_positive_int(constraint.max_time_window_file)
    limited_us = int(requested_us)
    if min_us is not None:
        limited_us = max(min_us, limited_us)
    if max_us is not None:
        limited_us = min(max_us, limited_us)
    return limited_us
```

- [ ] **Step 6: Defer write-path Tau integration to Task 4**

Task 2 only adds data-model support and clamp helpers. Task 4 wires policy into `apply_limit_to_rapl()` and adds integration tests that write both PL2 and short-term Tau. Keeping these separate ensures Task 2 can pass without depending on backend policy wiring.

- [ ] **Step 7: Run tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py -q
```

Expected: pass for the helper-level RAPL time-window tests while existing fixed-curve write-path tests continue to pass.

## Task 3: Add Power Source Detection And CLI Policy Controls

**Files:**
- Modify: `src/steamos_intel_handheld/power_control.py`
- Modify: `tests/test_power_control_backend.py`
- Modify: `tests/test_power_control_cli.py`

- [ ] **Step 1: Write power-source detection tests**

Add:

```python
def make_power_supply(sysfs_root: Path, name: str, supply_type: str, online: str) -> Path:
    supply = sysfs_root / "class" / "power_supply" / name
    supply.mkdir(parents=True)
    (supply / "type").write_text(supply_type)
    (supply / "online").write_text(online)
    return supply


def test_detect_power_source_prefers_online_ac_adapter(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_power_supply(sysfs_root, "BAT0", "Battery", "0")
    make_power_supply(sysfs_root, "ACAD", "Mains", "1")

    backend = TdpBackend(state_file=tmp_path / "state" / "tdp_w", sysfs_root=sysfs_root)

    assert backend.current_power_source() == PowerSource.AC


def test_detect_power_source_returns_battery_when_no_ac_is_online(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_power_supply(sysfs_root, "BAT0", "Battery", "0")
    make_power_supply(sysfs_root, "ACAD", "Mains", "0")

    backend = TdpBackend(state_file=tmp_path / "state" / "tdp_w", sysfs_root=sysfs_root)

    assert backend.current_power_source() == PowerSource.BATTERY


def test_detect_power_source_allows_override_for_tests_and_profiles(tmp_path):
    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        sysfs_root=tmp_path / "sys",
        power_source_override=PowerSource.AC,
    )

    assert backend.current_power_source() == PowerSource.AC
```

- [ ] **Step 2: Write CLI parser tests**

Add to `tests/test_power_control_cli.py`:

```python
def test_parser_configures_tdp_policy_mode_and_power_source_override():
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--tdp-policy",
            "battery-maxq",
            "--power-source-override",
            "battery",
        ]
    )
    backend = power_control.build_backend(args)

    assert backend.tdp_policy_mode == power_control.TdpPolicyMode.BATTERY_MAXQ
    assert backend.power_source_override == power_control.PowerSource.BATTERY


def test_parser_configures_power_source_poll_interval():
    args = power_control.build_parser().parse_args(
        ["serve", "--power-source-poll-s", "5"]
    )
    backend = power_control.build_backend(args)

    assert backend.power_source_poll_s == 5.0
```

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py::test_detect_power_source_prefers_online_ac_adapter tests/test_power_control_backend.py::test_detect_power_source_returns_battery_when_no_ac_is_online tests/test_power_control_backend.py::test_detect_power_source_allows_override_for_tests_and_profiles tests/test_power_control_cli.py::test_parser_configures_tdp_policy_mode_and_power_source_override tests/test_power_control_cli.py::test_parser_configures_power_source_poll_interval -q
```

Expected: fail until backend and parser arguments exist.

- [ ] **Step 4: Implement backend detection**

Add `TdpBackend.__init__` parameters:

```python
tdp_policy_mode: TdpPolicyMode | str = TdpPolicyMode.AUTO,
power_source_override: PowerSource | str | None = None,
power_source_poll_s: float = 2.0,
```

Store:

```python
self.tdp_policy_mode = TdpPolicyMode(tdp_policy_mode)
self.power_source_override = (
    PowerSource(power_source_override) if power_source_override is not None else None
)
self.power_source_poll_s = max(0.0, float(power_source_poll_s))
self._last_applied_power_source: PowerSource | None = None
```

Implement:

```python
def current_power_source(self) -> PowerSource:
    if self.power_source_override is not None:
        return self.power_source_override
    power_supply = self.sysfs_root / "class" / "power_supply"
    if not power_supply.exists():
        return PowerSource.UNKNOWN
    saw_battery = False
    for supply in sorted(power_supply.iterdir()):
        try:
            supply_type = (supply / "type").read_text().strip().lower()
        except OSError:
            continue
        if supply_type == "battery":
            saw_battery = True
            continue
        if supply_type in {"mains", "usb", "usb-c", "usb_pd", "usb_pd_drp"}:
            try:
                if (supply / "online").read_text().strip() == "1":
                    return PowerSource.AC
            except OSError:
                continue
    return PowerSource.BATTERY if saw_battery else PowerSource.UNKNOWN
```

- [ ] **Step 5: Add parser arguments and backend wiring**

In `build_parser()` add:

```python
parser.add_argument(
    "--tdp-policy",
    choices=[mode.value for mode in TdpPolicyMode],
    default=TdpPolicyMode.AUTO.value,
)
parser.add_argument(
    "--power-source-override",
    choices=[source.value for source in PowerSource],
)
parser.add_argument("--power-source-poll-s", type=float, default=2.0)
```

In `build_backend()` pass:

```python
tdp_policy_mode=args.tdp_policy,
power_source_override=args.power_source_override,
power_source_poll_s=args.power_source_poll_s,
```

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py tests/test_power_control_cli.py -q
```

Expected: pass after implementation.

## Task 4: Wire Policy Into RAPL And MSI EC Writes

**Files:**
- Modify: `src/steamos_intel_handheld/power_control.py`
- Modify: `tests/test_power_control_backend.py`

- [ ] **Step 1: Update write-path tests for profile-aware defaults**

Change `test_write_limit_updates_state_and_rapl()` expected values:

```python
backend = TdpBackend(
    state_file=state_file,
    sysfs_root=sysfs_root,
    tdp_policy_mode=TdpPolicyMode.BATTERY_MAXQ,
    power_source_override=PowerSource.BATTERY,
)
backend.write_limit_w(17)

assert state_file.read_text() == "17"
assert (domain / "constraint_0_power_limit_uw").read_text() == "17000000"
assert (domain / "constraint_1_power_limit_uw").read_text() == "25000000"
```

Add an AC performance test:

```python
def test_write_limit_uses_ac_performance_pl2_on_ac(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(
        state_file=state_file,
        sysfs_root=sysfs_root,
        tdp_policy_mode=TdpPolicyMode.AC_PERFORMANCE,
        power_source_override=PowerSource.AC,
    )
    backend.write_limit_w(18)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "18000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "37000000"
```

Add a legacy override test:

```python
def test_write_limit_explicit_pl2_override_still_wins(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        sysfs_root=sysfs_root,
        tdp_policy_mode=TdpPolicyMode.AC_PERFORMANCE,
        power_source_override=PowerSource.AC,
        pl2_w=22,
    )
    backend.write_limit_w(18)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "18000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "22000000"
```

Add short-term Tau write-path tests:

```python
def test_write_limit_writes_policy_short_term_tau_when_available(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(
        sysfs_root,
        pl1=30,
        pl2=37,
        pl1_tau_us=1_000_000,
        pl2_tau_us=28_000_000,
    )

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        sysfs_root=sysfs_root,
        tdp_policy_mode=TdpPolicyMode.BATTERY_MAXQ,
        power_source_override=PowerSource.BATTERY,
    )
    backend.write_limit_w(17)

    assert (domain / "constraint_0_time_window_us").read_text() == "1000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "25000000"
    assert (domain / "constraint_1_time_window_us").read_text() == "5000000"


def test_write_limit_skips_policy_tau_when_time_window_file_is_missing(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        sysfs_root=sysfs_root,
        tdp_policy_mode=TdpPolicyMode.BATTERY_MAXQ,
        power_source_override=PowerSource.BATTERY,
    )
    backend.write_limit_w(17)

    assert (domain / "constraint_1_power_limit_uw").read_text() == "25000000"
    assert not (domain / "constraint_1_time_window_us").exists()
```

Audit every existing fixed `+2W` write-path expectation and update it to either:
- Explicitly use `compute_tdp_limits()` when testing the legacy pure helper, or
- Instantiate `TdpBackend` with policy mode and assert the profile-aware PL2 table.

The specific existing tests that must be updated include:
- `test_write_limit_updates_msi_claw_ec_when_guard_matches`: 30W now maps to 35W under Battery Max-Q unless the test chooses a different policy.
- `test_write_limit_sets_msi_claw_ec_comfort_mode_at_17_watts`: 17W now maps to 25W under Battery Max-Q.
- `test_write_limit_sets_msi_claw_ec_turbo_mode_above_17_watts`: 18W now maps to 25W under Battery Max-Q.
- `test_write_limit_does_not_cap_long_term_to_reported_max_power`: 30W now maps to 35W under Battery Max-Q.
- `test_restore_state_to_rapl_applies_persisted_limit`: 30W now maps to 35W under Battery Max-Q.
- `test_restore_state_to_rapl_clamps_legacy_persisted_limit`: 30W now maps to 35W under Battery Max-Q.

- [ ] **Step 2: Update MSI EC tests for policy-aware PL2 with conservative shift default**

Change the existing 17W EC test so `tdp-threshold` remains comfort while PL2 becomes 25W:

```python
assert ec[0x50] == 17
assert ec[0x51] == 25
assert ec[0xD2] == 0xC1
```

Add profile shift-policy tests:

```python
def test_profile_shift_policy_uses_turbo_for_battery_maxq_at_17_watts(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, io_path = make_ec_io(tmp_path)

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
        tdp_policy_mode=TdpPolicyMode.BATTERY_MAXQ,
        power_source_override=PowerSource.BATTERY,
        msi_claw_ec_shift_policy=MsiClawEcShiftPolicy.PROFILE,
    )

    backend.write_limit_w(17)

    ec = io_path.read_bytes()
    assert ec[0x50] == 17
    assert ec[0x51] == 25
    assert ec[0xD2] == 0xC4
```

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py::test_write_limit_updates_state_and_rapl tests/test_power_control_backend.py::test_write_limit_uses_ac_performance_pl2_on_ac tests/test_power_control_backend.py::test_write_limit_explicit_pl2_override_still_wins tests/test_power_control_backend.py::test_write_limit_writes_policy_short_term_tau_when_available tests/test_power_control_backend.py::test_write_limit_skips_policy_tau_when_time_window_file_is_missing tests/test_power_control_backend.py::test_profile_shift_policy_uses_turbo_for_battery_maxq_at_17_watts -q
```

Expected: fail until backend uses `compute_tdp_policy()`.

- [ ] **Step 4: Implement policy computation inside backend**

Add:

```python
def _compute_current_policy(self, watts: int) -> TdpPolicy:
    power_source = self.current_power_source()
    policy = compute_tdp_policy(
        watts,
        mode=self.tdp_policy_mode,
        power_source=power_source,
        short_limit_max_w=self.short_limit_max_w,
        min_w=self.min_w,
        max_w=self.max_w,
    )
    if self.pl2_w is None:
        return policy
    override_pl2_w = min(
        self.short_limit_max_w,
        max(policy.pl1_w + 1, int(self.pl2_w)),
    )
    return TdpPolicy(
        pl1_w=policy.pl1_w,
        pl2_w=override_pl2_w,
        pl1_tau_us=policy.pl1_tau_us,
        pl2_tau_us=policy.pl2_tau_us,
        requested_mode=policy.requested_mode,
        resolved_mode=policy.resolved_mode,
        power_source=policy.power_source,
    )
```

Update `apply_limit_to_rapl()` to use `_compute_current_policy()` and `compute_tdp_limits_from_policy(policy)`. After writing short-term PL2, write short-term Tau when `policy.pl2_tau_us` and `short_term.time_window_file` are both present:

```python
if short_term.time_window_file is not None and policy.pl2_tau_us is not None:
    try:
        short_term.time_window_file.write_text(
            str(self._limit_time_window_for_constraint(policy.pl2_tau_us, short_term))
        )
    except OSError as exc:
        print(f"failed to write RAPL short-term time window: {exc}", file=sys.stderr)
```

Tau write failure should not roll back PL1/PL2 writes. PL1/PL2 power-limit writes remain hard failures, matching current backend behavior, because a failed power-limit write means the requested TDP was not applied.

Update `apply_limit_to_msi_claw_ec()` to compute the same policy and convert it to `WattLimits(policy.pl1_w, policy.pl2_w)`, with the existing EC PL1 cap preserved.

- [ ] **Step 5: Implement MSI EC shift policy enum**

Add:

```python
class MsiClawEcShiftPolicy(str, Enum):
    TDP_THRESHOLD = "tdp-threshold"
    PROFILE = "profile"
```

Add backend parameter and parser argument:

```python
parser.add_argument(
    "--msi-claw-ec-shift-policy",
    choices=[policy.value for policy in MsiClawEcShiftPolicy],
    default=MsiClawEcShiftPolicy.TDP_THRESHOLD.value,
)
```

Implement:

```python
def _msi_claw_ec_shift_mode_for_policy(self, policy: TdpPolicy) -> int:
    if self.msi_claw_ec_shift_policy == MsiClawEcShiftPolicy.TDP_THRESHOLD:
        return msi_claw_ec_shift_mode_for_tdp(policy.pl1_w)
    if policy.resolved_mode == TdpPolicyMode.BATTERY_LOW_POWER:
        return MSI_CLAW_EC_SHIFT_MODE_COMFORT
    if policy.resolved_mode == TdpPolicyMode.AC_QUIET and policy.pl1_w <= 17:
        return MSI_CLAW_EC_SHIFT_MODE_COMFORT
    if policy.pl1_w >= 17:
        return MSI_CLAW_EC_SHIFT_MODE_TURBO
    return MSI_CLAW_EC_SHIFT_MODE_COMFORT
```

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py tests/test_power_control_cli.py -q
```

Expected: pass.

## Task 5: Reapply Policy On AC/DC Changes

**Files:**
- Modify: `src/steamos_intel_handheld/power_control.py`
- Test: `tests/test_power_control_backend.py`
- Test: `tests/test_power_control_cli.py`

- [ ] **Step 1: Write backend method tests**

Add:

```python
def test_reapply_limit_when_power_source_changes_updates_pl2(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"
    state_file.parent.mkdir()
    state_file.write_text("17")

    backend = TdpBackend(
        state_file=state_file,
        sysfs_root=sysfs_root,
        tdp_policy_mode=TdpPolicyMode.AUTO,
        power_source_override=PowerSource.BATTERY,
    )
    assert backend.reapply_if_power_source_changed(force=True) == 17
    assert (domain / "constraint_1_power_limit_uw").read_text() == "25000000"

    backend.power_source_override = PowerSource.AC
    assert backend.reapply_if_power_source_changed() == 17
    assert (domain / "constraint_1_power_limit_uw").read_text() == "37000000"
```

- [ ] **Step 2: Implement `reapply_if_power_source_changed()`**

Add:

```python
def reapply_if_power_source_changed(self, *, force: bool = False) -> int | None:
    current_source = self.current_power_source()
    if not force and current_source == self._last_applied_power_source:
        return None
    watts = self._read_state_file()
    if watts is None:
        self._last_applied_power_source = current_source
        return None
    if self.apply_rapl:
        self.apply_limit_to_rapl(watts)
    if self.apply_msi_claw_ec:
        self.schedule_limit_to_msi_claw_ec(watts)
    self._last_applied_power_source = current_source
    return watts
```

Update `apply_limit_to_rapl()` to set `self._last_applied_power_source = policy.power_source` after successful writes.

- [ ] **Step 3: Add async polling in `serve()`**

Add:

```python
async def poll_power_source_changes(backend: TdpBackend) -> None:
    if backend.power_source_poll_s <= 0:
        return
    while True:
        await asyncio.sleep(backend.power_source_poll_s)
        try:
            reapplied = backend.reapply_if_power_source_changed()
        except Exception as exc:
            print(f"failed to reapply TDP policy after power-source check: {exc}", file=sys.stderr)
            continue
        if reapplied is not None:
            print(f"reapplied TDP policy for {reapplied}W after power-source change", file=sys.stderr)
```

In `serve()`, after bus setup and before waiting forever:

```python
asyncio.create_task(poll_power_source_changes(backend))
await asyncio.Future()
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py tests/test_power_control_cli.py -q
```

Expected: pass.

## Task 6: Update Verification Harness And Docs

**Files:**
- Modify: `scripts/verify-on-device.sh`
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `docs/hardware/msi-claw-8-ai-plus.md`

- [ ] **Step 1: Update verifier expected PL2 table**

Replace `expected_pl2_watts()` with a mode-aware implementation:

```bash
expected_pl2_watts() {
  local watts
  watts="$(expected_pl1_watts "$1")"
  local mode="${2:-battery-maxq}"
  case "$mode:$watts" in
    battery-maxq:8) echo 10 ;;
    battery-maxq:12) echo 15 ;;
    battery-maxq:17) echo 25 ;;
    battery-maxq:18) echo 25 ;;
    battery-maxq:20) echo 25 ;;
    battery-maxq:25) echo 30 ;;
    battery-maxq:30) echo 35 ;;
    ac-performance:8) echo 18 ;;
    ac-performance:12) echo 25 ;;
    ac-performance:17|ac-performance:18|ac-performance:20|ac-performance:25|ac-performance:30) echo 37 ;;
    *) echo "unsupported verifier TDP policy mode '$mode' for ${watts}W" >&2; return 2 ;;
  esac
}
```

Add `rapl_constraint_time_window_us()` and assert short-term Tau for test and restore wattages when the file exists. Use `VERIFY_TDP_POLICY_MODE="${VERIFY_TDP_POLICY_MODE:-battery-maxq}"` so the script can validate AC performance separately.

- [ ] **Step 2: Add verifier EC output**

Print current EC bytes when debugfs EC IO is readable:

```bash
report_msi_claw_ec_tdp_bytes() {
  local io="/sys/kernel/debug/ec/ec0/io"
  if [ ! -r "$io" ]; then
    echo "MSI EC TDP bytes unavailable: $io is not readable"
    return 0
  fi
  od -An -j 80 -N 2 -t u1 "$io" | awk '{print "MSI EC PL1/PL2 bytes: " $1 "W/" $2 "W"}'
  od -An -j 210 -N 1 -t x1 "$io" | awk '{print "MSI EC shift byte: 0x" $1}'
}
```

Call it after each TDP change.

- [ ] **Step 3: Update docs**

Document:
- Slider = PL1.
- Default `--tdp-policy auto` maps battery to Battery Max-Q and AC to AC Performance.
- Battery Max-Q uses 17W/25W and 18W/25W with short Tau.
- 37W PL2 is AC Performance only by default.
- `--pl2-w` remains an explicit override.
- `--msi-claw-ec-shift-policy profile` is staged behind hardware validation.
- Tau writes are skipped if the kernel does not expose writable time-window files.

- [ ] **Step 4: Run local checks**

Run:

```bash
python3 -m pytest tests/test_power_control_backend.py tests/test_power_control_cli.py tests/test_integration_assets.py -q
```

Expected: pass.

Run:

```bash
bash -n scripts/verify-on-device.sh
```

Expected: no output and exit 0.

## Task 7: On-Device Validation Gate Before Service Default Flip

**Files:**
- Modify only after validation: `data/systemd/steamos-intel-handheld-power-control.service`
- Record results in: `docs/hardware/msi-claw-8-ai-plus.md`

- [ ] **Step 1: Deploy without profile EC shift**

Install a build with:

```bash
--tdp-policy auto --msi-claw-ec-shift-policy tdp-threshold
```

Run on battery:

```bash
scripts/verify-on-device.sh root@steamdeck-host 17 30
```

Expected:
- SteamOS central TDP: 17W during test.
- Remote TDP: 17W during test.
- RAPL PL1: 17W.
- RAPL PL2: 25W.
- RAPL short-term Tau: 5s if exposed.
- EC PL1/PL2: 17W/25W.
- EC shift byte: `0xc1` under `tdp-threshold` policy.

- [ ] **Step 2: Manually validate profile EC shift**

Run the service manually or with a temporary systemd override using:

```bash
--tdp-policy battery-maxq --power-source-override battery --msi-claw-ec-shift-policy profile
```

Test 17W and 18W in a game workload for at least 10 minutes each. Record:
- MangoHud package power average.
- RAPL PL1/PL2/time-window readback.
- EC `0x50`, `0x51`, `0xd2`.
- 1% low observation if available.
- Battery discharge estimate.
- Whether sustained package power returns close to PL1 after each burst.

The profile EC shift default can be enabled only if both 17W and 18W runs meet these thresholds:
- 10-minute package-power average is no more than `PL1 + 2W`.
- After a burst, package power returns to at most `PL1 + 2W` within 10 seconds.
- No repeated burst pattern keeps package power above `PL1 + 4W` for more than 30 cumulative seconds per 5-minute window.
- The game remains stable and the system does not report thermal, EC, or RAPL write errors.

- [ ] **Step 3: Decide service default**

Only change `data/systemd/steamos-intel-handheld-power-control.service` from:

```text
--msi-claw-ec-shift-policy tdp-threshold
```

to:

```text
--msi-claw-ec-shift-policy profile
```

if the 17W and 18W Battery Max-Q runs meet the thresholds above. If not, keep `tdp-threshold` as the installed default and document that EC comfort mode prevents the intended PL2 burst on battery until a safer EC scenario mapping is found.

- [ ] **Step 4: Final verification**

Run:

```bash
scripts/check-local.sh
```

Expected: local test suite passes.

Run on device:

```bash
scripts/verify-on-device.sh root@steamdeck-host 17 30
```

Expected: verifier passes and restores the configured restore wattage.

## Acceptance Criteria

- SteamOS `TdpLimit` continues to mean PL1.
- Battery Auto/Max-Q maps 17W and 18W to PL2 25W, not 37W.
- AC Auto/Performance maps PL1 >= 17W to PL2 37W.
- Battery Low Power and AC Quiet are available as explicit backend policy modes.
- RAPL short-term Tau is written when the kernel exposes `constraint_X_time_window_us`, and skipped safely otherwise.
- PL2 remains at least PL1 + 1W and never exceeds `short_limit_max_w`.
- Existing `--pl2-w` override still works and remains capped by `short_limit_max_w`.
- AC/DC changes reapply the current saved PL1 with the newly resolved policy.
- MSI EC PL1/PL2 mirror the policy PL1/PL2 after the existing DMI and firmware guards.
- MSI EC shift-mode profile behavior is implemented, tested, and staged behind hardware validation before the installed service default changes.
- `python3 -m pytest tests/test_power_control_backend.py tests/test_power_control_cli.py tests/test_integration_assets.py -q` passes.
- `bash -n scripts/verify-on-device.sh` passes.
