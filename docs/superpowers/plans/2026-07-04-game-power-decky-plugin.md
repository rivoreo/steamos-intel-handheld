# Game Power Decky Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separate Decky Loader plugin that exposes safe game-power visibility and mode controls without exposing measured CPU/GPU policy internals.

**Architecture:** Add a new `decky/steamos-intel-handheld-game-power` plugin beside the existing charge-limit plugin. The Decky backend owns only status, short sampling, allowlisted runtime mode override, and restore-defaults calls; the measured CPU cap constants remain hidden in the system service command line.

**Tech Stack:** Decky Loader, React, `@decky/ui`, `@decky/api`, `react-icons`, Python async backend, systemd runtime drop-in, existing `steamos-intel-handheld-game-power` CLI.

---

## Files

- Create: `decky/steamos-intel-handheld-game-power/plugin.json`
- Create: `decky/steamos-intel-handheld-game-power/package.json`
- Create: `decky/steamos-intel-handheld-game-power/rollup.config.js`
- Create: `decky/steamos-intel-handheld-game-power/tsconfig.json`
- Create: `decky/steamos-intel-handheld-game-power/README.md`
- Create: `decky/steamos-intel-handheld-game-power/main.py`
- Create: `decky/steamos-intel-handheld-game-power/src/decky-ui.d.ts`
- Create: `decky/steamos-intel-handheld-game-power/src/index.tsx`
- Create: `decky/steamos-intel-handheld-game-power/dist/index.js`
- Modify: `scripts/install-on-device.sh`
- Modify: `packaging/arch/PKGBUILD`
- Modify: `scripts/verify-gitlab-pacman-artifact.sh`
- Modify: `tests/test_decky_plugin_assets.py`
- Modify: `tests/test_decky_plugin_backend.py`
- Modify: `tests/test_integration_assets.py`
- Modify: `tests/test_gitlab_ci_packaging.py`
- Modify: `tests/test_arch_release_workflow.py`
- Modify: `README.md`

## Task 1: Failing Tests

- [ ] Add tests that require the new Decky plugin package files, backend callables, packaging entries, and installer entries.
- [ ] Add tests that fail if unsafe raw tuning labels are present in the frontend.
- [ ] Run the focused tests and confirm they fail because the new plugin is missing.

## Task 2: Backend

- [ ] Implement `main.py` with:
  - `get_status()`,
  - `sample_once()`,
  - `set_mode(mode)`,
  - `restore_defaults()`.
- [ ] Allow only `off`, `observe`, and `automatic` modes.
- [ ] Generate only one plugin-owned runtime drop-in path:
  `/run/systemd/system/steamos-intel-handheld-power-control.service.d/70-game-power-decky.conf`.
- [ ] Generate the drop-in as:

```ini
[Service]
ExecStart=
ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control wait-and-serve --user deck --bus system --apply-rapl --apply-msi-claw-ec --ec-write-debounce-ms 750 --tdp-policy auto --msi-claw-ec-shift-policy tdp-threshold --prepare-mangohud-sensors --game-power-mode <mode> --game-power-cpu-cap on --game-power-pcore-max-mhz 3000 --game-power-ecore-max-mhz 2400 --game-power-cpu-cap-core-share-threshold 0.30 --min-w 8 --max-w 30 --short-limit-max-w 37 --state-file /var/lib/steamos-intel-handheld/tdp_w
```

- [ ] Keep balanced automatic CPU cap constants internal to the backend command.
- [ ] `get_status()` parses `systemctl show ... -p ActiveState -p SubState -p ExecStart --no-pager`.
- [ ] `sample_once()` runs `/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power --mode observe --duration-s 2 --poll-s 1 --output-format jsonl` and returns the last JSON line.
- [ ] `set_mode(mode)` writes only the plugin-owned `/run/systemd` drop-in through `tee`, then runs `systemctl daemon-reload` and `systemctl restart steamos-intel-handheld-power-control.service`.
- [ ] `restore_defaults()` runs only `rm -f <plugin drop-in>`, `systemctl daemon-reload`, and `systemctl restart steamos-intel-handheld-power-control.service`.
- [ ] Run backend tests and confirm they pass.

## Task 3: Frontend

- [ ] Implement a compact Decky panel using standard `@decky/ui` components.
- [ ] Add English and Traditional Chinese copy.
- [ ] Show status, latest sample, mode buttons, restore defaults, and refresh.
- [ ] Cover loading, ready, empty sample, applying, restore, and error states.
- [ ] Do not show P-core/E-core frequency, threshold, uclamp, CPUWeight, PL2/Tau, or affinity controls.
- [ ] Build or provide `dist/index.js`.

## Task 4: Install And Package

- [ ] Extend `scripts/install-on-device.sh` to copy the new plugin.
- [ ] Extend `packaging/arch/PKGBUILD` to include the new plugin.
- [ ] Extend artifact verifier and packaging tests for the new plugin.
- [ ] Update README repository layout and game-power section.

## Task 5: Verification

- [ ] Run focused Python tests for Decky assets/backend and integration packaging.
- [ ] Run Decky frontend build or equivalent bundle verification.
- [ ] Run `scripts/harness.py sweep required --report .cache/harness/required.json`.
- [ ] Review git diff for unsafe controls and unintended files.
