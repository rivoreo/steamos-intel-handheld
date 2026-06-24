#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'MSG'
The Rivoreo SteamOS package scaffold is online.
The public pacman repository is not activated yet because the signed package database has not been published to Pages.

Follow repository activation progress here:
  https://github.com/rivoreo/steamos-intel-handheld
MSG

exit 1
