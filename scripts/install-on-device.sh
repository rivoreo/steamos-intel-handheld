#!/usr/bin/env bash
# Developer install: push the working tree to a handheld over SSH and install
# it there. Run this on your development machine, not on the handheld.
#
#   scripts/install-on-device.sh root@10.100.0.19
#
# End users want scripts/install.sh instead, which runs on the handheld and
# downloads a published snapshot rather than shipping local edits.
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
  scripts/install-payload.sh \
  pyproject.toml \
  README.md \
  decky/steamos-intel-handheld-ec/README.md \
  decky/steamos-intel-handheld-ec/dist/index.js \
  decky/steamos-intel-handheld-ec/main.py \
  decky/steamos-intel-handheld-ec/package.json \
  decky/steamos-intel-handheld-ec/plugin.json \
  decky/steamos-intel-handheld-game-power/README.md \
  decky/steamos-intel-handheld-game-power/dist/index.js \
  decky/steamos-intel-handheld-game-power/main.py \
  decky/steamos-intel-handheld-game-power/package.json \
  decky/steamos-intel-handheld-game-power/plugin.json \
  | ssh "$target" "
  set -euo pipefail
  rm -rf '$remote_tmp'
  mkdir -p '$remote_tmp'
  tar -C '$remote_tmp' -xzf -
"

# The install steps live in one place so this path and the one-line installer
# cannot drift apart.
ssh "$target" "
  set -euo pipefail
  bash '$remote_tmp/scripts/install-payload.sh' '$remote_tmp'
  rm -rf '$remote_tmp'
"

echo "Installed steamos-intel-handheld power control on $target"
