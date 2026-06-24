#!/usr/bin/env bash
set -euo pipefail

RECOVERY_INDEX_URL="${RECOVERY_INDEX_URL:-https://steamdeck-images.steamos.cloud/recovery/}"

usage() {
  cat >&2 <<'EOF'
Usage: scripts/steamos-qemu-build-env.sh <action>

Actions:
  latest-url  Print the newest SteamOS recovery image URL
  fetch       Download and convert the SteamOS image to a qcow2 base
  run         Boot a writable qcow2 overlay with this repo mounted as 9p

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
EOF
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${STEAMOS_QEMU_DIR:-$repo_root/.cache/steamos-qemu}"
image_bz2="$cache_dir/steamos.img.bz2"
raw_image="$cache_dir/steamos.img"
base_qcow2="$cache_dir/steamos-base.qcow2"
overlay_qcow2="$cache_dir/steamos-overlay.qcow2"
ovmf_vars="$cache_dir/ovmf-vars.fd"

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

run_vm() {
  require_command qemu-img
  require_command qemu-system-x86_64

  if [ ! -e "$base_qcow2" ]; then
    echo "Base qcow2 is missing; run fetch first." >&2
    exit 1
  fi

  if [ ! -e "$overlay_qcow2" ]; then
    qemu-img create -f qcow2 -F qcow2 -b "$base_qcow2" "$overlay_qcow2"
  fi

  cpus="${STEAMOS_QEMU_CPUS:-4}"
  memory="${STEAMOS_QEMU_MEMORY:-8G}"
  accel="${STEAMOS_QEMU_ACCEL:-tcg}"
  ssh_port="${STEAMOS_QEMU_SSH_PORT:-2222}"
  display="${STEAMOS_QEMU_DISPLAY:-default}"

  ovmf_args=()
  ovmf_code="$(detect_ovmf_code)"
  ovmf_vars_template="$(detect_ovmf_vars_template)"
  if [ -n "$ovmf_code" ]; then
    ovmf_args=(-drive "if=pflash,format=raw,readonly=on,file=$ovmf_code")
    if [ -n "$ovmf_vars_template" ]; then
      if [ ! -e "$ovmf_vars" ]; then
        cp "$ovmf_vars_template" "$ovmf_vars"
      fi
      ovmf_args+=(-drive "if=pflash,format=raw,file=$ovmf_vars")
    fi
  fi

  extra_args=()
  if [ -n "${STEAMOS_QEMU_EXTRA_ARGS:-}" ]; then
    # Intentionally split like a shell command-line for local developer overrides.
    # shellcheck disable=SC2206
    extra_args=(${STEAMOS_QEMU_EXTRA_ARGS})
  fi

  exec qemu-system-x86_64 \
    -machine "q35,accel=$accel" \
    -cpu max \
    -smp "$cpus" \
    -m "$memory" \
    "${ovmf_args[@]}" \
    -drive "if=virtio,file=$overlay_qcow2,format=qcow2" \
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$ssh_port-:22" \
    -device virtio-net-pci,netdev=net0 \
    -virtfs "local,path=$repo_root,mount_tag=workspace,security_model=none,id=workspace" \
    -device virtio-vga \
    -display "$display" \
    "${extra_args[@]}"
}

action="${1:-}"
case "$action" in
  latest-url)
    latest_url
    ;;
  fetch)
    fetch_image
    ;;
  run)
    run_vm
    ;;
  *)
    usage
    exit 2
    ;;
esac
