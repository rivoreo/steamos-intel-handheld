# SteamOS Intel Handheld Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a formal `rivoreo/steamos-intel-handheld` project with a productionized SteamOS Manager TDP remote, tests, install harness, verification harness, docs, references, and packaging draft.

**Architecture:** A Python console service exports SteamOS Manager remote TDP interfaces and writes Intel RAPL through an isolated backend. Device harness scripts install the service over root SSH and verify the full SteamOS Manager to RAPL path. Packaging and docs stay in-tree so future package work and upstreaming have a stable base.

**Tech Stack:** Python 3.10+, dbus-next, pytest, systemd, D-Bus policy, SteamOS Manager remotes.d, GitHub Actions.

---

### Task 1: Backend and Tests

**Files:**
- Create: `src/steamos_intel_handheld/power_control.py`
- Test: `tests/test_power_control_backend.py`

- [x] **Step 1: Write tests for PL1/PL2 calculation, state reads, RAPL writes, range rejection, and state restore.**
- [x] **Step 2: Run tests and verify they fail before implementation.**
- [x] **Step 3: Implement `TdpBackend`, `compute_tdp_limits`, and range errors.**
- [x] **Step 4: Run `python3 -m pytest tests/test_power_control_backend.py`.**

### Task 2: SteamOS Manager Integration Assets

**Files:**
- Create: `data/systemd/steamos-intel-handheld-power-control.service`
- Create: `data/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf`
- Create: `data/steamos-manager/remotes.d/99-rivoreo-power-control.toml`
- Test: `tests/test_integration_assets.py`

- [x] **Step 1: Write tests proving the unit waits with `wait-and-serve` and the remote config uses the stable Rivoreo bus/object names.**
- [x] **Step 2: Add integration assets.**
- [x] **Step 3: Run `python3 -m pytest tests/test_integration_assets.py`.**

### Task 3: Device Harness

**Files:**
- Create: `scripts/install-on-device.sh`
- Create: `scripts/verify-on-device.sh`
- Create: `scripts/collect-device-info.sh`

- [x] **Step 1: Add an installer that copies the source tree to `/etc/rivoreo`, installs D-Bus and SteamOS Manager configs, restarts SteamOS Manager, and enables the systemd unit.**
- [x] **Step 2: Add a verifier that checks SteamOS Manager, the remote provider, RAPL PL1, restore wattage, and failed units.**
- [x] **Step 3: Add device inventory collection for future hardware profiles.**

### Task 4: Docs and Packaging Path

**Files:**
- Create: `README.md`
- Create: `docs/design.md`
- Create: `docs/ai-development-harness.md`
- Create: `docs/upstreaming.md`
- Create: `docs/hardware/msi-claw-8-ai-plus.md`
- Create: `packaging/arch/PKGBUILD`
- Create: `.github/workflows/ci.yml`

- [x] **Step 1: Document project scope, quickstart, architecture, harness, hardware notes, and upstreaming path.**
- [x] **Step 2: Add CI for pytest and compile checks.**
- [x] **Step 3: Add an Arch packaging draft.**
- [x] **Step 4: Add TDD harness rules, PR evidence template, and single local check command.**

### Task 5: Publish

**Files:**
- Modify: git metadata and remote repository

- [ ] **Step 1: Copy the verified project to `/Users/bmy001/Work/steamos-intel-handheld`.**
- [ ] **Step 2: Initialize git with author `JohnnySun <bmy001@gmail.com>`.**
- [ ] **Step 3: Create public GitHub repo `rivoreo/steamos-intel-handheld`.**
- [ ] **Step 4: Push `main` and verify the remote URL.**
