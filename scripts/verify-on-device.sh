#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 root@steamdeck-host [test-watts] [restore-watts]" >&2
  exit 2
fi

target="$1"
test_watts="${2:-28}"
restore_watts="${3:-30}"

ssh "$target" "TEST_WATTS='$test_watts' RESTORE_WATTS='$restore_watts' bash -s" <<'REMOTE'
set -euo pipefail

USER_ENV="XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"

wait_for_service() {
  local service="$1"
  for _ in $(seq 1 90); do
    if systemctl is-active --quiet "$service"; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for $service" >&2
  return 1
}

central_tdp() {
  runuser -u deck -- bash -lc "$USER_ENV busctl --user get-property com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.TdpLimit1 TdpLimit" | awk '{print $2}'
}

remote_tdp() {
  busctl --system get-property org.rivoreo.SteamOSManager.PowerControl /org/rivoreo/SteamOSManager/PowerControl com.steampowered.SteamOSManager1.TdpLimit1 TdpLimit | awk '{print $2}'
}

rapl_pl1_watts() {
  awk '{print int($1 / 1000000)}' /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw
}

assert_equals() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  if [ "$expected" != "$actual" ]; then
    echo "$label expected $expected, got $actual" >&2
    exit 1
  fi
}

wait_for_service steamos-intel-handheld-power-control.service

runuser -u deck -- bash -lc "$USER_ENV systemctl --user is-active --quiet steamos-manager"
runuser -u deck -- bash -lc "$USER_ENV busctl --user get-property com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.RemoteInterface1 RemoteInterfaces" | grep -F "com.steampowered.SteamOSManager1.TdpLimit1"

runuser -u deck -- bash -lc "$USER_ENV steamosctl set-tdp-limit $TEST_WATTS"
sleep 2
assert_equals central "$TEST_WATTS" "$(central_tdp)"
assert_equals remote "$TEST_WATTS" "$(remote_tdp)"
assert_equals rapl-pl1 "$TEST_WATTS" "$(rapl_pl1_watts)"

runuser -u deck -- bash -lc "$USER_ENV steamosctl set-tdp-limit $RESTORE_WATTS"
sleep 2
assert_equals central "$RESTORE_WATTS" "$(central_tdp)"
assert_equals remote "$RESTORE_WATTS" "$(remote_tdp)"
assert_equals rapl-pl1 "$RESTORE_WATTS" "$(rapl_pl1_watts)"

systemctl --failed --no-legend --no-pager | tee /tmp/steamos-intel-handheld-failed-units.txt
if [ -s /tmp/steamos-intel-handheld-failed-units.txt ]; then
  echo "There are failed systemd units" >&2
  exit 1
fi

echo "OK: SteamOS Manager TDP remote works and restored ${RESTORE_WATTS}W"
REMOTE
