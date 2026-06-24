#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'MSG'
The Rivoreo SteamOS pacman repository endpoint is online, but signed packages
have not been published yet.

Follow packaging progress here:
  https://github.com/rivoreo/steamos-intel-handheld
MSG

exit 1
