#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 root@steamdeck-host [test-watts] [restore-watts]" >&2
  exit 2
fi

target="$1"
test_watts="${2:-17}"
restore_watts="${3:-30}"
verify_tdp_policy_mode="${VERIFY_TDP_POLICY_MODE:-battery-maxq}"

ssh "$target" "TEST_WATTS='$test_watts' RESTORE_WATTS='$restore_watts' VERIFY_TDP_POLICY_MODE='$verify_tdp_policy_mode' bash -s" <<'REMOTE'
set -euo pipefail

VERIFY_TDP_POLICY_MODE="${VERIFY_TDP_POLICY_MODE:-battery-maxq}"
RAPL_TIME_WINDOW_TOLERANCE_US="${RAPL_TIME_WINDOW_TOLERANCE_US:-100000}"

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

remote_tdp() {
  busctl --system get-property org.rivoreo.SteamOSManager.PowerControl /org/rivoreo/SteamOSManager/PowerControl com.steampowered.SteamOSManager1.TdpLimit1 TdpLimit | awk '{print $2}'
}

set_remote_tdp() {
  busctl --system set-property org.rivoreo.SteamOSManager.PowerControl /org/rivoreo/SteamOSManager/PowerControl com.steampowered.SteamOSManager1.TdpLimit1 TdpLimit u "$1"
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
  local mode="${2:-battery-maxq}"
  case "$mode:$watts" in
    battery-maxq:8) echo 10 ;;
    battery-maxq:12) echo 15 ;;
    battery-maxq:17) echo 25 ;;
    battery-maxq:18) echo 25 ;;
    battery-maxq:20) echo 25 ;;
    battery-maxq:25) echo 30 ;;
    battery-maxq:30) echo 35 ;;
    ac-performance:8) echo 18 ;;
    ac-performance:12) echo 25 ;;
    ac-performance:17|ac-performance:18|ac-performance:20|ac-performance:25|ac-performance:30) echo 37 ;;
    *)
      if [ "$mode" = "battery-maxq" ]; then
        if [ "$watts" -le 12 ]; then
          local pl2=$(((watts * 125 + 99) / 100))
          if [ "$pl2" -lt $((watts + 1)) ]; then
            pl2=$((watts + 1))
          fi
          if [ "$pl2" -gt 15 ]; then
            pl2=15
          fi
          echo "$pl2"
        elif [ "$watts" -le 18 ]; then
          local pl2=$(((watts * 145 + 99) / 100))
          if [ "$pl2" -lt $((watts + 1)) ]; then
            pl2=$((watts + 1))
          fi
          if [ "$pl2" -gt 25 ]; then
            pl2=25
          fi
          echo "$pl2"
        elif [ "$watts" -le 25 ]; then
          local pl2=$((watts + 5))
          if [ "$pl2" -lt 25 ]; then
            pl2=25
          fi
          if [ "$pl2" -gt 30 ]; then
            pl2=30
          fi
          echo "$pl2"
        else
          local pl2=$((watts + 5))
          if [ "$pl2" -gt 35 ]; then
            pl2=35
          fi
          echo "$pl2"
        fi
      elif [ "$mode" = "ac-performance" ]; then
        if [ "$watts" -ge 17 ]; then
          echo 37
        elif [ "$watts" -le 8 ]; then
          echo 18
        else
          echo 25
        fi
      else
        echo "unsupported verifier TDP policy mode '$mode' for ${watts}W" >&2
        return 2
      fi
      ;;
  esac
}

expected_pl2_tau_us() {
  local watts
  watts="$(expected_pl1_watts "$1")"
  local mode="${2:-battery-maxq}"
  case "$mode" in
    battery-maxq)
      if [ "$watts" -le 8 ]; then
        echo 2000000
      elif [ "$watts" -le 12 ]; then
        echo 3000000
      elif [ "$watts" -le 20 ]; then
        echo 5000000
      else
        echo 8000000
      fi
      ;;
    ac-performance)
      if [ "$watts" -le 8 ]; then
        echo 8000000
      elif [ "$watts" -lt 17 ]; then
        echo 10000000
      else
        echo 28000000
      fi
      ;;
    *)
      echo "unsupported verifier TDP policy mode '$mode' for Tau" >&2
      return 2
      ;;
  esac
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

rapl_constraint_time_window_us() {
  local constraint_name="$1"
  local fallback_index="$2"
  local domain="/sys/class/powercap/intel-rapl:0"
  local name_file
  for name_file in "$domain"/constraint_*_name; do
    [ -e "$name_file" ] || continue
    if [ "$(cat "$name_file")" = "$constraint_name" ]; then
      local time_window_file="${name_file%_name}_time_window_us"
      [ -e "$time_window_file" ] || return 1
      cat "$time_window_file"
      return 0
    fi
  done
  local fallback_file="$domain/constraint_${fallback_index}_time_window_us"
  [ -e "$fallback_file" ] || return 1
  cat "$fallback_file"
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

assert_time_window_close() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  local diff=$((actual - expected))
  if [ "$diff" -lt 0 ]; then
    diff=$((-diff))
  fi
  if [ "$diff" -gt "$RAPL_TIME_WINDOW_TOLERANCE_US" ]; then
    echo "$label expected ${expected}us +/- ${RAPL_TIME_WINDOW_TOLERANCE_US}us, got ${actual}us" >&2
    exit 1
  fi
}

assert_optional_pl2_tau() {
  local watts="$1"
  local expected
  expected="$(expected_pl2_tau_us "$watts" "$VERIFY_TDP_POLICY_MODE")"
  local actual
  if actual="$(rapl_constraint_time_window_us short_term 1)"; then
    assert_time_window_close rapl-pl2-tau-us "$expected" "$actual"
  else
    echo "RAPL short-term Tau unavailable: constraint time-window file is not exposed"
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

report_mangohud_gpu_memory_fdinfo() {
  local found=0
  local line
  while IFS= read -r line; do
    found=1
    echo "$line"
  done < <(
    for proc in /proc/[0-9]*; do
      [ -d "$proc" ] || continue
      local comm
      comm="$(cat "$proc/comm" 2>/dev/null || true)"
      for fdinfo in "$proc"/fdinfo/*; do
        [ -e "$fdinfo" ] || continue
        awk -v proc="$proc" -v comm="$comm" -v fdinfo="$fdinfo" '
          /^drm-driver:/ { driver = $2 }
          /^drm-client-id:/ { client = $2 }
          /^drm-resident-gtt:/ { gtt = $2 " " $3; if ($2 + 0 > 0) nonzero = 1 }
          /^drm-resident-system0:/ { system0 = $2 " " $3; if ($2 + 0 > 0) nonzero = 1 }
          /^drm-resident-vram0:/ { vram0 = $2 " " $3; if ($2 + 0 > 0) nonzero = 1 }
          END {
            if ((driver == "i915" || driver == "xe") && nonzero) {
              print "MangoHud GPU memory fdinfo: comm=" comm \
                " pid=" substr(proc, 7) \
                " client=" client \
                " gtt=" gtt \
                " system0=" system0 \
                " vram0=" vram0
            }
          }
        ' "$fdinfo" 2>/dev/null
      done
    done | sort -u
  )

  if [ "$found" -eq 0 ]; then
    echo "MangoHud GPU memory fdinfo unavailable: no nonzero Intel DRM fdinfo memory was found"
  fi
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

report_msi_claw_ec_tdp_bytes() {
  local io="/sys/kernel/debug/ec/ec0/io"
  if [ ! -r "$io" ]; then
    echo "MSI EC TDP bytes unavailable: $io is not readable"
    return 0
  fi
  od -An -j 80 -N 2 -t u1 "$io" | awk '{print "MSI EC PL1/PL2 bytes: " $1 "W/" $2 "W"}'
  od -An -j 210 -N 1 -t x1 "$io" | awk '{print "MSI EC shift byte: 0x" $1}'
}

wait_for_service steamos-intel-handheld-power-control.service
verify_mangohud_cpu_power_sensor
verify_mangohud_gpu_power_sensor
report_mangohud_gpu_memory_fdinfo
report_mangohud_gpu_temperature_sensor

busctl --system get-property org.rivoreo.SteamOSManager.PowerControl /org/rivoreo/SteamOSManager/PowerControl com.steampowered.SteamOSManager1.RemoteInterface1 RemoteInterfaces | grep -F "com.steampowered.SteamOSManager1.TdpLimit1"

set_remote_tdp "$TEST_WATTS"
sleep 2
assert_equals remote "$(expected_pl1_watts "$TEST_WATTS")" "$(remote_tdp)"
assert_equals rapl-pl1 "$(expected_pl1_watts "$TEST_WATTS")" "$(rapl_constraint_watts long_term 0)"
assert_equals rapl-pl2 "$(expected_pl2_watts "$TEST_WATTS" "$VERIFY_TDP_POLICY_MODE")" "$(rapl_constraint_watts short_term 1)"
assert_optional_pl2_tau "$TEST_WATTS"
report_msi_claw_ec_tdp_bytes

set_remote_tdp "$RESTORE_WATTS"
sleep 2
assert_equals remote "$(expected_pl1_watts "$RESTORE_WATTS")" "$(remote_tdp)"
assert_equals rapl-pl1 "$(expected_pl1_watts "$RESTORE_WATTS")" "$(rapl_constraint_watts long_term 0)"
assert_equals rapl-pl2 "$(expected_pl2_watts "$RESTORE_WATTS" "$VERIFY_TDP_POLICY_MODE")" "$(rapl_constraint_watts short_term 1)"
assert_optional_pl2_tau "$RESTORE_WATTS"
report_msi_claw_ec_tdp_bytes

systemctl --failed --no-legend --no-pager | tee /tmp/steamos-intel-handheld-failed-units.txt
if [ -s /tmp/steamos-intel-handheld-failed-units.txt ]; then
  echo "There are failed systemd units" >&2
  exit 1
fi

echo "OK: system bus TDP provider works and restored ${RESTORE_WATTS}W"
REMOTE
