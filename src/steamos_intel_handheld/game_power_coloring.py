#!/usr/bin/env python3
"""Thread color ledger shared by the game-power daemon and profiler (V9 S3).

Design section 6. Observed threads and background cgroups are aggregated into
stable runtime *roles* (``cgroup_role:normalized_comm``) and each role is
assigned a color:

- A ``foreground-latency-hot``    -> gated ``uclamp-min`` (or compact-affinity)
- B ``foreground-throughput-wide``-> observe-only
- C ``compositor-overlay-sensitive`` -> observe-only, NEVER shaped
- D ``background-helper-shapable`` -> gated ``bg-weight`` / ``bg-uclamp``
- E ``unknown-unstable``          -> observe-only (default)

The same aggregation + classification functions are used by the daemon (cheap
runtime cadence over live ``/proc`` deltas) and by the profiler (full-artifact
cadence over the run's JSONL). Gated actuators are ``blocked`` until the verdict
ledger (S4) unlocks them; the profiler reports them ``advisory``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .game_power_cgroup_writers import is_background_shaping_write_target

# Budget cap on TIDs sampled per colorize window (design section 6).
COLOR_LEDGER_TID_BUDGET = 128

_COMPOSITOR_TOKENS = (
    "gamescope",
    "mangoapp",
    "pipewire",
    "wireplumber",
    "inputplumber",
    "plugin_loader",  # Decky
    "decky",
)


class Color(str, Enum):
    A = "A"  # foreground-latency-hot
    B = "B"  # foreground-throughput-wide
    C = "C"  # compositor-overlay-sensitive
    D = "D"  # background-helper-shapable
    E = "E"  # unknown-unstable


# Gated actuators (unlocked per-context by the verdict ledger). ``observe-only``
# is never gated. Color C is pinned to ``observe-only`` in code so it can never
# receive an actuator.
OBSERVE_ONLY = "observe-only"
GATED_ACTUATORS = frozenset({"uclamp-min", "bg-weight", "bg-uclamp", "compact-affinity"})


@dataclass(frozen=True)
class ColorThresholds:
    """Classification thresholds (design section 6).

    ``latency_wait_ms_per_window`` and ``latency_cpu_ms_per_window`` are deltas
    accrued over one *reference* colorize window (``reference_window_s``, 10 s
    by default); ``latency_wait_per_slice_ms`` is the per-timeslice runqueue
    wait. Observations from any window length are normalized to per-window
    rates (ms/s * reference window) before classification (Q1), so the daemon
    (10 s colorize windows) and the profiler (whole-run aggregates, e.g. 300 s)
    classify identically. The ledger reports the same signals per second.
    """

    latency_wait_ms_per_window: float = 25.0
    latency_wait_per_slice_ms: float = 1.0
    latency_cpu_ms_per_window: float = 100.0
    wide_sibling_min: int = 3
    wide_cpus_seen_min: int = 4
    reference_window_s: float = 10.0


DEFAULT_THRESHOLDS = ColorThresholds()


# ---------------------------------------------------------------------------
# Shared role normalization (single-sourced here; re-imported by the profiler).
# ---------------------------------------------------------------------------
def normalize_role_part(value: object) -> str:
    text = (str(value).strip().lower() if value is not None else "") or "unknown"
    normalized: list[str] = []
    previous_dash = False
    for char in text:
        if char.isalnum():
            normalized.append(char)
            previous_dash = False
        elif not previous_dash:
            normalized.append("-")
            previous_dash = True
    return "".join(normalized).strip("-") or "unknown"


def thread_cgroup_role(cgroup: object) -> str:
    text = (str(cgroup).strip().lower() if cgroup is not None else "")
    if "app-steam-app" in text:
        return "foreground-game"
    if "gamescope" in text or "mangoapp" in text:
        return "gamescope-helper"
    if "steam" in text:
        return "steam-helper"
    return "other"


def classify_background_cgroup(cgroup: str, command: object) -> str:
    command_text = str(command).strip() if command is not None else ""
    haystack = f"{cgroup} {command_text}".lower()
    if "gamescope" in haystack or "mangoapp" in haystack:
        return "gamescope-helper"
    if "steamwebhelper" in haystack or "steam" in haystack:
        return "steam-helper"
    if "/system.slice" in haystack:
        return "system-helper"
    if "/user.slice" in haystack:
        return "user-helper"
    return "other-background"


def role_key_for(cgroup: object, comm: object) -> str:
    return f"{thread_cgroup_role(cgroup)}:{normalize_role_part(comm)}"


def is_foreground_role(cgroup: str) -> bool:
    return "app-steam-app" in (cgroup or "").lower()


def is_compositor_role(cgroup: str, comm: object) -> bool:
    comm_text = str(comm).strip() if comm is not None else ""
    haystack = f"{cgroup} {comm_text}".lower()
    return any(token in haystack for token in _COMPOSITOR_TOKENS)


# ---------------------------------------------------------------------------
# Observation model.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoleObservation:
    """Per-role aggregate over one colorize window."""

    role_key: str
    cgroup: str
    comm: str | None
    tid_count: int
    cpu_time_ms_delta: float
    runqueue_wait_ms_delta: float
    timeslices_delta: int
    cpus_seen: tuple[int, ...]
    window_s: float
    restore_covered: bool = True
    stable: bool = True

    @property
    def cpu_time_ms_per_s(self) -> float:
        if self.window_s <= 0:
            return 0.0
        return self.cpu_time_ms_delta / self.window_s

    @property
    def runqueue_wait_ms_per_s(self) -> float:
        if self.window_s <= 0:
            return 0.0
        return self.runqueue_wait_ms_delta / self.window_s

    @property
    def wait_per_slice_ms(self) -> float:
        if self.timeslices_delta <= 0:
            return 0.0
        return self.runqueue_wait_ms_delta / self.timeslices_delta


@dataclass(frozen=True)
class ColorLedgerEntry:
    role_key: str
    color: Color
    tid_count: int
    cpu_time_ms_per_s: float
    runqueue_wait_ms_per_s: float
    cpus_seen: tuple[int, ...]
    actuator: str
    actuator_state: str
    blocking_reason_codes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "role_key": self.role_key,
            "color": self.color.value,
            "tid_count": self.tid_count,
            "cpu_time_ms_per_s": round(self.cpu_time_ms_per_s, 3),
            "runqueue_wait_ms_per_s": round(self.runqueue_wait_ms_per_s, 3),
            "cpus_seen": list(self.cpus_seen),
            "actuator": self.actuator,
            "actuator_state": self.actuator_state,
            "blocking_reason_codes": list(self.blocking_reason_codes),
        }


@dataclass(frozen=True)
class ColorLedger:
    entries: tuple[ColorLedgerEntry, ...] = ()
    truncated: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "truncated": self.truncated,
            "entries": [entry.to_json() for entry in self.entries],
        }


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------
def classify_role_color(
    observation: RoleObservation,
    *,
    appid: str | None,
    thresholds: ColorThresholds = DEFAULT_THRESHOLDS,
) -> Color:
    if is_compositor_role(observation.cgroup, observation.comm):
        return Color.C
    if not observation.restore_covered:
        return Color.E
    if is_foreground_role(observation.cgroup):
        # Q1: normalize the observation to per-reference-window rates so a
        # whole-run profiler aggregate (e.g. 300 s) does not inflate raw deltas
        # ~30x against the per-10s-window thresholds.
        if observation.window_s > 0:
            scale = thresholds.reference_window_s / observation.window_s
        else:
            scale = 1.0
        cpu_ms_per_window = observation.cpu_time_ms_delta * scale
        wait_ms_per_window = observation.runqueue_wait_ms_delta * scale
        cpu_hot = cpu_ms_per_window >= thresholds.latency_cpu_ms_per_window
        wait_hot = (
            wait_ms_per_window >= thresholds.latency_wait_ms_per_window
            or observation.wait_per_slice_ms >= thresholds.latency_wait_per_slice_ms
        )
        if observation.stable and cpu_hot and wait_hot:
            return Color.A
        wide = (
            observation.tid_count >= thresholds.wide_sibling_min
            and cpu_hot
            and wait_ms_per_window < thresholds.latency_wait_ms_per_window
            and len(observation.cpus_seen) >= thresholds.wide_cpus_seen_min
        )
        if wide:
            return Color.B
        return Color.E
    if appid is not None and is_background_shaping_write_target(
        observation.cgroup, appid=appid
    ):
        return Color.D
    return Color.E


def actuator_for_color(color: Color) -> str:
    if color == Color.A:
        return "uclamp-min"
    if color == Color.D:
        return "bg-weight"
    return OBSERVE_ONLY


def resolve_actuator_state(
    actuator: str,
    *,
    active_actuators: Iterable[str] = (),
    advisory: bool = False,
) -> tuple[str, tuple[str, ...]]:
    if actuator == OBSERVE_ONLY:
        return "active", ()
    if actuator in set(active_actuators):
        return "active", ()
    if advisory:
        return "advisory", ()
    return "blocked", ("no-verdict-for-context",)


def resolve_ledger_actuators(
    entries: Sequence[ColorLedgerEntry],
    *,
    active_actuators: Iterable[str] = (),
    advisory: bool = False,
) -> tuple[ColorLedgerEntry, ...]:
    """Re-resolve actuator states against a verdict-unlocked actuator set.

    Used by the daemon to flip gated entries from ``blocked`` to ``active`` once
    the verdict ledger unlocks the lane for the current context (S4), without
    re-classifying colors.
    """

    active = set(active_actuators)
    resolved: list[ColorLedgerEntry] = []
    for entry in entries:
        state, codes = resolve_actuator_state(
            entry.actuator, active_actuators=active, advisory=advisory
        )
        resolved.append(
            ColorLedgerEntry(
                role_key=entry.role_key,
                color=entry.color,
                tid_count=entry.tid_count,
                cpu_time_ms_per_s=entry.cpu_time_ms_per_s,
                runqueue_wait_ms_per_s=entry.runqueue_wait_ms_per_s,
                cpus_seen=entry.cpus_seen,
                actuator=entry.actuator,
                actuator_state=state,
                blocking_reason_codes=codes,
            )
        )
    return tuple(resolved)


def build_color_ledger(
    observations: Iterable[RoleObservation],
    *,
    appid: str | None,
    thresholds: ColorThresholds = DEFAULT_THRESHOLDS,
    active_actuators: Iterable[str] = (),
    advisory: bool = False,
    truncated: bool = False,
) -> ColorLedger:
    active = set(active_actuators)
    entries: list[ColorLedgerEntry] = []
    for observation in observations:
        color = classify_role_color(observation, appid=appid, thresholds=thresholds)
        actuator = actuator_for_color(color)
        state, codes = resolve_actuator_state(
            actuator, active_actuators=active, advisory=advisory
        )
        entries.append(
            ColorLedgerEntry(
                role_key=observation.role_key,
                color=color,
                tid_count=observation.tid_count,
                cpu_time_ms_per_s=observation.cpu_time_ms_per_s,
                runqueue_wait_ms_per_s=observation.runqueue_wait_ms_per_s,
                cpus_seen=tuple(sorted(observation.cpus_seen)),
                actuator=actuator,
                actuator_state=state,
                blocking_reason_codes=codes,
            )
        )
    entries.sort(
        key=lambda entry: (
            entry.color.value,
            -entry.cpu_time_ms_per_s,
            entry.role_key,
        )
    )
    return ColorLedger(entries=tuple(entries), truncated=truncated)


# ---------------------------------------------------------------------------
# Thread-sample aggregation (shared by daemon and profiler).
# ---------------------------------------------------------------------------
@dataclass
class _RoleAccumulator:
    role_key: str
    cgroup: str
    comm: str | None = None
    tids: set[int] = field(default_factory=set)
    cpu_time_ms_delta: float = 0.0
    runqueue_wait_ms_delta: float = 0.0
    timeslices_delta: int = 0
    cpus_seen: set[int] = field(default_factory=set)
    comm_known: bool = False
    restore_covered: bool = True


def cap_thread_samples(
    samples: Sequence[dict[str, object]],
    *,
    budget: int = COLOR_LEDGER_TID_BUDGET,
) -> tuple[list[dict[str, object]], bool]:
    """Keep at most ``budget`` TIDs by CPU-time delta; flag truncation."""

    if len(samples) <= budget:
        return list(samples), False
    ordered = sorted(
        samples,
        key=lambda item: float(item.get("cpu_time_ms_delta") or 0.0),
        reverse=True,
    )
    return ordered[:budget], True


def aggregate_role_observations(
    samples: Iterable[dict[str, object]],
    *,
    window_s: float,
) -> list[RoleObservation]:
    """Aggregate per-TID color samples into per-role observations.

    Each sample: ``{tid, comm, cgroup, cpu_time_ms_delta, runqueue_wait_ms_delta,
    timeslices_delta, cpus_seen, restore_covered}``.
    """

    roles: dict[str, _RoleAccumulator] = {}
    for sample in samples:
        cgroup = str(sample.get("cgroup") or "")
        comm = sample.get("comm")
        comm_text = str(comm).strip() if comm is not None else ""
        key = role_key_for(cgroup, comm)
        acc = roles.get(key)
        if acc is None:
            acc = _RoleAccumulator(role_key=key, cgroup=cgroup)
            roles[key] = acc
        if comm_text:
            acc.comm = comm_text
            acc.comm_known = True
        tid = sample.get("tid")
        if isinstance(tid, int):
            acc.tids.add(tid)
        else:
            # Aggregate sources (process-cgroups) may report a count instead.
            acc.tids.add(len(acc.tids) + 1)
        acc.cpu_time_ms_delta += float(sample.get("cpu_time_ms_delta") or 0.0)
        acc.runqueue_wait_ms_delta += float(sample.get("runqueue_wait_ms_delta") or 0.0)
        acc.timeslices_delta += int(sample.get("timeslices_delta") or 0)
        for cpu in sample.get("cpus_seen") or ():
            if isinstance(cpu, int):
                acc.cpus_seen.add(cpu)
        if sample.get("restore_covered") is False:
            acc.restore_covered = False

    observations: list[RoleObservation] = []
    for acc in roles.values():
        observations.append(
            RoleObservation(
                role_key=acc.role_key,
                cgroup=acc.cgroup,
                comm=acc.comm,
                tid_count=len(acc.tids),
                cpu_time_ms_delta=round(acc.cpu_time_ms_delta, 3),
                runqueue_wait_ms_delta=round(acc.runqueue_wait_ms_delta, 3),
                timeslices_delta=acc.timeslices_delta,
                cpus_seen=tuple(sorted(acc.cpus_seen)),
                window_s=window_s,
                restore_covered=acc.restore_covered,
                stable=acc.comm_known,
            )
        )
    return observations


# ---------------------------------------------------------------------------
# Profiler artifact reader (same aggregation + classification as the daemon).
# ---------------------------------------------------------------------------
def iter_jsonl_rows(path: str | Path) -> Iterable[dict[str, object]]:
    """Yield JSON-object rows from a JSONL file (shared hardened reader, Q3).

    Tolerates a truncated *final* line (a sampler killed mid-write leaves one);
    a malformed line anywhere else still raises so corrupt artifacts fail loud.
    """

    with Path(path).open() as handle:
        pending_error: json.JSONDecodeError | None = None
        for line in handle:
            text = line.strip()
            if not text:
                continue
            if pending_error is not None:
                raise pending_error
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                pending_error = exc
                continue
            if isinstance(row, dict):
                yield row


# Backwards-compatible internal alias (Q3: single reader, two historical names).
_iter_jsonl = iter_jsonl_rows


def _first_last_delta(first: object, last: object, scale: float) -> float:
    try:
        low = float(first)
        high = float(last)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (high - low) * scale)


def thread_samples_from_schedstat_jsonl(
    path: str | Path,
    *,
    restore_covered_cgroups: set[str] | None = None,
) -> list[dict[str, object]]:
    threads: dict[int, dict[str, object]] = {}
    for row in _iter_jsonl(path):
        for item in row.get("threads") or []:
            if not isinstance(item, dict):
                continue
            tid = item.get("tid")
            if not isinstance(tid, int):
                continue
            state = threads.setdefault(
                tid,
                {
                    "tid": tid,
                    "comm": None,
                    "cgroup": None,
                    "run_time_first": None,
                    "run_time_last": None,
                    "wait_first": None,
                    "wait_last": None,
                    "slices_first": None,
                    "slices_last": None,
                    "cpus_seen": set(),
                },
            )
            comm = item.get("comm")
            if comm:
                state["comm"] = comm
            cgroup = item.get("cgroup")
            if cgroup:
                state["cgroup"] = cgroup
            _update_first_last(state, "run_time", item.get("run_time_ns"))
            _update_first_last(state, "wait", item.get("runqueue_wait_ns"))
            _update_first_last(state, "slices", item.get("timeslices"))
            cpu = item.get("current_cpu")
            if isinstance(cpu, int):
                state["cpus_seen"].add(cpu)

    samples: list[dict[str, object]] = []
    for state in threads.values():
        cgroup = str(state.get("cgroup") or "")
        covered = (
            restore_covered_cgroups is None
            or any(cov in cgroup or cgroup in cov for cov in restore_covered_cgroups)
        )
        samples.append(
            {
                "tid": state["tid"],
                "comm": state.get("comm"),
                "cgroup": cgroup,
                "cpu_time_ms_delta": _first_last_delta(
                    state.get("run_time_first"), state.get("run_time_last"), 1e-6
                ),
                "runqueue_wait_ms_delta": _first_last_delta(
                    state.get("wait_first"), state.get("wait_last"), 1e-6
                ),
                "timeslices_delta": int(
                    _first_last_delta(
                        state.get("slices_first"), state.get("slices_last"), 1.0
                    )
                ),
                "cpus_seen": sorted(state["cpus_seen"]),
                "restore_covered": covered,
            }
        )
    return samples


def _update_first_last(state: dict[str, object], name: str, value: object) -> None:
    if value is None:
        return
    if state[f"{name}_first"] is None:
        state[f"{name}_first"] = value
    state[f"{name}_last"] = value


def thread_samples_from_process_cgroups_jsonl(
    path: str | Path,
    *,
    appid: str,
) -> list[dict[str, object]]:
    app_scope = f"app-steam-app{appid}"
    processes: dict[int, dict[str, object]] = {}
    for row in _iter_jsonl(path):
        for item in row.get("processes") or []:
            if not isinstance(item, dict):
                continue
            pid = item.get("pid")
            cgroup = item.get("cgroup")
            if not isinstance(pid, int) or not cgroup:
                continue
            if app_scope in str(cgroup):
                continue
            cpu_time_s = item.get("cpu_time_s")
            if cpu_time_s is None:
                continue
            state = processes.setdefault(
                pid,
                {
                    "tid": pid,
                    "comm": item.get("comm"),
                    "cgroup": cgroup,
                    "first": cpu_time_s,
                    "last": cpu_time_s,
                },
            )
            state["last"] = cpu_time_s
            if item.get("comm"):
                state["comm"] = item.get("comm")

    samples: list[dict[str, object]] = []
    for state in processes.values():
        samples.append(
            {
                "tid": state["tid"],
                "comm": state.get("comm"),
                "cgroup": str(state.get("cgroup") or ""),
                "cpu_time_ms_delta": _first_last_delta(
                    state.get("first"), state.get("last"), 1000.0
                ),
                "runqueue_wait_ms_delta": 0.0,
                "timeslices_delta": 0,
                "cpus_seen": [],
                "restore_covered": True,
            }
        )
    return samples


def build_color_ledger_from_artifacts(
    *,
    appid: str,
    window_s: float,
    thread_schedstat_jsonl: str | Path | None = None,
    process_cgroups_jsonl: str | Path | None = None,
    restore_covered_cgroups: set[str] | None = None,
    thresholds: ColorThresholds = DEFAULT_THRESHOLDS,
    budget: int = COLOR_LEDGER_TID_BUDGET,
) -> ColorLedger:
    """Build a color ledger from a profiler run's JSONL artifacts (advisory)."""

    samples: list[dict[str, object]] = []
    if thread_schedstat_jsonl is not None:
        samples.extend(
            thread_samples_from_schedstat_jsonl(
                thread_schedstat_jsonl,
                restore_covered_cgroups=restore_covered_cgroups,
            )
        )
    if process_cgroups_jsonl is not None:
        samples.extend(
            thread_samples_from_process_cgroups_jsonl(
                process_cgroups_jsonl, appid=appid
            )
        )
    kept, truncated = cap_thread_samples(samples, budget=budget)
    observations = aggregate_role_observations(kept, window_s=window_s)
    return build_color_ledger(
        observations,
        appid=appid,
        thresholds=thresholds,
        advisory=True,
        truncated=truncated,
    )
