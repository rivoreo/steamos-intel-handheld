---
name: steamos-qemu-build-env
description: Build, maintain, or verify the SteamOS QEMU/rootfs environment for compiling MangoHud or mangoapp in this Intel handheld repo. Use for Valve recovery images, QEMU/KVM build alternatives, SteamOS-compatible binaries, mangoapp drop-ins, or separating build evidence from real sensor validation.
---

# SteamOS QEMU build environment

Use the repository's existing scripts. Read
`docs/steamos-qemu-build-env.md` for commands, troubleshooting, deployment, and
device validation; it is the operational source of truth.

## Choose the evidence layer

- **Local/static**: source, submodule, patches, and deterministic tests.
- **QEMU VM**: SteamOS userland compatibility and exploratory builds.
- **SteamOS rootfs**: the release-parity mangoapp build path.
- **Real handheld**: MangoHud CPU/GPU power, DRM hwmon temperature, RAPL,
  gamescope, and runtime behavior.

Success in one layer never proves a later layer.

## Boundaries

- Do not substitute generic macOS/Linux or x86 Docker output for a SteamOS
  release artifact.
- Do not treat QEMU as hardware sensor evidence.
- Preserve the `external/MangoHud` submodule state unless the task explicitly
  updates it.
- Keep generated images, rootfs trees, and binaries under the documented cache
  paths; do not commit them.
- RAPL `package-0` and `uncore` can support CPU/package and integrated GPU power
  claims. Do not invent a GPU temperature when the target exposes no valid
  DRM hwmon sensor, and preserve existing Intel discrete temperature behavior.
- Deployment or device verification requires explicit device authority and the
  current target from repository configuration, not a stale hard-coded host.

## Execution

Inspect the current source and cache state, then select only the stages needed
from `scripts/steamos-qemu-build-env.sh --allow-qemu`. Use
`build-mangoapp-rootfs` for
release-parity output. If deployment is requested, follow the drop-in and
restore procedure in `docs/steamos-qemu-build-env.md`; do not improvise power
or service restoration.

Run the matching focused local tests for changed code. QEMU, downloads,
privileged rootfs work, deployment, and on-device verification are heavy or
external checks and require the request to reach that layer.

Report the exact artifact path and command outcome, then state separately what
still requires real-device evidence.
