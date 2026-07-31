#!/usr/bin/env python3
"""GPU utilisation from the xe PMU.

The governor's ``render_busy`` signal comes from DRM fdinfo engine accounting,
and on this platform that accounting does not exist: a full sweep of every
process on the reference device (kernel 6.16-valve, ``xe``) found no fdinfo
carrying ``drm-engine`` at all. So every threshold keyed on render busyness has
been permanently unreachable, and the scheduler has been guessing about the GPU.

The xe PMU does expose it. Two counters matter:

* ``engine-active-ticks`` / ``engine-total-ticks`` on the render engine give real
  utilisation. Measured 78-88% in a heavy scene.
* ``gt-c6-residency`` is the direct race-to-idle detector. A high clock with
  non-zero C6 means the GPU sprints and then sleeps - reclaimable waste. A high
  clock with zero C6 means the work is genuinely there and capping will only
  cost frames.

That pair answers a question p95 cannot: *is there anything to take* before we
try to take it. p95 only tells us afterwards that we broke pacing.

Read via ``perf stat`` rather than raw ``perf_event_open``: the counters are
free-running, we need a delta over a window, and shelling out keeps this out of
the governor's own accounting. Sampling is best-effort - every failure path
returns None so the governor degrades to its previous behaviour.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

PMU_ROOT = Path("/sys/bus/event_source/devices")
# Render engine, first instance, primary tile.
RENDER_ENGINE_CLASS = 0
RENDER_ENGINE_INSTANCE = 0
PRIMARY_GT = 0


def discover_xe_pmu(root: str | Path = PMU_ROOT) -> str | None:
    """Name of the xe PMU device, e.g. ``xe_0000_00_02.0``."""
    try:
        entries = sorted(p.name for p in Path(root).iterdir())
    except OSError:
        return None
    for name in entries:
        if name.startswith("xe_"):
            return name
    return None


@dataclass(frozen=True)
class GpuUtilisationSample:
    """One window of GPU behaviour.

    ``render_busy`` is the fraction the render engine was active, i.e. the
    replacement for the fdinfo signal. ``c6_ms`` is how long the GT spent in its
    deepest idle state during the window.
    """

    render_busy: float | None
    c6_ms: float | None
    actual_mhz: float | None
    window_s: float

    @property
    def racing_to_idle(self) -> bool | None:
        """Clock held high while the engine is idle a meaningful share of the time.

        This is the signature worth acting on: the frames are already being
        delivered, so the frequency is buying nothing but voltage.
        """
        if self.render_busy is None or self.c6_ms is None:
            return None
        idle_share = self.c6_ms / (self.window_s * 1000.0) if self.window_s else 0.0
        return self.render_busy < 0.60 and idle_share > 0.10


def _events(pmu: str) -> list[str]:
    engine = (
        f"engine_class={RENDER_ENGINE_CLASS},"
        f"engine_instance={RENDER_ENGINE_INSTANCE},"
        f"gt={PRIMARY_GT}"
    )
    return [
        f"{pmu}/engine-active-ticks,{engine}/",
        f"{pmu}/engine-total-ticks,{engine}/",
        f"{pmu}/gt-c6-residency,gt={PRIMARY_GT}/",
        f"{pmu}/gt-actual-frequency,gt={PRIMARY_GT}/",
    ]


def sample_gpu_utilisation(
    pmu: str | None = None,
    *,
    window_s: float = 1.0,
    runner=None,
) -> GpuUtilisationSample | None:
    """Measure one window. Returns None whenever the counters are unavailable."""
    pmu = pmu or discover_xe_pmu()
    if pmu is None:
        return None
    cmd = ["perf", "stat", "-a", "-x", ","]
    for event in _events(pmu):
        cmd += ["-e", event]
    cmd += ["--", "sleep", f"{window_s:g}"]
    run = runner or (
        lambda command: subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=window_s + 5.0,
        )
    )
    try:
        result = run(cmd)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 and not result.stderr:
        return None
    return parse_perf_csv(result.stderr or result.stdout, window_s=window_s)


def parse_perf_csv(text: str, *, window_s: float) -> GpuUtilisationSample | None:
    """Pull the four counters out of ``perf stat -x,`` output."""
    active = total = c6 = mhz = None
    for line in text.splitlines():
        fields = line.split(",")
        if len(fields) < 3:
            continue
        raw_value = fields[0].strip()
        # With -I the timestamp occupies field 0; without it the value does.
        if re.fullmatch(r"\d+\.\d+", raw_value) and len(fields) > 3:
            raw_value, event = fields[1].strip(), ",".join(fields[3:])
        else:
            event = ",".join(fields[2:])
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if "engine-active-ticks" in event:
            active = value
        elif "engine-total-ticks" in event:
            total = value
        elif "gt-c6-residency" in event:
            c6 = value
        elif "gt-actual-frequency" in event:
            mhz = value
    render_busy = None
    if active is not None and total and total > 0:
        render_busy = round(min(1.0, active / total), 4)
    if render_busy is None and c6 is None and mhz is None:
        return None
    return GpuUtilisationSample(
        render_busy=render_busy,
        c6_ms=c6,
        actual_mhz=mhz,
        window_s=window_s,
    )


class GpuUtilisationMonitor:
    """Samples the PMU on a background thread and publishes the latest window.

    ``perf stat`` blocks for the length of its window, so calling it from the
    governor's tick would stall scheduling for half of every poll. Same shape as
    the input monitor: a thread owns the blocking work, the governor reads a
    value that is already there.
    """

    def __init__(self, *, window_s: float = 1.0, sampler=sample_gpu_utilisation):
        self.window_s = window_s
        self._sampler = sampler
        self._pmu: str | None = None
        self._latest: GpuUtilisationSample | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> bool:
        self._pmu = discover_xe_pmu()
        if self._pmu is None:
            return False
        self._thread = threading.Thread(
            target=self._run, name="game-power-gpu-pmu", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = self._sampler(self._pmu, window_s=self.window_s)
            with self._lock:
                self._latest = sample
            # A failed read must not become a hot retry loop.
            if sample is None:
                self._stop.wait(5.0)

    def latest(self) -> GpuUtilisationSample | None:
        with self._lock:
            return self._latest
