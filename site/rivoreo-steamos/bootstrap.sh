#!/usr/bin/env bash
set -euo pipefail

repo_base_url="${REPO_BASE_URL:-https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos}"
key_fingerprint="__RIVOREO_KEY_FINGERPRINT__"
repo_conf="/etc/pacman.d/rivoreo-steamos.conf"
include_line="Include = /etc/pacman.d/rivoreo-steamos.conf"

die() {
  echo "error: $*" >&2
  exit 2
}

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  die "run as root: curl -fsSL $repo_base_url/bootstrap.sh | sudo bash"
fi

if [ "$key_fingerprint" = "__RIVOREO_KEY_FINGERPRINT__" ]; then
  die "bootstrap was not rendered with a Rivoreo signing key fingerprint"
fi

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

report_decky_loader_status() {
  local plugin_loader=/home/deck/homebrew/services/PluginLoader
  local plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-ec

  if [ -x "$plugin_loader" ]; then
    echo "Decky Loader detected. Charge Limit plugin files are installed at $plugin_dir."
    echo "If the panel is not visible, restart Steam or Decky Loader."
  else
    echo "Decky Loader not detected. Backend service and CLI are installed." >&2
    echo "Steam UI Charge Limit panel requires Decky Loader; install Decky Loader first, then rerun this bootstrap or reinstall the package." >&2
  fi

  return 0
}

need_command curl
need_command gpg
need_command pacman
need_command pacman-key

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

key_file="$tmpdir/rivoreo.gpg"
curl -fsSL "$repo_base_url/key/rivoreo.gpg" -o "$key_file"

actual_fingerprint="$(
  gpg --batch --with-colons --show-keys "$key_file" \
    | awk -F: '$1 == "fpr" {print $10; exit}'
)"

if [ "$actual_fingerprint" != "$key_fingerprint" ]; then
  die "downloaded Rivoreo key fingerprint $actual_fingerprint does not match $key_fingerprint"
fi

if [ ! -d /etc/pacman.d/gnupg ]; then
  pacman-key --init
fi

pacman-key --add "$key_file"
pacman-key --lsign-key "$key_fingerprint"

if command -v steamos-readonly >/dev/null 2>&1; then
  steamos-readonly disable
fi

install -d -m 0755 /etc/pacman.d
cat > "$repo_conf" <<CONF
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = $repo_base_url/os/\$arch
CONF

if ! grep -Fxq "$include_line" /etc/pacman.conf; then
  printf '\n%s\n' "$include_line" >> /etc/pacman.conf
fi

pacman -Sy
pacman -S --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld steamos-intel-handheld-mangoapp
report_decky_loader_status || true

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl enable --now steamos-intel-handheld-power-control.service || true
fi

echo "rivoreo-steamos repository configured and steamos-intel-handheld installed."
