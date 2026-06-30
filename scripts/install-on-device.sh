#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 root@steamdeck-host" >&2
  exit 2
fi

target="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_tmp="/tmp/steamos-intel-handheld-install.$$"

tar -C "$repo_root" -czf - \
  src \
  data \
  pyproject.toml \
  README.md \
  decky/steamos-intel-handheld-ec/README.md \
  decky/steamos-intel-handheld-ec/dist/index.js \
  decky/steamos-intel-handheld-ec/main.py \
  decky/steamos-intel-handheld-ec/plugin.json \
  | ssh "$target" "
  set -euo pipefail
  rm -rf '$remote_tmp'
  mkdir -p '$remote_tmp'
  tar -C '$remote_tmp' -xzf -
"

ssh "$target" "
  set -euo pipefail
  install -d -m 0755 /opt/steamos-intel-handheld/bin
  rm -rf /opt/steamos-intel-handheld/src
  cp -R '$remote_tmp/src' /opt/steamos-intel-handheld/src

  cat >/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/opt/steamos-intel-handheld/src
exec /usr/bin/python3 -m steamos_intel_handheld.power_control \"\$@\"
WRAPPER
  chmod 0755 /opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control
  cat >/opt/steamos-intel-handheld/bin/steamos-intel-handheld-ec-control <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/opt/steamos-intel-handheld/src
exec /usr/bin/python3 -m steamos_intel_handheld.ec_charge_control \"\$@\"
WRAPPER
  chmod 0755 /opt/steamos-intel-handheld/bin/steamos-intel-handheld-ec-control
  cat >/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH=/opt/steamos-intel-handheld/src
exec /usr/bin/python3 -m steamos_intel_handheld.restore_etc \"\$@\"
WRAPPER
  chmod 0755 /opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc
  rm -f /opt/rivoreo/bin/steamos-intel-handheld-power-control
  rm -f /opt/rivoreo/bin/steamos-intel-handheld-ec-control
  rm -f /opt/rivoreo/bin/steamos-intel-handheld-restore-etc
  rm -rf /opt/rivoreo/steamos-intel-handheld
  rmdir --ignore-fail-on-non-empty /opt/rivoreo/bin /opt/rivoreo 2>/dev/null || true
  rm -f /etc/rivoreo/bin/steamos-intel-handheld-power-control
  rm -f /etc/rivoreo/bin/steamos-intel-handheld-ec-control
  rm -rf /etc/rivoreo/steamos-intel-handheld

  report_decky_loader_status() {
    plugin_loader=/home/deck/homebrew/services/PluginLoader
    plugin_dir=/home/deck/homebrew/plugins/steamos-intel-handheld-ec

    if [ -x \"\$plugin_loader\" ]; then
      echo \"Decky Loader detected. Charge Limit plugin files are installed at \$plugin_dir.\"
      echo \"If the panel is not visible, restart Steam or Decky Loader.\"
    else
      echo \"Decky Loader not detected. Backend service and CLI are installed.\" >&2
      echo \"Steam UI Charge Limit panel requires Decky Loader; install Decky Loader first, then rerun scripts/install-on-device.sh.\" >&2
    fi

    return 0
  }

  decky_src='$remote_tmp/decky/steamos-intel-handheld-ec'
  decky_dst=/home/deck/homebrew/plugins/steamos-intel-handheld-ec
  install -d -m 0755 \"\$decky_dst/dist\"
  install -m 0644 \"\$decky_src/plugin.json\" \"\$decky_dst/plugin.json\"
  install -m 0644 \"\$decky_src/main.py\" \"\$decky_dst/main.py\"
  install -m 0644 \"\$decky_src/dist/index.js\" \"\$decky_dst/dist/index.js\"
  install -m 0644 \"\$decky_src/README.md\" \"\$decky_dst/README.md\"
  report_decky_loader_status || true

  artifact_root=/opt/steamos-intel-handheld/share/etc-artifacts
  install -d -m 0755 \
    \"\$artifact_root/dbus-1/system.d\" \
    \"\$artifact_root/steamos-manager/remotes.d\" \
    \"\$artifact_root/systemd/system\" \
    \"\$artifact_root/systemd/user/gamescope-session.service.d\" \
    \"\$artifact_root/systemd/user\" \
    \"\$artifact_root/gamescope/scripts/00-steamos-intel-handheld/displays\" \
    \"\$artifact_root/NetworkManager/dispatcher.d\" \
    /etc/dbus-1/system.d \
    /etc/steamos-manager/remotes.d \
    /etc/systemd/system \
    /etc/systemd/user/gamescope-session.service.d \
    /etc/systemd/user/gamescope-session.service.wants \
    /etc/gamescope/scripts/00-steamos-intel-handheld/displays \
    /etc/NetworkManager/dispatcher.d
  install -m 0644 '$remote_tmp/data/restore/manifest.toml' /opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml
  install -m 0644 '$remote_tmp/data/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf' /etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf
  install -m 0644 '$remote_tmp/data/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf' \"\$artifact_root/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf\"
  install -m 0644 '$remote_tmp/data/steamos-manager/remotes.d/99-rivoreo-power-control.toml' /etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml
  install -m 0644 '$remote_tmp/data/steamos-manager/remotes.d/99-rivoreo-power-control.toml' \"\$artifact_root/steamos-manager/remotes.d/99-rivoreo-power-control.toml\"
  install -m 0644 '$remote_tmp/data/systemd/steamos-intel-handheld-restore.service' /etc/systemd/system/steamos-intel-handheld-restore.service
  install -m 0644 '$remote_tmp/data/systemd/steamos-intel-handheld-restore.service' \"\$artifact_root/systemd/system/steamos-intel-handheld-restore.service\"
  install -m 0644 '$remote_tmp/data/systemd/steamos-intel-handheld-power-control.service' /etc/systemd/system/steamos-intel-handheld-power-control.service
  install -m 0644 '$remote_tmp/data/systemd/steamos-intel-handheld-power-control.service' \"\$artifact_root/systemd/system/steamos-intel-handheld-power-control.service\"
  install -m 0644 '$remote_tmp/data/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf' /etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf
  install -m 0644 '$remote_tmp/data/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf' \"\$artifact_root/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf\"
  install -m 0644 '$remote_tmp/data/systemd/user/steamos-intel-handheld-gamescope-display.service' /etc/systemd/user/steamos-intel-handheld-gamescope-display.service
  install -m 0644 '$remote_tmp/data/systemd/user/steamos-intel-handheld-gamescope-display.service' \"\$artifact_root/systemd/user/steamos-intel-handheld-gamescope-display.service\"
  ln -sfn ../steamos-intel-handheld-gamescope-display.service /etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-gamescope-display.service
  install -m 0644 '$remote_tmp/data/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua' /etc/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua
  install -m 0644 '$remote_tmp/data/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua' \"\$artifact_root/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua\"
  install -m 0755 '$remote_tmp/data/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg' /etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg
  install -m 0755 '$remote_tmp/data/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg' \"\$artifact_root/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg\"

  install -d -m 0755 /var/lib/steamos-intel-handheld
  if [ ! -e /var/lib/steamos-intel-handheld/tdp_w ] && [ -e /var/lib/rivoreo-steamos-manager-power-control/tdp_w ]; then
    cp /var/lib/rivoreo-steamos-manager-power-control/tdp_w /var/lib/steamos-intel-handheld/tdp_w
  fi

  systemctl stop rivoreo-steamos-manager-power-control.service 2>/dev/null || true
  systemctl disable rivoreo-steamos-manager-power-control.service 2>/dev/null || true
  systemctl stop steamos-intel-handheld-power-control.service 2>/dev/null || true
  busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig || true
  systemctl daemon-reload
  systemctl enable --now steamos-intel-handheld-restore.service
  /opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply

  if [ -S /run/user/1000/bus ]; then
    runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user restart steamos-manager || true
  fi

  systemctl enable --now steamos-intel-handheld-power-control.service
  rm -rf '$remote_tmp'
"

echo "Installed steamos-intel-handheld power control on $target"
