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
duration_s="${PROFILE_GAME_POWER_DURATION_S:-60}"
warmup_s="${PROFILE_GAME_POWER_WARMUP_S:-10}"
poll_s="${PROFILE_GAME_POWER_POLL_S:-2}"
capture_mode="${PROFILE_GAME_POWER_CAPTURE_MODE:-imported}"
epp="${PROFILE_GAME_POWER_EPP:-balance_power}"
pcore_max_mhz="${PROFILE_GAME_POWER_PCORE_MAX_MHZ:-3200}"
ecore_max_mhz="${PROFILE_GAME_POWER_ECORE_MAX_MHZ:-2800}"
cpu_cap_core_share_threshold="${PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD:-0.38}"
local_root="${PROFILE_GAME_POWER_OUTPUT_ROOT:-.cache/game-power/profiles}"
mkdir -p "$local_root"

remote_root="$(ssh "$target" "mktemp -d /tmp/game-power-profile.XXXXXX")"

ssh "$target" \
  "APPID='$appid' TDP_LEVELS='$tdp_levels' POLICIES='$policies' \
DURATION_S='$duration_s' WARMUP_S='$warmup_s' POLL_S='$poll_s' \
CAPTURE_MODE='$capture_mode' EPP='$epp' PCORE_MAX_MHZ='$pcore_max_mhz' \
ECORE_MAX_MHZ='$ecore_max_mhz' \
CPU_CAP_CORE_SHARE_THRESHOLD='$cpu_cap_core_share_threshold' \
REMOTE_ROOT='$remote_root' bash -s" <<'REMOTE'
set -euo pipefail
MANGOHUD_OUTPUT_DIR="$REMOTE_ROOT/mangohud-logs"
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
  cat >/run/systemd/system/steamos-intel-handheld-power-control.service.d/50-game-power-profile.conf <<EOF
[Service]
ExecStart=
ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control wait-and-serve --user deck --bus system --apply-rapl --apply-msi-claw-ec --ec-write-debounce-ms 750 --tdp-policy auto --msi-claw-ec-shift-policy tdp-threshold --prepare-mangohud-sensors --game-power-mode $mode --min-w 8 --max-w 30 --short-limit-max-w 37 --state-file /var/lib/steamos-intel-handheld/tdp_w
EOF
  systemctl daemon-reload
  systemctl restart steamos-intel-handheld-power-control.service
  wait_for_power_service
  wait_for_power_provider
}

restore_service_game_power_mode() {
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

for tdp in $TDP_LEVELS; do
  set_provider_tdp "$tdp"
  sleep "$WARMUP_S"
  for policy in $POLICIES; do
    run_dir="$REMOTE_ROOT/$(date +%Y%m%dT%H%M%S)-app${APPID}-${tdp}w-${policy}"
    mkdir -p "$run_dir"
    snapshot_cpu_policy >"$run_dir/cpu-policy.before"
    provider_tdp >"$run_dir/tdp.before"

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
          --pcore-max-mhz "$PCORE_MAX_MHZ"
          --ecore-max-mhz "$ECORE_MAX_MHZ"
          --cpu-cap-core-share-threshold "$CPU_CAP_CORE_SHARE_THRESHOLD"
        )
      ;;
      *)
        echo "unsupported PROFILE_GAME_POWER_POLICIES entry: $policy" >&2
        exit 2
      ;;
    esac

    start_mangohud_capture "$run_dir"
    sample_cgroup_pressure "$run_dir/cgroup-pressure.jsonl" "$DURATION_S" &
    pressure_pid="$!"
    if ! /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power \
      --mode "$mode" \
      --duration-s "$DURATION_S" \
      --poll-s "$POLL_S" \
      --target-appid "$APPID" \
      --output-format jsonl \
      "${policy_args[@]}" >"$run_dir/game-power.jsonl"; then
      stop_mangohud_capture || true
      wait "$pressure_pid" || true
      exit 1
    fi
    stop_mangohud_capture
    wait "$pressure_pid" || true

    collect_mangohud_csv "$run_dir"

    snapshot_cpu_policy >"$run_dir/cpu-policy.after"
    provider_tdp >"$run_dir/tdp.after"
    restored=true
    if ! diff -u "$run_dir/cpu-policy.before" "$run_dir/cpu-policy.after" \
      >"$run_dir/cpu-policy.diff"; then
      restored=false
    fi

    mangohud_args=(--mangohud-csv "$run_dir/mangohud.csv")
    if [ -f "$run_dir/mangohud-summary.csv" ]; then
      mangohud_args+=(--mangohud-summary-csv "$run_dir/mangohud-summary.csv")
    fi

    /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-profile summarize \
      --appid "$APPID" \
      --tdp-w "$tdp" \
      --policy "$policy" \
      --capture-mode "$CAPTURE_MODE" \
      "${mangohud_args[@]}" \
      --game-power-jsonl "$run_dir/game-power.jsonl" \
      --pressure-jsonl "$run_dir/cgroup-pressure.jsonl" \
      --epp "$EPP" \
      --pcore-max-mhz "$PCORE_MAX_MHZ" \
      --ecore-max-mhz "$ECORE_MAX_MHZ" \
      --cpu-cap-enabled "$cpu_cap_enabled" \
      --cpu-cap-core-share-threshold "$CPU_CAP_CORE_SHARE_THRESHOLD" \
      --restored "$restored" \
      --output "$run_dir"
  done
done

restore_state
trap - EXIT
REMOTE

scp -r "$target:$remote_root/." "$local_root/"
ssh "$target" "rm -rf '$remote_root'"
echo "profiles copied to $local_root"
