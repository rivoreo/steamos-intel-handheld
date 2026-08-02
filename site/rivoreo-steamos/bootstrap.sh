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

# Check the shape, never the placeholder text. The release renderer substitutes
# the placeholder globally, so a guard that names it gets rewritten too and ends
# up comparing the fingerprint against itself - which made every published copy
# of this script exit here before doing anything.
if [ "${#key_fingerprint}" -ne 40 ] ||
  [ -n "$(printf '%s' "$key_fingerprint" | tr -d '0-9A-F')" ]; then
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

# SteamOS ships the directory without a usable keyring inside it, so testing
# for the directory skips the init that is actually needed and the --add below
# then fails with "You do not have sufficient permissions to read the pacman
# keyring". Test whether the keyring works, not whether the path exists.
if ! pacman-key --list-keys >/dev/null 2>&1; then
  pacman-key --init
fi

pacman-key --add "$key_file"
pacman-key --lsign-key "$key_fingerprint"

# Unlock only if it is locked, and put it back the way it was on the way out.
# Leaving a handheld permanently read-write is a lasting change to how the
# machine protects itself, and none of these packages write to /usr anyway - the
# one file that lives there is placed by the restore service, which does its own
# unlock and re-lock around exactly that write.
relock_system_partition=0
if command -v steamos-readonly >/dev/null 2>&1; then
  if [ "$(steamos-readonly status 2>/dev/null)" = "enabled" ]; then
    steamos-readonly disable
    relock_system_partition=1
  fi
fi

relock() {
  if [ "$relock_system_partition" = "1" ]; then
    steamos-readonly enable || true
  fi
}
trap relock EXIT

install -d -m 0755 /etc/pacman.d
cat > "$repo_conf" <<CONF
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = $repo_base_url/os/\$arch
CONF

if ! grep -Fxq "$include_line" /etc/pacman.conf; then
  printf '\n%s\n' "$include_line" >> /etc/pacman.conf
fi

# --noconfirm is not optional here. The documented way to run this is
#   curl -fsSL .../bootstrap.sh | sudo bash
# where bash is reading the script from stdin, so an interactive pacman prompt
# would be answered with whatever the next lines of this file happen to be.
pacman -Sy --noconfirm
# --overwrite is scoped to paths this project installs and nothing else, so a
# machine set up with scripts/install.sh can move to packages without being told
# to uninstall something by hand first. Anything outside these globs still
# conflicts and still aborts the transaction, which is the behaviour that
# matters: pacman must never silently take over a file we do not own.
pacman -S --needed --noconfirm \
  --overwrite '/opt/steamos-intel-handheld/*' \
  --overwrite '/home/deck/homebrew/plugins/steamos-intel-handheld-*/*' \
  --overwrite '/etc/systemd/system/steamos-intel-handheld-*' \
  --overwrite '/etc/systemd/user/steamos-intel-handheld-*' \
  --overwrite '/etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf' \
  --overwrite '/etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-*' \
  --overwrite '/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf' \
  --overwrite '/etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf' \
  --overwrite '/etc/gamescope/scripts/00-steamos-intel-handheld/*' \
  --overwrite '/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg' \
  rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld steamos-intel-handheld-mangoapp
report_decky_loader_status || true

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl enable --now steamos-intel-handheld-power-control.service || true
fi

echo "rivoreo-steamos repository configured and steamos-intel-handheld installed."
