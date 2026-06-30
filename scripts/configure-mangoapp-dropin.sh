#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 enable root@steamdeck-host /path/to/mangoapp" >&2
  echo "       $0 disable root@steamdeck-host" >&2
  echo "Actions: enable|disable" >&2
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  usage
  exit 2
fi

action="$1"
target="$2"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_tmp="/tmp/steamos-intel-handheld-mangoapp.$$"
remote_mangoapp="/opt/steamos-intel-handheld/bin/mangoapp"
remote_dropin="/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf"
remote_dropin_dir="$(dirname "$remote_dropin")"
remote_artifact_root="/opt/steamos-intel-handheld/share/etc-artifacts"
remote_manifest_dir="/opt/steamos-intel-handheld/share/etc-artifacts/manifest.d"
export COPYFILE_DISABLE=1

reload_and_restart='
  if [ -S /run/user/1000/bus ]; then
    env_common="XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
    runuser -u deck -- env $env_common systemctl --user daemon-reload
    runuser -u deck -- env $env_common systemctl --user restart gamescope-mangoapp.service
  else
    echo "deck user bus is not active; restart the gamescope session later" >&2
  fi
'

if [ "$action" = "enable" ]; then
  if [ "$#" -ne 3 ]; then
    usage
    exit 2
  fi

  local_mangoapp="$3"
  if [ ! -x "$local_mangoapp" ]; then
    echo "mangoapp binary is missing or not executable: $local_mangoapp" >&2
    exit 1
  fi

  ssh "$target" "
    set -euo pipefail
    rm -rf '$remote_tmp'
    mkdir -p '$remote_tmp'
  "

  scp "$local_mangoapp" "$target:$remote_tmp/mangoapp"
  tar --no-xattrs -C "$repo_root" -czf - \
    data/restore/manifest.d/10-mangoapp.toml \
    data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf |
    ssh "$target" "
      set -euo pipefail
      tar -C '$remote_tmp' -xzf -
    "

  ssh "$target" "
    set -euo pipefail
    install -d -m 0755 /opt/steamos-intel-handheld/bin '$remote_dropin_dir' '$remote_manifest_dir' '$remote_artifact_root/systemd/user/gamescope-mangoapp.service.d'
    install -m 0755 '$remote_tmp/mangoapp' '$remote_mangoapp'
    install -m 0644 '$remote_tmp/data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf' '$remote_dropin'
    install -m 0644 '$remote_tmp/data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf' '$remote_artifact_root/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf'
    install -m 0644 '$remote_tmp/data/restore/manifest.d/10-mangoapp.toml' '$remote_manifest_dir/10-mangoapp.toml'
    rm -f /opt/rivoreo/bin/mangoapp
    rm -f /etc/rivoreo/bin/mangoapp
    rm -rf '$remote_tmp'
    $reload_and_restart
  "
elif [ "$action" = "disable" ]; then
  if [ "$#" -ne 2 ]; then
    usage
    exit 2
  fi

  ssh "$target" "
    set -euo pipefail
    rm -f '$remote_dropin' '$remote_mangoapp'
    rmdir --ignore-fail-on-non-empty '$remote_dropin_dir' 2>/dev/null || true
    $reload_and_restart
  "
else
  usage
  exit 2
fi

echo "mangoapp drop-in $action complete on $target"
