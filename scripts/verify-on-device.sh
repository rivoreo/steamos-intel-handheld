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

expected_pl1_watts() {
  local watts="$1"
  if [ "$watts" -lt 8 ]; then
    watts=8
  fi
  if [ "$watts" -gt 30 ]; then
    watts=30
  fi
  echo "$watts"
}

expected_pl2_watts() {
  local watts
  watts="$(expected_pl1_watts "$1")"
  local pl2=$((watts + 2))
  if [ "$pl2" -gt 32 ]; then
    pl2=32
  fi
  echo "$pl2"
}

rapl_constraint_watts() {
  local constraint_name="$1"
  local fallback_index="$2"
  local domain="/sys/class/powercap/intel-rapl:0"
  local name_file
  for name_file in "$domain"/constraint_*_name; do
    [ -e "$name_file" ] || continue
    if [ "$(cat "$name_file")" = "$constraint_name" ]; then
      awk '{print int($1 / 1000000)}' "${name_file%_name}_power_limit_uw"
      return 0
    fi
  done
  awk '{print int($1 / 1000000)}' "$domain/constraint_${fallback_index}_power_limit_uw"
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

verify_mangohud_power_sensor() {
  local sensor_label="$1"
  local domain_name="$2"
  local found=0
  local domain
  for domain in /sys/class/powercap/intel-rapl*; do
    [ -d "$domain" ] || continue
    [ "$(cat "$domain/name" 2>/dev/null || true)" = "$domain_name" ] || continue
    local energy_file="$domain/energy_uj"
    [ -e "$energy_file" ] || continue
    found=1

    local before after delta_uj
    before="$(runuser -u deck -- bash -lc "cat '$energy_file'" 2>/dev/null || true)"
    sleep 1
    after="$(runuser -u deck -- bash -lc "cat '$energy_file'" 2>/dev/null || true)"
    case "$before:$after" in
      *[!0-9:]* | :* | *:) continue ;;
    esac

    delta_uj=$((after - before))
    echo "$sensor_label readable: $energy_file delta=${delta_uj}uj"
    return 0
  done

  if [ "$found" -eq 0 ]; then
    echo "$sensor_label expected $domain_name energy_uj but none was found" >&2
  else
    echo "$sensor_label energy_uj exists but is not readable by deck" >&2
  fi
  return 1
}

verify_mangohud_cpu_power_sensor() {
  verify_mangohud_power_sensor "MangoHud CPU power sensor" package-0
}

verify_mangohud_gpu_power_sensor() {
  verify_mangohud_power_sensor "MangoHud GPU power sensor" uncore
}

report_mangohud_gpu_temperature_sensor() {
  local found=0
  local drm_device
  for drm_device in /sys/class/drm/renderD*/device /sys/class/drm/card*/device; do
    [ -d "$drm_device" ] || continue
    local temp_file
    while IFS= read -r temp_file; do
      found=1
      local label_file="${temp_file%_input}_label"
      local label
      label="$(cat "$label_file" 2>/dev/null || true)"
      echo "MangoHud GPU temperature sensor: $temp_file${label:+ label=$label}"
    done < <(find "$drm_device" -maxdepth 4 -path "*/hwmon*/*" -name "temp*_input" -print 2>/dev/null | sort)
  done

  if [ "$found" -eq 0 ]; then
    echo "MangoHud GPU temperature sensor unavailable: no DRM hwmon temp input is exposed"
  fi
}

wait_for_service steamos-intel-handheld-power-control.service
verify_mangohud_cpu_power_sensor
verify_mangohud_gpu_power_sensor
report_mangohud_gpu_temperature_sensor

runuser -u deck -- bash -lc "$USER_ENV systemctl --user is-active --quiet steamos-manager"
runuser -u deck -- bash -lc "$USER_ENV busctl --user get-property com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.RemoteInterface1 RemoteInterfaces" | grep -F "com.steampowered.SteamOSManager1.TdpLimit1"

runuser -u deck -- bash -lc "$USER_ENV steamosctl set-tdp-limit $TEST_WATTS"
sleep 2
assert_equals central "$(expected_pl1_watts "$TEST_WATTS")" "$(central_tdp)"
assert_equals remote "$(expected_pl1_watts "$TEST_WATTS")" "$(remote_tdp)"
assert_equals rapl-pl1 "$(expected_pl1_watts "$TEST_WATTS")" "$(rapl_constraint_watts long_term 0)"
assert_equals rapl-pl2 "$(expected_pl2_watts "$TEST_WATTS")" "$(rapl_constraint_watts short_term 1)"

runuser -u deck -- bash -lc "$USER_ENV steamosctl set-tdp-limit $RESTORE_WATTS"
sleep 2
assert_equals central "$(expected_pl1_watts "$RESTORE_WATTS")" "$(central_tdp)"
assert_equals remote "$(expected_pl1_watts "$RESTORE_WATTS")" "$(remote_tdp)"
assert_equals rapl-pl1 "$(expected_pl1_watts "$RESTORE_WATTS")" "$(rapl_constraint_watts long_term 0)"
assert_equals rapl-pl2 "$(expected_pl2_watts "$RESTORE_WATTS")" "$(rapl_constraint_watts short_term 1)"

systemctl --failed --no-legend --no-pager | tee /tmp/steamos-intel-handheld-failed-units.txt
if [ -s /tmp/steamos-intel-handheld-failed-units.txt ]; then
  echo "There are failed systemd units" >&2
  exit 1
fi

echo "OK: SteamOS Manager TDP remote works and restored ${RESTORE_WATTS}W"
REMOTE
