# Game Power Profiler And Adaptive Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable game-power profiler that can compare `off` and `gpu-priority` policies across TDP levels, report FPS and frame-pacing evidence, and prepare safe cgroup/uClamp experiments without enabling unproven controls by default.

**Architecture:** Add a focused `game_power_profile.py` module for MangoHud parsing, game-power JSONL parsing, run summaries, comparisons, and a local CLI. Extend `game_power.py` with JSONL decision output so device profiling produces machine-readable policy samples, then add a guarded SSH wrapper and harness entry for real-device A/B runs.

**Tech Stack:** Python 3.10+, pytest, csv/json argparse CLIs, MangoHud CSV summaries, SteamOS cgroup v2 paths, Linux PSI files, existing RAPL/TDP provider verification helpers, Bash SSH device wrappers.

---

## File Structure

- Create `src/steamos_intel_handheld/game_power_profile.py`
  - Parse MangoHud summary CSV and raw FPS CSV files.
  - Parse `game-power.jsonl` decision samples.
  - Produce `summary.json` and compare baseline/candidate runs.
  - Provide a CLI for imported-log run summaries and run comparison.
- Create `tests/test_game_power_profile.py`
  - Hardware-free parser, summary, comparison, and CLI tests.
- Modify `src/steamos_intel_handheld/game_power.py`
  - Add `--output-format text|jsonl` to the standalone probe.
  - Emit one JSON object per decision when JSONL is selected.
- Modify `tests/test_game_power.py`
  - Prove JSONL output contains appid, action, reason, RAPL watts, and render busy.
- Modify `pyproject.toml`
  - Add `steamos-intel-handheld-game-power-profile = "steamos_intel_handheld.game_power_profile:main"`.
- Modify `scripts/install-on-device.sh`
  - Install `/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile`.
- Create `scripts/profile-game-power-on-device.sh`
  - Guarded real-device SSH wrapper for imported or controlled capture runs.
  - Temporarily force the installed service governor to `off` so the baseline is
    not polluted by the default-on `gpu-priority` service loop.
  - Snapshot and restore CPU policy and SteamOS TDP.
  - Pull profile artifacts back into `.cache/game-power/profiles/`.
- Modify `harness.toml`
  - Add guarded `game-power-profile-device`.
- Modify `tests/test_integration_assets.py`
  - Prove the new wrapper is installed, guarded, restores state, and records capture mode.
- Modify `README.md` and `docs/design.md`
  - Document profiler commands, imported-vs-controlled capture semantics, and comparison thresholds.

## Task 1: Parse MangoHud FPS Artifacts

**Files:**
- Create: `src/steamos_intel_handheld/game_power_profile.py`
- Create: `tests/test_game_power_profile.py`

- [ ] **Step 1: Write failing MangoHud parser tests**

Create `tests/test_game_power_profile.py`:

```python
import csv
import json
import subprocess
import sys
from pathlib import Path

from steamos_intel_handheld.game_power_profile import (
    CaptureMode,
    MangoHudFpsSummary,
    parse_mangohud_fps_csv,
    parse_mangohud_summary_csv,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_parse_mangohud_summary_csv_reads_low_percentile_metrics(tmp_path):
    path = tmp_path / "mangohud-summary.csv"
    write_csv(
        path,
        ["0.1% Min FPS", "1% Min FPS", "97% Percentile FPS", "Average FPS"],
        [
            {
                "0.1% Min FPS": "24.1",
                "1% Min FPS": "31.2",
                "97% Percentile FPS": "45.8",
                "Average FPS": "42.3",
            }
        ],
    )

    summary = parse_mangohud_summary_csv(path)

    assert isinstance(summary, MangoHudFpsSummary)
    assert summary.avg_fps == 42.3
    assert summary.one_percent_low_fps == 31.2
    assert summary.point_one_percent_low_fps == 24.1
    assert summary.ninety_seven_percentile_fps == 45.8
    assert summary.capture_mode == CaptureMode.IMPORTED


def test_parse_mangohud_fps_csv_computes_average_and_frame_time_percentiles(tmp_path):
    path = tmp_path / "mangohud.csv"
    rows = [
        {"fps": "30", "frametime": "33.3"},
        {"fps": "40", "frametime": "25.0"},
        {"fps": "50", "frametime": "20.0"},
        {"fps": "60", "frametime": "16.7"},
    ]
    write_csv(path, ["fps", "frametime"], rows)

    summary = parse_mangohud_fps_csv(path)

    assert summary.avg_fps == 45.0
    assert summary.one_percent_low_fps == 30.0
    assert summary.point_one_percent_low_fps == 30.0
    assert summary.avg_frametime_ms == 23.75
    assert summary.p95_frametime_ms == 33.3
    assert summary.p99_frametime_ms == 33.3
```

- [ ] **Step 2: Run the focused parser tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_parse_mangohud_summary_csv_reads_low_percentile_metrics tests/test_game_power_profile.py::test_parse_mangohud_fps_csv_computes_average_and_frame_time_percentiles -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'steamos_intel_handheld.game_power_profile'`.

- [ ] **Step 3: Implement the MangoHud parser module**

Create `src/steamos_intel_handheld/game_power_profile.py`:

```python
#!/usr/bin/env python3
"""Profiler and comparison helpers for game-power policy experiments."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from statistics import mean
from typing import Any


class CaptureMode(str, Enum):
    CONTROLLED = "controlled"
    IMPORTED = "imported"


@dataclass(frozen=True)
class MangoHudFpsSummary:
    avg_fps: float | None = None
    one_percent_low_fps: float | None = None
    point_one_percent_low_fps: float | None = None
    ninety_seven_percentile_fps: float | None = None
    avg_frametime_ms: float | None = None
    p95_frametime_ms: float | None = None
    p99_frametime_ms: float | None = None
    capture_mode: CaptureMode = CaptureMode.IMPORTED


def parse_mangohud_summary_csv(
    path: str | Path,
    *,
    capture_mode: CaptureMode = CaptureMode.IMPORTED,
) -> MangoHudFpsSummary:
    rows = list(csv.DictReader(Path(path).open(newline="")))
    if not rows:
        raise ValueError(f"MangoHud summary CSV is empty: {path}")
    row = rows[0]
    return MangoHudFpsSummary(
        avg_fps=_float(row.get("Average FPS")),
        one_percent_low_fps=_float(row.get("1% Min FPS")),
        point_one_percent_low_fps=_float(row.get("0.1% Min FPS")),
        ninety_seven_percentile_fps=_float(row.get("97% Percentile FPS")),
        capture_mode=capture_mode,
    )


def parse_mangohud_fps_csv(
    path: str | Path,
    *,
    capture_mode: CaptureMode = CaptureMode.IMPORTED,
) -> MangoHudFpsSummary:
    fps_values: list[float] = []
    frametime_values: list[float] = []
    for row in csv.DictReader(Path(path).open(newline="")):
        fps = _float(row.get("fps"))
        frametime = _float(row.get("frametime"))
        if fps is not None:
            fps_values.append(fps)
        if frametime is not None:
            frametime_values.append(frametime)
    if not fps_values and not frametime_values:
        raise ValueError(f"MangoHud FPS CSV has no fps or frametime rows: {path}")
    return MangoHudFpsSummary(
        avg_fps=round(mean(fps_values), 3) if fps_values else None,
        one_percent_low_fps=_low_fps(fps_values, 0.01),
        point_one_percent_low_fps=_low_fps(fps_values, 0.001),
        avg_frametime_ms=round(mean(frametime_values), 3) if frametime_values else None,
        p95_frametime_ms=_high_percentile(frametime_values, 0.95),
        p99_frametime_ms=_high_percentile(frametime_values, 0.99),
        capture_mode=capture_mode,
    )


def _float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _low_fps(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    count = max(1, math.ceil(len(ordered) * fraction))
    return round(mean(ordered[:count]), 3)


def _high_percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)
```

- [ ] **Step 4: Run the focused parser tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_parse_mangohud_summary_csv_reads_low_percentile_metrics tests/test_game_power_profile.py::test_parse_mangohud_fps_csv_computes_average_and_frame_time_percentiles -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power_profile.py tests/test_game_power_profile.py
git commit -m "feat: parse game power fps profiles"
```

## Task 2: Summarize Game-Power JSONL And Compare Policies

**Files:**
- Modify: `src/steamos_intel_handheld/game_power_profile.py`
- Modify: `tests/test_game_power_profile.py`

- [ ] **Step 1: Write failing JSONL and comparison tests**

Append to `tests/test_game_power_profile.py`:

```python
from steamos_intel_handheld.game_power_profile import (
    GamePowerLogSummary,
    PolicyVerdict,
    RunSummary,
    compare_run_summaries,
    parse_game_power_jsonl,
)


def test_parse_game_power_jsonl_averages_power_and_counts_actions(tmp_path):
    path = tmp_path / "game-power.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "elapsed_s": 2.0,
                        "appid": "1091500",
                        "action": "gpu-priority-epp",
                        "package_w": 22.0,
                        "core_w": 7.0,
                        "uncore_w": 9.0,
                        "pl1_w": 22,
                        "render_busy": 0.8,
                    }
                ),
                json.dumps(
                    {
                        "elapsed_s": 4.0,
                        "appid": "1091500",
                        "action": "restore",
                        "package_w": 20.0,
                        "core_w": 6.0,
                        "uncore_w": 8.0,
                        "pl1_w": 22,
                        "render_busy": 0.7,
                    }
                ),
            ]
        )
        + "\n"
    )

    summary = parse_game_power_jsonl(path)

    assert isinstance(summary, GamePowerLogSummary)
    assert summary.samples == 2
    assert summary.avg_package_w == 21.0
    assert summary.avg_core_w == 6.5
    assert summary.avg_uncore_w == 8.5
    assert summary.avg_core_share == round(6.5 / 21.0, 3)
    assert summary.avg_uncore_share == round(8.5 / 21.0, 3)
    assert summary.actions == {"gpu-priority-epp": 1, "restore": 1}


def test_compare_run_summaries_accepts_better_one_percent_low_without_avg_regression():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        capture_mode=CaptureMode.CONTROLLED,
        avg_fps=40.0,
        one_percent_low_fps=30.0,
        p99_frametime_ms=36.0,
        restored=True,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        capture_mode=CaptureMode.CONTROLLED,
        avg_fps=39.8,
        one_percent_low_fps=32.0,
        p99_frametime_ms=35.0,
        restored=True,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.BETTER
    assert "1% low improved" in verdict.reason


def test_compare_run_summaries_rejects_imported_candidate_as_non_automated_ab():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        avg_fps=40.0,
        capture_mode=CaptureMode.CONTROLLED,
        restored=True,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        avg_fps=44.0,
        capture_mode=CaptureMode.IMPORTED,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.NEEDS_CONTROLLED_CAPTURE
    assert "imported" in verdict.reason


def test_compare_run_summaries_rejects_imported_baseline_as_non_automated_ab():
    baseline = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="off",
        avg_fps=40.0,
        capture_mode=CaptureMode.IMPORTED,
    )
    candidate = RunSummary(
        appid="1091500",
        tdp_w=22,
        policy="gpu-priority",
        avg_fps=44.0,
        capture_mode=CaptureMode.CONTROLLED,
    )

    verdict = compare_run_summaries(baseline, candidate)

    assert verdict.verdict == PolicyVerdict.NEEDS_CONTROLLED_CAPTURE
    assert "baseline" in verdict.reason
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_parse_game_power_jsonl_averages_power_and_counts_actions tests/test_game_power_profile.py::test_compare_run_summaries_accepts_better_one_percent_low_without_avg_regression tests/test_game_power_profile.py::test_compare_run_summaries_rejects_imported_candidate_as_non_automated_ab tests/test_game_power_profile.py::test_compare_run_summaries_rejects_imported_baseline_as_non_automated_ab -q
```

Expected: FAIL with missing `GamePowerLogSummary`, `RunSummary`, or `compare_run_summaries`.

- [ ] **Step 3: Implement JSONL summary and comparison logic**

Append to `src/steamos_intel_handheld/game_power_profile.py`:

```python
@dataclass(frozen=True)
class GamePowerLogSummary:
    samples: int
    avg_package_w: float | None = None
    avg_core_w: float | None = None
    avg_uncore_w: float | None = None
    avg_core_share: float | None = None
    avg_uncore_share: float | None = None
    avg_render_busy: float | None = None
    actions: dict[str, int] | None = None


@dataclass(frozen=True)
class RunSummary:
    appid: str
    tdp_w: int
    policy: str
    capture_mode: CaptureMode = CaptureMode.IMPORTED
    avg_fps: float | None = None
    one_percent_low_fps: float | None = None
    point_one_percent_low_fps: float | None = None
    avg_frametime_ms: float | None = None
    p95_frametime_ms: float | None = None
    p99_frametime_ms: float | None = None
    avg_package_w: float | None = None
    avg_core_w: float | None = None
    avg_uncore_w: float | None = None
    avg_core_share: float | None = None
    avg_uncore_share: float | None = None
    avg_render_busy: float | None = None
    actions: dict[str, int] | None = None
    restored: bool = False


class PolicyVerdict(str, Enum):
    BETTER = "better"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    NEEDS_CONTROLLED_CAPTURE = "needs-controlled-capture"


@dataclass(frozen=True)
class PolicyComparison:
    baseline_policy: str
    candidate_policy: str
    verdict: PolicyVerdict
    reason: str


def parse_game_power_jsonl(path: str | Path) -> GamePowerLogSummary:
    package_w: list[float] = []
    core_w: list[float] = []
    uncore_w: list[float] = []
    render_busy: list[float] = []
    actions: dict[str, int] = {}
    with Path(path).open() as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            _append_float(package_w, row.get("package_w"))
            _append_float(core_w, row.get("core_w"))
            _append_float(uncore_w, row.get("uncore_w"))
            _append_float(render_busy, row.get("render_busy"))
            action = str(row.get("action") or "")
            if action:
                actions[action] = actions.get(action, 0) + 1
    avg_package = _avg(package_w)
    avg_core = _avg(core_w)
    avg_uncore = _avg(uncore_w)
    return GamePowerLogSummary(
        samples=sum(actions.values()),
        avg_package_w=avg_package,
        avg_core_w=avg_core,
        avg_uncore_w=avg_uncore,
        avg_core_share=_ratio(avg_core, avg_package),
        avg_uncore_share=_ratio(avg_uncore, avg_package),
        avg_render_busy=_avg(render_busy),
        actions=actions,
    )


def merge_run_summary(
    *,
    appid: str,
    tdp_w: int,
    policy: str,
    fps: MangoHudFpsSummary,
    power: GamePowerLogSummary | None,
    restored: bool,
) -> RunSummary:
    return RunSummary(
        appid=appid,
        tdp_w=tdp_w,
        policy=policy,
        capture_mode=fps.capture_mode,
        avg_fps=fps.avg_fps,
        one_percent_low_fps=fps.one_percent_low_fps,
        point_one_percent_low_fps=fps.point_one_percent_low_fps,
        avg_frametime_ms=fps.avg_frametime_ms,
        p95_frametime_ms=fps.p95_frametime_ms,
        p99_frametime_ms=fps.p99_frametime_ms,
        avg_package_w=power.avg_package_w if power else None,
        avg_core_w=power.avg_core_w if power else None,
        avg_uncore_w=power.avg_uncore_w if power else None,
        avg_core_share=power.avg_core_share if power else None,
        avg_uncore_share=power.avg_uncore_share if power else None,
        avg_render_busy=power.avg_render_busy if power else None,
        actions=power.actions if power else None,
        restored=restored,
    )


def compare_run_summaries(baseline: RunSummary, candidate: RunSummary) -> PolicyComparison:
    if baseline.capture_mode != CaptureMode.CONTROLLED:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.NEEDS_CONTROLLED_CAPTURE,
            "baseline uses imported capture; controlled A/B capture is required",
        )
    if candidate.capture_mode != CaptureMode.CONTROLLED:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.NEEDS_CONTROLLED_CAPTURE,
            "candidate uses imported capture; controlled A/B capture is required",
        )
    if not baseline.restored or not candidate.restored:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            "restore verification did not pass for both runs",
        )
    low_gain = _percent_change(baseline.one_percent_low_fps, candidate.one_percent_low_fps)
    avg_gain = _percent_change(baseline.avg_fps, candidate.avg_fps)
    p99_gain = _percent_change(candidate.p99_frametime_ms, baseline.p99_frametime_ms)
    if low_gain is not None and low_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            f"1% low improved by {low_gain:.1f}% with average FPS change {avg_gain or 0:.1f}%",
        )
    if p99_gain is not None and p99_gain >= 5.0 and (avg_gain is None or avg_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            f"p99 frametime improved by {p99_gain:.1f}% with average FPS change {avg_gain or 0:.1f}%",
        )
    if avg_gain is not None and avg_gain >= 5.0 and (low_gain is None or low_gain >= -2.0):
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.BETTER,
            f"average FPS improved by {avg_gain:.1f}% without low-percentile regression",
        )
    if low_gain is not None and low_gain < -3.0:
        return PolicyComparison(
            baseline.policy,
            candidate.policy,
            PolicyVerdict.REJECTED,
            f"1% low worsened by {abs(low_gain):.1f}%",
        )
    return PolicyComparison(
        baseline.policy,
        candidate.policy,
        PolicyVerdict.INCONCLUSIVE,
        "candidate did not meet improvement or rejection thresholds",
    )


def _append_float(values: list[float], value: object) -> None:
    parsed = _float(value)
    if parsed is not None:
        values.append(parsed)


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def _ratio(part: float | None, total: float | None) -> float | None:
    if part is None or total is None or total <= 0:
        return None
    return round(part / total, 3)


def _percent_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / old * 100.0
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_parse_game_power_jsonl_averages_power_and_counts_actions tests/test_game_power_profile.py::test_compare_run_summaries_accepts_better_one_percent_low_without_avg_regression tests/test_game_power_profile.py::test_compare_run_summaries_rejects_imported_candidate_as_non_automated_ab tests/test_game_power_profile.py::test_compare_run_summaries_rejects_imported_baseline_as_non_automated_ab -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power_profile.py tests/test_game_power_profile.py
git commit -m "feat: compare game power profiles"
```

## Task 3: Add Offline Profile CLI

**Files:**
- Modify: `src/steamos_intel_handheld/game_power_profile.py`
- Modify: `tests/test_game_power_profile.py`
- Modify: `pyproject.toml`
- Modify: `scripts/install-on-device.sh`
- Modify: `tests/test_integration_assets.py`

- [ ] **Step 1: Write failing CLI and install tests**

Append to `tests/test_game_power_profile.py`:

```python
def test_profile_cli_summarize_writes_manifest_and_summary_json(tmp_path):
    mangohud = tmp_path / "mangohud.csv"
    game_power = tmp_path / "game-power.jsonl"
    output = tmp_path / "profile"
    write_csv(
        mangohud,
        ["fps", "frametime"],
        [{"fps": "40", "frametime": "25.0"}, {"fps": "44", "frametime": "22.7"}],
    )
    game_power.write_text(
        json.dumps(
            {
                "elapsed_s": 2.0,
                "appid": "1091500",
                "action": "gpu-priority-epp",
                "package_w": 22.0,
                "core_w": 7.0,
                "uncore_w": 9.0,
                "render_busy": 0.8,
            }
        )
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "summarize",
            "--appid",
            "1091500",
            "--tdp-w",
            "22",
            "--policy",
            "gpu-priority",
            "--capture-mode",
            "imported",
            "--mangohud-csv",
            str(mangohud),
            "--game-power-jsonl",
            str(game_power),
            "--output",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert "summary.json" in result.stdout
    assert manifest["appid"] == "1091500"
    assert manifest["policy"] == "gpu-priority"
    assert manifest["capture_mode"] == "imported"
    assert summary["avg_fps"] == 42.0
    assert summary["avg_uncore_w"] == 9.0
    assert summary["restored"] is True


def test_profile_cli_compare_reads_two_summary_files(tmp_path):
    baseline = tmp_path / "off-summary.json"
    candidate = tmp_path / "gpu-summary.json"
    baseline.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "tdp_w": 22,
                "policy": "off",
                "capture_mode": "controlled",
                "avg_fps": 40.0,
                "one_percent_low_fps": 30.0,
                "p99_frametime_ms": 36.0,
                "restored": True,
            }
        )
    )
    candidate.write_text(
        json.dumps(
            {
                "appid": "1091500",
                "tdp_w": 22,
                "policy": "gpu-priority",
                "capture_mode": "controlled",
                "avg_fps": 40.2,
                "one_percent_low_fps": 32.0,
                "p99_frametime_ms": 35.0,
                "restored": True,
            }
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.game_power_profile",
            "compare",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["verdict"] == "better"
    assert payload["candidate_policy"] == "gpu-priority"
```

Append to `tests/test_integration_assets.py`:

```python
def test_installer_installs_game_power_profile_cli_wrapper():
    script = (ROOT / "scripts/install-on-device.sh").read_text()

    assert "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile" in script
    assert r"python3 -m steamos_intel_handheld.game_power_profile \"\$@\"" in script
```

- [ ] **Step 2: Run the focused CLI tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_profile_cli_summarize_writes_manifest_and_summary_json tests/test_game_power_profile.py::test_profile_cli_compare_reads_two_summary_files tests/test_integration_assets.py::test_installer_installs_game_power_profile_cli_wrapper -q
```

Expected: FAIL because the CLI subcommands and installer wrapper are absent.

- [ ] **Step 3: Implement CLI functions**

Append to `src/steamos_intel_handheld/game_power_profile.py`:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    summarize = subcommands.add_parser("summarize")
    summarize.add_argument("--appid", required=True)
    summarize.add_argument("--tdp-w", required=True, type=int)
    summarize.add_argument("--policy", required=True)
    summarize.add_argument(
        "--capture-mode",
        choices=[mode.value for mode in CaptureMode],
        default=CaptureMode.IMPORTED.value,
    )
    summarize.add_argument("--mangohud-csv")
    summarize.add_argument("--mangohud-summary-csv")
    summarize.add_argument("--game-power-jsonl")
    summarize.add_argument("--output", required=True)
    summarize.add_argument("--restored", choices=["true", "false"], default="true")

    compare = subcommands.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    return parser


def run_summarize(args: argparse.Namespace) -> Path:
    capture_mode = CaptureMode(args.capture_mode)
    if args.mangohud_summary_csv:
        fps = parse_mangohud_summary_csv(args.mangohud_summary_csv, capture_mode=capture_mode)
    elif args.mangohud_csv:
        fps = parse_mangohud_fps_csv(args.mangohud_csv, capture_mode=capture_mode)
    else:
        raise SystemExit("summarize requires --mangohud-csv or --mangohud-summary-csv")
    power = parse_game_power_jsonl(args.game_power_jsonl) if args.game_power_jsonl else None
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "appid": args.appid,
        "tdp_w": args.tdp_w,
        "policy": args.policy,
        "capture_mode": capture_mode.value,
    }
    summary = merge_run_summary(
        appid=args.appid,
        tdp_w=args.tdp_w,
        policy=args.policy,
        fps=fps,
        power=power,
        restored=args.restored == "true",
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / "summary.json").write_text(
        json.dumps(_json_ready(asdict(summary)), indent=2, sort_keys=True) + "\n"
    )
    return output / "summary.json"


def run_compare(args: argparse.Namespace) -> PolicyComparison:
    baseline = _load_run_summary(args.baseline)
    candidate = _load_run_summary(args.candidate)
    return compare_run_summaries(baseline, candidate)


def _load_run_summary(path: str | Path) -> RunSummary:
    payload = json.loads(Path(path).read_text())
    if "capture_mode" in payload:
        payload["capture_mode"] = CaptureMode(payload["capture_mode"])
    return RunSummary(**payload)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "summarize":
        path = run_summarize(args)
        print(path)
        return
    if args.command == "compare":
        comparison = run_compare(args)
        print(json.dumps(_json_ready(asdict(comparison)), sort_keys=True))
        return
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add console script and device installer wrapper**

Add to `pyproject.toml` under `[project.scripts]`:

```toml
steamos-intel-handheld-game-power-profile = "steamos_intel_handheld.game_power_profile:main"
```

Add to `scripts/install-on-device.sh` after the `steamos-intel-handheld-game-power` wrapper:

```bash
  cat >/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/opt/steamos-intel-handheld/src
exec /usr/bin/python3 -m steamos_intel_handheld.game_power_profile \"\$@\"
WRAPPER
  chmod 0755 /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile
```

- [ ] **Step 5: Run the focused CLI tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_profile_cli_summarize_writes_manifest_and_summary_json tests/test_game_power_profile.py::test_profile_cli_compare_reads_two_summary_files tests/test_integration_assets.py::test_installer_installs_game_power_profile_cli_wrapper -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power_profile.py tests/test_game_power_profile.py pyproject.toml scripts/install-on-device.sh tests/test_integration_assets.py
git commit -m "feat: add game power profile cli"
```

## Task 4: Emit Machine-Readable Game-Power Samples

**Files:**
- Modify: `src/steamos_intel_handheld/game_power.py`
- Modify: `tests/test_game_power.py`

- [ ] **Step 1: Write failing JSONL output tests**

Append to `tests/test_game_power.py`:

```python
def test_format_decision_jsonl_contains_policy_sample_fields():
    decision = GamePowerAction.GPU_PRIORITY_EPP
    sample = make_sample(render_busy=0.75)

    payload = game_power.format_decision_jsonl(
        sample,
        game_power.GamePowerDecision(decision, "package limited with GPU activity"),
        elapsed_s=2.0,
    )

    row = json.loads(payload)
    assert row["elapsed_s"] == 2.0
    assert row["appid"] == "1091500"
    assert row["action"] == "gpu-priority-epp"
    assert row["reason"] == "package limited with GPU activity"
    assert row["package_w"] == 22.0
    assert row["core_w"] == 8.8
    assert row["uncore_w"] == 7.4
    assert row["pl1_w"] == 22
    assert row["render_busy"] == 0.75


def test_build_parser_accepts_jsonl_output_format():
    args = game_power.build_parser().parse_args(["--output-format", "jsonl"])

    assert args.output_format == "jsonl"
```

Also add `import json` near the top of `tests/test_game_power.py`.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_format_decision_jsonl_contains_policy_sample_fields tests/test_game_power.py::test_build_parser_accepts_jsonl_output_format -q
```

Expected: FAIL because `format_decision_jsonl` and `--output-format` are absent.

- [ ] **Step 3: Implement JSONL formatting**

Modify imports in `src/steamos_intel_handheld/game_power.py`:

```python
import json
```

Add after `_format_decision()`:

```python
def format_decision_jsonl(
    sample: GamePowerSample,
    decision: GamePowerDecision,
    *,
    elapsed_s: float,
) -> str:
    rapl = sample.rapl
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
    }
    return json.dumps(payload, sort_keys=True)


def _round_or_none(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None
```

- [ ] **Step 4: Wire JSONL into the standalone CLI**

Add `output_format` to `GamePowerGovernor.__init__` with default `"text"`, store `self.output_format`, and change `run_once()` printing to:

```python
        elapsed_s = time.monotonic() - self._started_s
        if self.output_format == "jsonl":
            print(format_decision_jsonl(sample, decision, elapsed_s=elapsed_s), flush=True)
        else:
            print(_format_decision(sample, decision), flush=True)
```

Set `self._started_s = time.monotonic()` in `GamePowerGovernor.__init__`.

Add to `build_parser()`:

```python
    parser.add_argument("--output-format", choices=["text", "jsonl"], default="text")
```

Pass the value in `run_cli()`:

```python
    governor = GamePowerGovernor(
        config=config,
        observer=observer,
        actuator=actuator,
        output_format=args.output_format,
    )
```

- [ ] **Step 5: Run the focused JSONL tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power.py::test_format_decision_jsonl_contains_policy_sample_fields tests/test_game_power.py::test_build_parser_accepts_jsonl_output_format -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power.py tests/test_game_power.py
git commit -m "feat: emit game power jsonl samples"
```

## Task 5: Add Guarded Device Profile Wrapper

**Files:**
- Create: `scripts/profile-game-power-on-device.sh`
- Modify: `harness.toml`
- Modify: `tests/test_integration_assets.py`

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_integration_assets.py`:

```python
def test_game_power_profile_device_check_is_guarded():
    payload = tomllib.loads((ROOT / "harness.toml").read_text())
    checks = {check["id"]: check for check in payload["checks"]}

    check = checks["game-power-profile-device"]
    assert check["command"] == "scripts/profile-game-power-on-device.sh root@10.100.0.19"
    assert check["tier"] == "guarded"
    assert check["safe_for_agents"] is False
    assert check["expectation"] == "blocked"
    assert check["requires"] == ["root-ssh", "handheld", "foreground-game"]


def test_game_power_profile_wrapper_restores_tdp_and_cpu_policy():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "snapshot_cpu_policy()" in script
    assert "restore_cpu_policy()" in script
    assert "set_service_game_power_mode()" in script
    assert "restore_service_game_power_mode()" in script
    assert "provider_tdp()" in script
    assert "set_provider_tdp()" in script
    assert 'trap restore_state EXIT' in script
    assert "--game-power-mode off" in script
    assert "--output-format jsonl" in script
    assert "steamos-intel-handheld-game-power-profile summarize" in script
    assert "capture_mode" in script
    assert ".cache/game-power/profiles" in script
    assert '"$target:$remote_root/."' in script
```

- [ ] **Step 2: Run the focused integration tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_game_power_profile_device_check_is_guarded tests/test_integration_assets.py::test_game_power_profile_wrapper_restores_tdp_and_cpu_policy -q
```

Expected: FAIL because the harness check and script are absent.

- [ ] **Step 3: Add guarded harness check**

Append this check to `harness.toml` before `release-artifact`:

```toml
[[checks]]
id = "game-power-profile-device"
description = "Real foreground-game A/B profiler for game-power policy and MangoHud FPS artifacts."
command = "scripts/profile-game-power-on-device.sh root@10.100.0.19"
requires = ["root-ssh", "handheld", "foreground-game"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "several minutes depending on PROFILE_GAME_POWER_MATRIX"
evidence = "Profile directories with manifest.json, summary.json, MangoHud CSV, game-power JSONL, and restore snapshots."
```

- [ ] **Step 4: Create device wrapper with imported capture support**

Create `scripts/profile-game-power-on-device.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 root@steamdeck-host" >&2
  exit 2
fi

target="$1"
appid="${PROFILE_GAME_POWER_APPID:-1091500}"
tdp_levels="${PROFILE_GAME_POWER_TDPS:-22}"
policies="${PROFILE_GAME_POWER_POLICIES:-off gpu-priority}"
duration_s="${PROFILE_GAME_POWER_DURATION_S:-60}"
warmup_s="${PROFILE_GAME_POWER_WARMUP_S:-10}"
poll_s="${PROFILE_GAME_POWER_POLL_S:-2}"
capture_mode="${PROFILE_GAME_POWER_CAPTURE_MODE:-imported}"
local_root="${PROFILE_GAME_POWER_OUTPUT_ROOT:-.cache/game-power/profiles}"
mkdir -p "$local_root"

remote_root="$(ssh "$target" "mktemp -d /tmp/game-power-profile.XXXXXX")"

ssh "$target" \
  "APPID='$appid' TDP_LEVELS='$tdp_levels' POLICIES='$policies' DURATION_S='$duration_s' WARMUP_S='$warmup_s' POLL_S='$poll_s' CAPTURE_MODE='$capture_mode' REMOTE_ROOT='$remote_root' bash -s" <<'REMOTE'
set -euo pipefail

snapshot_cpu_policy() {
  for policy in /sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$policy" ] || continue
    name="${policy##*/}"
    epp_value="$(cat "$policy/energy_performance_preference" 2>/dev/null || true)"
    max_freq="$(cat "$policy/scaling_max_freq" 2>/dev/null || true)"
    printf '%s\t%s\t%s\n' "$name" "$epp_value" "$max_freq"
  done | sort
}

restore_cpu_policy() {
  [ -f "$REMOTE_ROOT/cpu-policy.initial" ] || return 0
  while IFS=$'\t' read -r name epp_value max_freq; do
    policy="/sys/devices/system/cpu/cpufreq/$name"
    [ -d "$policy" ] || continue
    [ -n "$epp_value" ] && [ -w "$policy/energy_performance_preference" ] && printf '%s\n' "$epp_value" >"$policy/energy_performance_preference"
    [ -n "$max_freq" ] && [ -w "$policy/scaling_max_freq" ] && printf '%s\n' "$max_freq" >"$policy/scaling_max_freq"
  done <"$REMOTE_ROOT/cpu-policy.initial"
}

provider_tdp() {
  busctl --system get-property \
    org.rivoreo.SteamOSManager.PowerControl \
    /org/rivoreo/SteamOSManager/PowerControl \
    com.steampowered.SteamOSManager1.TdpLimit1 \
    TdpLimit | awk '{print $2}'
}

set_provider_tdp() {
  busctl --system set-property \
    org.rivoreo.SteamOSManager.PowerControl \
    /org/rivoreo/SteamOSManager/PowerControl \
    com.steampowered.SteamOSManager1.TdpLimit1 \
    TdpLimit u "$1"
}

set_service_game_power_mode() {
  local mode="$1"
  install -d -m 0755 /run/systemd/system/steamos-intel-handheld-power-control.service.d
  cat >/run/systemd/system/steamos-intel-handheld-power-control.service.d/50-game-power-profile.conf <<EOF
[Service]
ExecStart=
ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control wait-and-serve --user deck --bus system --apply-rapl --apply-msi-claw-ec --ec-write-debounce-ms 750 --tdp-policy auto --msi-claw-ec-shift-policy tdp-threshold --prepare-mangohud-sensors --game-power-mode $mode --min-w 8 --max-w 30 --short-limit-max-w 37 --state-file /var/lib/steamos-intel-handheld/tdp_w
EOF
  systemctl daemon-reload
  systemctl restart steamos-intel-handheld-power-control.service
}

restore_service_game_power_mode() {
  rm -f /run/systemd/system/steamos-intel-handheld-power-control.service.d/50-game-power-profile.conf
  rmdir /run/systemd/system/steamos-intel-handheld-power-control.service.d 2>/dev/null || true
  systemctl daemon-reload
  systemctl restart steamos-intel-handheld-power-control.service
}

restore_state() {
  restore_cpu_policy || true
  if [ -f "$REMOTE_ROOT/tdp.initial" ]; then
    set_provider_tdp "$(cat "$REMOTE_ROOT/tdp.initial")" || true
  fi
  restore_service_game_power_mode || true
}

latest_mangohud_csv() {
  find /home/deck -maxdepth 1 -name 'mangoapp_*.csv' -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-
}

if [ "$CAPTURE_MODE" != "imported" ]; then
  echo "controlled capture mode is not enabled by this wrapper until the trigger path is validated" >&2
  exit 2
fi

snapshot_cpu_policy >"$REMOTE_ROOT/cpu-policy.initial"
provider_tdp >"$REMOTE_ROOT/tdp.initial"
trap restore_state EXIT
set_service_game_power_mode off

for tdp in $TDP_LEVELS; do
  set_provider_tdp "$tdp"
  sleep "$WARMUP_S"
  for policy in $POLICIES; do
    run_dir="$REMOTE_ROOT/$(date +%Y%m%dT%H%M%S)-app${APPID}-${tdp}w-${policy}"
    mkdir -p "$run_dir"
    snapshot_cpu_policy >"$run_dir/cpu-policy.before"
    provider_tdp >"$run_dir/tdp.before"
    mode="observe"
    if [ "$policy" = "gpu-priority" ]; then
      mode="gpu-priority"
    fi
    /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power \
      --mode "$mode" \
      --duration-s "$DURATION_S" \
      --poll-s "$POLL_S" \
      --target-appid "$APPID" \
      --output-format jsonl >"$run_dir/game-power.jsonl"
    csv="$(latest_mangohud_csv)"
    if [ -n "$csv" ]; then
      cp "$csv" "$run_dir/mangohud.csv"
    fi
    snapshot_cpu_policy >"$run_dir/cpu-policy.after"
    provider_tdp >"$run_dir/tdp.after"
    restored=true
    diff -u "$run_dir/cpu-policy.before" "$run_dir/cpu-policy.after" >"$run_dir/cpu-policy.diff" || restored=false
    /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile summarize \
      --appid "$APPID" \
      --tdp-w "$tdp" \
      --policy "$policy" \
      --capture-mode "$CAPTURE_MODE" \
      --mangohud-csv "$run_dir/mangohud.csv" \
      --game-power-jsonl "$run_dir/game-power.jsonl" \
      --restored "$restored" \
      --output "$run_dir"
  done
done

restore_state
trap - EXIT
REMOTE

scp -r "$target:$remote_root/." "$local_root/"
ssh "$target" "rm -rf '$remote_root'"
echo "profiles copied to $local_root"
```

Make the script executable:

```bash
chmod 0755 scripts/profile-game-power-on-device.sh
```

- [ ] **Step 5: Run the focused integration tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration_assets.py::test_game_power_profile_device_check_is_guarded tests/test_integration_assets.py::test_game_power_profile_wrapper_restores_tdp_and_cpu_policy -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add scripts/profile-game-power-on-device.sh harness.toml tests/test_integration_assets.py
git commit -m "feat: add guarded game power profiler"
```

## Task 6: Add cgroup/PSI Observation Without Runtime Writes

**Files:**
- Modify: `src/steamos_intel_handheld/game_power_profile.py`
- Modify: `tests/test_game_power_profile.py`
- Modify: `scripts/profile-game-power-on-device.sh`

- [ ] **Step 1: Write failing cgroup pressure tests**

Append to `tests/test_game_power_profile.py`:

```python
from steamos_intel_handheld.game_power_profile import parse_pressure_file, summarize_pressure_jsonl


def test_parse_pressure_file_reads_some_and_full_avg10():
    text = "some avg10=2.10 avg60=1.00 avg300=0.20 total=12345\nfull avg10=0.30 avg60=0.10 avg300=0.00 total=456\n"

    pressure = parse_pressure_file(text)

    assert pressure["some"]["avg10"] == 2.10
    assert pressure["full"]["avg10"] == 0.30


def test_summarize_pressure_jsonl_reports_peak_cpu_pressure(tmp_path):
    path = tmp_path / "cgroup-pressure.jsonl"
    path.write_text(
        json.dumps({"elapsed_s": 1.0, "cpu": {"some": {"avg10": 1.2}, "full": {"avg10": 0.0}}})
        + "\n"
        + json.dumps({"elapsed_s": 2.0, "cpu": {"some": {"avg10": 3.4}, "full": {"avg10": 0.2}}})
        + "\n"
    )

    summary = summarize_pressure_jsonl(path)

    assert summary == {"cpu_pressure_some_avg10_peak": 3.4, "cpu_pressure_full_avg10_peak": 0.2}
```

- [ ] **Step 2: Run the focused pressure tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_parse_pressure_file_reads_some_and_full_avg10 tests/test_game_power_profile.py::test_summarize_pressure_jsonl_reports_peak_cpu_pressure -q
```

Expected: FAIL because the pressure helpers are absent.

- [ ] **Step 3: Implement PSI parsers**

Append to `src/steamos_intel_handheld/game_power_profile.py`:

```python
def parse_pressure_file(text: str) -> dict[str, dict[str, float]]:
    parsed: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        category = parts[0]
        parsed[category] = {}
        for item in parts[1:]:
            key, value = item.split("=", 1)
            parsed[category][key] = float(value)
    return parsed


def summarize_pressure_jsonl(path: str | Path) -> dict[str, float]:
    some_peak = 0.0
    full_peak = 0.0
    with Path(path).open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            cpu = row.get("cpu") or {}
            some_peak = max(some_peak, float(((cpu.get("some") or {}).get("avg10") or 0.0)))
            full_peak = max(full_peak, float(((cpu.get("full") or {}).get("avg10") or 0.0)))
    return {
        "cpu_pressure_some_avg10_peak": round(some_peak, 3),
        "cpu_pressure_full_avg10_peak": round(full_peak, 3),
    }
```

- [ ] **Step 4: Add device pressure sampling to the wrapper**

Add this remote function to `scripts/profile-game-power-on-device.sh`:

```bash
sample_cgroup_pressure() {
  local output="$1"
  local seconds="$2"
  local start elapsed
  start="$(date +%s)"
  : >"$output"
  while true; do
    elapsed=$(( $(date +%s) - start ))
    [ "$elapsed" -gt "$seconds" ] && break
    if [ -r /sys/fs/cgroup/cpu.pressure ]; then
      python3 - "$elapsed" /sys/fs/cgroup/cpu.pressure >>"$output" <<'PY'
import json
import pathlib
import sys

elapsed = float(sys.argv[1])
text = pathlib.Path(sys.argv[2]).read_text()
payload = {"elapsed_s": elapsed, "cpu": {}}
for line in text.splitlines():
    parts = line.split()
    if not parts:
        continue
    payload["cpu"][parts[0]] = {}
    for item in parts[1:]:
        key, value = item.split("=", 1)
        payload["cpu"][parts[0]][key] = float(value)
print(json.dumps(payload, sort_keys=True))
PY
    fi
    sleep 1
  done
}
```

Start it before each `steamos-intel-handheld-game-power` run:

```bash
    sample_cgroup_pressure "$run_dir/cgroup-pressure.jsonl" "$DURATION_S" &
    pressure_pid="$!"
```

Wait after the run:

```bash
    wait "$pressure_pid" || true
```

- [ ] **Step 5: Run pressure tests and script syntax check**

Run:

```bash
.venv/bin/python -m pytest tests/test_game_power_profile.py::test_parse_pressure_file_reads_some_and_full_avg10 tests/test_game_power_profile.py::test_summarize_pressure_jsonl_reports_peak_cpu_pressure -q
bash -n scripts/profile-game-power-on-device.sh
```

Expected: both commands pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/steamos_intel_handheld/game_power_profile.py tests/test_game_power_profile.py scripts/profile-game-power-on-device.sh
git commit -m "feat: record game power pressure samples"
```

## Task 7: Documentation, Required Sweep, And First Device Run

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `.cache/harness/required.json` after running the required sweep

- [ ] **Step 1: Document profiler usage**

Add to `README.md` under the game-power governor section:

~~~markdown
### Game-power profiling

The game-power profiler compares policy runs using MangoHud FPS data and
machine-readable game-power samples:

```bash
scripts/profile-game-power-on-device.sh root@10.100.0.19
```

By default the guarded wrapper runs an imported-log capture at 22W for:

- `off`
- `gpu-priority`

Results are copied into `.cache/game-power/profiles/`. Each run directory
contains `manifest.json`, `summary.json`, `game-power.jsonl`, CPU policy
snapshots, TDP snapshots, and the MangoHud CSV used for FPS analysis.

Imported captures are useful for parser and comparison development, but they do
not prove an automated A/B result. A policy recommendation requires controlled
capture, exact restore, and repeated runs that meet the comparison thresholds.
~~~

Add to `docs/design.md`:

```markdown
## Game-power profiler

The profiler is the measurement layer for game-aware CPU/iGPU shared-power
policy. It keeps the installed default at the validated EPP-only
`gpu-priority` mode, then compares candidate policies through saved run
artifacts instead of live overlay observation.

The first supported metrics are average FPS, 1% low FPS, 0.1% low FPS, p95/p99
frame time, package/core/uncore power, render busy, CPU policy restore status,
and cgroup CPU pressure. New runtime controls such as CPU caps or cgroup
uClamp background limits remain experiment-only until controlled captures show
better low-percentile frame pacing without average-FPS regression.
```

- [ ] **Step 2: Run the full local required sweep**

Run:

```bash
scripts/harness.py sweep required --report .cache/harness/required.json
```

Expected: PASS for the `local` check.

- [ ] **Step 3: Commit docs and harness report**

Run:

```bash
git add README.md docs/design.md .cache/harness/required.json
git commit -m "docs: document game power profiler"
```

- [ ] **Step 4: Deploy to the device when the user wants live profiling evidence**

Run:

```bash
scripts/install-on-device.sh root@10.100.0.19
```

Expected: wrappers are installed under `/opt/steamos-intel-handheld/bin/` and `steamos-intel-handheld-power-control.service` remains active.

- [ ] **Step 5: Run the guarded imported-log profiler**

Run:

```bash
PROFILE_GAME_POWER_TDPS=22 PROFILE_GAME_POWER_POLICIES="off gpu-priority" PROFILE_GAME_POWER_DURATION_S=60 scripts/profile-game-power-on-device.sh root@10.100.0.19
```

Expected: new run directories appear under `.cache/game-power/profiles/`, each containing `manifest.json`, `summary.json`, `game-power.jsonl`, and restore snapshots. `summary.json` uses `"capture_mode": "imported"` unless controlled capture support has been explicitly enabled.

- [ ] **Step 6: Compare the first two summaries**

Run:

```bash
.venv/bin/python -m steamos_intel_handheld.game_power_profile compare --baseline .cache/game-power/profiles/<off-run>/summary.json --candidate .cache/game-power/profiles/<gpu-run>/summary.json
```

Expected for imported capture: JSON verdict is `needs-controlled-capture`. If controlled capture is enabled and both runs restore exactly, the verdict is `better`, `rejected`, or `inconclusive` according to the documented thresholds.

## Plan Self-Review

- Spec coverage: Tasks 1-3 implement the local parser, run summary, and comparison model. Task 4 makes the existing governor produce JSONL control-loop samples. Task 5 adds guarded real-device profile capture with TDP and CPU-policy restore. Task 6 records cgroup/PSI evidence for 1% low attribution without enabling new runtime controls. Task 7 documents the profiler and runs the required harness.
- Scope boundary: This plan does not enable CPU caps, cgroup uClamp writes, per-game persistent profiles, or sched_ext. Those controls require profiler evidence from controlled captures before they become runtime policies.
- First A/B target: Cyberpunk 2077 AppID `1091500`, 22W, `off` versus `gpu-priority`, static in-game scene, 60 seconds per policy.
