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
  rm -f /opt/rivoreo/bin/steamos-intel-handheld-power-control
  rm -f /opt/rivoreo/bin/steamos-intel-handheld-ec-control
  rm -rf /opt/rivoreo/steamos-intel-handheld
  rmdir --ignore-fail-on-non-empty /opt/rivoreo/bin /opt/rivoreo 2>/dev/null || true
  rm -f /etc/rivoreo/bin/steamos-intel-handheld-power-control
  rm -f /etc/rivoreo/bin/steamos-intel-handheld-ec-control
  rm -rf /etc/rivoreo/steamos-intel-handheld

  decky_src='$remote_tmp/decky/steamos-intel-handheld-ec'
  decky_dst=/home/deck/homebrew/plugins/steamos-intel-handheld-ec
  install -d -m 0755 \"\$decky_dst/dist\"
  install -m 0644 \"\$decky_src/plugin.json\" \"\$decky_dst/plugin.json\"
  install -m 0644 \"\$decky_src/main.py\" \"\$decky_dst/main.py\"
  install -m 0644 \"\$decky_src/dist/index.js\" \"\$decky_dst/dist/index.js\"
  install -m 0644 \"\$decky_src/README.md\" \"\$decky_dst/README.md\"

  install -d -m 0755 /etc/dbus-1/system.d /etc/steamos-manager/remotes.d /etc/systemd/system
  install -m 0644 '$remote_tmp/data/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf' /etc/dbus-1/system.d/org.rivoreo.SteamOSManager.PowerControl.conf
  install -m 0644 '$remote_tmp/data/steamos-manager/remotes.d/99-rivoreo-power-control.toml' /etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml
  install -m 0644 '$remote_tmp/data/systemd/steamos-intel-handheld-power-control.service' /etc/systemd/system/steamos-intel-handheld-power-control.service

  install -d -m 0755 /var/lib/steamos-intel-handheld
  if [ ! -e /var/lib/steamos-intel-handheld/tdp_w ] && [ -e /var/lib/rivoreo-steamos-manager-power-control/tdp_w ]; then
    cp /var/lib/rivoreo-steamos-manager-power-control/tdp_w /var/lib/steamos-intel-handheld/tdp_w
  fi

  systemctl stop rivoreo-steamos-manager-power-control.service 2>/dev/null || true
  systemctl disable rivoreo-steamos-manager-power-control.service 2>/dev/null || true
  systemctl stop steamos-intel-handheld-power-control.service 2>/dev/null || true
  busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig || true
  systemctl daemon-reload

  if [ -S /run/user/1000/bus ]; then
    runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user restart steamos-manager || true
  fi

  systemctl enable --now steamos-intel-handheld-power-control.service
  rm -rf '$remote_tmp'
"

echo "Installed steamos-intel-handheld power control on $target"
