#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON:-python3}"
pkgver="$("$python_bin" - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
PY
)"
release_tag="${RELEASE_TAG:-v$pkgver}"

if [[ ! "$release_tag" =~ ^v[0-9]+[.][0-9]+[.][0-9]+([-.][A-Za-z0-9._]+)?$ ]]; then
  echo "RELEASE_TAG must be a vX.Y.Z tag, got: $release_tag" >&2
  exit 2
fi

release_tag_pattern="^v${pkgver//./[.]}([-.][A-Za-z0-9._]+)?$"
if [[ ! "$release_tag" =~ $release_tag_pattern ]]; then
  echo "Tag $release_tag does not match pyproject version $pkgver" >&2
  exit 2
fi

: "${ARCH_REPO_GPG_KEY_ID:?ARCH_REPO_GPG_KEY_ID is required}"
export GPGKEY="${GPGKEY:-$ARCH_REPO_GPG_KEY_ID}"

package_out="${PACKAGE_OUT:-$repo_root/.cache/arch-release/packages}"
repo_out="${REPO_OUT:-$repo_root/.cache/arch-release/public/rivoreo-steamos/os/x86_64}"
key_out="${KEY_OUT:-$repo_root/site/rivoreo-steamos/key}"
src_archive="$repo_root/packaging/arch/steamos-intel-handheld-$pkgver.tar.gz"
mangoapp_pkgdir="$repo_root/packaging/arch/steamos-intel-handheld-mangoapp"

rm -rf "$package_out" "$repo_out"
mkdir -p "$package_out" "$repo_out" "$key_out"

sed -i "s/^pkgver=.*/pkgver=$pkgver/" packaging/arch/PKGBUILD
sed -i "s/^pkgver=.*/pkgver=$pkgver/" packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD
git archive --format=tar --prefix="steamos-intel-handheld-$pkgver/" "$release_tag" \
  | gzip -n > "$src_archive"

gpg --batch --export "$ARCH_REPO_GPG_KEY_ID" > "$key_out/rivoreo.gpg"
fingerprint="$(
  gpg --batch --with-colons --show-keys "$key_out/rivoreo.gpg" \
    | awk -F: '$1 == "fpr" {print $10; exit}'
)"

if [ "$fingerprint" != "$ARCH_REPO_GPG_KEY_ID" ]; then
  echo "Imported key fingerprint $fingerprint does not match ARCH_REPO_GPG_KEY_ID" >&2
  exit 2
fi

printf '%s\n' "$fingerprint" > "$key_out/fingerprint.txt"
install -Dm0644 "$key_out/rivoreo.gpg" packaging/arch/rivoreo-keyring/rivoreo.gpg
printf '%s:4:\n' "$fingerprint" > packaging/arch/rivoreo-keyring/rivoreo-trusted
: > packaging/arch/rivoreo-keyring/rivoreo-revoked

build_pkg() {
  local pkgdir="$1"
  (
    cd "$pkgdir"
    if grep -q '0000000000000000000000000000000000000000000000000000000000000000' PKGBUILD; then
      updpkgsums
    fi
    PKGDEST="$package_out" makepkg --cleanbuild --syncdeps --noconfirm --needed --sign
  )
}

prepare_mangoapp_package_inputs() {
  local mangoapp_binary="${MANGOAPP_BINARY:-$repo_root/.cache/steamos-qemu/mangoapp}"

  if [ ! -x "$mangoapp_binary" ]; then
    echo "MANGOAPP_BINARY must point to the patched executable mangoapp binary, got: $mangoapp_binary" >&2
    exit 2
  fi

  install -Dm0755 "$mangoapp_binary" "$mangoapp_pkgdir/mangoapp"
  install -Dm0644 \
    data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf \
    "$mangoapp_pkgdir/10-rivoreo-mangoapp.conf"
  install -Dm0644 external/MangoHud/LICENSE "$mangoapp_pkgdir/MangoHud-LICENSE"
}

prepare_mangoapp_package_inputs
build_pkg packaging/arch
build_pkg packaging/arch/steamos-intel-handheld-mangoapp
build_pkg packaging/arch/rivoreo-keyring
build_pkg packaging/arch/rivoreo-steamos-repo

cp "$package_out"/*.pkg.tar.zst "$package_out"/*.pkg.tar.zst.sig "$repo_out"/
repo_db="$repo_out/rivoreo-steamos.db.tar.zst"
repo_files="$repo_out/rivoreo-steamos.files.tar.zst"
repo-add --sign --verify "$repo_db" "$repo_out"/*.pkg.tar.zst
rm -f \
  "$repo_out/rivoreo-steamos.db" \
  "$repo_out/rivoreo-steamos.db.sig" \
  "$repo_out/rivoreo-steamos.files" \
  "$repo_out/rivoreo-steamos.files.sig"
cp "$repo_db" "$repo_out/rivoreo-steamos.db"
cp "$repo_db.sig" "$repo_out/rivoreo-steamos.db.sig"
cp "$repo_files" "$repo_out/rivoreo-steamos.files"
cp "$repo_files.sig" "$repo_out/rivoreo-steamos.files.sig"
test ! -L "$repo_out/rivoreo-steamos.db"
test ! -L "$repo_out/rivoreo-steamos.db.sig"
test ! -L "$repo_out/rivoreo-steamos.files"
test ! -L "$repo_out/rivoreo-steamos.files.sig"
