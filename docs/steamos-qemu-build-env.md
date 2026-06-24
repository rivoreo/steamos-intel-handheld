# SteamOS QEMU Build Environment

Use this when a MangoHud change must be compiled against the same SteamOS userland
as the target handheld. The build VM uses Valve's SteamOS recovery image instead
of the host system, so glibc, Mesa, system libraries, and package versions stay
close to the device.

## Image Source

The helper script discovers the newest `*.img.bz2` from Valve's official recovery
index:

https://steamdeck-images.steamos.cloud/recovery/

As of 2026-06-24, the newest image listed there is:

`steamdeck-oobe-repair-20260618.10-3.8.10.img.bz2`

If the target device is on a newer unreleased OTA than the public image, prefer
the newest public image and keep the final smoke test on real hardware.

## Host Requirements

- `qemu-system-x86_64`
- `qemu-img`
- `curl`
- `bzip2`
- 20GB+ free disk space for the raw image, qcow2 base, and overlay
- Optional: OVMF firmware paths exported as `STEAMOS_QEMU_OVMF_CODE` and
  `STEAMOS_QEMU_OVMF_VARS`; Homebrew QEMU is auto-detected.

On Apple Silicon, `qemu-system-x86_64` runs through emulation and will be slower.
On Linux x86_64, use KVM with `STEAMOS_QEMU_ACCEL=kvm`.

## Prepare And Boot

```bash
scripts/steamos-qemu-build-env.sh fetch
scripts/steamos-qemu-build-env.sh run
```

The script stores images under `.cache/steamos-qemu/`, converts the downloaded
raw image to a reusable qcow2 base, and boots a writable qcow2 overlay. The repo
is exposed to the VM as a 9p mount named `workspace`.

Inside SteamOS, mount the workspace:

```bash
sudo mkdir -p /workspace
sudo mount -t 9p -o trans=virtio,version=9p2000.L workspace /workspace
```

Enable SSH if you want to drive builds from the host:

```bash
sudo systemctl enable --now sshd
```

The QEMU user network forwards `127.0.0.1:2222` to guest port `22` by default.
Override with `STEAMOS_QEMU_SSH_PORT`.

For a headless smoke boot:

```bash
STEAMOS_QEMU_DISPLAY=none scripts/steamos-qemu-build-env.sh run
```

## Build MangoHud Mangoapp

Inside the VM:

```bash
cd /workspace/external/MangoHud
meson setup build/steamos-qemu \
  --prefix=/usr \
  -Dmangoapp=true \
  -Dwith_xnvctrl=disabled \
  -Dinclude_doc=false \
  -Dtests=disabled
meson compile -C build/steamos-qemu mangoapp
```

Then deploy the binary to the target handheld from the host:

```bash
scripts/configure-mangoapp-dropin.sh \
  enable root@192.168.128.214 \
  external/MangoHud/build/steamos-qemu/src/mangoapp
scripts/verify-on-device.sh root@192.168.128.214
```

## Notes

- Keep the qcow2 base immutable; throw away and recreate only the overlay.
- The writable OVMF vars file is stored at `.cache/steamos-qemu/ovmf-vars.fd`.
- Use the real handheld as the final verification source for sensors and
  gamescope/systemd behavior.
- If Valve publishes a newer recovery image, rerun `fetch`; the helper resolves
  the newest image at runtime unless `STEAMOS_IMAGE_URL` is set.
