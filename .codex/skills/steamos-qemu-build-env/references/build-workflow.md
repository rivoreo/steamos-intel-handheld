# Build Workflow Reference

Use this reference for the detailed SteamOS MangoHud build paths in this
repository.

## Purpose

The build harness compiles MangoHud's `mangoapp` against a SteamOS userland that
is close to the target handheld. It avoids host-side glibc, Mesa, and package
version drift. It does not replace final testing on the Intel handheld.

Release CI uses a Linux x86_64 SteamOS rootfs chroot. The QEMU VM path remains
for local macOS/non-Linux development and boot-level debugging.

## Image Source

`scripts/steamos-qemu-build-env.sh` discovers the newest `*.img.bz2` from:

```text
https://steamdeck-images.steamos.cloud/recovery/
```

Override with `STEAMOS_IMAGE_URL` only when the target system requires a
specific image. If the handheld runs a newer OTA than Valve's public recovery
image, build with the newest public image and keep the final smoke test on real
hardware.

## Host Requirements

For Linux x86_64 rootfs builds:

- `curl`
- `bzip2`
- `losetup`, `lsblk`, `mount`, `chroot`, and `sudo`
- `rsync`
- 20GB or more free disk space for the raw image and rootfs

For the QEMU VM path:

- `qemu-system-x86_64`
- `qemu-img`
- `curl`
- `bzip2`
- `ssh`, `scp`, `ssh-keygen`
- macOS provisioning helpers: `hdiutil`, `diskutil`, `expect`
- 20GB or more free disk space for raw image, qcow2 base, overlay, and build
  image
- Optional OVMF variables:
  - `STEAMOS_QEMU_OVMF_CODE`
  - `STEAMOS_QEMU_OVMF_VARS`

On Apple Silicon, expect x86_64 emulation to be slow. On Linux x86_64, prefer
the rootfs chroot path for builds. Use `STEAMOS_QEMU_ACCEL=kvm` only when you
need to boot the VM.

## Harness Actions

Common actions:

```bash
scripts/steamos-qemu-build-env.sh latest-url
scripts/steamos-qemu-build-env.sh fetch-raw
scripts/steamos-qemu-build-env.sh prepare-rootfs
scripts/steamos-qemu-build-env.sh build-mangoapp-rootfs
scripts/steamos-qemu-build-env.sh fetch
scripts/steamos-qemu-build-env.sh provision
scripts/steamos-qemu-build-env.sh run-build
scripts/steamos-qemu-build-env.sh ssh
scripts/steamos-qemu-build-env.sh install-deps
scripts/steamos-qemu-build-env.sh build-mangoapp
```

Useful environment:

```bash
STEAMOS_QEMU_DIR=.cache/steamos-qemu
STEAMOS_ROOTFS_DIR=.cache/steamos-qemu/rootfs
STEAMOS_QEMU_CPUS=4
STEAMOS_QEMU_MEMORY=4G
STEAMOS_QEMU_ACCEL=tcg
STEAMOS_QEMU_SSH_PORT=2224
STEAMOS_QEMU_DISPLAY=none
STEAMOS_QEMU_EXTRA_ARGS='-serial mon:stdio'
STEAMOS_QEMU_BUILD_JOBS=3
STEAMOS_QEMU_SKIP_DEPS=1
STEAMOS_QEMU_MANGOAPP_ARTIFACT=.cache/steamos-qemu/mangoapp
```

## Rootfs Build Behavior

`fetch-raw` downloads and decompresses the recovery image without converting it
to qcow2. `prepare-rootfs` attaches the raw image with `losetup --partscan`,
finds the partition that contains `/usr/bin/pacman`, and copies it into a
writable rootfs directory. `build-mangoapp-rootfs` bind-mounts the repository at
`/home/workspace`, installs the SteamOS build dependencies, builds
`/home/build/mangohud/src/mangoapp`, and copies it to the configured artifact
path.

This path requires a Linux x86_64 host because the SteamOS target is x86_64.
ARM Linux requires an explicit cross-toolchain or qemu-user binfmt layer before
using a chroot build.

## Provisioning Behavior

`fetch` downloads the recovery image to `.cache/steamos-qemu/`, converts it to a
reusable qcow2 base, and creates the build image.

`provision` performs a one-time serial boot, enables root SSH using generated
keys in `.cache/steamos-qemu/`, configures the repo as a 9p mount named
`workspace`, and restores normal SteamOS boot. The guest mounts the repo at
`/home/workspace` because SteamOS keeps `/` read-only.

## MangoHud Build

With `run-build` still running:

```bash
STEAMOS_QEMU_SSH_PORT=2224 \
STEAMOS_QEMU_BUILD_JOBS=3 \
scripts/steamos-qemu-build-env.sh build-mangoapp
```

`build-mangoapp` installs SteamOS build dependencies, including package
reinstalls for headers and pkg-config files that are stripped from the recovery
image. The guest build directory is `/home/build/mangohud`. The output copied
back to the host is:

```text
.cache/steamos-qemu/mangoapp
```

Use `STEAMOS_QEMU_SKIP_DEPS=1` for repeat builds after dependencies are already
installed in the provisioned build image.

## Deployment

Deploy the locally built `mangoapp` to the handheld:

```bash
scripts/configure-mangoapp-dropin.sh \
  enable root@<host> \
  .cache/steamos-qemu/mangoapp
```

The script installs the binary at:

```text
/opt/steamos-intel-handheld/bin/mangoapp
```

It installs the user service drop-in at:

```text
/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf
```

It also removes the earlier experimental paths:

```text
/opt/rivoreo/bin/mangoapp
/etc/rivoreo/bin/mangoapp
```

Disable the drop-in with:

```bash
scripts/configure-mangoapp-dropin.sh disable root@<host>
```

## Real Device Verification

Run:

```bash
scripts/verify-on-device.sh root@<host>
```

The verifier checks:

- `steamos-intel-handheld-power-control.service` is active
- MangoHud CPU power sensor can be read by `deck` from RAPL `package-0`
- MangoHud GPU power sensor can be read by `deck` from RAPL `uncore`
- SteamOS Manager discovers the remote `TdpLimit1` provider
- `steamosctl set-tdp-limit` updates central TDP, remote TDP, RAPL PL1, and
  RAPL PL2
- No failed systemd units remain

The default test is 28W and restore is 30W. Override with:

```bash
scripts/verify-on-device.sh root@<host> <test-watts> <restore-watts>
```

## Sensor Notes

For the tested MSI Claw 8 AI+ / Intel Lunar Lake class device:

- CPU temperature is native `coretemp`.
- CPU power is RAPL `package-0` `energy_uj`.
- GPU power is RAPL `uncore` `energy_uj`, matching
  `perf stat -a -e power/energy-gpu/`.
- GPU temperature is not faked. On the tested SteamOS build, Intel `xe` exposes
  fdinfo and GT frequency, but not the DRM hwmon directory MangoHud expects for
  Intel GPU temperature.

When reviewing MangoHud changes, avoid breaking existing Intel discrete GPU
paths. The local Intel handheld path should add integrated `i915`/`xe` RAPL
`uncore` power support without replacing DRM hwmon temperature behavior for
platforms that already expose it.
