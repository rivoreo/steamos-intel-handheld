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
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/plugin.json" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/main.py" >/dev/null
tar -tf "$main_pkg" | grep -Fx "home/deck/homebrew/plugins/steamos-intel-handheld-ec/dist/index.js" >/dev/null
tar -tf "$main_pkg" | grep -Fx ".INSTALL" >/dev/null
tar -xOf "$main_pkg" .INSTALL | grep -F "Decky Loader not detected" >/dev/null

echo "GitLab pacman artifact dry run passed: $repo"
