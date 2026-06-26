# SteamOS MangoHud Build Environment

Use this when a MangoHud change must be compiled against the same SteamOS userland
as the target handheld. The helper uses Valve's SteamOS recovery image instead
of the host system, so glibc, Mesa, system libraries, and package versions stay
close to the device.

CI builds `mangoapp` on Linux x86_64 with a SteamOS rootfs chroot extracted from
the recovery image. The QEMU VM path is still available for local macOS and
non-Linux development, but release CI should not boot a SteamOS VM or wait for
SSH just to compile a userland binary.

## Image Source

The helper script discovers the newest `*.img.bz2` from Valve's official recovery
index:

https://steamdeck-images.steamos.cloud/recovery/

As of 2026-06-24, the newest image listed there is:

`steamdeck-oobe-repair-20260618.10-3.8.10.img.bz2`

If the target device is on a newer unreleased OTA than the public image, prefer
the newest public image and keep the final smoke test on real hardware.

## Host Requirements

For CI or Linux x86_64 rootfs builds:

- Linux x86_64 host
- `curl`
- `bzip2`
- `losetup`, `lsblk`, `mount`, `chroot`, and `sudo`
- `rsync`
- 20GB+ free disk space for the raw image and extracted rootfs

For the local QEMU VM path:

- `qemu-system-x86_64`
- `qemu-img`
- `curl`
- `bzip2`
- `ssh`, `scp`, `ssh-keygen`
- macOS provisioning helpers: `hdiutil`, `diskutil`, and `expect`
- 20GB+ free disk space for the raw image, qcow2 base, and overlay
- Optional: OVMF firmware paths exported as `STEAMOS_QEMU_OVMF_CODE` and
  `STEAMOS_QEMU_OVMF_VARS`; Homebrew QEMU is auto-detected.

On Apple Silicon, `qemu-system-x86_64` runs through emulation and will be slower.
On Linux x86_64, prefer the rootfs chroot path for builds. Use QEMU with
`STEAMOS_QEMU_ACCEL=kvm` only when you need to boot the SteamOS image.

## CI Rootfs Build

Use this path on Linux x86_64 CI:

```bash
STEAMOS_ROOTFS_DIR="$PWD/.cache/steamos-rootfs/rootfs" \
STEAMOS_QEMU_MANGOAPP_ARTIFACT="$PWD/.cache/arch-release/mangoapp/mangoapp" \
scripts/steamos-qemu-build-env.sh fetch-raw
STEAMOS_ROOTFS_DIR="$PWD/.cache/steamos-rootfs/rootfs" \
scripts/steamos-qemu-build-env.sh prepare-rootfs
STEAMOS_ROOTFS_DIR="$PWD/.cache/steamos-rootfs/rootfs" \
STEAMOS_QEMU_BUILD_JOBS=3 \
STEAMOS_QEMU_MANGOAPP_ARTIFACT="$PWD/.cache/arch-release/mangoapp/mangoapp" \
scripts/steamos-qemu-build-env.sh build-mangoapp-rootfs
```

`fetch-raw` downloads and decompresses the recovery image without converting it
to qcow2. `prepare-rootfs` attaches the raw image with `losetup --partscan`,
finds the SteamOS partition containing `/usr/bin/pacman`, and copies it to a
writable rootfs directory. `build-mangoapp-rootfs` bind-mounts this repository
into `/home/workspace`, installs the same SteamOS build dependencies, builds
`mangoapp`, and copies the binary to the configured artifact path.

This path requires the host CPU architecture to match the SteamOS target
architecture. For ARM Linux hosts, add an explicit cross-toolchain or qemu-user
binfmt layer before using a chroot build.

## Prepare And Provision

Use this path for local VM-based development:

```bash
scripts/steamos-qemu-build-env.sh fetch
scripts/steamos-qemu-build-env.sh provision
```

The script stores images under `.cache/steamos-qemu/`, converts the downloaded
raw image to a reusable qcow2 base, and creates a writable raw build image at
`.cache/steamos-qemu/steamos-build.raw`. `provision` performs a one-time serial
boot, enables root SSH with a generated key under `.cache/steamos-qemu/`, then
restores normal SteamOS boot.

The repo is exposed to the VM as a 9p mount named `workspace`. The build image
mounts it at `/home/workspace` because SteamOS keeps `/` read-only.

## Boot The Build VM

Run the provisioned VM in one terminal:

```bash
STEAMOS_QEMU_MEMORY=4G \
STEAMOS_QEMU_SSH_PORT=2224 \
scripts/steamos-qemu-build-env.sh run-build
```

The QEMU user network forwards `127.0.0.1:2222` to guest port `22` by default.
Override with `STEAMOS_QEMU_SSH_PORT`.

For a headless smoke boot of the generic qcow2 overlay:

```bash
STEAMOS_QEMU_DISPLAY=none scripts/steamos-qemu-build-env.sh run
```

For an interactive shell into the provisioned build VM:

```bash
STEAMOS_QEMU_SSH_PORT=2224 scripts/steamos-qemu-build-env.sh ssh
```

## Build MangoHud Mangoapp

With `run-build` still running:

```bash
STEAMOS_QEMU_SSH_PORT=2224 \
STEAMOS_QEMU_BUILD_JOBS=3 \
scripts/steamos-qemu-build-env.sh build-mangoapp
```

`build-mangoapp` installs the SteamOS build dependencies, including a reinstall
of runtime packages whose development headers and `pkg-config` files are
stripped from the recovery image. It builds in `/home/build/mangohud` inside the
guest and copies the resulting x86_64 binary to:

`./.cache/steamos-qemu/mangoapp`

Set `STEAMOS_QEMU_SKIP_DEPS=1` after the first successful dependency provision
to skip the package step on later builds. `build-mangoapp` reuses the guest
Meson build directory by default so follow-up MangoHud edits only recompile the
changed files. Set `STEAMOS_QEMU_CLEAN_BUILD=1` when you need to recreate the
build directory from scratch. On Apple Silicon or other hosts that must emulate
x86_64 with TCG, set `STEAMOS_QEMU_MESON_OPTIMIZATION=0` for faster local
verification builds; omit it for MangoHud's default release optimization.

## Deploy To The Handheld

Deploy the locally built binary to the target handheld from the host:

```bash
scripts/configure-mangoapp-dropin.sh \
  enable root@192.168.128.214 \
  .cache/steamos-qemu/mangoapp
scripts/verify-on-device.sh root@192.168.128.214
```

## Notes

- Keep the qcow2 base immutable; throw away and recreate only the overlay.
- The writable OVMF vars file is stored at `.cache/steamos-qemu/ovmf-vars.fd`.
- The provisioned raw build image uses `.cache/steamos-qemu/ovmf-vars-build.fd`.
- Use the real handheld as the final verification source for sensors and
  gamescope/systemd behavior.
- If Valve publishes a newer recovery image, rerun `fetch`; the helper resolves
  the newest image at runtime unless `STEAMOS_IMAGE_URL` is set.
