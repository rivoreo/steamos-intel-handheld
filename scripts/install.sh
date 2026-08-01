#!/usr/bin/env bash
# One-line installer, run on the handheld itself:
#
#   curl -fsSL https://raw.githubusercontent.com/rivoreo/steamos-intel-handheld/main/scripts/install.sh | sudo bash
#
# Downloads a source snapshot and installs it. This path needs no signing key
# and no package repository, so it works today; once the signed pacman
# repository is published, prefer that, because it brings updates with it.
set -euo pipefail

REPO="${STEAMOS_INTEL_HANDHELD_REPO:-rivoreo/steamos-intel-handheld}"
REF="${STEAMOS_INTEL_HANDHELD_REF:-main}"
TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF"

die() {
  echo "error: $*" >&2
  exit 2
}

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  die "run as root, for example: curl -fsSL <url> | sudo bash"
fi

for required in curl tar systemctl python3; do
  command -v "$required" >/dev/null 2>&1 || die "missing required command: $required"
done

# Installing a power manager for the wrong hardware is worse than not
# installing one, so refuse rather than guess.
if [ "${STEAMOS_INTEL_HANDHELD_FORCE:-0}" != "1" ]; then
  vendor="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)"
  product="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"
  case "$vendor" in
    *"Micro-Star"*) ;;
    *)
      die "this targets MSI Intel handhelds; found '$vendor $product'. Set STEAMOS_INTEL_HANDHELD_FORCE=1 to override." ;;
  esac

  if [ ! -d /sys/class/powercap/intel-rapl:0 ]; then
    die "no Intel RAPL powercap interface found; this is not an Intel handheld"
  fi
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

echo "Downloading $REPO@$REF ..."
curl -fsSL "$TARBALL" -o "$tmpdir/source.tar.gz"
tar -C "$tmpdir" -xzf "$tmpdir/source.tar.gz"

source_dir="$(find "$tmpdir" -maxdepth 1 -type d -name 'steamos-intel-handheld-*' | head -n 1)"
[ -n "$source_dir" ] || die "downloaded archive did not contain a source tree"

echo "Installing ..."
bash "$source_dir/scripts/install-payload.sh" "$source_dir"

cat <<'DONE'

Done. Open the Steam quick access menu (the button with three dots), pick the
Decky plug icon, and you will find Game Power and Charge Limit there.

If the panels are missing, Decky Loader is not installed yet. Install it, then
run this again.
DONE
