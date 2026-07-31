#!/usr/bin/env python3
"""Auto frame-target estimation.

Answers "what frame rate can this scene actually hold?" so the governor can aim
at something reachable instead of burning full power forever against a target the
hardware cannot meet.

Product boundaries live in ``.codex/skills/game-power-scheduler``. The two that
shape this module:

* **Targets are divisors of the panel refresh rate.** There is no working VRR on
  the reference panel (no ``vrr_capable`` on the eDP connector), so a non-divisor
  cap gives structurally uneven frame intervals: at a fixed 120 Hz, 50 FPS
  alternates 2- and 3-refresh frames forever. 60/40/30 are exact. Because the
  candidates are already even at the panel's own rate, nothing here needs to
  touch the refresh rate.
* **Minimise felt changes.** A cap change is something the player notices, so the
  rungs are deliberately far apart and the rules are asymmetric: drop readily on
  a proven, *material* shortfall; climb back only on a long, decisive win.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# n=1 is the panel rate itself; n=4 at 120 Hz is 30 FPS, the floor we would ever
# propose. Deeper divisors (24 FPS at 120 Hz) are below what the FPS target
# contract accepts.
DIVISOR_RANGE = (1, 2, 3, 4)


def divisor_candidates(
    refresh_hz: float | None,
    *,
    min_fps: int,
    max_fps: int,
) -> tuple[int, ...]:
    """Reachable targets that divide the panel refresh rate evenly, high to low."""
    if refresh_hz is None or refresh_hz <= 0:
        return ()
    seen: list[int] = []
    for n in DIVISOR_RANGE:
        value = refresh_hz / n
        candidate = int(round(value))
        # Only exact divisors: 120/3 == 40 qualifies, 90/4 == 22.5 does not.
        if abs(value - candidate) > 1e-6:
            continue
        if candidate < min_fps or candidate > max_fps:
            continue
        if candidate not in seen:
            seen.append(candidate)
    return tuple(sorted(seen, reverse=True))


def snap_down_to_candidate(
    fps: float, candidates: tuple[int, ...], *, tolerance: float = 0.0
) -> int | None:
    """Largest candidate the scene can hold, within ``tolerance``.

    The tolerance matters because the rungs are far apart. A scene sustaining
    58 FPS plainly holds a 60 cap - the cap only limits, so the occasional dip
    below it is no worse than today while the over-rendering above it goes away.
    Without tolerance, 58 snaps past 60 to 40 and throws away 20 FPS.
    """
    ceiling = fps * (1.0 + tolerance)
    for candidate in candidates:
        if candidate <= ceiling:
            return candidate
    return None


def next_candidate_up(current: int, candidates: tuple[int, ...]) -> int | None:
    """Smallest candidate strictly above ``current``."""
    above = [value for value in candidates if value > current]
    return min(above) if above else None


@dataclass(frozen=True)
class AutoTargetProposal:
    """A recommendation. Whether it is acted on is the caller's decision."""

    fps: int
    reason: str
    sustainable_fps: float
    samples: int


@dataclass
class AutoTargetConfig:
    # Window over which a scene is judged. "Sustained" means repeatedly short
    # across this window, NOT short without interruption: a scene that bounces
    # 36-60 FPS is exactly the instability worth fixing, and a consecutive-only
    # counter resets on every good sample and never fires (device evidence
    # 2026-07-31: 130 s of visibly unstable play produced no proposal).
    sustained_shortfall_s: float = 40.0
    # Fraction of the window that must be below target to count as a problem.
    shortfall_fraction: float = 0.34
    # ...and this long to earn a climb back. Deliberately much longer: dropping
    # is recoverable, a needless raise puts the player back in the stutter they
    # just escaped.
    sustained_headroom_s: float = 180.0
    # How short is "materially" short, judged against the reachability
    # percentile below. A scene running at 97% of target is a near-miss for the
    # power scheduler to absorb, NOT a reason to give up a whole divisor rung
    # (at 120 Hz the next rung down from 60 is 40).
    material_shortfall_ratio: float = 0.90
    # Reachability is judged on a HIGH percentile: if the scene reaches the
    # target even occasionally, the target is attainable and the misses are
    # transient (asset streaming, shader compilation, a combat burst). Lowering
    # the cap does nothing for those -- the hitch happens regardless, you just
    # render fewer frames between hitches. Only a scene that *rarely* reaches
    # the target is actually capability-limited.
    reachable_percentile: float = 0.90
    # Margin required over the next rung up before climbing, so a game sitting
    # exactly on a boundary cannot oscillate across it.
    climb_margin_ratio: float = 1.08
    # Percentile of observed frame rate treated as "sustainable". Low rather
    # than mean: the point is a rate the scene holds, not one it averages.
    sustainable_percentile: float = 0.25
    # How far above the sustainable rate a rung may still be chosen. The rungs
    # are ~50% apart, so without this a scene sustaining 58 lands on 40 rather
    # than the 60 it demonstrably holds.
    snap_tolerance: float = 0.05
    # Felt changes per session, downward. Beyond this we stop proposing and let
    # the power scheduler do what it can.
    max_drops_per_session: int = 2


class AutoTargetEstimator:
    """Tracks whether the current frame target is reachable.

    Only samples taken with **nothing of ours applied** count toward a shortfall:
    if a trim is active, a miss may be our own fault and lowering the target
    would be blaming the game for our doing.
    """

    def __init__(self, config: AutoTargetConfig | None = None, *, poll_s: float = 2.0):
        self.config = config or AutoTargetConfig()
        self.poll_s = max(0.1, poll_s)
        self._appid: str | None = None
        self._drops = 0
        short_window = max(1, int(self.config.sustained_shortfall_s / self.poll_s))
        clear_window = max(1, int(self.config.sustained_headroom_s / self.poll_s))
        # (below_target, avg_fps) over the judging window.
        self._window: deque[tuple[bool, float]] = deque(maxlen=short_window)
        self._clear_samples: deque[float] = deque(maxlen=clear_window)

    def reset(self, appid: str | None = None) -> None:
        self._appid = appid
        self._drops = 0
        self._window.clear()
        self._clear_samples.clear()

    @property
    def drops_this_session(self) -> int:
        return self._drops

    def observe(
        self,
        *,
        appid: str | None,
        target_fps: float | None,
        avg_fps: float | None,
        refresh_hz: float | None,
        below_target: bool,
        trims_active: bool,
        min_fps: int,
        max_fps: int,
    ) -> AutoTargetProposal | None:
        if appid != self._appid:
            self.reset(appid)
        if target_fps is None or avg_fps is None or avg_fps <= 0:
            return None

        candidates = divisor_candidates(refresh_hz, min_fps=min_fps, max_fps=max_fps)
        if not candidates:
            return None

        # A miss while we are trimming is not evidence about the game, so those
        # ticks are dropped from the window entirely rather than counted either way.
        if trims_active and below_target:
            return None
        self._window.append((below_target, avg_fps))
        if below_target:
            self._clear_samples.clear()
        else:
            self._clear_samples.append(avg_fps)

        drop = self._propose_drop(target_fps, candidates)
        if drop is not None:
            return drop
        return self._propose_climb(target_fps, candidates)

    def _propose_drop(
        self, target_fps: float, candidates: tuple[int, ...]
    ) -> AutoTargetProposal | None:
        if self._drops >= self.config.max_drops_per_session:
            return None
        if len(self._window) < (self._window.maxlen or 1):
            return None
        short = [fps for below, fps in self._window if below]
        if len(short) < len(self._window) * self.config.shortfall_fraction:
            return None
        observed = deque(sorted(fps for _, fps in self._window))
        # Can this scene reach the target at all? If its good moments still land
        # on target, the misses are transient and capping cannot fix them.
        reachable = _percentile(observed, self.config.reachable_percentile)
        if reachable is None:
            return None
        if reachable >= target_fps * self.config.material_shortfall_ratio:
            # Either a near-miss, or transient hitches. Both are the power
            # scheduler's problem, not the target's.
            return None
        # Capability-limited: cap where it can actually hold, not at its average.
        sustainable = _percentile(observed, self.config.sustainable_percentile)
        if sustainable is None:
            return None
        proposed = snap_down_to_candidate(
            sustainable, candidates, tolerance=self.config.snap_tolerance
        )
        if proposed is None or proposed >= target_fps:
            return None
        observed = len(self._window)
        self._drops += 1
        self._window.clear()
        return AutoTargetProposal(
            fps=proposed,
            reason="sustained-shortfall",
            sustainable_fps=round(sustainable, 2),
            samples=observed,
        )

    def _propose_climb(
        self, target_fps: float, candidates: tuple[int, ...]
    ) -> AutoTargetProposal | None:
        if len(self._clear_samples) < (self._clear_samples.maxlen or 1):
            return None
        higher = next_candidate_up(int(round(target_fps)), candidates)
        if higher is None:
            return None
        sustainable = _percentile(
            self._clear_samples, self.config.sustainable_percentile
        )
        if sustainable is None:
            return None
        if sustainable < higher * self.config.climb_margin_ratio:
            return None
        observed = len(self._clear_samples)
        self._clear = 0
        self._clear_samples.clear()
        return AutoTargetProposal(
            fps=higher,
            reason="sustained-headroom",
            sustainable_fps=round(sustainable, 2),
            samples=observed,
        )


def _percentile(values: deque[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(len(ordered) * fraction)
    return ordered[min(index, len(ordered) - 1)]
