#!/usr/bin/env bash
set -euo pipefail

RECOVERY_INDEX_URL="${RECOVERY_INDEX_URL:-https://steamdeck-images.steamos.cloud/recovery/}"

usage() {
  cat >&2 <<'EOF'
Usage: scripts/steamos-qemu-build-env.sh <action>

Actions:
  latest-url  Print the newest SteamOS recovery image URL
  fetch       Download and convert the SteamOS image to a qcow2 base
  provision   Create a raw build image and enable SSH via a one-time serial boot
  run         Boot a writable qcow2 overlay with this repo mounted as 9p
  run-build   Boot the provisioned raw build image with this repo mounted as 9p
  ssh         Open an SSH session to a running provisioned build VM
  install-deps
              Install SteamOS build dependencies in a running build VM
  build-mangoapp
              Build MangoHud mangoapp in a running build VM and copy it to cache

Environment:
  STEAMOS_IMAGE_URL          Override the discovered recovery image URL
  STEAMOS_QEMU_DIR           Cache directory (default: .cache/steamos-qemu)
  STEAMOS_QEMU_CPUS          VM CPU count (default: 4)
  STEAMOS_QEMU_MEMORY        VM memory (default: 8G)
  STEAMOS_QEMU_ACCEL         QEMU accelerator (default: tcg)
  STEAMOS_QEMU_SSH_PORT      Host SSH forward port (default: 2222)
  STEAMOS_QEMU_DISPLAY       QEMU display backend (default: default)
  STEAMOS_QEMU_OVMF_CODE     Optional OVMF_CODE firmware path
  STEAMOS_QEMU_OVMF_VARS     Optional OVMF vars template path
  STEAMOS_QEMU_EXTRA_ARGS    Extra QEMU arguments appended at the end
  STEAMOS_QEMU_BUILD_JOBS    Ninja jobs for build-mangoapp (default: 3)
  STEAMOS_QEMU_SKIP_DEPS     Skip dependency install during build-mangoapp when set
  STEAMOS_QEMU_CLEAN_BUILD   Recreate the guest MangoHud build directory when set
  STEAMOS_QEMU_MESON_OPTIMIZATION
                            Optional Meson optimization level override for build-mangoapp
  STEAMOS_QEMU_MANGOAPP_ARTIFACT
                            Host output path (default: .cache/steamos-qemu/mangoapp)
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${STEAMOS_QEMU_DIR:-$repo_root/.cache/steamos-qemu}"
image_bz2="$cache_dir/steamos.img.bz2"
raw_image="$cache_dir/steamos.img"
base_qcow2="$cache_dir/steamos-base.qcow2"
overlay_qcow2="$cache_dir/steamos-overlay.qcow2"
ovmf_vars="$cache_dir/ovmf-vars.fd"
build_raw="$cache_dir/steamos-build.raw"
build_ovmf_vars="$cache_dir/ovmf-vars-build.fd"
ssh_key="$cache_dir/id_ed25519"
ssh_known_hosts="$cache_dir/known_hosts"
mangoapp_artifact="${STEAMOS_QEMU_MANGOAPP_ARTIFACT:-$cache_dir/mangoapp}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

latest_url() {
  if [ -n "${STEAMOS_IMAGE_URL:-}" ]; then
    printf '%s\n' "$STEAMOS_IMAGE_URL"
    return
  fi

  require_command python3
  python3 - "$RECOVERY_INDEX_URL" <<'PY'
from html.parser import HTMLParser
from urllib.request import urlopen
from urllib.parse import urljoin
import re
import sys

index_url = sys.argv[1]

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        href = attrs.get("href")
        if href:
            self.links.append(href)

html = urlopen(index_url, timeout=30).read().decode("utf-8", "replace")
parser = LinkParser()
parser.feed(html)

pattern = re.compile(r"steamdeck-(?:oobe-)?repair-(\d{8})\.(\d+)-([\d.]+)\.img\.bz2$")
candidates = []
for href in parser.links:
    name = href.rsplit("/", 1)[-1]
    match = pattern.search(name)
    if not match:
        continue
    date, build, version = match.groups()
    version_key = tuple(int(part) for part in version.split("."))
    candidates.append((version_key, int(date), int(build), href))

if not candidates:
    raise SystemExit(f"no SteamOS repair image found at {index_url}")

print(urljoin(index_url, max(candidates)[3]))
PY
}

fetch_image() {
  require_command curl
  require_command bzip2
  require_command qemu-img

  mkdir -p "$cache_dir"
  url="$(latest_url)"
  echo "Fetching $url"
  curl -fL --continue-at - --output "$image_bz2" "$url"

  if [ ! -e "$raw_image" ] || [ "$image_bz2" -nt "$raw_image" ]; then
    echo "Decompressing $image_bz2"
    bzip2 -dc "$image_bz2" > "$raw_image"
  fi

  if [ ! -e "$base_qcow2" ] || [ "$raw_image" -nt "$base_qcow2" ]; then
    echo "Converting raw image to $base_qcow2"
    qemu-img convert -f raw -O qcow2 "$raw_image" "$base_qcow2"
  fi
}

copy_reflink_or_plain() {
  src="$1"
  dst="$2"

  if cp -c "$src" "$dst" >/dev/null 2>&1; then
    return
  fi

  cp "$src" "$dst"
}

ensure_build_raw() {
  if [ ! -e "$raw_image" ]; then
    echo "Raw image is missing; run fetch first." >&2
    exit 1
  fi

  mkdir -p "$cache_dir"
  if [ ! -e "$build_raw" ]; then
    echo "Creating build image $build_raw"
    copy_reflink_or_plain "$raw_image" "$build_raw"
  fi
}

ensure_ssh_key() {
  require_command ssh-keygen

  mkdir -p "$cache_dir"
  if [ ! -e "$ssh_key" ]; then
    ssh-keygen -t ed25519 -f "$ssh_key" -N '' -C steamos-qemu-build
  fi
}

ensure_build_ovmf_vars() {
  ovmf_vars_template="$(detect_ovmf_vars_template)"
  if [ -n "$ovmf_vars_template" ] && [ ! -e "$build_ovmf_vars" ]; then
    cp "$ovmf_vars_template" "$build_ovmf_vars"
  fi
}

first_existing_path() {
  for path in "$@"; do
    if [ -e "$path" ]; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  return 1
}

detect_ovmf_code() {
  if [ -n "${STEAMOS_QEMU_OVMF_CODE:-}" ]; then
    printf '%s\n' "$STEAMOS_QEMU_OVMF_CODE"
    return
  fi

  first_existing_path \
    /opt/homebrew/share/qemu/edk2-x86_64-code.fd \
    /opt/homebrew/Cellar/qemu/*/share/qemu/edk2-x86_64-code.fd \
    /usr/share/OVMF/OVMF_CODE.fd \
    /usr/share/edk2/x64/OVMF_CODE.fd \
    /usr/share/qemu/OVMF.fd || true
}

detect_ovmf_vars_template() {
  if [ -n "${STEAMOS_QEMU_OVMF_VARS:-}" ]; then
    printf '%s\n' "$STEAMOS_QEMU_OVMF_VARS"
    return
  fi

  first_existing_path \
    /opt/homebrew/share/qemu/edk2-i386-vars.fd \
    /opt/homebrew/Cellar/qemu/*/share/qemu/edk2-i386-vars.fd \
    /usr/share/OVMF/OVMF_VARS.fd \
    /usr/share/edk2/x64/OVMF_VARS.fd || true
}

run_qemu_disk() {
  disk_path="$1"
  disk_format="$2"
  vars_path="$3"
  default_display="$4"

  require_command qemu-img
  require_command qemu-system-x86_64

  cpus="${STEAMOS_QEMU_CPUS:-4}"
  memory="${STEAMOS_QEMU_MEMORY:-8G}"
  accel="${STEAMOS_QEMU_ACCEL:-tcg}"
  ssh_port="${STEAMOS_QEMU_SSH_PORT:-2222}"
  display="${STEAMOS_QEMU_DISPLAY:-$default_display}"

  ovmf_args=()
  ovmf_code="$(detect_ovmf_code)"
  ovmf_vars_template="$(detect_ovmf_vars_template)"
  if [ -n "$ovmf_code" ]; then
    ovmf_args=(-drive "if=pflash,format=raw,readonly=on,file=$ovmf_code")
    if [ -n "$ovmf_vars_template" ]; then
      if [ ! -e "$vars_path" ]; then
        cp "$ovmf_vars_template" "$vars_path"
      fi
      ovmf_args+=(-drive "if=pflash,format=raw,file=$vars_path")
    fi
  fi

  extra_args=()
  if [ -n "${STEAMOS_QEMU_EXTRA_ARGS:-}" ]; then
    # Intentionally split like a shell command-line for local developer overrides.
    # shellcheck disable=SC2206
    extra_args=(${STEAMOS_QEMU_EXTRA_ARGS})
  fi

  qemu_args=(
    -machine "q35,accel=$accel"
    -cpu max
    -smp "$cpus"
    -m "$memory"
    "${ovmf_args[@]}"
    -drive "if=virtio,file=$disk_path,format=$disk_format"
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$ssh_port-:22"
    -device virtio-net-pci,netdev=net0
    -virtfs "local,path=$repo_root,mount_tag=workspace,security_model=none,id=workspace"
    -device virtio-vga
    -display "$display"
  )

  if [ "${#extra_args[@]}" -gt 0 ]; then
    qemu_args+=("${extra_args[@]}")
  fi

  exec qemu-system-x86_64 "${qemu_args[@]}"
}

run_vm() {
  require_command qemu-img

  if [ ! -e "$base_qcow2" ]; then
    echo "Base qcow2 is missing; run fetch first." >&2
    exit 1
  fi

  if [ ! -e "$overlay_qcow2" ]; then
    qemu-img create -f qcow2 -F qcow2 -b "$base_qcow2" "$overlay_qcow2"
  fi

  run_qemu_disk "$overlay_qcow2" qcow2 "$ovmf_vars" default
}

run_build_vm() {
  ensure_build_raw
  ensure_build_ovmf_vars
  run_qemu_disk "$build_raw" raw "$build_ovmf_vars" none
}

attach_build_image() {
  require_command hdiutil

  hdiutil attach -nomount -imagekey diskimage-class=CRawDiskImage "$build_raw" |
    awk 'NR == 1 { print $1 }'
}

mount_build_efi() {
  require_command diskutil

  disk="$1"
  part="${disk}s2"
  diskutil mount "$part" >/dev/null
  mount | awk -v part="$part" '$1 == part { print $3; exit }'
}

with_build_efi_mounted() {
  callback="$1"
  disk="$(attach_build_image)"

  mount_point="$(mount_build_efi "$disk")"
  if [ -z "$mount_point" ]; then
    diskutil unmountDisk "$disk" >/dev/null 2>&1 || true
    hdiutil detach "$disk" >/dev/null 2>&1 || true
    echo "failed to find mounted EFI partition for $disk" >&2
    return 1
  fi

  set +e
  "$callback" "$mount_point/EFI/steamos/grub.cfg"
  rc=$?
  set -e
  diskutil unmountDisk "$disk" >/dev/null 2>&1 || true
  hdiutil detach "$disk" >/dev/null 2>&1 || true
  return "$rc"
}

patch_grub_for_provision() {
  grub="$1"

  if [ ! -e "$grub.qemu-provision.bak" ]; then
    cp "$grub" "$grub.qemu-provision.bak"
  fi

  perl -0pi -e \
    's/console=ttyS0,115200 console=tty1/console=tty1 console=ttyS0,115200/g;
     s/console=tty1(?! console=ttyS0,115200)/console=tty1 console=ttyS0,115200/g;
     s/plymouth\.ignore-serial-consoles(?! init=\/usr\/bin\/bash)/plymouth.ignore-serial-consoles init=\/usr\/bin\/bash/g' \
    "$grub"
  sync
}

restore_grub_after_provision() {
  grub="$1"

  perl -0pi -e 's/ init=\/usr\/bin\/bash//g' "$grub"
  sync
}

provision_build_image() {
  require_command expect
  require_command hdiutil
  require_command diskutil

  ensure_build_raw
  ensure_ssh_key
  ensure_build_ovmf_vars
  with_build_efi_mounted patch_grub_for_provision

  pubkey="$(cat "$ssh_key.pub")"
  script_path="$repo_root/scripts/steamos-qemu-build-env.sh"
  memory="${STEAMOS_QEMU_MEMORY:-4G}"
  ssh_port="${STEAMOS_QEMU_SSH_PORT:-2222}"

  set +e
  expect <<EOF
set timeout 300
spawn sh -c "STEAMOS_QEMU_MEMORY='$memory' STEAMOS_QEMU_SSH_PORT='$ssh_port' STEAMOS_QEMU_DISPLAY=none STEAMOS_QEMU_EXTRA_ARGS='-serial mon:stdio' '$script_path' run-build"
expect {
  -re {root@.*# } {}
  timeout {
    puts stderr "timed out waiting for SteamOS provisioning shell"
    exit 1
  }
}
send -- "set +e\r"
send -- "mkdir -p /etc/ssh/sshd_config.d /etc/systemd/system/multi-user.target.wants\r"
send -- "printf '%s\\\\n' '$pubkey' > /etc/ssh/qemu_authorized_keys\r"
send -- "chmod 600 /etc/ssh/qemu_authorized_keys\r"
send -- "cat > /etc/ssh/sshd_config.d/90-qemu-build.conf <<'EOS'\r"
send -- "PermitRootLogin prohibit-password\r"
send -- "PubkeyAuthentication yes\r"
send -- "AuthorizedKeysFile /etc/ssh/qemu_authorized_keys .ssh/authorized_keys\r"
send -- "PasswordAuthentication no\r"
send -- "EOS\r"
send -- "ln -sf /usr/lib/systemd/system/sshd.service /etc/systemd/system/multi-user.target.wants/sshd.service\r"
send -- "git config --global --add safe.directory /home/workspace/external/MangoHud || true\r"
send -- "sync; echo PROVISION_DONE\r"
expect {
  "PROVISION_DONE" {}
  timeout {
    puts stderr "timed out while provisioning SSH"
    exit 1
  }
}
send "\001x"
expect eof
EOF
  rc=$?
  set -e

  with_build_efi_mounted restore_grub_after_provision
  return "$rc"
}

ssh_port_value() {
  printf '%s\n' "${STEAMOS_QEMU_SSH_PORT:-2222}"
}

ssh_base_args() {
  ensure_ssh_key
  printf '%s\n' \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=no \
    -o "UserKnownHostsFile=$ssh_known_hosts" \
    -i "$ssh_key" \
    -p "$(ssh_port_value)" \
    root@127.0.0.1
}

ssh_guest() {
  # shellcheck disable=SC2207
  ssh_args=($(ssh_base_args))
  ssh "${ssh_args[@]}" "$@"
}

open_guest_ssh() {
  # shellcheck disable=SC2207
  ssh_args=($(ssh_base_args))
  exec ssh "${ssh_args[@]}"
}

install_build_deps() {
  ssh_guest 'bash -s' <<'EOS'
set -eux
steamos-readonly disable
pacman-key --init || true
pacman-key --populate archlinux holo
pacman -Sy --needed --noconfirm \
  base-devel meson ninja pkgconf python-mako vulkan-headers
pacman -S --noconfirm \
  glibc linux-api-headers \
  libx11 xorgproto libxcb xcb-proto xtrans libxau libxdmcp \
  wayland libxkbcommon dbus glfw glslang vulkan-icd-loader \
  libffi systemd-libs libglvnd \
  libxrandr libxinerama libxcursor libxi libxrender libxfixes
EOS
}

build_mangoapp() {
  if [ -z "${STEAMOS_QEMU_SKIP_DEPS:-}" ]; then
    install_build_deps
  fi

  jobs="${STEAMOS_QEMU_BUILD_JOBS:-3}"
  remote_env="STEAMOS_QEMU_BUILD_JOBS=$jobs"
  remote_env="$remote_env STEAMOS_QEMU_CLEAN_BUILD=${STEAMOS_QEMU_CLEAN_BUILD:-}"
  remote_env="$remote_env STEAMOS_QEMU_MESON_OPTIMIZATION=${STEAMOS_QEMU_MESON_OPTIMIZATION:-}"
  ssh_guest "$remote_env bash -s" <<'EOS'
set -eux
mkdir -p /home/workspace /home/build
mountpoint -q /home/workspace || \
  mount -t 9p -o trans=virtio,version=9p2000.L workspace /home/workspace
git config --global --add safe.directory /home/workspace/external/MangoHud || true
meson_options=(
  --prefix=/usr
  -Dmangoapp=true
  -Dwith_xnvctrl=disabled
  -Dwith_nvml=disabled
  -Dinclude_doc=false
  -Dtests=disabled
  -Dmangoplot=disabled
  -Dwith_mangohud_next=false
  -Dwith_server=false
)
if [ -n "${STEAMOS_QEMU_MESON_OPTIMIZATION:-}" ]; then
  meson_options+=("-Doptimization=$STEAMOS_QEMU_MESON_OPTIMIZATION")
fi
if [ -n "${STEAMOS_QEMU_CLEAN_BUILD:-}" ]; then
  rm -rf /home/build/mangohud
fi
if [ -e /home/build/mangohud/build.ninja ]; then
  meson setup --reconfigure /home/build/mangohud /home/workspace/external/MangoHud \
    "${meson_options[@]}"
else
  meson setup /home/build/mangohud /home/workspace/external/MangoHud \
    "${meson_options[@]}"
fi
meson compile -C /home/build/mangohud -j "$STEAMOS_QEMU_BUILD_JOBS" mangoapp
file /home/build/mangohud/src/mangoapp
EOS

  mkdir -p "$(dirname "$mangoapp_artifact")"
  scp \
    -o StrictHostKeyChecking=no \
    -o "UserKnownHostsFile=$ssh_known_hosts" \
    -i "$ssh_key" \
    -P "$(ssh_port_value)" \
    root@127.0.0.1:/home/build/mangohud/src/mangoapp \
    "$mangoapp_artifact"
  file "$mangoapp_artifact"
}

action="${1:-}"
case "$action" in
  latest-url)
    latest_url
    ;;
  fetch)
    fetch_image
    ;;
  provision)
    provision_build_image
    ;;
  run)
    run_vm
    ;;
  run-build)
    run_build_vm
    ;;
  ssh)
    open_guest_ssh
    ;;
  install-deps)
    install_build_deps
    ;;
  build-mangoapp)
    build_mangoapp
    ;;
  *)
    usage
    exit 2
    ;;
esac
