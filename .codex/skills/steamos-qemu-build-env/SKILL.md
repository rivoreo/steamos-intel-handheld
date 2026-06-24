---
name: steamos-qemu-build-env
description: Build, maintain, and verify the SteamOS QEMU build environment for this Intel handheld repo. Use whenever the user asks about compiling MangoHud/mangoapp for SteamOS, using Valve recovery images, QEMU/KVM/x86 Docker alternatives, cross-compilation/build VMs, deploying a mangoapp systemd drop-in, or validating MangoHud CPU/GPU sensor support on the target handheld. Prefer this skill even when the user says only "build env", "SteamOS image", "QEMU", "mangoapp", "MangoHud sensor", or "local compile".
---

# SteamOS QEMU Build Env

Use the repo's existing build harness instead of rediscovering the SteamOS setup.
Read `references/build-workflow.md` when the task needs command details,
debugging steps, deployment, or real-device validation.

## Working Rules

- Treat the QEMU VM as a SteamOS userland compatibility build box, not as proof
  that MangoHud sensors work on the handheld.
- Keep final sensor, gamescope, systemd, and D-Bus claims grounded in a real
  device run with `scripts/verify-on-device.sh`.
- Keep generated images, VM overlays, SSH keys, and mangoapp binaries under
  `.cache/steamos-qemu/`; they are local build artifacts.
- Preserve the MangoHud submodule branch unless the user asks to rebase or
  retarget it. Check `external/MangoHud` status before editing upstream code.
- Install replacement mangoapp binaries with
  `scripts/configure-mangoapp-dropin.sh`, which places them under
  `/opt/steamos-intel-handheld/bin/`.
- Avoid faking telemetry. The project goal is to make MangoHud see existing
  Linux sensor files, especially RAPL `package-0` for CPU power and RAPL
  `uncore` for Intel GPU power.

## Standard Workflow

1. Inspect the current repo state:
   ```bash
   git status --short --branch
   git submodule status --recursive
   git -C external/MangoHud status --short --branch
   ```

2. Prepare or refresh the build VM:
   ```bash
   scripts/steamos-qemu-build-env.sh fetch
   scripts/steamos-qemu-build-env.sh provision
   ```

3. Boot the provisioned build VM in one terminal:
   ```bash
   STEAMOS_QEMU_MEMORY=4G \
   STEAMOS_QEMU_SSH_PORT=2224 \
   scripts/steamos-qemu-build-env.sh run-build
   ```

4. Build mangoapp from the MangoHud submodule while the VM is running:
   ```bash
   STEAMOS_QEMU_SSH_PORT=2224 \
   STEAMOS_QEMU_BUILD_JOBS=3 \
   scripts/steamos-qemu-build-env.sh build-mangoapp
   ```

5. After the first successful dependency install, speed up later builds with:
   ```bash
   STEAMOS_QEMU_SKIP_DEPS=1 \
   STEAMOS_QEMU_SSH_PORT=2224 \
   STEAMOS_QEMU_BUILD_JOBS=3 \
   scripts/steamos-qemu-build-env.sh build-mangoapp
   ```

6. Deploy and verify on the handheld only when the target is online:
   ```bash
   scripts/configure-mangoapp-dropin.sh enable root@<host> .cache/steamos-qemu/mangoapp
   scripts/verify-on-device.sh root@<host>
   ```

## Validation

- Run `scripts/check-local.sh` for repo-level Python and shell checks.
- Run the official skill validator for this skill after edits:
  ```bash
  python3 .codex/skills/skill-creator/scripts/quick_validate.py .codex/skills/steamos-qemu-build-env
  ```
- If a command needs network, root SSH, or writes outside the workspace, explain
  why before requesting permission.

## When Reporting Results

- Say exactly which image or cache was used when known.
- Separate "compiled successfully in SteamOS VM" from "verified on hardware".
- If the handheld is offline, report the remaining hardware validation step
  instead of implying sensor support is proven.
