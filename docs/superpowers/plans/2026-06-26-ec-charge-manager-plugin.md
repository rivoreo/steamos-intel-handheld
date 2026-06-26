# EC Charge Manager Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe first framework for a SteamOS/Decky EC charge-limit manager.

**Architecture:** Add a pure Python EC charge-limit module for decoding and previewing MSI threshold presets, expose it through CLI JSON commands, and scaffold a Decky Loader plugin that calls those commands. Actual EC charge-limit writes remain disabled until Claw-specific paired EC dumps validate the mapping.

**Tech Stack:** Python 3.10, pytest, Decky Loader plugin skeleton, React/TypeScript frontend files, Python Decky backend file.

---

### Task 1: EC Charge-Limit Core

**Files:**
- Create: `src/steamos_intel_handheld/ec_charge_control.py`
- Test: `tests/test_ec_charge_control.py`

- [ ] Write failing tests for decoding `0xd0` as start `70` and end `80`, preset encoding for `60/80/100`, unsupported preset rejection, and disabled writes raising a safety error.
- [ ] Run `pytest tests/test_ec_charge_control.py -q` and verify the tests fail because the module is missing.
- [ ] Implement the minimal module with `MsiEcChargeController`, `ChargeLimitStatus`, and preset helpers.
- [ ] Run `pytest tests/test_ec_charge_control.py -q` and verify it passes.

### Task 2: CLI JSON Adapter

**Files:**
- Modify: `src/steamos_intel_handheld/ec_charge_control.py`
- Modify: `pyproject.toml`
- Test: `tests/test_ec_charge_control_cli.py`

- [ ] Write failing CLI tests for `status --json` and `preview --json 80` using temporary EC debugfs fixtures.
- [ ] Run `pytest tests/test_ec_charge_control_cli.py -q` and verify the tests fail because the CLI entry point is missing.
- [ ] Add `steamos-intel-handheld-ec-control = "steamos_intel_handheld.ec_charge_control:main"` to `pyproject.toml`.
- [ ] Implement argparse commands `status` and `preview`; keep `apply` unavailable in this framework pass.
- [ ] Run `pytest tests/test_ec_charge_control_cli.py -q` and verify it passes.

### Task 3: Decky Plugin Skeleton

**Files:**
- Create: `decky/steamos-intel-handheld-ec/plugin.json`
- Create: `decky/steamos-intel-handheld-ec/package.json`
- Create: `decky/steamos-intel-handheld-ec/tsconfig.json`
- Create: `decky/steamos-intel-handheld-ec/main.py`
- Create: `decky/steamos-intel-handheld-ec/src/index.tsx`
- Create: `decky/steamos-intel-handheld-ec/README.md`
- Test: `tests/test_decky_plugin_assets.py`

- [ ] Write failing asset tests that assert required Decky files exist, metadata names this plugin, backend exposes `get_status` and `preview_limit`, and frontend contains the three preset labels.
- [ ] Run `pytest tests/test_decky_plugin_assets.py -q` and verify it fails because the skeleton is missing.
- [ ] Create the Decky plugin skeleton with a compact status panel and disabled apply path.
- [ ] Run `pytest tests/test_decky_plugin_assets.py -q` and verify it passes.

### Task 4: Full Verification

**Files:**
- Existing tests and docs only.

- [ ] Run `pytest -q`.
- [ ] Run `scripts/check-local.sh`.
- [ ] Review `git diff` for accidental EC write enablement; confirm charge-limit write path remains disabled.
