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
* ``gt-c6-residency`` is recorded but is **inert during gameplay**: it read
  exactly 0 ms in all 145 samples of a live session. C6 is a deep idle state the
  GT does not enter between frames, so it cannot detect "finished the frame early
  and waited". Kept in telemetry only because zero is itself the finding.

What utilisation is *not* good for: choosing a frequency. Measured correlation
between GT frequency and utilisation over that session was -0.916 - frequency is
the cause and utilisation the effect, because a higher clock finishes the same
frame sooner and leaves the engine idle longer. SLPC already runs this loop and
lands utilisation in 0.80-0.97 for most of a session. Feeding utilisation into a
frequency formula would only re-derive SLPC's own controller.

What it *is* good for: detecting that we have over-capped. Utilisation pinned at
the ceiling means the engine has no slack left and frames are about to be missed.
That is a leading indicator, where p95 is a lagging one - p95 only tells us
afterwards that pacing already broke.

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

# Above this the render engine has no slack and frames start slipping; measured
# on device as the point where FPS fell to 56.2 and below-target classification
# jumped to 29%. The healthy band beneath it is roughly 0.80-0.97.
SATURATED_RENDER_BUSY = 0.97


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
    def saturated(self) -> bool | None:
        """Engine has no slack left, so a deeper cap will cost frames.

        Device evidence: samples at or above this level ran 56.2 FPS against a 60
        target with p95 19.7 ms and were classified below-target 29% of the time,
        while the 0.80-0.97 band held 59.6-59.9 FPS at p95 17.9 ms. This is the
        useful direction of the signal - a leading indicator of over-capping.
        """
        if self.render_busy is None:
            return None
        return self.render_busy >= SATURATED_RENDER_BUSY


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
