#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 root@steamdeck-host" >&2
  exit 2
fi

target="$1"
duration_s="${VERIFY_GAME_POWER_DURATION_S:-30}"
poll_s="${VERIFY_GAME_POWER_POLL_S:-2}"
appid="${VERIFY_GAME_POWER_APPID:-}"
epp="${VERIFY_GAME_POWER_EPP:-balance_power}"
cpu_cap="${VERIFY_GAME_POWER_CPU_CAP:-off}"
pcore_max_mhz="${VERIFY_GAME_POWER_PCORE_MAX_MHZ:-3200}"
ecore_max_mhz="${VERIFY_GAME_POWER_ECORE_MAX_MHZ:-2800}"
cpu_cap_core_share_threshold="${VERIFY_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD:-0.38}"

ssh "$target" \
  "VERIFY_GAME_POWER_DURATION_S='$duration_s' \
VERIFY_GAME_POWER_POLL_S='$poll_s' \
VERIFY_GAME_POWER_APPID='$appid' \
VERIFY_GAME_POWER_EPP='$epp' \
VERIFY_GAME_POWER_CPU_CAP='$cpu_cap' \
VERIFY_GAME_POWER_PCORE_MAX_MHZ='$pcore_max_mhz' \
VERIFY_GAME_POWER_ECORE_MAX_MHZ='$ecore_max_mhz' \
VERIFY_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD='$cpu_cap_core_share_threshold' \
bash -s" <<'REMOTE'
set -euo pipefail

duration_s="${VERIFY_GAME_POWER_DURATION_S}"
poll_s="${VERIFY_GAME_POWER_POLL_S}"
appid="${VERIFY_GAME_POWER_APPID}"
epp="${VERIFY_GAME_POWER_EPP}"
cpu_cap="${VERIFY_GAME_POWER_CPU_CAP}"
pcore_max_mhz="${VERIFY_GAME_POWER_PCORE_MAX_MHZ}"
ecore_max_mhz="${VERIFY_GAME_POWER_ECORE_MAX_MHZ}"
cpu_cap_core_share_threshold="${VERIFY_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD}"
tmpdir="$(mktemp -d)"
snapshot="$tmpdir/cpu-policy.before"
after="$tmpdir/cpu-policy.after"

snapshot_cpu_policy() {
  for policy in /sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$policy" ] || continue
    name="${policy##*/}"
    epp_value=""
    max_freq=""
    if [ -r "$policy/energy_performance_preference" ]; then
      epp_value="$(cat "$policy/energy_performance_preference")"
    fi
    if [ -r "$policy/scaling_max_freq" ]; then
      max_freq="$(cat "$policy/scaling_max_freq")"
    fi
    printf '%s\t%s\t%s\n' "$name" "$epp_value" "$max_freq"
  done | sort
}

restore_cpu_policy() {
  if [ ! -f "$snapshot" ]; then
    return 0
  fi

  while IFS=$'\t' read -r name epp_value max_freq; do
    policy="/sys/devices/system/cpu/cpufreq/$name"
    [ -d "$policy" ] || continue
    if [ -n "$epp_value" ] && [ -w "$policy/energy_performance_preference" ]; then
      printf '%s\n' "$epp_value" >"$policy/energy_performance_preference"
    fi
    if [ -n "$max_freq" ] && [ -w "$policy/scaling_max_freq" ]; then
      printf '%s\n' "$max_freq" >"$policy/scaling_max_freq"
    fi
  done <"$snapshot"
}

assert_cpu_policy_restored() {
  snapshot_cpu_policy >"$after"
  if ! diff -u "$snapshot" "$after"; then
    echo "game-power verifier: CPU policy was not restored" >&2
    exit 1
  fi
}

report_gpu_memory_fdinfo() {
  echo "== MangoHud GPU memory fdinfo candidates =="
  matches=0
  for fdinfo in /proc/[0-9]*/fdinfo/*; do
    [ -r "$fdinfo" ] || continue
    if grep -Eq 'drm-resident-(vram0|gtt|system0)' "$fdinfo"; then
      echo "-- $fdinfo"
      grep -E 'drm-resident-(vram0|gtt|system0)' "$fdinfo" || true
      matches=$((matches + 1))
    fi
    if [ "$matches" -ge 20 ]; then
      break
    fi
  done
  if [ "$matches" -eq 0 ]; then
    echo "no drm-resident-vram0, drm-resident-gtt, or drm-resident-system0 fdinfo lines found"
  fi
}

game_power_base_args=(
  --duration-s "$duration_s"
  --poll-s "$poll_s"
  --epp "$epp"
)
if [ -n "$appid" ]; then
  game_power_base_args+=(--target-appid "$appid")
fi

game_power_cap_args=()
if [ "$cpu_cap" = "on" ]; then
  game_power_cap_args+=(
    --cpu-cap
    --pcore-max-mhz "$pcore_max_mhz"
    --ecore-max-mhz "$ecore_max_mhz"
    --cpu-cap-core-share-threshold "$cpu_cap_core_share_threshold"
  )
fi

if [ ! -x /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power ]; then
  echo "missing /opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power" >&2
  exit 1
fi

snapshot_cpu_policy >"$snapshot"
trap restore_cpu_policy EXIT

report_gpu_memory_fdinfo

echo "== game-power observe =="
/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power --mode observe "${game_power_base_args[@]}"

echo "== game-power gpu-priority =="
/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power --mode gpu-priority "${game_power_base_args[@]}" "${game_power_cap_args[@]}"

restore_cpu_policy
assert_cpu_policy_restored
echo "game-power verifier: CPU policy restored"
trap - EXIT
rm -rf "$tmpdir"
REMOTE
