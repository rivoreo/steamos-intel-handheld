#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

: "${RIVOREO_KEY_FINGERPRINT:?RIVOREO_KEY_FINGERPRINT is required}"

repo_tree="${REPO_TREE:-$repo_root/.cache/arch-release/public/rivoreo-steamos/os/x86_64}"
site_out="${SITE_OUT:-$repo_root/_site}"
key_dir="${KEY_DIR:-$repo_root/site/rivoreo-steamos/key}"

rm -rf "$site_out"
mkdir -p "$site_out/rivoreo-steamos/os/x86_64" "$site_out/rivoreo-steamos/key"
cp -R site/. "$site_out/"
cp "$key_dir/rivoreo.gpg" "$site_out/rivoreo-steamos/key/rivoreo.gpg"
printf '%s\n' "$RIVOREO_KEY_FINGERPRINT" > "$site_out/rivoreo-steamos/key/fingerprint.txt"
cp "$repo_tree"/* "$site_out/rivoreo-steamos/os/x86_64/"

sed "s/__RIVOREO_KEY_FINGERPRINT__/$RIVOREO_KEY_FINGERPRINT/g" \
  site/rivoreo-steamos/bootstrap.sh > "$site_out/rivoreo-steamos/bootstrap.sh"
chmod 0755 "$site_out/rivoreo-steamos/bootstrap.sh"

test -f "$site_out/index.html"
test -f "$site_out/rivoreo-steamos/bootstrap.sh"
test -f "$site_out/rivoreo-steamos/key/rivoreo.gpg"
test -f "$site_out/rivoreo-steamos/key/fingerprint.txt"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db.sig"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files"
test -f "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files.sig"
test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db"
test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.db.sig"
test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files"
test ! -L "$site_out/rivoreo-steamos/os/x86_64/rivoreo-steamos.files.sig"
