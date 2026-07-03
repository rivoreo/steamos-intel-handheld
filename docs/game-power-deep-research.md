# Game Power Deep Research

This document is the source index and working synthesis for the next
game-power governor. It is intentionally research-first: runtime policy changes
must be derived from this evidence, not from a single Cyberpunk 2077 scene or a
fixed CPU cap assumption.

## Research Goal

Design a generic SteamOS game-power governor for Intel handhelds that:

- uses FPS target and frame-time budget as the primary control objective,
- improves average FPS only when low-percentile frame pacing is not harmed,
- reduces power when the game is already meeting the FPS target,
- balances CPU, iGPU, uncore, memory, and platform power inside the SteamOS TDP
  contract,
- is CPU-topology aware across P-cores, E-cores, and policy domains,
- works across games through telemetry and A/B profiling instead of per-game
  hard-coded policy,
- restores every touched control exactly.

## Source Classes

### Android ADPF, Game Mode, And FPS Throttling

Source:
https://developer.android.com/games/optimize/adpf

Key finding:
Android exposes game-oriented APIs because mobile SoCs have dynamic thermal
state, changing CPU clocks, and mixed CPU core types. ADPF is explicitly meant
to let games interact with power and thermal behavior so performance can stay
sustainable and predictable.

Design impact:
The SteamOS governor should not be a blind CPU limiter. It needs a sustainable
performance loop that observes target frame time, thermal/power headroom,
foreground game state, and CPU topology.

Source:
https://developer.android.com/reference/android/os/PerformanceHintManager.Session

Key finding:
Android performance hint sessions are built around target work duration and
actual work duration feedback. Android can adjust core placement and frequency
to bring actual work duration close to the target. API level 35 also exposes a
power-efficiency preference for threads.

Design impact:
Our closest user-space equivalent is an FPS-target controller:

```text
target_frame_ms = 1000 / fps_target
actual_frame_ms = observed frametime percentile or frame period
error = actual_frame_ms - target_frame_ms
```

Controls should be selected from that error and from bottleneck attribution.
When below target, protect the bottleneck. When above target with stable pacing,
save power.

Source:
https://developer.android.com/games/optimize/adpf/gamemode/fps-throttling

Key finding:
Android's platform-level FPS throttling is explicitly framed as a way to limit
frame rate, reduce GPU/system power, and smooth unpaced games. The API exposes
separate performance and battery mode targets, and recommends divisors of the
display refresh rate.

Design impact:
The governor should treat "above target FPS" as wasted work, not as success to
maximize. Target selection should prefer Steam/gamescope limit when available,
then refresh-rate divisors and user TDP profile as fallback.

Source:
https://developer.android.com/games/optimize/adpf/gamemode/gamemode-interventions

Key finding:
Android Game Mode interventions are platform-level, game-scoped, and mode
aware. The platform can apply interventions when the game has not opted into
the richer game mode API.

Design impact:
SteamOS can follow the same structure: use AppID/session identity only as
activation and artifact grouping. The runtime policy itself should be generic
and telemetry-driven.

Source:
https://developer.android.com/games/sdk/frame-pacing

Key finding:
Android treats frame pacing as a first-class game optimization area, separate
from raw average FPS.

Design impact:
The governor must optimize frame-time percentiles and variance. A policy that
raises average FPS but worsens 1% low or p99 frametime is a rejected policy.

### GPU Vendor FPS Target And Battery Policies

Source:
https://www.amd.com/en/products/software/adrenalin/radeon-software-chill.html

Key finding:
Radeon Chill is a game power-saving feature that regulates frame rate based on
in-game movement. It can run up to a cap during intense motion and lower FPS
when there is little screen motion. AMD frames target frame rate as a way to
reduce heat, noise, and power.

Design impact:
Movement-aware policy is a later feature, but the main idea is important:
frame-rate headroom can be converted to power savings instead of unused FPS.

Source:
https://www.nvidia.com/en-us/geforce/technologies/battery-boost/

Key finding:
NVIDIA BatteryBoost targets a smooth 30+ FPS battery experience, turns on when
gaming unplugged, regulates GPU performance, adjusts to complex scenes, and
lets users set a target frame rate.

Design impact:
The target should be user-visible and policy-mode aware. Battery mode can
prefer lower FPS targets and power savings; AC mode can prefer higher target
headroom. Both still need low-percentile pacing protection.

### SteamOS, Gamescope, And Linux Game Tooling

Source:
https://github.com/ValveSoftware/gamescope

Key finding:
Gamescope is the SteamOS session compositor, can directly flip game frames in
embedded sessions, can spoof resolution/refresh rate, and exposes a frame-rate
limit option (`-r`) and an unfocused limit option (`-o`).

Design impact:
gamescope is the most natural source or proxy for FPS target, foreground state,
and frame pacing state. The governor should prefer compositor/Steam target
signals over guessing from MangoHud logs.

Source:
https://github.com/FeralInteractive/gamemode

Key finding:
GameMode is a Linux daemon/library allowing games to request temporary host OS
and game-process optimizations. It supports CPU governor changes, I/O priority,
process niceness, scheduler policy, GPU performance mode, CPU core pinning or
parking, and custom scripts.

Design impact:
Game-scoped host policy is established practice on Linux. Our governor should
stay compatible with GameMode concepts: temporary activation, exact restore,
and process-scoped controls rather than permanent system-wide state.

### Linux Scheduler And Power Interfaces

Source:
https://docs.kernel.org/scheduler/sched-util-clamp.html

Key finding:
Utilization clamping is a scheduler hint interface. It exposes lower and upper
performance bounds and affects schedutil frequency selection. Kernel docs
explicitly describe a game feedback loop where perceived FPS drives dynamic
uclamp changes to avoid dropped frames.

Design impact:
uclamp should become the preferred fine-grained control before hard CPU
frequency caps:

- foreground game `uclamp.min` can prime CPU-bound or frame-prep-limited scenes,
- background scopes `uclamp.max` can reserve CPU/GPU package headroom,
- foreground `uclamp.max` should only be used when evidence proves CPU boost is
  wasting package power without helping frame pacing.

Source:
https://docs.kernel.org/admin-guide/cgroup-v2.html

Key finding:
cgroup v2 exposes `cpu.uclamp.min`, `cpu.uclamp.max`, `cpu.max`, `cpu.weight`,
and per-cgroup `cpu.pressure`.

Design impact:
Steam app cgroups give a reversible scope for foreground and background
controls. The profiler should snapshot touched cgroup files and fail the run if
restore is not exact.

Source:
https://docs.kernel.org/accounting/psi.html

Key finding:
PSI quantifies latency spikes and throughput loss caused by CPU, memory, and IO
contention. It also supports threshold monitoring over windows.

Design impact:
PSI is the governor's stutter attribution input. CPU PSI can indicate game
thread starvation; IO or memory PSI can explain 1% low drops that CPU/GPU power
shaping cannot fix.

Source:
https://docs.kernel.org/scheduler/schedutil.html

Key finding:
schedutil chooses CPU frequency from scheduler utilization signals, util_est,
and uclamp. The docs call out DVFS ramp-up behavior and rate-limited frequency
updates.

Design impact:
The controller needs hysteresis and look-ahead. Fast per-frame toggles are
wrong; policy should operate over stable windows, with separate emergency
boost/restore paths for sustained misses.

Source:
https://docs.kernel.org/scheduler/sched-energy.html

Key finding:
Energy Aware Scheduling uses CPU capacity, energy models, and current
utilization to choose efficient task placement without harming throughput. It
applies to heterogeneous CPU topologies and falls back when over-utilized.

Design impact:
The SteamOS governor should learn an approximate user-space energy model for
this device:

- P-core policy domains: higher peak, higher package pressure,
- E-core policy domains: lower peak, better background and support work target,
- LP E-core or unknown domains if exposed in future: background/idle only until
  measured,
- per-domain efficiency curves must be measured across TDP levels instead of
  assumed.

Source:
https://docs.kernel.org/admin-guide/pm/intel_pstate.html

Key finding:
Intel HWP exposes Energy Performance Preference through CPUFreq policy sysfs.
EPP lets user space bias hardware P-state selection toward performance or
energy efficiency. The docs warn that different hints on different CPUs can
produce undesirable outcomes unless task placement is controlled.

Design impact:
Global EPP is safe but coarse. Per-core or per-policy EPP only makes sense if
the governor also controls placement or cgroup/core affinity. The current
EPP-only policy is a conservative baseline, not the final CPU-aware design.

Source:
https://docs.kernel.org/power/powercap/powercap.html

Key finding:
Linux powercap exposes hierarchical power zones. Intel RAPL can report package,
core, and uncore energy counters and package constraints. Intel RAPL does not
provide instantaneous power; power must be derived from energy deltas.

Design impact:
The governor should keep SteamOS TDP/PL1 as the package contract and use
RAPL deltas as low-frequency attribution, not as an instantaneous per-frame
control input.

### Academic And Research Literature

Source:
https://arxiv.org/abs/1808.09651

Key finding:
Integrated CPU-GPU processors have shared thermal and power budgets. CPU/GPU
DVFS and workload scheduling are coupled, and architecture/topology differences
matter for performance, power, and temperature.

Design impact:
The controller must be coupled: CPU decisions are GPU power decisions on an
integrated handheld. CPU caps, EPP, uclamp, and affinity are all package-power
allocation knobs, not isolated CPU knobs.

Source:
https://arxiv.org/abs/2405.03831

Key finding:
CPU-GPU shared power management is a system-wide optimization problem involving
co-scheduling, resource partitioning, and power capping. Predictive modeling can
outperform naive even power splits.

Design impact:
The profiler should store enough data to build a simple predictive model later:
policy, TDP, target FPS, core/uncore share, render busy, CPU PSI, IO/memory PSI,
EPP, frequency caps, uclamp, and outcome percentiles.

Source:
https://arxiv.org/abs/2306.01691

Key finding:
Variable frame timing affects perceived smoothness even when average FPS looks
acceptable.

Design impact:
Average FPS is secondary. The policy comparator must keep 1% low, p99
frametime, and variance as first-class acceptance metrics.

Source:
https://arxiv.org/abs/2305.06782

Key finding:
Counter-based CPU/GPU subsystem power models on heterogeneous platforms can be
lightweight enough for online dynamic power management.

Design impact:
The first governor should be rule-based, but profiling artifacts should support
future per-device learned curves. The model should be per-device and per-TDP,
not a universal constant.

Source:
https://arxiv.org/abs/1712.08738

Key finding:
Integrated CPU-GPU systems can see GPU slowdown from co-running CPU memory
activity because CPU and GPU share main memory bandwidth.

Design impact:
Package power is not the only contention path. The profiler must record memory
pressure and possibly memory bandwidth proxies when available; CPU limiting may
improve GPU performance by reducing memory/fabric contention even when package
watts do not fully explain the result.

## CPU-Aware Control Model

The next governor should classify CPU control at four levels:

1. Package-level contract
   - SteamOS TDP / RAPL PL1 remains the sustained total-power target.
   - The governor does not silently raise PL1.

2. Policy-domain controls
   - CPUFreq policy domains expose EPP and max frequency.
   - Current code already classifies policies into P-core, E-core, and unknown
     from `cpu_capacity` when available.
   - Static max-frequency caps are experiment-only until repeated profiling
     proves they do not hurt 1% low.

3. Cgroup/task controls
   - Foreground game scope can receive `uclamp.min` when CPU-starved.
   - Background scopes can receive `uclamp.max`, `cpu.weight`, or `cpu.idle`
     only when restore is exact and foreground pacing benefits.
   - Foreground `uclamp.max` is a last resort for GPU-bound scenes with excess
     CPU package share.

4. Placement/affinity controls
   - P-core placement is for latency-sensitive render/game threads when CPU
     frametime misses target.
   - E-core placement is for background, helper, shader compile, launcher, and
     non-critical work when that improves pacing.
   - Pinning is risky because games have different threading models. It should
     start as a profiler candidate and advisor output, not as default runtime
     policy.

## First Control Loop Shape

The governor should reason in states:

```text
no-game:
    restore all controls

profiling/observe:
    collect FPS target, frame timing, RAPL, fdinfo, PSI, CPU policy, cgroups

below-target-cpu-limited:
    prefer foreground uclamp.min / P-core protection
    avoid CPU caps

below-target-gpu-limited-with-high-core-share:
    prefer EPP balance_power or background caps
    try foreground CPU max cap only in profiler variants

at-target-stable:
    reduce power gradually via EPP/uclamp/background controls
    preserve p99 frame-time guard band

above-target:
    preserve target FPS and reduce power; do not chase extra FPS

unstable-or-unknown:
    restore or observe-only
```

## Research Gaps

- Identify the most reliable SteamOS source for FPS target:
  Steam client setting, gamescope state, gamescopectl, stats pipe, or MangoHud.
- Determine whether the target handheld exposes enough CPU capacity/topology
  data to distinguish P-core, E-core, and any LP E-core policy domains.
- Measure per-policy frequency, package/core/uncore watts, and FPS outcome
  across TDP values to build an empirical efficiency curve.
- Investigate whether gamescope stats can provide frametime or app-present
  timestamps with lower latency than MangoHud CSV summaries.
- Evaluate cgroup background controls before foreground CPU caps.
- Evaluate whether memory bandwidth or IO pressure explains 1% low drops in
  scenes where package power is already near PL1.

## Current Design Direction

The recommended direction is a generic FPS-target, topology-aware governor:

- activation is game-scoped,
- objective is target frame-time and pacing, not maximum raw FPS,
- primary controls are EPP and cgroup/uclamp,
- CPU max frequency caps are measured variants, not default behavior,
- CPU topology affects every control choice,
- AppID is an artifact grouping key and optional cache key, not a hard-coded
  policy rule,
- every experiment must include repeated A/B runs, exact restore evidence, and
  low-percentile frame-time acceptance gates.
