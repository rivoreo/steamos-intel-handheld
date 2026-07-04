#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 root@steamdeck-host" >&2
  exit 2
fi

target="$1"
appid="${PROFILE_GAME_POWER_APPID:-1091500}"
tdp_levels="${PROFILE_GAME_POWER_TDPS:-12 22}"
policies="${PROFILE_GAME_POWER_POLICIES:-off gpu-priority gpu-priority-cpu-cap}"
repeats="${PROFILE_GAME_POWER_REPEATS:-1}"
duration_s="${PROFILE_GAME_POWER_DURATION_S:-60}"
warmup_s="${PROFILE_GAME_POWER_WARMUP_S:-10}"
poll_s="${PROFILE_GAME_POWER_POLL_S:-2}"
capture_mode="${PROFILE_GAME_POWER_CAPTURE_MODE:-imported}"
fps_target="${PROFILE_GAME_POWER_FPS_TARGET:-}"
epp="${PROFILE_GAME_POWER_EPP:-balance_power}"
pcore_max_mhz="${PROFILE_GAME_POWER_PCORE_MAX_MHZ:-3000}"
ecore_max_mhz="${PROFILE_GAME_POWER_ECORE_MAX_MHZ:-2400}"
cpu_cap_core_share_threshold="${PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD:-0.30}"
cpu_cap_variants="${PROFILE_GAME_POWER_CPU_CAP_VARIANTS:-}"
ab_order_strategy="${PROFILE_GAME_POWER_AB_ORDER_STRATEGY:-paired-baseline}"
scene_evidence="${PROFILE_GAME_POWER_SCENE_EVIDENCE:-}"
cooldown_rule="${PROFILE_GAME_POWER_COOLDOWN_RULE:-fixed-60s}"
frame_performance_window_samples="${PROFILE_GAME_POWER_FRAME_PERFORMANCE_WINDOW_SAMPLES:-20}"
frame_performance_min_samples="${PROFILE_GAME_POWER_FRAME_PERFORMANCE_MIN_SAMPLES:-12}"
frame_performance_live_timeout_s="${PROFILE_GAME_POWER_FRAME_PERFORMANCE_LIVE_TIMEOUT_S:-15}"
target_satisfied_tdps="${PROFILE_GAME_POWER_TARGET_SATISFIED_TDPS:-22}"
local_root="${PROFILE_GAME_POWER_OUTPUT_ROOT:-.cache/game-power/profiles}"
mkdir -p "$local_root"

remote_root="$(ssh "$target" "mktemp -d /tmp/game-power-profile.XXXXXX")"
failure_marker="${remote_root##*/}.failed"

ssh "$target" \
  "APPID='$appid' TDP_LEVELS='$tdp_levels' POLICIES='$policies' \
REPEATS='$repeats' DURATION_S='$duration_s' WARMUP_S='$warmup_s' POLL_S='$poll_s' \
CAPTURE_MODE='$capture_mode' FPS_TARGET='$fps_target' EPP='$epp' PCORE_MAX_MHZ='$pcore_max_mhz' \
ECORE_MAX_MHZ='$ecore_max_mhz' \
CPU_CAP_CORE_SHARE_THRESHOLD='$cpu_cap_core_share_threshold' \
CPU_CAP_VARIANTS='$cpu_cap_variants' \
AB_ORDER_STRATEGY='$ab_order_strategy' \
SCENE_EVIDENCE='$scene_evidence' \
COOLDOWN_RULE='$cooldown_rule' \
FRAME_PERFORMANCE_WINDOW_SAMPLES='$frame_performance_window_samples' \
FRAME_PERFORMANCE_MIN_SAMPLES='$frame_performance_min_samples' \
FRAME_PERFORMANCE_LIVE_TIMEOUT_S='$frame_performance_live_timeout_s' \
TARGET_SATISFIED_TDPS='$target_satisfied_tdps' \
FAILURE_MARKER='$failure_marker' \
REMOTE_ROOT='$remote_root' bash -s" <<'REMOTE'
set -euo pipefail
MANGOHUD_OUTPUT_DIR="$REMOTE_ROOT/mangohud-logs"
PROFILE_CONTROL_FILE=/run/steamos-intel-handheld/game-power-profile-control.json
chmod 0755 "$REMOTE_ROOT"

wait_for_power_service() {
  for _ in $(seq 1 45); do
    if systemctl is-active --quiet steamos-intel-handheld-power-control.service; then
      return 0
    fi
    sleep 1
  done
  echo "timed out waiting for steamos-intel-handheld-power-control.service" >&2
  exit 1
}

snapshot_cpu_policy() {
  for policy in /sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$policy" ] || continue
    name="${policy##*/}"
    epp_value="$(cat "$policy/energy_performance_preference" 2>/dev/null || true)"
    max_freq="$(cat "$policy/scaling_max_freq" 2>/dev/null || true)"
    printf '%s\t%s\t%s\n' "$name" "$epp_value" "$max_freq"
  done | sort
}

restore_cpu_policy() {
  [ -f "$REMOTE_ROOT/cpu-policy.initial" ] || return 0
  while IFS=$'\t' read -r name epp_value max_freq; do
    policy="/sys/devices/system/cpu/cpufreq/$name"
    [ -d "$policy" ] || continue
    if [ -n "$epp_value" ] && [ -w "$policy/energy_performance_preference" ]; then
      printf '%s\n' "$epp_value" >"$policy/energy_performance_preference"
    fi
    if [ -n "$max_freq" ] && [ -w "$policy/scaling_max_freq" ]; then
      printf '%s\n' "$max_freq" >"$policy/scaling_max_freq"
    fi
  done <"$REMOTE_ROOT/cpu-policy.initial"
}

provider_tdp() {
  busctl --system get-property \
    org.rivoreo.SteamOSManager.PowerControl \
    /org/rivoreo/SteamOSManager/PowerControl \
    com.steampowered.SteamOSManager1.TdpLimit1 \
    TdpLimit | awk '{print $2}'
}

set_provider_tdp() {
  busctl --system set-property \
    org.rivoreo.SteamOSManager.PowerControl \
    /org/rivoreo/SteamOSManager/PowerControl \
    com.steampowered.SteamOSManager1.TdpLimit1 \
    TdpLimit u "$1"
}

wait_for_power_provider() {
  local current
  for _ in $(seq 1 45); do
    if current="$(provider_tdp 2>/dev/null)" \
      && [ -n "$current" ] \
      && set_provider_tdp "$current" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "timed out waiting for PowerControl TDP provider" >&2
  return 1
}

set_service_game_power_mode() {
  local mode="$1"
  install -d -m 0755 /run/systemd/system/steamos-intel-handheld-power-control.service.d
  install -d -m 0755 "$(dirname "$PROFILE_CONTROL_FILE")"
  rm -f "$PROFILE_CONTROL_FILE"
  cat >/run/systemd/system/steamos-intel-handheld-power-control.service.d/50-game-power-profile.conf <<EOF
[Service]
ExecStart=
ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control wait-and-serve --user deck --bus system --apply-rapl --apply-msi-claw-ec --ec-write-debounce-ms 750 --tdp-policy auto --msi-claw-ec-shift-policy tdp-threshold --prepare-mangohud-sensors --game-power-mode $mode --game-power-control-file $PROFILE_CONTROL_FILE --game-power-hint-cache /var/lib/steamos-intel-handheld/game-power-hints.json --min-w 8 --max-w 30 --short-limit-max-w 37 --state-file /var/lib/steamos-intel-handheld/tdp_w
EOF
  systemctl daemon-reload
  systemctl restart steamos-intel-handheld-power-control.service
  wait_for_power_service
  wait_for_power_provider
}

restore_service_game_power_mode() {
  rm -f "$PROFILE_CONTROL_FILE"
  rm -f /run/systemd/system/steamos-intel-handheld-power-control.service.d/50-game-power-profile.conf
  rmdir /run/systemd/system/steamos-intel-handheld-power-control.service.d 2>/dev/null || true
  systemctl daemon-reload
  systemctl restart steamos-intel-handheld-power-control.service
  wait_for_power_service
  wait_for_power_provider
}

run_as_deck() {
  local uid runtime_dir
  uid="$(id -u deck)"
  runtime_dir="/run/user/$uid"
  runuser -u deck -- env \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
    "$@"
}

deck_systemctl() {
  run_as_deck systemctl --user "$@"
}

wait_for_mangoapp_service() {
  for _ in $(seq 1 45); do
    if deck_systemctl is-active --quiet gamescope-mangoapp.service; then
      return 0
    fi
    sleep 1
  done
  echo "timed out waiting for gamescope-mangoapp.service" >&2
  return 1
}

setup_mangohud_controlled_capture() {
  if [ "$CAPTURE_MODE" = "controlled" ]; then
    if ! command -v mangohudctl >/dev/null; then
      echo "controlled capture requires mangohudctl on the target" >&2
      exit 1
    fi
    install -d -o deck -g deck -m 0755 "$MANGOHUD_OUTPUT_DIR"
    local uid dropin_dir
    uid="$(id -u deck)"
    dropin_dir="/run/user/$uid/systemd/user/gamescope-mangoapp.service.d"
    install -d -o deck -g deck -m 0755 "$dropin_dir"
    cat >"$dropin_dir/50-game-power-profile.conf" <<EOF
[Service]
WorkingDirectory=/home/deck
Environment=MANGOHUD_CONFIG=output_folder=$MANGOHUD_OUTPUT_DIR,log_interval=100,fps_metrics=avg+0.01+0.001,benchmark_percentiles=97+AVG
EOF
    chown deck:deck "$dropin_dir/50-game-power-profile.conf"
    deck_systemctl daemon-reload
    deck_systemctl restart gamescope-mangoapp.service
    wait_for_mangoapp_service
    run_as_deck sh -c 'cd /home/deck && mangohudctl set log_session false' \
      >/dev/null 2>&1 || true
  fi
}

restore_mangohud_controlled_capture() {
  if [ "$CAPTURE_MODE" = "controlled" ]; then
    run_as_deck sh -c 'cd /home/deck && mangohudctl set log_session false' \
      >/dev/null 2>&1 || true
    local uid dropin_dir
    uid="$(id -u deck)"
    dropin_dir="/run/user/$uid/systemd/user/gamescope-mangoapp.service.d"
    rm -f "$dropin_dir/50-game-power-profile.conf"
    rmdir "$dropin_dir" 2>/dev/null || true
    deck_systemctl daemon-reload || true
    deck_systemctl restart gamescope-mangoapp.service || true
    wait_for_mangoapp_service || true
  fi
}

restore_state() {
  restore_mangohud_controlled_capture || true
  restore_cpu_policy || true
  if [ -f "$REMOTE_ROOT/tdp.initial" ]; then
    if wait_for_power_provider >/dev/null 2>&1; then
      set_provider_tdp "$(cat "$REMOTE_ROOT/tdp.initial")" || true
    else
      echo "skipping TDP restore because PowerControl provider is unavailable" >&2
    fi
  fi
  restore_service_game_power_mode || true
}

CPU_CAP_VARIANTS_EFFECTIVE="${CPU_CAP_VARIANTS:-default:$PCORE_MAX_MHZ:$ECORE_MAX_MHZ:$CPU_CAP_CORE_SHARE_THRESHOLD}"

parse_cpu_cap_variant() {
  local variant="$1"
  local extra=""
  IFS=: read -r variant_label variant_pcore_max_mhz variant_ecore_max_mhz variant_core_share_threshold extra <<<"$variant"
  if [ -n "$extra" ] \
    || [ -z "$variant_label" ] \
    || [ -z "$variant_pcore_max_mhz" ] \
    || [ -z "$variant_ecore_max_mhz" ] \
    || [ -z "$variant_core_share_threshold" ]; then
    echo "invalid PROFILE_GAME_POWER_CPU_CAP_VARIANTS entry: $variant" >&2
    echo "expected variant_label:pcore_mhz:ecore_mhz:threshold" >&2
    exit 2
  fi
  case "$variant_label" in
    *[!A-Za-z0-9._-]*)
      echo "invalid PROFILE_GAME_POWER_CPU_CAP_VARIANTS label: $variant_label" >&2
      echo "variant labels may only contain letters, numbers, dot, underscore, and dash" >&2
      exit 2
    ;;
  esac
}

write_manual_fps_target_discovery() {
  local output="$1"
  python3 - "$output" "$FPS_TARGET" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
target = float(sys.argv[2])
payload = {
    "fps_target": target,
    "fps_target_source": "manual",
    "fps_target_confidence": "high",
    "raw": "PROFILE_GAME_POWER_FPS_TARGET",
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}

discover_fps_target() {
  local output="$1"
  python3 - "$output" <<'PY'
import json
import pathlib
import sys

output = pathlib.Path(sys.argv[1])


def parse_target(args):
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        if token == "-r" and index + 1 < len(args):
            return parse_value(args[index + 1], f"-r {args[index + 1]}")
        index += 1
    return None


def parse_value(value, raw):
    try:
        target = float(value)
    except ValueError:
        return None
    if target <= 0:
        return {
            "fps_target": None,
            "fps_target_source": "gamescope-cmdline-unlimited",
            "fps_target_confidence": "medium",
            "raw": raw,
        }
    return {
        "fps_target": target,
        "fps_target_source": "gamescope-cmdline",
        "fps_target_confidence": "medium",
        "raw": raw,
    }


payload = {
    "fps_target": None,
    "fps_target_source": "unknown",
    "fps_target_confidence": "low",
    "candidates": [],
}
for cmdline in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
    try:
        raw = cmdline.read_bytes()
    except OSError:
        continue
    args = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    if not args or "gamescope" not in pathlib.Path(args[0]).name:
        continue
    candidate = {"pid": int(cmdline.parent.name), "argv": args}
    target = parse_target(args)
    if target is not None:
        candidate.update(target)
        if target["fps_target"] is not None and payload["fps_target"] is None:
            payload.update(target)
    payload["candidates"].append(candidate)

output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
if payload["fps_target"] is not None:
    print(payload["fps_target"])
PY
}

unsupported_paired_baseline_shape() {
  echo "paired-baseline supports exactly one non-off candidate, one effective CPU-cap variant, and fixed-60s cooldown in the first V3 implementation" >&2
}

count_words() {
  local count=0
  local item
  for item in "$@"; do
    [ -n "$item" ] || continue
    count=$((count + 1))
  done
  printf '%s\n' "$count"
}

validate_ab_profile_shape() {
  AB_CANDIDATE_POLICY=""
  if [ "$CAPTURE_MODE" != "controlled" ]; then
    POLICY_SEQUENCE="$POLICIES"
    return 0
  fi
  if [ "$AB_ORDER_STRATEGY" != "paired-baseline" ]; then
    unsupported_paired_baseline_shape
    exit 2
  fi
  if [ "$COOLDOWN_RULE" != "fixed-60s" ]; then
    unsupported_paired_baseline_shape
    exit 2
  fi

  local policy off_count non_off_count candidate_policy
  off_count=0
  non_off_count=0
  candidate_policy=""
  for policy in $POLICIES; do
    if [ "$policy" = "off" ]; then
      off_count=$((off_count + 1))
    else
      non_off_count=$((non_off_count + 1))
      candidate_policy="$policy"
    fi
  done
  if [ "$non_off_count" -ne 1 ] || [ "$off_count" -lt 1 ]; then
    unsupported_paired_baseline_shape
    exit 2
  fi
  if [ "$candidate_policy" = "gpu-priority-cpu-cap" ] \
    && [ "$(count_words $CPU_CAP_VARIANTS_EFFECTIVE)" -ne 1 ]; then
    unsupported_paired_baseline_shape
    exit 2
  fi
  AB_CANDIDATE_POLICY="$candidate_policy"
  POLICY_SEQUENCE="off $AB_CANDIDATE_POLICY off"
}

monotonic_now() {
  python3 - <<'PY'
import time

print(f"{time.monotonic():.6f}")
PY
}

monotonic_delta() {
  python3 - "$1" "$2" <<'PY'
import sys

started = float(sys.argv[1])
ended = float(sys.argv[2])
print(f"{ended - started:.3f}")
PY
}

read_power_source_state() {
  python3 - <<'PY'
from pathlib import Path

root = Path("/sys/class/power_supply")
if not root.exists():
    print("unknown")
    raise SystemExit

has_battery = False
for supply in sorted(root.iterdir()):
    type_text = (supply / "type").read_text(errors="ignore").strip().lower() \
        if (supply / "type").exists() else ""
    online_text = (supply / "online").read_text(errors="ignore").strip() \
        if (supply / "online").exists() else ""
    if type_text in {"mains", "usb", "usb_c", "usb-c"} and online_text == "1":
        print("ac")
        raise SystemExit
    if type_text == "battery":
        has_battery = True

if has_battery:
    print("battery")
else:
    print("unknown")
PY
}

select_thermal_source() {
  python3 - <<'PY'
from pathlib import Path

candidates = []
for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
    name = ""
    try:
        name = (hwmon / "name").read_text().strip()
    except OSError:
        pass
    for temp_input in sorted(hwmon.glob("temp*_input")):
        label_path = temp_input.with_name(temp_input.name.replace("_input", "_label"))
        try:
            label = label_path.read_text().strip()
        except OSError:
            label = temp_input.stem
        lowered = f"{name} {label}".lower()
        if "package" in lowered or "x86_pkg" in lowered or name == "coretemp":
            kind = "cpu-package"
            rank = 0
        elif "platform" in lowered or "pch" in lowered:
            kind = "platform"
            rank = 1
        else:
            kind = "other"
            rank = 2
        source_id = f"hwmon:{name or hwmon.name}:{label}"
        candidates.append((rank, source_id, kind, label, str(temp_input)))

if not candidates:
    print("unknown\t\t\t")
else:
    _rank, source_id, kind, label, path = sorted(candidates)[0]
    print(f"{kind}\t{source_id}\t{label}\t{path}")
PY
}

read_thermal_c() {
  local path="$1"
  if [ -z "$path" ] || [ ! -r "$path" ]; then
    return 0
  fi
  awk '{ printf "%.3f\n", $1 / 1000.0 }' "$path"
}

latest_mangohud_csv() {
  find /home/deck -maxdepth 1 -name 'mangoapp_*.csv' -type f -printf '%T@ %p\n' \
    2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-
}

count_valid_mangohud_frame_rows() {
  local csv="$1"
  python3 - "$csv" <<'PY'
import csv
import math
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
count = 0
try:
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        header = None
        fps_index = None
        frametime_index = None
        for row in reader:
            if not row:
                continue
            normalized = [item.strip().lower() for item in row]
            if header is None:
                if "fps" not in normalized or "frametime" not in normalized:
                    continue
                header = normalized
                fps_index = header.index("fps")
                frametime_index = header.index("frametime")
                continue
            try:
                fps = float(row[fps_index])
                frametime = float(row[frametime_index])
            except (IndexError, TypeError, ValueError):
                continue
            if math.isfinite(fps) and fps > 0 and math.isfinite(frametime) and frametime > 0:
                count += 1
except OSError:
    pass
print(count)
PY
}

latest_live_mangohud_csv() {
  local run_dir="$1"
  find "$MANGOHUD_OUTPUT_DIR" -maxdepth 1 -name 'mangoapp_*.csv' \
    ! -name '*_summary.csv' -newer "$run_dir/mangohud.start" \
    -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-
}

wait_for_live_mangohud_csv() {
  local run_dir="$1"
  local min_rows="$2"
  local timeout_s="$3"
  local deadline csv rows
  deadline=$((SECONDS + timeout_s))
  while [ "$SECONDS" -le "$deadline" ]; do
    csv="$(latest_live_mangohud_csv "$run_dir")"
    if [ -n "$csv" ] && [ -f "$csv" ]; then
      rows="$(count_valid_mangohud_frame_rows "$csv")"
      if [ "$rows" -ge "$min_rows" ]; then
        printf '%s\n' "$csv"
        return 0
      fi
    fi
    sleep 1
  done
  {
    echo "reason=live-mangohud-csv-timeout"
    echo "min_rows=$min_rows"
    echo "timeout_s=$timeout_s"
  } >"$run_dir/frame-performance.fallback"
  return 1
}

start_mangohud_capture() {
  local run_dir="$1"
  if [ "$CAPTURE_MODE" = "controlled" ]; then
    run_as_deck sh -c 'cd /home/deck && mangohudctl set log_session false' \
      >/dev/null 2>&1 || true
    touch "$run_dir/mangohud.start"
    run_as_deck sh -c 'cd /home/deck && mangohudctl set log_session true'
  fi
}

stop_mangohud_capture() {
  if [ "$CAPTURE_MODE" = "controlled" ]; then
    run_as_deck sh -c 'cd /home/deck && mangohudctl set log_session false'
  fi
}

collect_imported_mangohud_csv() {
  local run_dir="$1"
  local csv summary
  csv="$(latest_mangohud_csv)"
  if [ -z "$csv" ]; then
    echo "no MangoHud CSV log found under /home/deck" >&2
    exit 1
  fi
  cp "$csv" "$run_dir/mangohud.csv"
  summary="${csv%.csv}_summary.csv"
  if [ -f "$summary" ]; then
    cp "$summary" "$run_dir/mangohud-summary.csv"
  fi
}

collect_controlled_mangohud_csv() {
  local run_dir="$1"
  local summary csv
  for _ in $(seq 1 20); do
    summary="$(
      find "$MANGOHUD_OUTPUT_DIR" -maxdepth 1 -name 'mangoapp_*_summary.csv' \
        -newer "$run_dir/mangohud.start" -printf '%T@ %p\n' 2>/dev/null \
        | sort -n | tail -n 1 | cut -d' ' -f2-
    )"
    if [ -n "$summary" ]; then
      csv="${summary%_summary.csv}.csv"
      if [ -f "$csv" ]; then
        cp "$csv" "$run_dir/mangohud.csv"
        cp "$summary" "$run_dir/mangohud-summary.csv"
        return 0
      fi
    fi
    sleep 1
  done
  echo "controlled MangoHud capture did not produce a new CSV under $MANGOHUD_OUTPUT_DIR" >&2
  exit 1
}

collect_mangohud_csv() {
  local run_dir="$1"
  if [ "$CAPTURE_MODE" = "controlled" ]; then
    collect_controlled_mangohud_csv "$run_dir"
  else
    collect_imported_mangohud_csv "$run_dir"
  fi
}

list_contains_word() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [ "$item" = "$needle" ]; then
      return 0
    fi
  done
  return 1
}

should_require_fps_target_satisfied() {
  local tdp="$1"
  local policy="$2"
  [ -n "$FPS_TARGET" ] || return 1
  [ "$policy" = "gpu-priority" ] || return 1
  # shellcheck disable=SC2086
  list_contains_word "$tdp" $TARGET_SATISFIED_TDPS
}

sample_cgroup_pressure() {
  local output="$1"
  local seconds="$2"
  local start elapsed
  start="$(date +%s)"
  : >"$output"
  while true; do
    elapsed=$(($(date +%s) - start))
    [ "$elapsed" -gt "$seconds" ] && break
    if [ -r /sys/fs/cgroup/cpu.pressure ]; then
      python3 - "$elapsed" /sys/fs/cgroup/cpu.pressure >>"$output" <<'PY'
import json
import pathlib
import sys

elapsed = float(sys.argv[1])
text = pathlib.Path(sys.argv[2]).read_text()
payload = {"elapsed_s": elapsed, "cpu": {}}
for line in text.splitlines():
    parts = line.split()
    if not parts:
        continue
    payload["cpu"][parts[0]] = {}
    for item in parts[1:]:
        key, value = item.split("=", 1)
        payload["cpu"][parts[0]][key] = float(value)
print(json.dumps(payload, sort_keys=True))
PY
    fi
    sleep 1
  done
}

sample_thread_affinity() {
  local output="$1"
  local seconds="$2"
  local start elapsed
  start="$(date +%s)"
  : >"$output"
  while true; do
    elapsed=$(($(date +%s) - start))
    [ "$elapsed" -gt "$seconds" ] && break
    python3 - "$elapsed" "$APPID" >>"$output" <<'PY'
import json
import os
import pathlib
import sys

elapsed = float(sys.argv[1])
appid = sys.argv[2]
clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
payload = {"elapsed_s": elapsed, "threads": []}


def read_text(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def parse_status(text):
    parsed = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key] = value.strip()
    return parsed


def parse_stat(text):
    if not text:
        return None
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2 :].split()
    try:
        utime = int(fields[11])
        stime = int(fields[12])
        processor = int(fields[36]) if len(fields) > 36 else None
    except (IndexError, ValueError):
        return None
    return {
        "cpu_time_s": (utime + stime) / clock_ticks,
        "current_cpu": processor,
    }


def parse_sched_migrations(text):
    for line in text.splitlines():
        if line.strip().startswith("nr_migrations"):
            try:
                return int(line.split(":", 1)[1].strip())
            except (IndexError, ValueError):
                return None
    return None


proc = pathlib.Path("/proc")
for cgroup_path in proc.glob("[0-9]*/cgroup"):
    cgroup = read_text(cgroup_path)
    if f"app-steam-app{appid}" not in cgroup:
        continue
    pid_dir = cgroup_path.parent
    for task_dir in (pid_dir / "task").glob("[0-9]*"):
        tid_text = task_dir.name
        try:
            tid = int(tid_text)
        except ValueError:
            continue
        status = parse_status(read_text(task_dir / "status"))
        stat = parse_stat(read_text(task_dir / "stat"))
        if stat is None:
            continue
        payload["threads"].append(
            {
                "tid": tid,
                "comm": status.get("Name"),
                "cpu_time_s": round(float(stat["cpu_time_s"]), 6),
                "migration_count": parse_sched_migrations(read_text(task_dir / "sched")),
                "voluntary_ctxt_switches": status.get("voluntary_ctxt_switches"),
                "nonvoluntary_ctxt_switches": status.get(
                    "nonvoluntary_ctxt_switches"
                ),
                "current_cpu": stat["current_cpu"],
                "affinity": status.get("Cpus_allowed_list"),
                "cgroup": cgroup.strip().replace("\n", ";"),
            }
        )

print(json.dumps(payload, sort_keys=True))
PY
    sleep 1
  done
}

sample_thread_schedstat() {
  local output="$1"
  local seconds="$2"
  local start elapsed
  start="$(date +%s)"
  : >"$output"
  while true; do
    elapsed=$(($(date +%s) - start))
    [ "$elapsed" -gt "$seconds" ] && break
    python3 - "$elapsed" "$APPID" >>"$output" <<'PY'
import json
import pathlib
import sys

elapsed = float(sys.argv[1])
appid = sys.argv[2]
payload = {"elapsed_s": elapsed, "threads": []}


def read_text(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def parse_status(text):
    parsed = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key] = value.strip()
    return parsed


def parse_stat_cpu(text):
    if not text:
        return None
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2 :].split()
    try:
        return int(fields[36]) if len(fields) > 36 else None
    except ValueError:
        return None


def parse_schedstat(text):
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        return {
            "run_time_ns": int(parts[0]),
            "runqueue_wait_ns": int(parts[1]),
            "timeslices": int(parts[2]),
        }
    except ValueError:
        return None


proc = pathlib.Path("/proc")
for cgroup_path in proc.glob("[0-9]*/cgroup"):
    cgroup = read_text(cgroup_path)
    if f"app-steam-app{appid}" not in cgroup:
        continue
    pid_dir = cgroup_path.parent
    for task_dir in (pid_dir / "task").glob("[0-9]*"):
        try:
            tid = int(task_dir.name)
        except ValueError:
            continue
        schedstat = parse_schedstat(read_text(task_dir / "schedstat"))
        if schedstat is None:
            continue
        status = parse_status(read_text(task_dir / "status"))
        schedstat.update(
            {
                "tid": tid,
                "comm": status.get("Name"),
                "current_cpu": parse_stat_cpu(read_text(task_dir / "stat")),
                "cgroup": cgroup.strip().replace("\n", ";"),
            }
        )
        payload["threads"].append(schedstat)

print(json.dumps(payload, sort_keys=True))
PY
    sleep 1
  done
}

sample_process_cgroups() {
  local output="$1"
  local seconds="$2"
  local start elapsed
  start="$(date +%s)"
  : >"$output"
  while true; do
    elapsed=$(($(date +%s) - start))
    [ "$elapsed" -gt "$seconds" ] && break
    python3 - "$elapsed" >>"$output" <<'PY'
import json
import os
import pathlib
import sys

elapsed = float(sys.argv[1])
clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
payload = {"elapsed_s": elapsed, "processes": []}


def read_text(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def parse_stat(text):
    if not text:
        return None
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2 :].split()
    try:
        utime = int(fields[11])
        stime = int(fields[12])
    except (IndexError, ValueError):
        return None
    return (utime + stime) / clock_ticks


def relevant(cgroup, comm):
    lowered = f"{cgroup} {comm}".lower()
    tokens = (
        "app-steam-app",
        "steam",
        "gamescope",
        "mangoapp",
        "/user.slice/",
        "/system.slice/",
    )
    return any(token in lowered for token in tokens)


proc = pathlib.Path("/proc")
for cgroup_path in proc.glob("[0-9]*/cgroup"):
    pid_dir = cgroup_path.parent
    try:
        pid = int(pid_dir.name)
    except ValueError:
        continue
    cgroup = read_text(cgroup_path).strip().replace("\n", ";")
    comm = read_text(pid_dir / "comm").strip()
    if not cgroup or not relevant(cgroup, comm):
        continue
    cpu_time = parse_stat(read_text(pid_dir / "stat"))
    if cpu_time is None:
        continue
    payload["processes"].append(
        {
            "pid": pid,
            "comm": comm or None,
            "cpu_time_s": round(float(cpu_time), 6),
            "cgroup": cgroup,
        }
    )

print(json.dumps(payload, sort_keys=True))
PY
    sleep 1
  done
}

snapshot_affinity_restore_state() {
  local output="$1"
  python3 - "$APPID" "$output" <<'PY'
import json
import pathlib
import sys

appid = sys.argv[1]
output = pathlib.Path(sys.argv[2])
proc = pathlib.Path("/proc")
cgroup_root = pathlib.Path("/sys/fs/cgroup")
control_files = (
    "cgroup.type",
    "cpu.uclamp.min",
    "cpu.uclamp.max",
    "cpu.weight",
    "cpu.max",
    "cpuset.cpus",
    "cpuset.cpus.effective",
    "cpuset.mems",
    "cpuset.mems.effective",
)


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def parse_status(text):
    parsed = {}
    if not text:
        return parsed
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key] = value.strip()
    return parsed


def normalize_cgroup(text):
    return text.strip().replace("\n", ";")


def cgroup_relative_path(text):
    for line in text.splitlines():
        if line.startswith("0::"):
            return line.removeprefix("0::")
    return ""


def cgroup_fs_path(relative):
    if not relative.startswith("/"):
        return None
    parts = [part for part in relative.split("/") if part and part not in {".", ".."}]
    return cgroup_root.joinpath(*parts)


def foreground_app_cgroup(text):
    return f"app-steam-app{appid}" in text


def restore_snapshot_relevant_cgroup(cgroup, comm):
    lowered = f"{cgroup} {comm or ''}".lower()
    tokens = (
        f"app-steam-app{appid}",
        "app-steam-client",
        "steamwebhelper",
        "steam",
        "gamescope",
        "mangoapp",
        "/user.slice/",
        "/system.slice/",
    )
    return any(token in lowered for token in tokens)


threads = []
cgroups = {}
for cgroup_file in proc.glob("[0-9]*/cgroup"):
    cgroup_text = read_text(cgroup_file)
    if not cgroup_text:
        continue
    pid_dir = cgroup_file.parent
    try:
        pid = int(pid_dir.name)
    except ValueError:
        continue
    comm = read_text(pid_dir / "comm")
    if not restore_snapshot_relevant_cgroup(cgroup_text, comm):
        continue
    normalized = normalize_cgroup(cgroup_text)
    relative = cgroup_relative_path(cgroup_text)
    fs_path = cgroup_fs_path(relative)
    if fs_path is not None:
        cgroups[normalized] = fs_path
    if not foreground_app_cgroup(cgroup_text):
        continue
    for task_dir in (pid_dir / "task").glob("[0-9]*"):
        try:
            tid = int(task_dir.name)
        except ValueError:
            continue
        status = parse_status(read_text(task_dir / "status"))
        threads.append(
            {
                "pid": pid,
                "tid": tid,
                "comm": status.get("Name"),
                "cgroup": normalized,
                "cpus_allowed": status.get("Cpus_allowed"),
                "cpus_allowed_list": status.get("Cpus_allowed_list"),
            }
        )

cgroup_payload = []
for cgroup, path in sorted(cgroups.items()):
    files = {}
    for name in control_files:
        value = read_text(path / name)
        if value is not None:
            files[name] = value
    cgroup_payload.append(
        {
            "cgroup": cgroup,
            "path": str(path),
            "files": files,
        }
    )

payload = {
    "appid": appid,
    "mode": "restore-snapshot",
    "write_policy": "snapshot-only",
    "threads": sorted(threads, key=lambda item: (item["pid"], item["tid"])),
    "cgroups": cgroup_payload,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}

apply_background_shaping_variant() {
  local run_dir="$1"
  local variant="$2"
  [ -n "$variant" ] || return 0
  /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile \
    apply-background-shaping \
    --appid "$APPID" \
    --restore-affinity-json "$run_dir/restore-affinity.json" \
    --variant "$variant" \
    --output "$run_dir/background-shaping-writes.json" \
    >"$run_dir/background-shaping-apply.stdout"
}

restore_background_shaping_variant() {
  local run_dir="$1"
  [ -f "$run_dir/background-shaping-writes.json" ] || return 0
  /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile \
    restore-background-shaping \
    --writes-json "$run_dir/background-shaping-writes.json" \
    --output "$run_dir/background-shaping-restore.json" \
    >"$run_dir/background-shaping-restore.stdout"
  python3 - "$run_dir/background-shaping-restore.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
raise SystemExit(0 if payload.get("restored") is True else 1)
PY
}

collect_cpu_topology() {
  local output="$1"
  python3 - "$output" <<'PY'
import json
import pathlib
import re
import sys

output = pathlib.Path(sys.argv[1])
cpu_root = pathlib.Path("/sys/devices/system/cpu")
cpus = []


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_int(path):
    text = read_text(path)
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def cpufreq_policy(cpu_dir):
    cpufreq = cpu_dir / "cpufreq"
    if not cpufreq.exists():
        return None
    try:
        resolved = cpufreq.resolve()
    except OSError:
        resolved = cpufreq
    return resolved.name


def core_type_label(cpu_dir, capacity):
    text = read_text(cpu_dir / "topology" / "core_type")
    if text:
        lowered = text.lower()
        if lowered in {"atom", "efficient", "e-core", "1"}:
            return "e-core"
        if lowered in {"core", "performance", "p-core", "2"}:
            return "p-core"
        return lowered
    if capacity is not None:
        return "p-core" if capacity >= 1024 else "e-core"
    return "unknown"


for cpu_dir in sorted(cpu_root.glob("cpu[0-9]*"), key=lambda item: int(item.name[3:])):
    match = re.fullmatch(r"cpu([0-9]+)", cpu_dir.name)
    if not match:
        continue
    cpu = int(match.group(1))
    online_text = read_text(cpu_dir / "online")
    online = True if online_text is None else online_text != "0"
    policy_name = cpufreq_policy(cpu_dir)
    policy_dir = cpu_dir / "cpufreq"
    if policy_name:
        candidate = cpu_root / "cpufreq" / policy_name
        if candidate.exists():
            policy_dir = candidate
    capacity = read_int(cpu_dir / "cpu_capacity")
    cpus.append(
        {
            "cpu": cpu,
            "online": online,
            "policy": policy_name,
            "core_type": core_type_label(cpu_dir, capacity),
            "capacity": capacity,
            "thread_siblings": read_text(
                cpu_dir / "topology" / "thread_siblings_list"
            ),
            "core_id": read_int(cpu_dir / "topology" / "core_id"),
            "physical_package_id": read_int(
                cpu_dir / "topology" / "physical_package_id"
            ),
            "max_freq_khz": read_int(policy_dir / "scaling_max_freq")
            or read_int(policy_dir / "cpuinfo_max_freq"),
            "epp": read_text(policy_dir / "energy_performance_preference"),
            "scaling_driver": read_text(policy_dir / "scaling_driver"),
            "affected_cpus": read_text(policy_dir / "affected_cpus"),
        }
    )

output.write_text(json.dumps({"cpus": cpus}, indent=2, sort_keys=True) + "\n")
PY
}

if [ "$CAPTURE_MODE" != "imported" ] && [ "$CAPTURE_MODE" != "controlled" ]; then
  echo "unsupported PROFILE_GAME_POWER_CAPTURE_MODE: $CAPTURE_MODE" >&2
  exit 2
fi

validate_ab_profile_shape
AB_INVOCATION_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
wait_for_power_service
wait_for_power_provider
snapshot_cpu_policy >"$REMOTE_ROOT/cpu-policy.initial"
provider_tdp >"$REMOTE_ROOT/tdp.initial"
trap restore_state EXIT
set_service_game_power_mode off
setup_mangohud_controlled_capture
profile_failed=false
FPS_TARGET_SOURCE="unknown"
FPS_TARGET_CONFIDENCE="low"
if [ -n "$FPS_TARGET" ]; then
  FPS_TARGET_SOURCE="manual"
  FPS_TARGET_CONFIDENCE="high"
  write_manual_fps_target_discovery "$REMOTE_ROOT/fps-target.discovery.json"
else
  FPS_TARGET="$(discover_fps_target "$REMOTE_ROOT/fps-target.discovery.json" || true)"
  if [ -n "$FPS_TARGET" ]; then
    FPS_TARGET_SOURCE="gamescope-cmdline"
    FPS_TARGET_CONFIDENCE="medium"
  fi
fi

/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile \
  replay-action-equivalence \
  --output "$REMOTE_ROOT/action-equivalence.json" \
  >"$REMOTE_ROOT/action-equivalence.stdout"

for repeat in $(seq 1 "$REPEATS"); do
  for tdp in $TDP_LEVELS; do
    set_provider_tdp "$tdp"
    sleep "$WARMUP_S"
    ab_pair_variant_suffix=""
    if [ "$CAPTURE_MODE" = "controlled" ] && [ "$AB_CANDIDATE_POLICY" = "gpu-priority-cpu-cap" ]; then
      IFS=: read -r ab_variant_label _ab_pcore _ab_ecore _ab_threshold _ab_extra <<<"$CPU_CAP_VARIANTS_EFFECTIVE"
      ab_pair_variant_suffix="-variant-${ab_variant_label}"
    fi
    ab_pair_id="${AB_INVOCATION_ID}-r${repeat}-tdp${tdp}-candidate-${AB_CANDIDATE_POLICY}${ab_pair_variant_suffix}"
    ab_run_order="off,$AB_CANDIDATE_POLICY,off"
    ab_position_index=0
    for policy in $POLICY_SEQUENCE; do
      ab_pair_position=""
      ab_order_valid=false
      if [ "$CAPTURE_MODE" = "controlled" ]; then
        ab_order_valid=true
        ab_position_index=$((ab_position_index + 1))
        case "$ab_position_index" in
          1) ab_pair_position="baseline-before" ;;
          2) ab_pair_position="candidate" ;;
          3) ab_pair_position="baseline-after" ;;
          *)
            unsupported_paired_baseline_shape
            exit 2
          ;;
        esac
      fi
      policy_variants="-"
      if [ "$policy" = "gpu-priority-cpu-cap" ]; then
        policy_variants="$CPU_CAP_VARIANTS_EFFECTIVE"
      fi
      for cpu_cap_variant in $policy_variants; do
        variant_label=""
        variant_pcore_max_mhz="$PCORE_MAX_MHZ"
        variant_ecore_max_mhz="$ECORE_MAX_MHZ"
        variant_core_share_threshold="$CPU_CAP_CORE_SHARE_THRESHOLD"
        if [ "$policy" = "gpu-priority-cpu-cap" ]; then
          parse_cpu_cap_variant "$cpu_cap_variant"
        fi

        run_policy_label="$policy"
        if [ -n "$variant_label" ]; then
          run_policy_label="${policy}-${variant_label}"
        fi
        if [ -n "$ab_pair_position" ]; then
          run_policy_label="${run_policy_label}-${ab_pair_position}"
        fi
        run_dir="$REMOTE_ROOT/$(date +%Y%m%dT%H%M%S)-app${APPID}-${tdp}w-${run_policy_label}-r${repeat}"
        mkdir -p "$run_dir"
        cp "$REMOTE_ROOT/fps-target.discovery.json" "$run_dir/fps-target.discovery.json"
        collect_cpu_topology "$run_dir/cpu-topology.json"
        snapshot_affinity_restore_state "$run_dir/restore-affinity.json"
        snapshot_cpu_policy >"$run_dir/cpu-policy.before"
        provider_tdp >"$run_dir/tdp.before"

        background_shaping_variant=""
        case "$policy" in
          off)
            mode="observe"
            cpu_cap_enabled=false
            policy_args=(--epp "$EPP")
          ;;
          gpu-priority)
            mode="gpu-priority"
            cpu_cap_enabled=false
            policy_args=(--epp "$EPP")
          ;;
          gpu-priority-cpu-cap)
            mode="gpu-priority"
            cpu_cap_enabled=true
            policy_args=(
              --epp "$EPP"
              --cpu-cap
              --pcore-max-mhz "$variant_pcore_max_mhz"
              --ecore-max-mhz "$variant_ecore_max_mhz"
              --cpu-cap-core-share-threshold "$variant_core_share_threshold"
            )
          ;;
          gpu-priority-bg-weight)
            mode="gpu-priority"
            cpu_cap_enabled=false
            background_shaping_variant="cpu-weight-80"
            policy_args=(--epp "$EPP")
          ;;
          gpu-priority-bg-uclamp)
            mode="gpu-priority"
            cpu_cap_enabled=false
            background_shaping_variant="uclamp-max-85"
            policy_args=(--epp "$EPP")
          ;;
          *)
            echo "unsupported PROFILE_GAME_POWER_POLICIES entry: $policy" >&2
            exit 2
          ;;
        esac

        restored=true
        apply_background_shaping_variant "$run_dir" "$background_shaping_variant"
        ab_summary_args=()
        thermal_source_kind="unknown"
        thermal_source_id=""
        thermal_source_label=""
        thermal_source_path=""
        thermal_start_c=""
        thermal_end_c=""
        thermal_unavailable=true
        power_source_start_state="unknown"
        power_source_pre_run_state="unknown"
        power_source_end_state="unknown"
        power_source_samples="unknown,unknown,unknown"
        power_source_stable=false
        cooldown_enforced=false
        cooldown_sleep_s=0
        cooldown_started_at_s=""
        cooldown_ended_at_s=""
        cooldown_elapsed_s=""
        run_started_at_s=""
        run_ended_at_s=""
        if [ "$CAPTURE_MODE" = "controlled" ]; then
          cooldown_enforced=true
          cooldown_sleep_s=60
          power_source_start_state="$(read_power_source_state)"
          IFS=$'\t' read -r thermal_source_kind thermal_source_id thermal_source_label thermal_source_path \
            <<<"$(select_thermal_source)"
          cooldown_started_at_s="$(monotonic_now)"
          sleep "$cooldown_sleep_s"
          cooldown_ended_at_s="$(monotonic_now)"
          cooldown_elapsed_s="$(monotonic_delta "$cooldown_started_at_s" "$cooldown_ended_at_s")"
          power_source_pre_run_state="$(read_power_source_state)"
          thermal_start_c="$(read_thermal_c "$thermal_source_path" || true)"
          if [ -n "$thermal_start_c" ] && [ "$thermal_source_kind" != "unknown" ]; then
            thermal_unavailable=false
          fi
        fi
        start_mangohud_capture "$run_dir"
        live_frame_performance_csv=""
        if [ "$CAPTURE_MODE" = "controlled" ]; then
          if live_frame_performance_csv="$(
            wait_for_live_mangohud_csv \
              "$run_dir" \
              "$FRAME_PERFORMANCE_MIN_SAMPLES" \
              "$FRAME_PERFORMANCE_LIVE_TIMEOUT_S"
          )"; then
            printf '%s\n' "$live_frame_performance_csv" \
              >"$run_dir/frame-performance-source.txt"
          else
            live_frame_performance_csv=""
          fi
        fi
        fps_target_runtime_args=()
        if [ -n "$FPS_TARGET" ]; then
          fps_target_runtime_args=(
            --fps-target "$FPS_TARGET"
            --fps-target-source "$FPS_TARGET_SOURCE"
            --fps-target-confidence "$FPS_TARGET_CONFIDENCE"
          )
        fi
        frame_performance_runtime_args=()
        if [ -n "$live_frame_performance_csv" ]; then
          frame_performance_runtime_args=(
            --frame-performance-csv "$live_frame_performance_csv"
            --frame-performance-window-samples "$FRAME_PERFORMANCE_WINDOW_SAMPLES"
            --frame-performance-min-samples "$FRAME_PERFORMANCE_MIN_SAMPLES"
          )
        fi
        sample_cgroup_pressure "$run_dir/cgroup-pressure.jsonl" "$DURATION_S" &
        pressure_pid="$!"
        sample_thread_affinity "$run_dir/thread-affinity.jsonl" "$DURATION_S" &
        thread_affinity_pid="$!"
        sample_thread_schedstat "$run_dir/thread-schedstat.jsonl" "$DURATION_S" &
        thread_schedstat_pid="$!"
        sample_process_cgroups "$run_dir/process-cgroups.jsonl" "$DURATION_S" &
        process_cgroups_pid="$!"
        run_started_at_s="$(monotonic_now)"
        if ! /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power \
          --mode "$mode" \
          --duration-s "$DURATION_S" \
          --poll-s "$POLL_S" \
          --target-appid "$APPID" \
          --output-format jsonl \
          "${fps_target_runtime_args[@]}" \
          "${frame_performance_runtime_args[@]}" \
          "${policy_args[@]}" >"$run_dir/game-power.jsonl"; then
          stop_mangohud_capture || true
          wait "$pressure_pid" || true
          wait "$thread_affinity_pid" || true
          wait "$thread_schedstat_pid" || true
          wait "$process_cgroups_pid" || true
          restore_background_shaping_variant "$run_dir" || true
          exit 1
        fi
        run_ended_at_s="$(monotonic_now)"
        stop_mangohud_capture
        wait "$pressure_pid" || true
        wait "$thread_affinity_pid" || true
        wait "$thread_schedstat_pid" || true
        wait "$process_cgroups_pid" || true
        restore_background_shaping_variant "$run_dir" || restored=false
        if [ "$CAPTURE_MODE" = "controlled" ]; then
          power_source_end_state="$(read_power_source_state)"
          thermal_end_c="$(read_thermal_c "$thermal_source_path" || true)"
          if [ -n "$thermal_end_c" ] && [ "$thermal_source_kind" != "unknown" ]; then
            thermal_unavailable=false
          fi
          power_source_samples="${power_source_start_state},${power_source_pre_run_state},${power_source_end_state}"
          if [ "$power_source_start_state" = "$power_source_pre_run_state" ] \
            && [ "$power_source_pre_run_state" = "$power_source_end_state" ] \
            && [ "$power_source_start_state" != "unknown" ]; then
            power_source_stable=true
            power_source_state="$power_source_start_state"
          elif [ "$power_source_start_state" = "unknown" ] \
            || [ "$power_source_pre_run_state" = "unknown" ] \
            || [ "$power_source_end_state" = "unknown" ]; then
            power_source_state="unknown"
          else
            power_source_state="mixed"
          fi
          ab_summary_args=(
            --ab-order-strategy "$AB_ORDER_STRATEGY"
            --ab-run-order "$ab_run_order"
            --ab-order-valid "$ab_order_valid"
            --ab-candidate-policy "$AB_CANDIDATE_POLICY"
            --ab-invocation-id "$AB_INVOCATION_ID"
            --ab-pair-id "$ab_pair_id"
            --ab-pair-position "$ab_pair_position"
            --scene-evidence "$SCENE_EVIDENCE"
            --power-source-state "$power_source_state"
            --power-source-start-state "$power_source_start_state"
            --power-source-pre-run-state "$power_source_pre_run_state"
            --power-source-end-state "$power_source_end_state"
            --power-source-samples "$power_source_samples"
            --power-source-stable "$power_source_stable"
            --thermal-unavailable "$thermal_unavailable"
            --thermal-source-kind "$thermal_source_kind"
            --thermal-source-id "$thermal_source_id"
            --thermal-source-label "$thermal_source_label"
            --run-started-at-s "$run_started_at_s"
            --run-ended-at-s "$run_ended_at_s"
            --cooldown-rule "$COOLDOWN_RULE"
            --cooldown-enforced "$cooldown_enforced"
            --cooldown-started-at-s "$cooldown_started_at_s"
            --cooldown-ended-at-s "$cooldown_ended_at_s"
            --cooldown-elapsed-s "$cooldown_elapsed_s"
          )
          if [ -n "$thermal_start_c" ]; then
            ab_summary_args+=(--thermal-start-c "$thermal_start_c")
          fi
          if [ -n "$thermal_end_c" ]; then
            ab_summary_args+=(--thermal-end-c "$thermal_end_c")
          fi
        fi

        collect_mangohud_csv "$run_dir"

        snapshot_cpu_policy >"$run_dir/cpu-policy.after"
        provider_tdp >"$run_dir/tdp.after"
        if ! diff -u "$run_dir/cpu-policy.before" "$run_dir/cpu-policy.after" \
          >"$run_dir/cpu-policy.diff"; then
          restored=false
        fi

        mangohud_args=(--mangohud-csv "$run_dir/mangohud.csv")
        if [ -f "$run_dir/mangohud-summary.csv" ]; then
          mangohud_args+=(--mangohud-summary-csv "$run_dir/mangohud-summary.csv")
        fi
        fps_target_args=()
        if [ -n "$FPS_TARGET" ]; then
          fps_target_args=(--fps-target "$FPS_TARGET")
        fi
        fps_target_source_args=(--fps-target-source "$FPS_TARGET_SOURCE")
        fps_target_confidence_args=(--fps-target-confidence "$FPS_TARGET_CONFIDENCE")

        /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile summarize \
          --appid "$APPID" \
          --tdp-w "$tdp" \
          --policy "$policy" \
          --capture-mode "$CAPTURE_MODE" \
          "${mangohud_args[@]}" \
          "${fps_target_args[@]}" \
          "${fps_target_source_args[@]}" \
          "${fps_target_confidence_args[@]}" \
          --game-power-jsonl "$run_dir/game-power.jsonl" \
          --pressure-jsonl "$run_dir/cgroup-pressure.jsonl" \
          --thread-affinity-jsonl "$run_dir/thread-affinity.jsonl" \
          --thread-schedstat-jsonl "$run_dir/thread-schedstat.jsonl" \
          --cpu-topology-json "$run_dir/cpu-topology.json" \
          --process-cgroups-jsonl "$run_dir/process-cgroups.jsonl" \
          --restore-affinity-json "$run_dir/restore-affinity.json" \
          --epp "$EPP" \
          --pcore-max-mhz "$variant_pcore_max_mhz" \
          --ecore-max-mhz "$variant_ecore_max_mhz" \
          --cpu-cap-enabled "$cpu_cap_enabled" \
          --cpu-cap-core-share-threshold "$variant_core_share_threshold" \
          --duration-s "$DURATION_S" \
          --warmup-s "$WARMUP_S" \
          --poll-s "$POLL_S" \
          "${ab_summary_args[@]}" \
          --restored "$restored" \
          --output "$run_dir"

        runtime_contract_args=(
          --game-power-jsonl "$run_dir/game-power.jsonl"
          --summary-json "$run_dir/summary.json"
          --action-replay-json "$REMOTE_ROOT/action-equivalence.json"
          --require-classification
          --require-pressure
        )
        if [ -n "$FPS_TARGET" ]; then
          runtime_contract_args+=(
            --expect-fps-target "$FPS_TARGET"
            --expect-fps-target-source "$FPS_TARGET_SOURCE"
            --expect-fps-target-confidence "$FPS_TARGET_CONFIDENCE"
            --expect-target-frame-ms "$(python3 - "$FPS_TARGET" <<'PY'
import sys

print(f"{1000.0 / float(sys.argv[1]):.3f}")
PY
)"
          )
        fi
        if [ "$policy" = "gpu-priority-cpu-cap" ]; then
          runtime_contract_args+=(--require-cpu-cap-action)
        fi
        if [ -n "${live_frame_performance_csv:-}" ]; then
          runtime_contract_args+=(--require-frame-performance)
        fi
        if [ -n "${live_frame_performance_csv:-}" ] && \
          should_require_fps_target_satisfied "$tdp" "$policy"; then
          runtime_contract_args+=(--require-fps-target-satisfied)
        fi
        /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile \
          validate-runtime-telemetry \
          "${runtime_contract_args[@]}" \
          --output "$run_dir/runtime-telemetry-contract.json" \
          >"$run_dir/runtime-telemetry-contract.stdout"
        if [ ! -f "$run_dir/affinity-advice.json" ]; then
          echo "missing affinity-advice.json in $run_dir" >&2
          exit 1
        fi
        if [ ! -f "$run_dir/background-shaping.json" ]; then
          echo "missing background-shaping.json in $run_dir" >&2
          exit 1
        fi
        if [ "$restored" != true ]; then
          echo "run did not restore cleanly: $run_dir" >&2
          printf '%s\n' "run did not restore cleanly: $run_dir" \
            >"$REMOTE_ROOT/$FAILURE_MARKER"
          profile_failed=true
          break 4
        fi
        echo "profile artifact manifest.json: $run_dir/manifest.json"
        echo "profile artifact summary.json: $run_dir/summary.json"
        echo "profile artifact mangohud.csv: $run_dir/mangohud.csv"
        echo "profile artifact game-power.jsonl: $run_dir/game-power.jsonl"
        echo "profile artifact restore snapshot: $run_dir/restore-affinity.json"
        echo "profile artifact runtime telemetry contract: $run_dir/runtime-telemetry-contract.json"
      done
    done
  done
done

python3 - "$REMOTE_ROOT" "$REMOTE_ROOT/profile-runtime-telemetry-contract.json" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
contracts = []
for path in sorted(root.glob("*/runtime-telemetry-contract.json")):
    payload = json.loads(path.read_text())
    contracts.append(
        {
            "path": str(path),
            "status": payload.get("status"),
            "samples": payload.get("samples"),
            "classification_samples": payload.get("classification_samples"),
            "pressure_samples": payload.get("pressure_samples"),
            "cpu_cap_action_reached": payload.get("cpu_cap_action_reached"),
        }
    )
payload = {
    "schema_version": "game-power-profile-runtime-telemetry-contract-v1",
    "status": "pass" if contracts and all(item["status"] == "pass" for item in contracts) else "fail",
    "run_contract_count": len(contracts),
    "contracts": contracts,
}
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
raise SystemExit(0 if payload["status"] == "pass" else 1)
PY

restore_state
trap - EXIT
REMOTE

scp -r "$target:$remote_root/." "$local_root/"
ssh "$target" "rm -rf '$remote_root'"
echo "profiles copied to $local_root"
echo "profile artifact action equivalence: $local_root/action-equivalence.json"
echo "profile artifact runtime telemetry aggregate: $local_root/profile-runtime-telemetry-contract.json"
if [ -f "$local_root/$failure_marker" ]; then
  cat "$local_root/$failure_marker" >&2
  exit 1
fi
