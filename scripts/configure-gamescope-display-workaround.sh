#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ] || { [ "$1" != "enable" ] && [ "$1" != "disable" ]; }; then
  echo "Usage: $0 enable|disable root@steamdeck-host" >&2
  exit 2
fi

action="$1"
target="$2"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_tmp="/tmp/steamos-intel-handheld-gamescope.$$"
remote_helper="/opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display"
remote_gamescope_wrapper="/opt/steamos-intel-handheld/bin/gamescope"
remote_service="/etc/systemd/user/steamos-intel-handheld-gamescope-display.service"
remote_native_resolution_dropin="/etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf"
legacy_dropin="/etc/systemd/user/gamescope-session.service.d/10-force-composition.conf"
export COPYFILE_DISABLE=1

if [ "$action" = "enable" ]; then
  tar --no-xattrs -C "$repo_root" -czf - \
    data/bin/gamescope \
    data/bin/steamos-intel-handheld-gamescope-display \
    data/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf \
    data/systemd/user/steamos-intel-handheld-gamescope-display.service |
    ssh "$target" "
      set -euo pipefail
      rm -rf '$remote_tmp'
      mkdir -p '$remote_tmp'
      tar -C '$remote_tmp' -xzf -
    "

  ssh "$target" "
    set -euo pipefail
    install -d -m 0755 /opt/steamos-intel-handheld/bin /etc/systemd/user /etc/systemd/user/gamescope-session.service.d
    install -m 0755 '$remote_tmp/data/bin/gamescope' '$remote_gamescope_wrapper'
    install -m 0755 '$remote_tmp/data/bin/steamos-intel-handheld-gamescope-display' '$remote_helper'
    install -m 0644 '$remote_tmp/data/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf' '$remote_native_resolution_dropin'
    install -m 0644 '$remote_tmp/data/systemd/user/steamos-intel-handheld-gamescope-display.service' '$remote_service'

    rm -f /opt/rivoreo/bin/steamos-intel-handheld-gamescope-display
    rm -f /etc/rivoreo/bin/gamescope /etc/rivoreo/bin/gamescope-force-composition-wrapper /etc/rivoreo/bin/steamos-intel-handheld-gamescope-display '$legacy_dropin'
    rmdir --ignore-fail-on-non-empty /etc/systemd/user/gamescope-session.service.d 2>/dev/null || true
    rm -rf '$remote_tmp'

    if [ -S /run/user/1000/bus ]; then
      env_common='XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus'
      runuser -u deck -- env \$env_common systemctl --user daemon-reload
      runuser -u deck -- env \$env_common systemctl --user disable --now steamos-intel-handheld-gamescope-display.service || true
      runuser -u deck -- env \$env_common systemctl --user enable steamos-intel-handheld-gamescope-display.service
      runuser -u deck -- env \$env_common systemctl --user restart --no-block steamos-intel-handheld-gamescope-display.service
    else
      echo 'deck user bus is not active; reboot or restart the gamescope session later' >&2
    fi
    echo 'native panel gamescope sizing will take effect after restarting the gamescope session or rebooting' >&2
  "
else
  ssh "$target" "
    set -euo pipefail
    if [ -S /run/user/1000/bus ]; then
      env_common='XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus'
      if [ -x '$remote_helper' ]; then
        runuser -u deck -- env \$env_common '$remote_helper' reset || true
      fi
      runuser -u deck -- env \$env_common systemctl --user disable --now steamos-intel-handheld-gamescope-display.service || true
    fi

    rm -f '$remote_service' '$remote_helper' '$remote_gamescope_wrapper' '$remote_native_resolution_dropin'
    rm -f /opt/rivoreo/bin/steamos-intel-handheld-gamescope-display
    rm -f /etc/rivoreo/bin/gamescope /etc/rivoreo/bin/gamescope-force-composition-wrapper /etc/rivoreo/bin/steamos-intel-handheld-gamescope-display '$legacy_dropin'
    rmdir --ignore-fail-on-non-empty /etc/systemd/user/gamescope-session.service.d 2>/dev/null || true

    if [ -S /run/user/1000/bus ]; then
      env_common='XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus'
      runuser -u deck -- env \$env_common systemctl --user daemon-reload
    fi
  "
fi

echo "gamescope display workaround $action complete on $target"
