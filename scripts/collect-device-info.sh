#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 root@steamdeck-host" >&2
  exit 2
fi

target="$1"

ssh "$target" 'set -euo pipefail
echo "--- os ---"
cat /etc/os-release
echo "--- kernel ---"
uname -a
echo "--- dmi ---"
for f in sys_vendor product_name product_version board_name board_version bios_version; do
  printf "%s=" "$f"
  cat "/sys/class/dmi/id/$f" 2>/dev/null || true
done
echo "--- rapl ---"
find /sys/class/powercap -maxdepth 2 -type f -name "constraint_*_power_limit_uw" -print -exec cat {} \;
echo "--- steamos manager ---"
pacman -Q steamos-manager 2>/dev/null || true
'
