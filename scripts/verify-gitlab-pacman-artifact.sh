#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/gitlab/artifact/root" >&2
  exit 2
fi

artifact_root="$1"
repo="$artifact_root/rivoreo-steamos/os/x86_64"

if [ ! -d "$repo" ]; then
  if [ -d "$artifact_root/.cache/pacman-repo/public/rivoreo-steamos/os/x86_64" ]; then
    repo="$artifact_root/.cache/pacman-repo/public/rivoreo-steamos/os/x86_64"
  else
    echo "Could not find rivoreo-steamos/os/x86_64 under $artifact_root" >&2
    exit 2
  fi
fi

test -s "$repo/rivoreo-steamos.db"
test -s "$repo/rivoreo-steamos.files"
test -s "$repo/rivoreo-steamos.db.tar.zst"
test -s "$repo/rivoreo-steamos.files.tar.zst"
test ! -L "$repo/rivoreo-steamos.db"
test ! -L "$repo/rivoreo-steamos.files"
cmp -s "$repo/rivoreo-steamos.db" "$repo/rivoreo-steamos.db.tar.zst"
cmp -s "$repo/rivoreo-steamos.files" "$repo/rivoreo-steamos.files.tar.zst"

main_pkg=""
for pkg in "$repo"/*.pkg.tar.zst; do
  if tar -xOf "$pkg" .PKGINFO | grep -Fx "pkgname = steamos-intel-handheld" >/dev/null; then
    if [ -n "$main_pkg" ]; then
      echo "Found more than one steamos-intel-handheld main package" >&2
      exit 2
    fi
    main_pkg="$pkg"
  fi
done

if [ -z "$main_pkg" ]; then
  echo "Expected one steamos-intel-handheld main package, found none" >&2
  exit 2
fi

tar -xOf "$main_pkg" .PKGINFO | grep -Fx "pkgname = steamos-intel-handheld" >/dev/null
tar -tf "$main_pkg" | grep -Fx "usr/bin/steamos-intel-handheld-power-control" >/dev/null
tar -tf "$main_pkg" | grep -Fx "usr/bin/steamos-intel-handheld-ec-control" >/dev/null
tar -tf "$main_pkg" | grep -Fx "usr/bin/steamos-intel-handheld-restore-etc" >/dev/null
tar -tf "$main_pkg" | grep -Fx "usr/lib/systemd/system/steamos-intel-handheld-restore.service" >/dev/null
tar -tf "$main_pkg" | grep -Fx "etc/systemd/system/steamos-intel-handheld-restore.service" >/dev/null
tar -tf "$main_pkg" | grep -Fx "etc/systemd/system/steamos-intel-handheld-power-control.service" >/dev/null
tar -tf "$main_pkg" | grep -Fx "opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml" >/dev/null
tar -tf "$main_pkg" | grep -Fx "opt/steamos-intel-handheld/share/etc-artifacts/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg" >/dev/null
tar -tf "$main_pkg" | grep -Fx "opt/steamos-intel-handheld/bin/gamescope" >/dev/null
tar -tf "$main_pkg" | grep -Fx "opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display" >/dev/null
tar -tf "$main_pkg" | grep -Fx "etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf" >/dev/null
tar -tf "$main_pkg" | grep -Fx "etc/systemd/user/steamos-intel-handheld-gamescope-display.service" >/dev/null
tar -tf "$main_pkg" | grep -Fx "etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-gamescope-display.service" >/dev/null
tar -tf "$main_pkg" | grep -Fx "etc/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/plugin.json" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/main.py" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/dist/index.js" >/dev/null
tar -tf "$main_pkg" | grep -Fx ".INSTALL" >/dev/null
tar -xOf "$main_pkg" .INSTALL | grep -F "Decky Loader not detected" >/dev/null
tar -xOf "$main_pkg" .INSTALL | grep -F "gamescope display profile and session hooks are installed" >/dev/null

echo "GitLab pacman artifact dry run passed: $repo"
