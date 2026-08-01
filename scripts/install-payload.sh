#!/usr/bin/env bash
# Install steamos-intel-handheld from an unpacked source tree that is already
# present on this machine. Runs as root on the handheld itself.
#
# Both install paths end up here: scripts/install.sh downloads a source tree and
# runs this locally, and scripts/install-on-device.sh ships a source tree over
# SSH and runs this on the far end. Keeping one copy of the steps means the
# one-line install and the developer install cannot drift apart.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <unpacked-source-dir>" >&2
  exit 2
fi

src="$1"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "error: run as root" >&2
  exit 2
fi

for required in src/steamos_intel_handheld data/restore/manifest.toml; do
  if [ ! -e "$src/$required" ]; then
    echo "error: $src does not look like a steamos-intel-handheld source tree (missing $required)" >&2
    exit 2
  fi
done

install -d -m 0755 /opt/steamos-intel-handheld/bin
rm -rf /opt/steamos-intel-handheld/src
cp -R "$src/src" /opt/steamos-intel-handheld/src

write_wrapper() {
  local name="$1" module="$2"
  cat >"/opt/steamos-intel-handheld/bin/$name" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/opt/steamos-intel-handheld/src
exec /usr/bin/python3 -m steamos_intel_handheld.$module "\$@"
WRAPPER
  chmod 0755 "/opt/steamos-intel-handheld/bin/$name"
}

write_wrapper steamos-intel-handheld-power-control power_control
write_wrapper steamos-intel-handheld-ec-control ec_charge_control
write_wrapper steamos-intel-handheld-restore-etc restore_etc
write_wrapper steamos-intel-handheld-game-power game_power
write_wrapper steamos-intel-handheld-game-power-profile game_power_profile
write_wrapper steamos-intel-handheld-game-power-control game_power_control

# Paths this project used before it settled on /opt/steamos-intel-handheld.
rm -f /opt/steamos-intel-handheld/bin/steamos-intel-handheld-steamos-manager-remote
rm -f /opt/rivoreo/bin/steamos-intel-handheld-power-control
rm -f /opt/rivoreo/bin/steamos-intel-handheld-ec-control
rm -f /opt/rivoreo/bin/steamos-intel-handheld-restore-etc
rm -f /opt/rivoreo/bin/steamos-intel-handheld-game-power
rm -f /opt/rivoreo/bin/steamos-intel-handheld-game-power-control
rm -rf /opt/rivoreo/steamos-intel-handheld
rmdir --ignore-fail-on-non-empty /opt/rivoreo/bin /opt/rivoreo 2>/dev/null || true
rm -f /etc/rivoreo/bin/steamos-intel-handheld-power-control
rm -f /etc/rivoreo/bin/steamos-intel-handheld-ec-control
rm -rf /etc/rivoreo/steamos-intel-handheld

report_decky_loader_status() {
  local plugin_loader=/home/deck/homebrew/services/PluginLoader
  local charge_plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-ec
  local game_power_plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-game-power

  if [ -x "$plugin_loader" ]; then
    echo "Decky Loader detected. Charge Limit plugin files are installed at $charge_plugin_dir."
    echo "Game Power plugin files are installed at $game_power_plugin_dir."
    echo "If the panel is not visible, restart Steam or Decky Loader."
  else
    echo "Decky Loader not detected. Backend service and CLI are installed." >&2
    echo "The Steam panels need Decky Loader; install it, then run this installer again." >&2
  fi

  return 0
}

restart_user_steamos_manager_without_provider() {
  local uid runtime_dir
  uid="$(id -u deck 2>/dev/null || true)"
  if [ -z "$uid" ]; then
    return 0
  fi

  runtime_dir="/run/user/$uid"
  if [ ! -S "$runtime_dir/bus" ]; then
    return 0
  fi

  timeout 45 runuser -u deck -- env \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
    systemctl --user restart steamos-manager.service || true
}

install_decky_plugin() {
  local plugin="$1"
  local plugin_src="$src/decky/$plugin"
  local plugin_dst="/home/deck/homebrew/plugins/$plugin"

  install -d -m 0755 "$plugin_dst/dist"
  install -m 0644 "$plugin_src/plugin.json" "$plugin_dst/plugin.json"
  install -m 0644 "$plugin_src/package.json" "$plugin_dst/package.json"
  install -m 0644 "$plugin_src/main.py" "$plugin_dst/main.py"
  install -m 0644 "$plugin_src/dist/index.js" "$plugin_dst/dist/index.js"
  install -m 0644 "$plugin_src/README.md" "$plugin_dst/README.md"
}

install_decky_plugin steamos-intel-handheld-ec
install_decky_plugin steamos-intel-handheld-game-power
report_decky_loader_status || true

artifact_root=/opt/steamos-intel-handheld/share/etc-artifacts
install -d -m 0755 \
  "$artifact_root/dbus-1/system.d" \
  "$artifact_root/steamos-manager/remotes.d" \
  "$artifact_root/steamos-manager/devices" \
  "$artifact_root/systemd/system" \
  "$artifact_root/systemd/user/gamescope-session.service.d" \
  "$artifact_root/systemd/user" \
  "$artifact_root/gamescope/scripts/00-steamos-intel-handheld/displays" \
  "$artifact_root/NetworkManager/dispatcher.d" \
  /etc/dbus-1/system.d \
  /etc/systemd/system \
  /etc/systemd/user/gamescope-session.service.d \
  /etc/systemd/user/gamescope-session.service.wants \
  /etc/gamescope/scripts/00-steamos-intel-handheld/displays \
  /etc/NetworkManager/dispatcher.d

# Each managed file lands in its live location and in the artifact tree, which
# is what the restore service replays after a SteamOS update rotates /etc.
install_managed() {
  local mode="$1" relative="$2" live="$3"
  install -m "$mode" "$src/data/$relative" "$live"
  install -m "$mode" "$src/data/$relative" "$artifact_root/$relative"
}

install -m 0644 "$src/data/restore/manifest.toml" "$artifact_root/manifest.toml"
install_managed 0644 dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf \
  /etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf
install -m 0644 "$src/data/steamos-manager/remotes.d/99-rivoreo-power-control.toml" \
  "$artifact_root/steamos-manager/remotes.d/99-rivoreo-power-control.toml"
# Device profile for steamos-manager. Only the artifact copy is installed here;
# the restore service places the live copy, because writing it means unlocking
# the read-only system partition and that belongs in one place.
install -m 0644 "$src/data/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml" \
  "$artifact_root/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml"
install_managed 0644 systemd/steamos-intel-handheld-restore.service \
  /etc/systemd/system/steamos-intel-handheld-restore.service
install_managed 0644 systemd/steamos-intel-handheld-power-control.service \
  /etc/systemd/system/steamos-intel-handheld-power-control.service
install_managed 0644 systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf \
  /etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf
install_managed 0644 systemd/user/steamos-intel-handheld-gamescope-display.service \
  /etc/systemd/user/steamos-intel-handheld-gamescope-display.service
install_managed 0644 gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua \
  /etc/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua
install_managed 0755 NetworkManager/dispatcher.d/90-rncn-steamdeck-wg \
  /etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg

# The systemd artifact tree carries the restore service in its own subdirectory.
install -d -m 0755 "$artifact_root/systemd/system"
install -m 0644 "$src/data/systemd/steamos-intel-handheld-restore.service" \
  "$artifact_root/systemd/system/steamos-intel-handheld-restore.service"
install -m 0644 "$src/data/systemd/steamos-intel-handheld-power-control.service" \
  "$artifact_root/systemd/system/steamos-intel-handheld-power-control.service"

rm -f /etc/systemd/system/steamos-intel-handheld-steamos-manager-remote.service
rm -f "$artifact_root/systemd/system/steamos-intel-handheld-steamos-manager-remote.service"
ln -sfn ../steamos-intel-handheld-gamescope-display.service \
  /etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-gamescope-display.service

install -d -m 0755 /var/lib/steamos-intel-handheld
if [ ! -e /var/lib/steamos-intel-handheld/tdp_w ] && [ -e /var/lib/rivoreo-steamos-manager-power-control/tdp_w ]; then
  cp /var/lib/rivoreo-steamos-manager-power-control/tdp_w /var/lib/steamos-intel-handheld/tdp_w
fi

systemctl stop rivoreo-steamos-manager-power-control.service 2>/dev/null || true
systemctl disable rivoreo-steamos-manager-power-control.service 2>/dev/null || true
systemctl stop steamos-intel-handheld-power-control.service 2>/dev/null || true
systemctl stop steamos-intel-handheld-steamos-manager-remote.service 2>/dev/null || true
systemctl disable steamos-intel-handheld-steamos-manager-remote.service 2>/dev/null || true
busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig || true
systemctl daemon-reload
systemctl enable --now steamos-intel-handheld-restore.service
/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply
restart_user_steamos_manager_without_provider
systemctl enable --now steamos-intel-handheld-power-control.service

echo "steamos-intel-handheld installed."
