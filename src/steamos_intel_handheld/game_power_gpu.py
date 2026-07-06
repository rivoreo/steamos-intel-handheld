"""Game Power V10 Contract 1.2 -- xe GPU frequency actuator.

Self-contained, stdlib-only module that discovers Intel ``xe`` GPU GT
frequency controls under sysfs and actuates ``min_freq`` / ``max_freq``
(integer MHz) with clamped, fail-closed writes and readback-verified restore.

Real device layout (verified in ``scripts/profile-game-power-on-device.sh``)::

    /sys/class/drm/card0/device/tile0/gt0/freq0/{min,max,rp0,rpe,rpn}_freq
    /sys/class/drm/card0/device/tile0/gt0/<*slpc*power*profile*>   (optional)

All frequency values are integer MHz.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GpuGt:
    name: str  # e.g. "gt0"
    freq_path: Path  # .../gt0/freq0
    gt_path: Path  # .../gt0  (parent of freq0; where the SLPC knob lives)
    rp0_mhz: int | None
    rpe_mhz: int | None
    rpn_mhz: int | None
    min_writable: bool  # min_freq exists and is writable
    max_writable: bool  # max_freq exists and is writable
    slpc_power_profile_path: Path | None  # feature-detected knob, else None


@dataclass(frozen=True)
class GpuFreqSnapshot:
    # gt.name -> (min_mhz, max_mhz) as read at snapshot time
    values: dict[str, tuple[int | None, int | None]]


def _read_int(path: Path) -> int | None:
    """Read an integer MHz value; return ``None`` if missing/unparseable."""

    try:
        text = path.read_text().strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _is_slpc_knob(name: str) -> bool:
    lowered = name.lower()
    if "slpc" in lowered:
        return True
    return "power" in lowered and "profile" in lowered


def _detect_slpc_knob(gt_path: Path) -> Path | None:
    """Find the first (sorted) SLPC power-profile file under ``gt_path``.

    Non-recursive; matches a file whose name contains both "power" and
    "profile" (case-insensitive) OR contains "slpc". Returns ``None`` when
    no such file exists.
    """

    try:
        candidates = sorted(gt_path.iterdir(), key=str)
    except OSError:
        return None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if _is_slpc_knob(candidate.name):
            return candidate
    return None


def discover_gpu_gts(sysfs_root: str | Path = "/sys") -> list[GpuGt]:
    """Discover every xe GPU GT frequency control under ``sysfs_root``.

    Globs ``<sysfs_root>/class/drm`` for ``card*/device/tile*/gt*/freq0`` and
    returns one :class:`GpuGt` per match, sorted deterministically by path.
    Never raises; missing trees or OSErrors yield an empty (or partial) list.
    """

    drm_root = Path(sysfs_root) / "class" / "drm"
    try:
        freq_dirs = sorted(
            drm_root.glob("card*/device/tile*/gt*/freq0"), key=str
        )
    except OSError:
        return []

    gts: list[GpuGt] = []
    for freq_path in freq_dirs:
        try:
            if not freq_path.is_dir():
                continue
        except OSError:
            continue
        gt_path = freq_path.parent
        min_freq = freq_path / "min_freq"
        max_freq = freq_path / "max_freq"
        gts.append(
            GpuGt(
                name=gt_path.name,
                freq_path=freq_path,
                gt_path=gt_path,
                rp0_mhz=_read_int(freq_path / "rp0_freq"),
                rpe_mhz=_read_int(freq_path / "rpe_freq"),
                rpn_mhz=_read_int(freq_path / "rpn_freq"),
                min_writable=min_freq.exists() and os.access(min_freq, os.W_OK),
                max_writable=max_freq.exists() and os.access(max_freq, os.W_OK),
                slpc_power_profile_path=_detect_slpc_knob(gt_path),
            )
        )
    return gts


def _clamp(value: int, gt: GpuGt) -> int:
    """Clamp a requested MHz value into the GT's [rpn, rp0] bounds."""

    if gt.rpn_mhz is not None:
        value = max(value, gt.rpn_mhz)
    if gt.rp0_mhz is not None:
        value = min(value, gt.rp0_mhz)
    return value


class GpuFreqActuator:
    def __init__(self, gts: Iterable[GpuGt]) -> None:
        self.gts = list(gts)
        self._failed = False
        # The min_freq value actually written by the most recent ``apply`` (the
        # deepest floor across GTs), or ``None`` when no min write occurred. The
        # governor reads this for telemetry because the min the cap forced is
        # data-dependent: it is only lowered when a GT's latched min sits above
        # the new max cap (see the D1 note in ``apply``).
        self.last_applied_min_mhz: int | None = None
        # D6: per-GT (min_mhz, max_mhz) actually applied by the most recent
        # ``apply``, keyed by ``gt.name``. A ratio cap is derived from EACH GT's
        # own rp0, so the render GT (gt0, rp0 1950) and the media GT (gt1, rp0
        # 1200) receive different absolute caps. ``None`` in a slot means that
        # knob was left at its baseline for that GT. The governor reports this as
        # the per-GT telemetry breakdown (render-GT values stay in the flat
        # min_mhz/max_mhz keys for backward compatibility).
        self.last_applied: dict[str, tuple[int | None, int | None]] = {}

    @property
    def failed(self) -> bool:
        return self._failed

    def snapshot(self) -> GpuFreqSnapshot:
        values: dict[str, tuple[int | None, int | None]] = {}
        for gt in self.gts:
            values[gt.name] = (
                _read_int(gt.freq_path / "min_freq"),
                _read_int(gt.freq_path / "max_freq"),
            )
        return GpuFreqSnapshot(values=values)

    def apply(
        self,
        *,
        min_mhz: int | None = None,
        max_mhz: int | None = None,
        max_ratio: float | None = None,
    ) -> None:
        """Clamp and write min/max MHz per GT; fail-closed on any write error.

        Writes ``max_freq`` FIRST then ``min_freq`` (avoids a transient
        min>max hardware rejection). No-op writes are skipped. On the first
        OSError the ``_failed`` latch is set and we stop immediately.

        D6: ``max_ratio`` (0..1) caps each GT at ``int(gt.rp0 * (1 - ratio))``
        computed from THAT GT's own rp0, so a shared cap rung trims the render
        GT and the media GT proportionally instead of collapsing the render GT
        to the smaller GT's absolute cap. ``max_ratio`` takes precedence over
        ``max_mhz`` on any GT that exposes an rp0; a GT without an rp0 falls
        back to the absolute ``max_mhz`` (if given).
        """

        if self._failed:
            return
        self.last_applied_min_mhz = None
        self.last_applied = {}
        for gt in self.gts:
            if max_ratio is not None and gt.rp0_mhz is not None:
                new_max = _clamp(max(1, int(gt.rp0_mhz * (1.0 - max_ratio))), gt)
            elif max_mhz is not None:
                new_max = _clamp(max_mhz, gt)
            else:
                new_max = None
            new_min = _clamp(min_mhz, gt) if min_mhz is not None else None

            # D1: a max cap below the GT's latched min_freq is a live no-op --
            # the kernel/xe keeps cur pinned at min whenever min > max (the real
            # device latches gt0 min at rp0=1950). So when we cap max and the
            # caller did not pass an explicit min, LOWER min to min(cap, rpe),
            # clamped into [rpn, rp0], so the cap actually takes effect
            # regardless of the prior latched min. This is reduction-only: we
            # only touch min when the current min sits ABOVE the new max.
            if new_max is not None and new_min is None and gt.min_writable:
                current_min = _read_int(gt.freq_path / "min_freq")
                if current_min is not None and current_min > new_max:
                    floor = (
                        new_max
                        if gt.rpe_mhz is None
                        else min(new_max, gt.rpe_mhz)
                    )
                    new_min = _clamp(floor, gt)

            if new_min is not None:
                # Ensure final min does not exceed final max for this GT.
                effective_max = new_max
                if effective_max is None:
                    effective_max = _read_int(gt.freq_path / "max_freq")
                if effective_max is not None and new_min > effective_max:
                    new_min = effective_max

            applied_max: int | None = None
            applied_min: int | None = None
            try:
                if new_max is not None and gt.max_writable:
                    _write_int_if_changed(gt.freq_path / "max_freq", new_max)
                    applied_max = new_max
                if new_min is not None and gt.min_writable:
                    _write_int_if_changed(gt.freq_path / "min_freq", new_min)
                    applied_min = new_min
                    if (
                        self.last_applied_min_mhz is None
                        or new_min < self.last_applied_min_mhz
                    ):
                        self.last_applied_min_mhz = new_min
            except OSError:
                self._failed = True
                return
            self.last_applied[gt.name] = (applied_min, applied_max)

    def restore(self, snapshot: GpuFreqSnapshot) -> list[str]:
        """Restore snapshot min/max on every GT, readback-verified.

        Writes ``max_freq`` THEN ``min_freq`` (max first avoids min>max),
        each verified by readback with one retry. Returns the string paths
        that could not be verified as restored (empty on success). Restore
        always attempts regardless of the ``_failed`` latch.
        """

        failed: list[str] = []
        for gt in self.gts:
            snap_min, snap_max = snapshot.values.get(gt.name, (None, None))
            if snap_max is not None and not _restore_verified(
                gt.freq_path / "max_freq", str(snap_max)
            ):
                failed.append(str(gt.freq_path / "max_freq"))
            if snap_min is not None and not _restore_verified(
                gt.freq_path / "min_freq", str(snap_min)
            ):
                failed.append(str(gt.freq_path / "min_freq"))
        return failed

    def slpc_power_profile_targets(self) -> list[GpuGt]:
        """Return the GTs that expose an SLPC power-profile knob."""

        return [gt for gt in self.gts if gt.slpc_power_profile_path is not None]

    def set_slpc_power_profile(self, value: str) -> dict[str, object]:
        """Write ``value`` to every GT's SLPC knob; feature-detected per GT.

        Returns a report ``{"applied": [...], "skipped": [...], "failed": [...]}``.
        GTs without the knob are skipped with reason "absent". A write/readback
        failure records the GT under "failed" and sets the ``_failed`` latch.
        """

        applied: list[str] = []
        skipped: list[dict[str, str]] = []
        failed: list[str] = []
        for gt in self.gts:
            knob = gt.slpc_power_profile_path
            if knob is None:
                skipped.append({"gt": gt.name, "reason": "absent"})
                continue
            if _restore_verified(knob, value):
                applied.append(gt.name)
            else:
                failed.append(gt.name)
                self._failed = True
        return {"applied": applied, "skipped": skipped, "failed": failed}


def _write_int_if_changed(path: Path, value: int) -> None:
    """Write ``value`` (as text) only when the current readback differs."""

    if _read_int(path) == value:
        return
    path.write_text(str(value))


def _restore_verified(path: Path, value: str, *, attempts: int = 2) -> bool:
    """Write ``value`` and verify by readback; retry once on mismatch.

    The write is skipped only when the pre-read already matches. Any write
    error or persistent readback mismatch returns ``False``.
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
