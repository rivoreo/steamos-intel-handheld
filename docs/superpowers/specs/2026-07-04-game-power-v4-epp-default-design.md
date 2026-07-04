# Game Power V4 EPP-Only Default Design

## Status

Implementation-approved iteration for the current development branch.

## Goal

Make the installed Game Power governor default to `gpu-priority` with EPP-only
actuation. Keep CPU max-frequency caps available for explicit profiler and
debug runs, but stop applying the measured P-core/E-core caps in the default
daemon path until a per-context evidence gate proves they improve the target
scene.

## Review Surface

In scope:

- `src/steamos_intel_handheld/power_control.py`
- `data/systemd/steamos-intel-handheld-power-control.service`
- `README.md`
- `docs/design.md`
- tests that lock CLI defaults, packaged systemd defaults, and user-facing docs
- local required Harness sweep
- guarded handheld deployment and Game Power verification on `root@10.100.0.19`

Out of scope for this iteration:

- replacing the Linux scheduler
- enabling sched_ext or SCX-LAVD
- automatic thread affinity or hot-thread pinning
- automatic per-AppID CPU cap winner promotion
- Decky UI changes

Those remain future scheduler-policy work, not blockers for this default safety
fix.

## Evidence

### Controlled Cyberpunk Profile

The current Cyberpunk 2077 foreground scene uses Steam AppID `1091500`. The
captured profile artifacts are under `.cache/game-power/profiles/`:

- 12W EPP-only:
  - `20260704T201459-app1091500-12w-off-baseline-before-r1`
  - `20260704T201701-app1091500-12w-gpu-priority-candidate-r1`
  - `20260704T201904-app1091500-12w-off-baseline-after-r1`
- 22W EPP-only:
  - `20260704T202213-app1091500-22w-off-baseline-before-r1`
  - `20260704T202414-app1091500-22w-gpu-priority-candidate-r1`
  - `20260704T202617-app1091500-22w-off-baseline-after-r1`
- 12W CPU-cap:
  - `20260704T202928-app1091500-12w-off-baseline-before-r1`
  - `20260704T203131-app1091500-12w-gpu-priority-cpu-cap-default-candidate-r1`
  - `20260704T203333-app1091500-12w-off-baseline-after-r1`

Measured deltas:

| Run | Candidate action | Avg FPS | 1% low | 0.1% low | Result |
| --- | --- | ---: | ---: | ---: | --- |
| 12W EPP-only | `gpu-priority-epp` | +0.00% | +3.35% | +8.40% | Helps frame pacing without harming average FPS |
| 22W EPP-only | `gpu-priority-epp` | -1.50% | +0.78% | +13.81% | Mixed average FPS, still improves deepest lows |
| 12W CPU-cap default | `gpu-priority-cpu-cap` | -0.58% | +0.33% | -5.30% | Not safe as default |

The CPU-cap run reached the cap action in 29 of 30 samples but did not improve
average FPS or deepest lows. On this device and scene, the cap is therefore a
candidate to test, not a default to ship.

### Live Sample

The live sample after the profile showed the service still running with capped
CPUFreq policy:

- high-cap policies around 3000 MHz
- low-cap policies around 2400 MHz
- package/core/uncore near 22.4W / 6.8W / 9.2W
- all game threads still broadly distributed across CPU0-CPU7

This indicates the default daemon can contaminate later profile runs unless it
is either disabled or made EPP-only by default.

## Research Summary

Linux util-clamp is a scheduler hinting mechanism. It allows userspace to set
minimum and maximum performance requirements for tasks; the kernel docs also
describe games forming an FPS feedback loop with uclamp, and Android-style
cgroup classes reserving resources for top-app work:
<https://docs.kernel.org/scheduler/sched-util-clamp.html>.

Energy Aware Scheduling uses platform energy models and CPU capacity to choose
efficient task placement, and assumes schedutil so utilization signals and CPU
frequency requests are coherent:
<https://docs.kernel.org/scheduler/sched-energy.html>.

sched_ext can host BPF schedulers and can be turned on/off dynamically with
fallback to the default scheduler on errors or stalls, making it a future
research vehicle rather than an immediate SteamOS packaging default:
<https://docs.kernel.org/scheduler/sched-ext.html>.

systemd resource control exposes cgroup CPU weights, quotas, and CPU sets.
Those are useful future tools for background shaping and affinity experiments,
but they are stronger than EPP hints and need separate validation:
<https://man.archlinux.org/man/systemd.resource-control.5.en>.

Feral GameMode is an existing Linux game-performance daemon. Its model supports
on-demand system tuning for games rather than applying every possible knob
unconditionally:
<https://github.com/FeralInteractive/gamemode>.

The aligned design rule is: prefer reversible scheduler hints as the default,
keep hard caps or placement controls behind explicit evidence, and persist only
high-level winners, not measured frequency constants.

## Decision

1. Installed daemon default remains `--game-power-mode gpu-priority`.
2. Installed daemon default changes from `--game-power-cpu-cap on` to
   `--game-power-cpu-cap off`.
3. CLI parser default changes to `--game-power-cpu-cap off`, so manual daemon
   launches and tests match the packaged service.
4. CPU-cap tunables remain present and explicit:
   `--game-power-cpu-cap on --game-power-pcore-max-mhz ...`.
5. Documentation says CPU cap is an explicit profiler/debug candidate, not the
   default automatic policy.
6. Profile scripts keep `gpu-priority-cpu-cap` so controlled A/B can continue.

## Acceptance Criteria

- The packaged systemd service includes `--game-power-mode gpu-priority` and
  `--game-power-cpu-cap off`.
- `power_control.build_parser().parse_args(["serve"])` builds a Game Power
  config with `mode == GPU_PRIORITY` and `cpu_cap_enabled is False`.
- Explicit `--game-power-cpu-cap on` still enables the cap and keeps the
  existing threshold and P/E frequency tunables.
- README and design docs no longer state that default automatic mode uses CPU
  max-frequency caps.
- Local required Harness sweep passes.
- Device deployment shows the active service ExecStart has
  `--game-power-cpu-cap off`.
- A guarded foreground-game Game Power verifier passes with CPU policy restore
  clean.

## Follow-Up Work

Future iterations should evaluate:

- a per-context winner cache that can promote CPU cap only after repeated
  controlled profile wins for the same AppID, PL1, power source, FPS target,
  topology, and OS/driver signature;
- uclamp or CPUWeight background-helper shaping for non-game helper cgroups;
- hot-thread placement advice using observed thread roles and CPU capacity,
  but without default pinning until migration and frame pacing data prove it;
- sched_ext or SCX-LAVD experiments only as opt-in research artifacts with
  clean fallback and separate deployment gates.
