#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 root@steamdeck-host" >&2
  exit 2
fi

target="$1"
appid="${PROFILE_GAME_POWER_APPID:-1091500}"
tdp_levels="${PROFILE_GAME_POWER_TDPS:-22}"
policies="${PROFILE_GAME_POWER_POLICIES:-off gpu-priority}"
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
ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control wait-and-serve --user deck --bus system --apply-rapl --apply-msi-claw-ec --ec-write-debounce-ms 750 --tdp-policy auto --msi-claw-ec-shift-policy tdp-threshold --prepare-mangohud-sensors --game-power-mode $mode --game-power-control-file $PROFILE_CONTROL_FILE --min-w 8 --max-w 30 --short-limit-max-w 37 --state-file /var/lib/steamos-intel-handheld/tdp_w
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

latest_mangohud_csv() {
  find /home/deck -maxdepth 1 -name 'mangoapp_*.csv' -type f -printf '%T@ %p\n' \
    2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-
}

start_mangohud_capture() {
  local run_dir="$1"
  if [ "$CAPTURE_MODE" = "controlled" ]; then
    touch "$run_dir/mangohud.start"
    run_as_deck sh -c 'cd /home/deck && mangohudctl set log_session false' \
      >/dev/null 2>&1 || true
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

wait_for_power_service
wait_for_power_provider
snapshot_cpu_policy >"$REMOTE_ROOT/cpu-policy.initial"
provider_tdp >"$REMOTE_ROOT/tdp.initial"
trap restore_state EXIT
set_service_game_power_mode off
setup_mangohud_controlled_capture
profile_failed=false
FPS_TARGET_SOURCE="unknown"
if [ -n "$FPS_TARGET" ]; then
  FPS_TARGET_SOURCE="manual"
  write_manual_fps_target_discovery "$REMOTE_ROOT/fps-target.discovery.json"
else
  FPS_TARGET="$(discover_fps_target "$REMOTE_ROOT/fps-target.discovery.json" || true)"
  if [ -n "$FPS_TARGET" ]; then
    FPS_TARGET_SOURCE="gamescope-cmdline"
  fi
fi

for repeat in $(seq 1 "$REPEATS"); do
  for tdp in $TDP_LEVELS; do
    set_provider_tdp "$tdp"
    sleep "$WARMUP_S"
    for policy in $POLICIES; do
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
        start_mangohud_capture "$run_dir"
        sample_cgroup_pressure "$run_dir/cgroup-pressure.jsonl" "$DURATION_S" &
        pressure_pid="$!"
        sample_thread_affinity "$run_dir/thread-affinity.jsonl" "$DURATION_S" &
        thread_affinity_pid="$!"
        sample_thread_schedstat "$run_dir/thread-schedstat.jsonl" "$DURATION_S" &
        thread_schedstat_pid="$!"
        sample_process_cgroups "$run_dir/process-cgroups.jsonl" "$DURATION_S" &
        process_cgroups_pid="$!"
        if ! /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power \
          --mode "$mode" \
          --duration-s "$DURATION_S" \
          --poll-s "$POLL_S" \
          --target-appid "$APPID" \
          --output-format jsonl \
          "${policy_args[@]}" >"$run_dir/game-power.jsonl"; then
          stop_mangohud_capture || true
          wait "$pressure_pid" || true
          wait "$thread_affinity_pid" || true
          wait "$thread_schedstat_pid" || true
          wait "$process_cgroups_pid" || true
          restore_background_shaping_variant "$run_dir" || true
          exit 1
        fi
        stop_mangohud_capture
        wait "$pressure_pid" || true
        wait "$thread_affinity_pid" || true
        wait "$thread_schedstat_pid" || true
        wait "$process_cgroups_pid" || true
        restore_background_shaping_variant "$run_dir" || restored=false

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

        /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile summarize \
          --appid "$APPID" \
          --tdp-w "$tdp" \
          --policy "$policy" \
          --capture-mode "$CAPTURE_MODE" \
          "${mangohud_args[@]}" \
          "${fps_target_args[@]}" \
          "${fps_target_source_args[@]}" \
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
          --restored "$restored" \
          --output "$run_dir"
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
      done
    done
  done
done

restore_state
trap - EXIT
REMOTE

scp -r "$target:$remote_root/." "$local_root/"
ssh "$target" "rm -rf '$remote_root'"
echo "profiles copied to $local_root"
if [ -f "$local_root/$failure_marker" ]; then
  cat "$local_root/$failure_marker" >&2
  exit 1
fi
