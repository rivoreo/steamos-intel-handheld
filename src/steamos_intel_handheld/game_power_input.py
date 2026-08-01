#!/usr/bin/env python3
"""Input-activity tracking for the idle frame cap.

Why evdev and not the compositor's counter: ``GAMESCOPE_INPUT_COUNTER`` does not
observe in-game input. On the reference device Steam Input grabs the physical
controller and re-emits on a virtual pad, and the atom stayed frozen through
active play - which made an idle detector built on it cap a player mid-game.
Measured 2026-08-01: 8 s of active play produced 52 events on the virtual pad
and 0 on the atom.

The failure mode here is deliberately the safe one. This reports *time since the
last input event*, so a device that chatters while untouched (analog drift, a
sensor) makes the machine look permanently active and the idle cap simply never
engages. The dangerous direction - claiming idle while someone is playing -
requires every watched device to go silent, which is what being idle means.
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import struct
import threading
import time
from pathlib import Path

# One evdev event record: struct input_event.
_EVENT_SIZE = struct.calcsize("llHHi")

# EV_KEY (bit 1) and EV_ABS (bit 3) in the device's EV capability mask. A device
# that reports neither cannot represent a person touching the machine.
_EV_KEY_BIT = 1 << 1
_EV_ABS_BIT = 1 << 3

# Devices that advertise keys but fire from lid/power/thermal events rather than
# from someone playing. Treating these as activity would defeat the cap.
_EXCLUDED_NAME_PATTERNS = (
    "lid switch",
    "sleep button",
    "power button",
    "video bus",
    "pc speaker",
    "sof-hda",
    "hdmi",
)


def discover_input_event_devices(
    proc_devices: str | Path = "/proc/bus/input/devices",
) -> list[str]:
    """Event device paths that can represent a person using the machine."""
    try:
        text = Path(proc_devices).read_text()
    except OSError:
        return []
    paths: list[str] = []
    for block in text.strip().split("\n\n"):
        name_match = re.search(r'N: Name="(.*)"', block)
        name = (name_match.group(1) if name_match else "").lower()
        if any(pattern in name for pattern in _EXCLUDED_NAME_PATTERNS):
            continue
        ev_match = re.search(r"B: EV=([0-9a-fA-F]+)", block)
        if not ev_match:
            continue
        try:
            ev_mask = int(ev_match.group(1), 16)
        except ValueError:
            continue
        if not (ev_mask & (_EV_KEY_BIT | _EV_ABS_BIT)):
            continue
        handlers = re.search(r"H: Handlers=(.*)", block)
        if not handlers:
            continue
        event = re.search(r"event\d+", handlers.group(1))
        if event:
            paths.append(f"/dev/input/{event.group(0)}")
    return paths


class InputActivityMonitor:
    """Tracks seconds since the last input event across the watched devices.

    Runs a daemon thread blocked in ``select`` rather than polling, so the moment
    a button is pressed is observed immediately. Release latency is what matters:
    a player picking the device up must not wait for a governor tick.
    """

    def __init__(
        self,
        paths: list[str] | None = None,
        *,
        clock=time.monotonic,
        rediscover_s: float = 20.0,
    ) -> None:
        self._clock = clock
        # None means "keep asking the system"; an explicit list is fixed (tests).
        self._fixed_paths = paths
        self._paths = paths if paths is not None else discover_input_event_devices()
        self._rediscover_s = rediscover_s
        self._last_discovery = clock()
        self._lock = threading.Lock()
        self._last_event = clock()
        self._fds: dict[int, str] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def watched(self) -> list[str]:
        return list(self._fds.values())

    def start(self) -> bool:
        self._last_discovery = self._clock() - self._rediscover_s
        for path in self._paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                # A device we cannot read simply is not watched; never fatal.
                continue
            self._fds[fd] = path
        if not self._fds and self._fixed_paths is not None:
            return False
        self._thread = threading.Thread(
            target=self._run, name="game-power-input", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        for fd in list(self._fds):
            with contextlib.suppress(OSError):
                os.close(fd)
        self._fds.clear()

    def _rediscover(self) -> None:
        """Pick up devices that appeared after start.

        Input devices are renumbered when a controller reconnects or the machine
        resumes, and Steam Input's virtual pad - the only node that carries
        in-game input - is created late. Enumerating once at startup left the
        monitor watching neither controller, which would make an actively playing
        user look idle: the exact failure this signal was chosen to avoid.
        """
        if self._fixed_paths is not None:
            return
        now = self._clock()
        if now - self._last_discovery < self._rediscover_s:
            return
        self._last_discovery = now
        watched = set(self._fds.values())
        for path in discover_input_event_devices():
            if path in watched:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self._fds[fd] = path

    def _drop(self, fd: int) -> None:
        self._fds.pop(fd, None)
        with contextlib.suppress(OSError):
            os.close(fd)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._rediscover()
            if not self._fds:
                self._stop.wait(1.0)
                continue
            try:
                readable, _, _ = select.select(list(self._fds), [], [], 0.5)
            except (OSError, ValueError):
                # A device vanished; drop the dead ones and carry on rather than
                # killing the watcher and silently losing idle detection.
                for fd in list(self._fds):
                    try:
                        select.select([fd], [], [], 0)
                    except (OSError, ValueError):
                        self._drop(fd)
                continue
            if not readable:
                continue
            for fd in readable:
                try:
                    if not os.read(fd, _EVENT_SIZE * 64):
                        self._drop(fd)
                        continue
                except OSError:
                    self._drop(fd)
                    continue
            with self._lock:
                self._last_event = self._clock()

    def idle_s(self) -> float:
        with self._lock:
            last = self._last_event
        return max(0.0, self._clock() - last)

    def mark_active(self) -> None:
        """Used by tests and by callers that learn of input another way."""
        with self._lock:
            self._last_event = self._clock()
