# Game Power Deep Research

This document is the source index and working synthesis for the next
game-power governor. It is intentionally research-first: runtime policy changes
must be derived from this evidence, not from a single Cyberpunk 2077 scene or a
fixed CPU cap assumption.

Research persistence rule: after a new source or design insight materially
changes the scheduler direction, append it here before relying on conversation
context. This document is the recovery point after context compaction.

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

Source:
https://github.com/flightlessmango/MangoHud

Key finding:
MangoHud monitors FPS, frame timing, CPU/GPU load, CPU/GPU watts, CPU MHz, and
benchmark percentiles. In gamescope sessions it must be used through
`mangoapp`; normal MangoHud injection with gamescope is not supported. Its
options also include Intel core-type display, CPU frames-per-joule efficiency,
and gamescope app frametime/latency debug output.

Design impact:
MangoHud/mangoapp is a strong profiler and artifact source, especially for
percentiles and before/after evidence. It should not be the only runtime source
for control-loop decisions if a lower-latency gamescope or Steam target signal
is available.

Source:
https://github.com/NGnius/PowerTools

Key finding:
PowerTools is an existing Decky power-user plugin. It can disable CPU threads
and SMT, set CPU frequencies, set GPU frequency and power controls, show battery
data, and persist per-game settings under a game-id keyed config file.

Design impact:
There is clear user demand for power-user Steam UI controls. However, this
project should avoid copying direct write behavior into the Decky layer. The
plugin should ask the root service to apply reversible policy and should present
per-game state as experiment history, not as hard-coded policy.

Source:
https://github.com/aarron-lee/SimpleDeckyTDP

Key finding:
SimpleDeckyTDP ships per-game TDP profiles, TDP limits, power governor/EPP
controls, SMT, CPU boost, AC/suspend-resume handling, and polling. Intel support
is experimental and is explicitly built around the `intel_pstate` scaling
driver. Its docs warn that CPU boost can cause excessive power draw on some
handhelds and that overlapping control surfaces can conflict.

Design impact:
The game-power governor should feature-detect `intel_pstate`, EPP, boost, and
TDP controls instead of assuming them. It also needs conflict detection for
other Decky/system performance tools so two controllers do not fight over the
same package-power contract.

Source:
https://github.com/hhd-dev/hhd

Key finding:
Handheld Daemon provides Linux hardware enablement for many Windows handhelds,
including TDP controls, fan curves, controller emulation, SteamOS shortcuts, RGB,
and a gamescope overlay/desktop app. Its supported devices list includes MSI
Claw variants.

Design impact:
The governor should behave like one part of the handheld power stack, not like
the only owner of the device. Runtime code should expose a clear API boundary,
detect known competing services when possible, and preserve exact restore
semantics so it can coexist with HHD/adjustor-style control stacks.

Source:
https://wiki.deckbrew.xyz/en/plugin-dev/getting-started

Key finding:
Decky plugins have a normal structure with `plugin.json`, `package.json`,
frontend TypeScript under `src/`, and optional Python backend code in
`main.py`. The frontend can call backend functions through the provided
`ServerAPI`.

Design impact:
Decky is a good control surface for game-power policy, but not the right place
for the core privileged scheduler. The plugin should call a stable service API
to toggle the governor, select profile intent, set FPS target overrides, start
A/B profiling, and render results.

Source:
https://github.com/SteamDeckHomebrew/decky-plugin-template

Key finding:
The Decky plugin template is the reference starting point. It uses `@decky/ui`
for frontend work, supports Python and custom backend code, and documents
plugin-store distribution expectations. Backend binaries belong under
`backend/out` during build and are packaged under `bin/` for distribution.

Design impact:
If this project ships a Decky plugin, it should be a separate package boundary
with a small API client, not a copy of the root governor. Store-readiness will
require clean metadata, license handling, reproducible builds, and a conservative
permission story.

### Linux Scheduler And Power Interfaces

Source:
https://docs.kernel.org/scheduler/sched-capacity.html

Key finding:
Linux capacity-aware scheduling models heterogeneous CPUs as different capacity
classes. CPU capacity depends on microarchitecture and maximum frequency. The
scheduler uses CPU and frequency invariant task utilization and checks whether a
task fits the CPU capacity. `uclamp` can influence this placement by clamping
the utilization value seen by CFS.

Design impact:
P-core/E-core behavior must be modeled from topology and measurements, not from
one global CPU frequency knob. The governor should record per-policy
`affected_cpus`, `cpu_capacity`, current/max frequency, EPP, task/cgroup
utilization pressure, and FPS outcome before choosing caps or placement hints.

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
https://raw.githubusercontent.com/torvalds/linux/master/drivers/thermal/intel/intel_hfi.c

Key finding:
Intel's Hardware Feedback Interface reports per-CPU performance and energy
efficiency capability information. Hardware may update those capabilities as
power limits or thermal constraints change, and the driver relays updates to
userspace.

Design impact:
If the target kernel exposes HFI signals, they are the right dynamic input for
per-core Max-Q and performance/efficiency classification. If not available, the
fallback is static `cpu_capacity` plus measured per-policy efficiency curves.

Source:
https://raw.githubusercontent.com/torvalds/linux/master/arch/x86/kernel/itmt.c

Key finding:
Intel Turbo Boost Max Technology support lets the scheduler prefer logical CPUs
whose cores have higher turbo capability by assigning scheduler core priorities.

Design impact:
The governor should observe existing kernel priority/topology information and
avoid fighting the scheduler's own asymmetric-capacity choices. Manual affinity
or pinning should remain an experiment until evidence shows it improves frame
pacing without hurting portability.

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

Source:
https://docs.kernel.org/scheduler/sched-ext.html

Key finding:
sched_ext lets user space load BPF-defined schedulers, group CPUs, and switch
the BPF scheduler on and off dynamically. The kernel restores default scheduling
when errors or stalls are detected, and state is visible under
`/sys/kernel/sched_ext`.

Design impact:
sched_ext is a future research lane, not the first default policy. It could
eventually express "foreground game gets low-latency capacity, background work
uses idle capacity" more directly than cgroup hints, but only when the target
kernel enables it and the profiler proves improvement beyond EPP/uclamp/cgroup
controls.

Source:
https://raw.githubusercontent.com/sched-ext/scx/main/scheds/rust/scx_lavd/README.md

Key finding:
`scx_lavd` is a sched_ext scheduler implementing latency-criticality aware
virtual deadline scheduling. It measures task latency criticality and uses that
information for deadline, time-slice, and other scheduling decisions. It is
motivated by gaming, targets high throughput with low tail latency, and creates
separate scheduling domains by LLC, core type, and NUMA domain.

Design impact:
This is the clearest upstream-adjacent direction for a truly generic game
scheduler. It does not require per-game hard-coded affinity. The first step for
this repo should be compatibility/profiling, not bundling it as default, because
the current device state previously reported sched_ext disabled.

Source:
https://raw.githubusercontent.com/sched-ext/scx/main/OVERVIEW.md

Key finding:
sched_ext's overview frames modern scheduling as harder because of
heterogeneous CPUs, dynamic frequency scaling, chiplet/cache topology, strict
mobile/VR latency requirements, and stacked workloads. It also describes
experiments where machine learning predicted whether a task would soon yield so
the scheduler could decide whether to keep it on the current CPU rather than
migrating it to an idle CPU.

Design impact:
Automatic game affinity should be behavior-driven, not rule-table-driven. Useful
features include recent wake/sleep cadence, run-queue delay, yield probability,
CPU migration rate, last CPU, core type, LLC domain, and frame-time correlation.

### Thread Affinity And Core Placement

Source:
https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html

Key finding:
Linux affinity is per thread. Restricting a thread to one CPU can reduce cache
invalidation from migration, but the kernel may further intersect the requested
mask with cpuset constraints. Setting affinity can require `CAP_SYS_NICE` when
controlling another user's thread.

Design impact:
Hard affinity is powerful but dangerous. The governor must snapshot existing
per-thread affinity masks before experiments, restore them exactly, and avoid
default hard pinning because it can reduce available CPU time or fight cpuset
state.

Source:
https://man7.org/linux/man-pages/man3/pthread_setaffinity_np.3.html

Key finding:
Thread-level affinity can be set from inside a process with
`pthread_setaffinity_np()`. New threads inherit a copy of the creator's CPU
affinity mask.

Design impact:
External affinity control cannot assume only current threads matter. A game can
spawn new render, streaming, or shader threads after the governor starts. Any
advisor must track `/proc/<pid>/task` continuously and avoid one-shot setup.

Source:
https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

Key finding:
cgroup v2 cpuset files define the CPUs granted to a cgroup. `cpuset.cpus` can
inherit from ancestors, `cpuset.cpus.effective` shows the actual CPUs available,
and partition roots can create exclusive or isolated CPU partitions.

Design impact:
The generic first step is cgroup-level soft shaping and compact CPU sets, not
per-TID hard pinning. Steam app cgroups give a reversible boundary for
foreground game plus background scopes, while exclusive/isolated partitions are
too disruptive for the default handheld policy.

Source:
https://learn.microsoft.com/en-us/windows/win32/procthread/cpu-sets

Key finding:
Windows CPU Sets provide a soft affinity API compatible with OS power
management. Process default CPU sets can move background threads to a subset of
processors, while thread-selected CPU sets override the process default. Hard
affinity masks still take precedence over conflicting CPU Set assignments.

Design impact:
The Windows direction is a useful model for SteamOS: prefer soft preferred CPU
sets and background-thread containment over hard masks. The closest Linux
equivalents are cgroup cpuset/uclamp hints today and sched_ext placement hints
later.

Source:
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadaffinitymask

Key finding:
Windows hard affinity masks restrict where a thread can run, but Microsoft
warns that setting a mask can reduce processor time and that in most cases the
system should select the processor.

Design impact:
This supports the same safety rule on Linux: hard pinning should be opt-in,
measured, reversible, and limited to hot threads whose migration correlates with
frame-time misses.

Source:
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadidealprocessor

Key finding:
Windows exposes a preferred processor API. The OS schedules the thread on that
processor whenever possible, rather than making it the only legal CPU.

Design impact:
The ideal algorithm for this project should act more like "preferred
processor/CPU set" than "only CPU". If Linux lacks a direct per-thread soft
affinity primitive in the current kernel, emulate this with scheduler hints,
compact cgroups, or sched_ext rather than strict masks.

Source:
https://docs.kernel.org/trace/events.html

Key finding:
Linux event tracing can enable scheduler tracepoints such as `sched_wakeup`,
filter events by PID, and inspect trace event fields. This can be used without
building custom kernel modules.

Design impact:
The affinity profiler should use tracepoints or `perf sched`-style captures to
measure migration, wakeup, sleep, and run-queue behavior for game threads. A
thread should not be pinned just because it has high CPU time; it should be
considered only when it is latency-sensitive, migrates frequently, and its
migration/queue delay correlates with frame-time spikes.

Source:
https://arxiv.org/abs/2604.27915

Key finding:
Affinity Tailor argues for dynamic locality-aware scheduling with demand-sized,
topologically compact CPU sets used as hints rather than hard partitions. A
userspace controller estimates workload CPU demand online and chooses compact
sets that preserve locality while allowing execution elsewhere when needed.

Design impact:
This maps well to games. The default strategy should be "adaptive compactness":
estimate foreground game runnable demand, prefer a compact P-core/LLC set for
hot latency threads, keep background work away, and preserve escape capacity
instead of forcing static core partitions.

Current design note:
The practical automatic-affinity path for this project is not raw `taskset` or
one-time TID pinning. The safer generic path is:

1. observe hot foreground threads and background/helper cgroups,
2. shape non-foreground work first when it steals CPU/package headroom,
3. express foreground placement as preferred compact CPU sets where the kernel
   has a soft mechanism (`uclamp`, cgroup weights/cpuset experiments, or
   sched_ext/LAVD),
4. reserve hard per-TID affinity only for guarded profiler variants with exact
   restore and run-queue-delay evidence.

This mirrors the common lesson from Windows CPU Sets, scx_lavd, and Affinity
Tailor: latency-sensitive work benefits from locality, but strict partitions
can create local queueing and visible frame pacing regressions.

### Automatic Thread-Affinity Control Synthesis

Sources:
https://developer.android.com/reference/android/os/PerformanceHintManager.Session
https://docs.kernel.org/scheduler/sched-util-clamp.html
https://learn.microsoft.com/en-us/windows/win32/procthread/cpu-sets
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadaffinitymask
https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html
https://man7.org/linux/man-pages/man1/perf-sched.1.html
https://raw.githubusercontent.com/FeralInteractive/gamemode/master/example/gamemode.ini
https://raw.githubusercontent.com/sched-ext/scx/main/scheds/rust/scx_lavd/README.md
https://arxiv.org/abs/2604.27915

Key finding:
The strongest cross-platform pattern is not "pin every hot thread." Android's
performance hints model groups threads, compares actual work duration to a
target, and lets the platform adjust core placement and frequency. Windows CPU
Sets similarly provide preferred processor sets that still let the scheduler
choose among valid CPUs, while Microsoft warns that hard affinity can reduce
available processor time. Linux exposes hard per-thread masks through
`sched_setaffinity()`, but those masks are intersected with cpuset constraints,
can require elevated privileges for other users' threads, and are inherited by
forked children. GameMode's current config shows that Linux gaming tools already
experiment with core pinning/parking and CPU/iGPU power balance, but its
controls are still profile-like rather than a frame-pacing feedback scheduler.

Design impact:
The generic SteamOS strategy should be a staged controller:

1. classify stable thread roles across repeated controlled runs, not raw TIDs,
2. keep background/helper shaping as the first write-capable experiment because
   it is less likely to starve foreground work,
3. express foreground locality as a soft compact preferred-set experiment when
   the platform has a suitable primitive,
4. keep hard per-TID affinity profiler-only until repeated A/B data proves it
   improves 1% low or p99 frametime without average-FPS or restore regression,
5. use sched_ext/LAVD as the long-term upstream-aligned path when the target
   kernel exposes it, because it already models gaming as high-throughput,
   low-tail-latency scheduling with domains by LLC and core type.

The controller's machine-readable gates should be conservative:

- both baseline and candidate runs must be controlled captures,
- both sides must meet the repeated-run count and exact-restore checks,
- the candidate policy must already compare as better on FPS, low-percentile
  frame pacing, or target-sustained power saving,
- a foreground-game role must appear in most candidate runs,
- that role must be latency-hot, have preferred latency-CPU overlap, and show
  runqueue-wait or migration-harm evidence,
- the resulting plan must still be advisory until a guarded writer snapshots
  and restores every touched affinity/cgroup/uclamp file.

Current implementation note:
`steamos-intel-handheld-game-power-profile aggregate` now emits an
`affinity_experiment_plan` alongside `candidate_affinity_roles`. This converts
the research into a next-run decision artifact. A `ready-for-guarded-experiment`
plan means the profiler has enough repeated performance, role-stability, and
rollback-snapshot evidence to test a soft compact foreground-role placement
variant next; it does not mean production hard affinity should be enabled.

Source:
https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html

Key finding:
Linux affinity is a per-thread eligibility mask. The kernel may silently
intersect a requested mask with cpuset restrictions, and changing the mask can
migrate the thread immediately if it is running outside the new mask. The man
page names the upside clearly: avoiding cache invalidation when a thread moves
between CPUs. It also implies the main risk for games: hard affinity is a
restriction, not only a hint.

Source:
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadaffinitymask

Key finding:
Windows exposes the same distinction. `SetThreadAffinityMask` is a hard
restriction and Microsoft's own guidance says this can cause a thread to
receive less processor time because the scheduler is restricted. The companion
`SetThreadIdealProcessor` API is weaker: it sets a preferred processor and lets
the system honor it when possible.

Design impact:
The generic handheld scheduler should treat hard per-TID affinity as the last
stage, not the default. The safer first design is:

1. detect stable roles across runs, not raw TIDs,
2. prefer soft placement or compact preferred CPU sets where the platform has
   the primitive,
3. shape background/helper cgroups before restricting foreground game threads,
4. require repeated controlled A/B evidence and restore snapshots before any
   writer is allowed.

Source:
https://github.com/sched-ext/scx
https://raw.githubusercontent.com/sched-ext/scx/main/OVERVIEW.md

Key finding:
`sched_ext` is the upstream Linux path for rapidly testing alternative
schedulers in BPF and unloading them dynamically. The overview explicitly calls
out modern power-limited, cache-heterogeneous, latency-sensitive systems as the
reason scheduling needs easier experimentation. It also describes dynamic CPU
selection as an optimization hint rather than a final binding decision.

Design impact:
If the target kernel exposes sched_ext, the ideal long-term path is not a
pile of per-game pinning rules. It is a Steam-session-aware scheduling policy
that can classify latency-sensitive foreground roles, background helpers, core
type, LLC/cache locality, and package-power pressure continuously. The current
Python profiler should therefore produce the same evidence a future sched_ext
policy would need: stable roles, runqueue wait, migrations, topology, cgroup
state, and A/B verdicts.

Source:
https://www.open-mpi.org/projects/hwloc/

Key finding:
`hwloc` exists because topology-aware placement is hard to do portably. It
models NUMA nodes, packages, shared caches, cores, SMT threads, cgroups, hybrid
CPUs, GPU/I/O locality, and exposes binding APIs. Its examples also show the
placement trade-off: tightly cooperating tasks may benefit from shared cache,
while independent memory-heavy tasks should be spread out.

Design impact:
Automatic affinity must be topology-aware. For this handheld that means at
least P-core/E-core class, CPUFreq policy domain, SMT sibling, shared cache or
effective capacity when exposed, and future GPU/I/O locality. A raw "pin hot
thread to CPU 0" rule is not upstreamable; a topology-aware role-to-compact-set
algorithm is.

Source:
https://raw.githubusercontent.com/FeralInteractive/gamemode/master/example/gamemode.ini
https://github.com/FeralInteractive/gamemode

Key finding:
GameMode already contains CPU/iGPU power-balance logic plus optional core
pinning/parking, but its own sample config restricts automatic pin/park
autodetection to known hybrid or X3D layouts. It is a useful signal that the
Linux gaming ecosystem is moving toward core-class-aware placement, but also a
warning that broad automatic hard pinning is fragile.

Design impact:
The project should avoid copying a static pinning table. Use GameMode-like
integration as a compatibility signal, but keep this scheduler telemetry-first:
profile, compare, cache by AppID/topology/kernel/TDP/FPS target fingerprint,
and revalidate after stack changes.

Source:
https://docs.kernel.org/admin-guide/cgroup-v2.html

Key finding:
cgroup v2's process and thread migration files have explicit permission and
domain constraints. `cgroup.threads` can move individual TIDs only within the
same resource domain, and `cgroup.procs` moves whole processes. The same
document describes `cpu.uclamp.min` and `cpu.uclamp.max` as cgroup-wide
utilization clamp controls which affect all processes in that cgroup.

Design impact:
Every write-mode affinity/uclamp experiment needs a pre-run restore seed. The
profiler should snapshot foreground thread affinity masks, foreground cgroup
paths, and cgroup CPU controller files before any writer is introduced. A
future writer can then refuse to run if this snapshot is missing, incomplete, or
not restorable.

Current implementation note:
Each guarded device profile run now writes `restore-affinity.json` before the
policy runner starts. The snapshot is still read-only. It records foreground
Steam-app TIDs, original `Cpus_allowed` / `Cpus_allowed_list` masks, cgroup
membership, and cgroup files for `cpu.uclamp.*`, `cpu.weight`, `cpu.max`, and
`cpuset.*`. The summarizer records the snapshot's thread count, cgroup count,
and file list in `summary.json`, and the aggregate planner refuses to mark an
affinity experiment ready unless every aggregated baseline and candidate run
has a non-empty restore-affinity snapshot.

Source:
https://docs.kernel.org/scheduler/sched-util-clamp.html

Key finding:
Utilization clamp is a scheduler hinting mechanism. `UCLAMP_MIN` expresses a
minimum performance requirement, `UCLAMP_MAX` expresses an upper bound, and
the cgroup controller exposes `cpu.uclamp.min` and `cpu.uclamp.max`. The same
documentation warns that severe `UCLAMP_MAX` caps can distort PELT signals and
cause frequency spikes when capped and uncapped tasks share a runqueue.

Design impact:
Background/helper shaping should be soft and guarded. Prefer small A/B steps
on `cpu.weight` or `cpu.uclamp.max`, reject on 1% low/p99 regression or
restore mismatch, and avoid severe caps that can confuse scheduler frequency
signals.

### Academic And Research Literature

Source:
https://arxiv.org/abs/2604.27915

Key finding:
Affinity Tailor argues that Linux-style load balancing can spread workloads too
widely and damage locality in caches, branch predictors, prefetchers, and LLC
domains. Its key idea is a userspace controller that estimates each workload's
CPU demand online and assigns a demand-sized, topologically compact preferred
CPU set. The kernel treats that set as an affinity hint, not a hard partition,
so work can still run elsewhere when needed to preserve utilization.

Design impact:
This is the best currently found blueprint for a generic automatic game
affinity controller. Adapt the idea to a handheld game session:

1. workload equals foreground game role, Steam/gamescope helper role, or system
   helper cgroup,
2. demand comes from CPU-time deltas, runqueue wait, timeslices, and frame
   pacing,
3. topology compactness comes from P/E class, CPUFreq policy, SMT sibling, and
   shared-cache data when available,
4. preferred sets are advisory experiment outputs until a kernel or cgroup
   primitive can express them without hard pinning,
5. capacity may be left intentionally imperfect if it improves 1% low/p99
   frame pacing and keeps GPU package headroom.

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

The CPU model must be data-driven:

- Do not treat all CPU work as one global knob. P-core, E-core, unknown, and
  future LP E-core domains need separate observation and policy records.
- Build per-policy efficiency curves by TDP level. Each row should include
  policy class, `affected_cpus`, `scaling_driver`, min/max/current frequency,
  EPP, optional HFI performance/efficiency capability, package/core/uncore
  watts, CPU pressure, FPS target, and frame-time outcome.
- Use HFI when exposed because it reflects dynamic thermal and power-limit
  conditions. Fall back to `cpu_capacity` and profiler-derived curves.
- Per-policy EPP is only safe when task placement is also controlled or
  understood. Otherwise prefer a conservative global EPP and cgroup/uclamp
  hints.
- Max-Q limits for P-core/E-core domains must be learned per device and guarded
  by 1% low and p99 frame-time checks. A policy that saves CPU watts but lowers
  GPU frametime stability is rejected.
- Reserve foreground CPU caps for evidence-backed GPU-bound scenes. First try
  background work shaping, EPP, and uclamp so game render/game threads do not
  lose latency capacity.

## Automatic Thread Affinity Direction

A generic game affinity layer should be an observer/advisor first, then a
controlled experiment path, then a policy. It should not ship as fixed per-game
pinning rules.

The 2026-07-04 research conclusion is that "automatic affinity" should be
implemented as adaptive placement, not as a universal hard-pin rule. Public
interfaces and research point in the same direction:

- Linux `sched_setaffinity()` is per-thread and can avoid migration-related
  cache loss, but it is a hard eligibility mask and can be further restricted
  by cpuset state.
- Linux scheduler statistics are counter-based, so user space should record a
  baseline and calculate deltas. `/proc/<pid>/schedstat` exposes time on CPU,
  time waiting on a runqueue, and timeslice count; the same file exists under
  `/proc/<pid>/task/<tid>/schedstat`, which makes it a low-risk first signal
  for per-thread game latency before enabling tracefs.
- Windows CPU Sets model the safer default: process/thread CPU preferences are
  reconciled by the OS, and restrictive affinity masks take precedence only
  when explicitly set.
- Linux cgroup v2 cpusets can express compact CPU eligibility for scopes, but
  exclusive or isolated partitions are disruptive and should remain guarded
  experiments.
- Affinity Tailor's demand-sized compact sets and sched_ext/LAVD's
  latency-criticality model are better generic shapes than static per-game
  pinning.
- `scx_lavd` is the closest open-source reference shape for this direction:
  its README describes a gaming-motivated, latency-criticality-aware virtual
  deadline scheduler which considers CPU topology such as LLC domains and
  P/E-core classes. That supports our design choice to classify behavior from
  telemetry rather than ship a hand-written per-game affinity table.
- `perf sched timehist` is a useful future escalation path because it reports
  wait time, scheduler delay, and run time per task event. The current
  `/proc/<pid>/task/<tid>/schedstat` sampler is intentionally cheaper and less
  invasive; trace/perf windows should be reserved for short captures around
  frame-time spikes.

Current implementation state: the guarded device profiler now emits
`thread-affinity.jsonl` and `thread-schedstat.jsonl` for the foreground Steam
app cgroup. It samples TID, thread name, CPU-time counter, migration counter,
voluntary and involuntary context-switch counters, scheduler run time,
runqueue wait time, timeslice count, current CPU, affinity mask, and cgroup
path. The summary ranks hot threads by CPU-time delta, preserves
migration/context-switch deltas, and now adds runqueue-wait deltas to
`affinity-advice.json`. The advisor also emits `role_candidates`, grouping raw
TIDs into stable signatures such as `foreground-game:worker-thread`. Raw TIDs
are useful inside one capture, but role keys are the only acceptable unit for
cross-launch A/B comparison or future policy caches. This is intentionally
observe-only; it creates the evidence needed for later affinity A/B experiments
without changing scheduler state.

Primary references used for this slice:

- Linux `sched_setaffinity(2)`:
  https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html
- Linux scheduler statistics:
  https://docs.kernel.org/scheduler/sched-stats.html
- Linux cgroup v2:
  https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux trace events:
  https://docs.kernel.org/trace/events.html
- Linux perf sched:
  https://man7.org/linux/man-pages/man1/perf-sched.1.html
- Linux sched_ext:
  https://docs.kernel.org/scheduler/sched-ext.html
- Windows CPU Sets:
  https://learn.microsoft.com/en-us/windows/win32/procthread/cpu-sets
- sched-ext `scx_lavd` README:
  https://github.com/sched-ext/scx/tree/main/scheds/rust/scx_lavd

### Signals To Collect

- TID inventory from `/proc/<pid>/task`, including `comm`, parent process,
  cgroup, current affinity mask, and current CPU.
- Per-thread CPU time deltas, voluntary/involuntary context switches, and
  migration count when exposed through `/proc/<pid>/task/<tid>/sched`.
- Per-thread scheduler runtime, runqueue wait time, and timeslice deltas from
  `/proc/<pid>/task/<tid>/schedstat`. A hot thread with high CPU time but low
  runqueue wait is a different tuning target from a hot thread that frequently
  waits several milliseconds before running.
- Scheduler tracepoint or `perf sched` windows for wakeup, switch, migration,
  and run-queue delay around frame-time spikes.
- Core topology: P/E/unknown class, SMT siblings, LLC domain, CPU capacity,
  HFI/ITMT hints when available.
- Frame pacing: target frame time, p95/p99, 1% low, and spike timestamps from
  gamescope or MangoHud/mangoapp.
- Power attribution: package/core/uncore watts so affinity experiments do not
  solve stutter by starving the iGPU.

### Classification

The governor should classify threads by behavior over a sliding window:

- `latency-critical-hot`: high CPU time or frequent wakeups, high run-queue
  delay sensitivity, frame-time correlation, and repeated migration.
- `latency-critical-light`: low CPU time but frequent wakeups near frame
  boundaries; candidate for `uclamp.min` or preferred P-core placement.
- `throughput-worker`: sustained CPU work with low frame-time correlation; keep
  compact but not necessarily on best cores.
- `background/helper`: launcher, overlay, shader compile, IO, network, crash
  reporter, or unrelated app scopes; shape with cgroup controls before touching
  foreground game threads.

### Algorithm Candidates

1. Observe-only hotspot detector
   - Rank TIDs by CPU time, wakeup cadence, run-queue delay, migration rate,
     and frame-time correlation.
   - Group TIDs into role signatures by foreground/background cgroup role and
     normalized thread name, then emit recommendations only; no affinity
     writes.

2. Soft compact placement
   - Keep foreground game threads eligible on enough CPUs, but bias hot threads
     toward a compact P-core/LLC set and move background work away with cgroup
     cpuset/uclamp/weight.
   - This follows the Windows CPU Sets and Affinity Tailor model: preference
     and compact locality before hard partitioning.

3. Selective hard affinity experiment
   - Only in profiler mode, pin one or two repeatedly identified hot TIDs to a
     small P-core set for a short A/B run.
   - Reject if average FPS, 1% low, p99, package balance, or restore gets worse.
   - Never persist by thread ID alone because TIDs and engine thread layouts
     change across launches and updates.
   - Future write-mode experiments should select a role signature from repeated
     captures, map it to the live TID set at runtime, snapshot original masks,
     apply for one bounded run, and restore immediately after the run.

4. sched_ext/LAVD experiment
   - When sched_ext is available, test `scx_lavd` or a future custom scheduler
     that uses latency-criticality and topology domains instead of external
     per-thread masks.
   - This is the most upstreamable long-term route because the scheduler can
     make placement decisions at wakeup and preserve escape capacity.

### Acceptance Rules

- Prefer soft placement when it improves p99/1% low without reducing average
  FPS more than the existing policy thresholds.
- Reject hard pinning when the pinned thread spends measurable time waiting for
  its selected CPU while other suitable CPUs are idle.
- Reject any affinity policy that increases core watts enough to lower iGPU
  uncore/frequency headroom in GPU-bound scenes.
- Re-learn after game updates, Proton changes, driver/kernel changes, topology
  changes, or FPS target/TDP changes.
- Store affinity observations as profiler artifacts, not as permanent manual
  profiles, until repeated controlled captures validate the same pattern.
- Store cross-run affinity evidence by role key plus AppID/topology/kernel/
  Proton/TDP/FPS-target fingerprint. Do not cache raw TID, because it is a
  launch-local implementation detail rather than a stable game behavior.
- Aggregate role evidence across repeated controlled runs before acting. A role
  should be considered for any write-mode experiment only if it has good
  run-coverage in the same AppID/TDP/FPS-target/topology bucket and the
  aggregated FPS/power comparison is not already rejected.

## Decky Plugin Control Surface

The Decky plugin should be treated as an optional UI and experiment surface:

- Toggle the system governor: off, observe, automatic, profiling.
- Show current FPS target, actual FPS, 1% low, p99 frametime, package/core/
  uncore watts, CPU pressure, and active policy action.
- Select intent: battery, balanced, performance, quiet, custom.
- Override FPS target for a game session when Steam/gamescope target discovery
  is unavailable or ambiguous.
- Start a guided A/B run across selected TDP levels and policy candidates.
- Show restore health: CPU policy restored, cgroup restored, TDP restored,
  service drop-ins absent, last error.
- Expose advanced tuning only under an expert gate:
  - EPP target,
  - P-core/E-core max frequency experiment variants,
  - foreground `uclamp.min`,
  - background `uclamp.max`,
  - CPU pressure thresholds,
  - frame-time guard band.

The plugin should not directly write RAPL, CPUFreq, cgroup, or gamescope state.
The safer model is:

```text
Decky frontend
    -> Decky Python backend
        -> steamos-intel-handheld D-Bus or CLI API
            -> root system service
                -> snapshot, apply, observe, restore
```

This keeps privileged writes in the already tested restore boundary and makes
Decky removable without leaving scheduler state behind.

Existing plugins prove the UI demand but also define the safety boundary:

- PowerTools and SimpleDeckyTDP expose raw controls power users expect: TDP,
  CPU frequency, SMT, EPP/governor, boost, GPU controls, and per-game profiles.
- This project should expose intent first: target FPS, battery/balanced/
  performance/quiet, observe/automatic/profiling mode, and expert overrides.
- The plugin must show when another controller appears active and should avoid
  applying policy in conflict-heavy states unless the service can prove exact
  ownership and restore.
- Store-ready packaging should follow Decky conventions: `plugin.json`,
  `package.json`, frontend `dist/`, optional `main.py`, license, and backend
  binaries under the expected `backend/out` to plugin `bin/` flow when needed.

## Automatic Thread Affinity Deep Dive

This section is a persistent research checkpoint for the generic automatic
thread-affinity direction. It intentionally avoids per-game pinning tables. The
portable target is adaptive placement: observe thread behavior, choose compact
preferred CPU sets when there is evidence of migration or topology harm, and
only use hard affinity in short, reversible profiler experiments.

Source:
https://docs.kernel.org/scheduler/sched-capacity.html

Key finding:
Linux capacity-aware scheduling already models heterogeneous CPUs with
capacity, frequency-invariant utilization, CPU-invariant utilization, wakeup CPU
selection, and misfit migration. Uclamp can influence wakeup CPU selection by
changing the utilization bounds that the scheduler compares against CPU
capacity.

Design impact:
The governor should not duplicate the scheduler's basic P/E-core logic. It
should feed better game-context hints into existing placement and frequency
machinery: FPS target, foreground game scope, render-busy state, CPU pressure,
and package-power state. If a task already "fits" on a selected CPU and frame
pacing is stable, forcing affinity is unnecessary risk.

Source:
https://docs.kernel.org/scheduler/sched-util-clamp.html

Key finding:
Uclamp is explicitly designed as a user-space hinting interface for task
performance bounds, and the kernel documentation calls out FPS feedback loops
for games and background-task capping on mobile/heterogeneous systems.

Design impact:
The first generic control should be uclamp/cgroup shaping, not per-TID hard
pinning. Foreground latency threads can receive a limited `uclamp.min` only
when CPU-side frame misses are observed. Background/helper scopes can receive
`uclamp.max` or lower weight when they interfere with the foreground game.

Source:
https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

Key finding:
cgroup v2 has both process and thread organization primitives. `cgroup.threads`
can move individual TIDs within a threaded subtree, and the `cpu` and `cpuset`
controllers support threaded mode. The same document also warns that frequent
cgroup migration is relatively expensive; workloads should normally be
organized once, then tuned through controller files.

Design impact:
Thread-level cgroup placement is possible, but it is not the default runtime
tool. A practical handheld governor should prefer stable game/background
boundaries, then adjust controller values. Moving individual TIDs into special
threaded cgroups is a guarded experiment for a small number of repeatedly
identified hot threads, not a continuous per-frame controller.

Source:
https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html

Key finding:
Linux CPU affinity is per thread, inherited across fork/exec, and intersected
with cpuset constraints. Setting affinity on another user's thread can require
privilege.

Design impact:
Hard affinity must snapshot every original mask, restore exactly, and track
newly spawned threads. It is unsuitable as the first default because it can
reduce legal CPU capacity, conflict with cpuset policy, and accidentally inherit
into later child work.

Source:
https://learn.microsoft.com/en-us/windows/win32/procthread/cpu-sets

Key finding:
Windows CPU Sets expose soft affinity that remains compatible with OS power
management. Process default CPU Sets can move background threads to a subset of
processors, while thread-selected CPU Sets override the process default. A hard
affinity mask still takes precedence when present.

Design impact:
This is the right product model for SteamOS even though Linux exposes different
primitives: prefer "where this task should usually run" over "where this task
is allowed to run". On current Linux, emulate that with cgroup/uclamp/cpuset
and later sched_ext placement hints rather than strict masks.

Source:
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadidealprocessor

Key finding:
Windows also exposes a preferred-processor API: the OS schedules the thread on
that processor whenever possible, but it is not the only legal processor.

Design impact:
The advisor should output preferred CPU/core-set recommendations before it
outputs hard masks. If the current kernel cannot apply a soft per-thread
preference, keep the recommendation as an artifact or test it through a
sched_ext scheduler that can make wakeup-time placement decisions.

Source:
https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadaffinitymask

Key finding:
Microsoft documents hard affinity as a restrictive mask and warns that in most
cases it is better to let the system select an available processor because a
mask can reduce the processor time available to the thread.

Design impact:
The acceptance gate for any hard-pinning experiment must include run-queue
delay and missed-idle evidence. If a pinned thread waits while another suitable
P-core or SMT sibling is idle, the policy is rejected even if average FPS looks
flat.

Source:
https://raw.githubusercontent.com/FeralInteractive/gamemode/master/README.md

Key finding:
GameMode is a Linux daemon/library for temporary game optimizations and already
supports CPU governor, I/O priority, nice, scheduler policy, GPU performance
mode, CPU core pinning/parking, and custom scripts.

Design impact:
GameMode proves that game-triggered temporary host tuning is a normal Linux
shape. It also reinforces that the tuning boundary must be session-scoped and
reversible. This project should stay compatible with GameMode-style activation
instead of assuming it owns the whole system forever.

Source:
https://raw.githubusercontent.com/FeralInteractive/gamemode/master/example/gamemode.ini

Key finding:
GameMode's example configuration now includes CPU/iGPU power-balance logic,
`igpu_power_threshold`, and CPU core pinning/parking. The comments say automatic
pin/park detection currently targets selected Ryzen X3D and Intel P/E-core
systems.

Design impact:
There is already ecosystem demand for CPU/GPU balance and P/E-aware pinning,
but the public configuration is still heuristic and platform-limited. Our
generic scheduler should treat this as a compatibility and conflict-detection
surface, not as an algorithm to copy blindly.

Source:
https://github.com/FeralInteractive/gamemode/tree/master/daemon

Key finding:
GameMode's daemon documentation warns about priority inversion and inconsistent
FPS when stronger scheduler priority or CPU binding interacts badly with busy
loops, graphics drivers, CPU count, or architecture.

Design impact:
This is directly relevant to games: boosting or pinning the game can starve the
driver/compositor path and make frame pacing worse. The governor must measure
the whole presentation path, not just the main game process CPU time.

Source:
https://raw.githubusercontent.com/NGnius/PowerTools/main/README.md

Key finding:
PowerTools exposes manual Decky controls for CPU threads/SMT, CPU frequencies,
GPU frequencies/power, charge behavior, and per-game persistence.

Design impact:
Decky is a good expert UI for visibility and opt-in experiments, but raw
thread/frequency controls are too sharp for automatic defaults. The default
service should expose intent and telemetry; expert Decky controls should be
clearly separate from automatic policy ownership.

Source:
https://raw.githubusercontent.com/sched-ext/scx/main/scheds/rust/scx_lavd/README.md

Key finding:
`scx_lavd` implements Latency-criticality Aware Virtual Deadline scheduling,
was initially motivated by gaming workloads, targets interactivity and reduced
stutter, and creates scheduling domains by LLC, core type, and NUMA domain.

Design impact:
This is the closest public shape to a generic game scheduler. Instead of
guessing thread names or static pins, it classifies latency criticality and
uses topology domains. For this project, sched_ext/LAVD should become a guarded
future experiment when the target kernel exposes sched_ext.

Source:
https://raw.githubusercontent.com/sched-ext/scx/main/OVERVIEW.md

Key finding:
sched_ext exists because modern scheduling is hard across heterogeneous cores,
dynamic frequency scaling, chiplet/cache topology, mobile/VR latency targets,
and stacked workloads. Its CPU selection callback is an optimization hint, not
a binding decision, and sched_ext has system-integrity rollback when a scheduler
misbehaves.

Design impact:
This supports a two-track plan: current kernels use observer/advisor plus
reversible cgroup/uclamp experiments; future kernels test a sched_ext policy
that can make wakeup-time preferred-placement decisions without hard masks.

Source:
https://arxiv.org/abs/2604.27915

Key finding:
Affinity Tailor argues that strict partitioning wastes capacity, while
demand-sized, topologically compact CPU sets used as hints preserve locality
without forbidding escape execution. A userspace controller estimates workload
CPU demand online and chooses compact sets that span as few LLC domains as
possible.

Design impact:
This is the best generic algorithmic template for SteamOS game affinity:
estimate foreground game CPU demand, choose a compact preferred set with guard
capacity, keep background work away from that set when it interferes, and keep
escape capacity available. On an Intel handheld, the compact set should also
respect P/E-core class, SMT sibling state, HFI/ITMT hints, and the current TDP.

Source:
https://raw.githubusercontent.com/torvalds/linux/master/drivers/thermal/intel/intel_hfi.c

Key finding:
Intel HFI reports per-CPU performance and energy-efficiency capabilities, and
hardware may update those capabilities when power limits or thermal constraints
change.

Design impact:
Core choice is not static. The governor should record HFI capability snapshots
when available and treat core quality as dynamic under handheld power/thermal
limits. If HFI is unavailable, fall back to CPU capacity, CPUFreq policy class,
and measured per-policy efficiency curves.

Source:
https://raw.githubusercontent.com/torvalds/linux/master/arch/x86/kernel/itmt.c

Key finding:
Intel ITMT lets the scheduler prefer cores with higher maximum turbo
capability by setting per-CPU scheduler core priorities and rebuilding
scheduler domains when support changes.

Design impact:
The governor should not override ITMT with static masks unless profiling proves
that the scheduler's preferred high-turbo cores still cause frame-time misses.
ITMT priority is an input to the preferred-set ranking, not something to ignore.

### Generic Affinity Algorithm Shape

1. Observe, never write:
   - sample TID inventory, CPU-time deltas, current CPU, allowed mask,
     voluntary/involuntary context switches, migration counters, cgroup path,
     CPU pressure, package/core/uncore watts, render busy, and frame pacing.
   - add tracefs or `perf sched` windows later for wakeup delay and run-queue
     latency around p99 frame spikes.

2. Classify behavior:
   - `latency-hot`: high CPU time or frequent wakeups, repeated migration, and
     frame-time correlation.
   - `latency-light`: short but frequent frame-boundary work that suffers from
     ramp-up latency.
   - `throughput-worker`: sustained work with weak frame-time correlation.
   - `background/helper`: launcher, overlay, shader compile, IO/network/helper,
     or unrelated scopes that can be shaped before foreground threads.

3. Estimate demand and choose preferred sets:
   - estimate foreground runnable demand over a sliding window.
   - choose a compact set sized to demand plus guard capacity.
   - rank CPUs by core class, HFI/ITMT/capacity, SMT sibling pressure,
     LLC/topology compactness, current thermals/power, and measured per-TDP
     efficiency.
   - when CPU-limited below target, prefer latency threads on stronger cores.
   - when GPU-limited and CPU core watts are stealing iGPU headroom, avoid
     foreground boosts and first move or cap background/helper work.

4. Apply only reversible controls:
   - default: advisor output plus cgroup/uclamp shaping.
   - next: compact foreground/background cpuset experiments in profiler mode.
   - last: selective hard affinity for one or two stable hot thread roles, only
     during short A/B captures with exact restore.

5. Accept by frame pacing and power balance:
   - require p99/1% low improvement or equal pacing with lower watts at target.
   - reject if run-queue delay grows, driver/compositor work is starved,
     package core watts lower iGPU headroom, or restore differs from snapshot.
   - cache only by AppID plus topology/kernel/driver/Proton/TDP/FPS-target
     fingerprint; never cache by raw TID.

### Profiler Artifacts

- `cpu-topology.json`: CPU to policy, core type, SMT sibling, LLC/domain,
  capacity, HFI/ITMT hints when present, max frequency, and EPP state.
- `thread-schedstat.jsonl`: read-only foreground-thread scheduler counters:
  run time, runqueue wait time, timeslice count, current CPU, and cgroup. The
  summarizer converts counters to deltas and adds runqueue-wait fields to
  `affinity-advice.json`.
- `affinity-advice.json`: observe-only ranking of hot thread roles, migration
  harm score, runqueue-wait score, preferred set candidates, stable role
  candidates, and explicit reasons for no-op.
- `process-cgroups.jsonl`: read-only process-level CPU-time and cgroup samples
  for foreground game, Steam helpers, gamescope/mangoapp, user, and system
  scopes.
- `background-shaping.json`: observe-only cgroup candidates for background or
  helper work to shape before touching foreground game thread affinity.
- `restore-affinity.json`: read-only pre-run rollback seed containing
  foreground thread affinity masks, cgroup membership, and cgroup
  `cpu.uclamp.*`, `cpu.weight`, `cpu.max`, and `cpuset.*` files.
- Aggregate reports now include `baseline_affinity_roles` and
  `candidate_affinity_roles` when sibling `affinity-advice.json` files exist.
  These summaries keep median CPU time, migration count, runqueue wait,
  runqueue-wait-per-slice, migration harm, run coverage, and CPU-set overlap
  per stable role key across the included runs.
- Aggregate reports also include `baseline_background_shaping_candidates`,
  `candidate_background_shaping_candidates`, and
  `background_shaping_experiment_plan` when sibling `background-shaping.json`
  files exist. These summaries keep stable non-foreground cgroup keys, median
  CPU time, median process count, command names, run coverage, and a disabled
  write policy for a future guarded background-helper soft-cap experiment.

### Profiler Artifacts To Add Next

- `sched-trace.jsonl`: optional guarded tracefs/perf-sched window around
  frame-time spikes with wakeup, switch, migration, and run-queue delay.

## Research Matrix

| Area | Source signal/control | Governor use | Main risk | First validation |
| --- | --- | --- | --- | --- |
| FPS target | Steam/gamescope target, Steam UI cap, refresh divisor, Decky override | Convert the user target into `target_frame_ms`; stop chasing FPS above target | Target source may be missing or stale | Compare discovered target against visible Steam/gamescope setting |
| Frame pacing | gamescope app frametimes, MangoHud/mangoapp CSV and summaries | Accept/reject policies by 1% low, p99, and variance before average FPS | Logging can be delayed or imported instead of controlled | Short A/B capture with known static scene and repeated samples |
| CPU topology | `cpu_capacity`, CPUFreq policy domains, HFI, ITMT/core priority | Classify P-core/E-core/unknown domains and build per-domain curves | Missing or inconsistent kernel exposure | Device probe of `/sys/devices/system/cpu` and CPUFreq policies |
| Thread affinity | `/proc/<pid>/task`, `sched` data, tracepoints, cgroup cpuset, sched_ext/LAVD | Detect hot latency threads, reduce harmful migrations, keep background work away | Hard pinning can reduce CPU time or fight scheduler placement | Observe-only migration/run-queue trace before any writes |
| CPU controls | EPP, scaling max freq, foreground/background `uclamp`, cgroup weights | Bias placement/frequency and reserve package headroom without hard pinning | Foreground latency regression or scheduler conflict | Profiler variants gated by 1% low and exact restore |
| Shared power | RAPL package/core/uncore/psys energy deltas, SteamOS TDP/PL1 | Attribute CPU vs uncore/iGPU package share over stable windows | RAPL is energy delta, not instant power | Same-window energy diff with policy snapshots |
| Background contention | cgroup CPU/IO/memory PSI, process tree, AppID session | Shape non-critical work before capping foreground game threads | Misidentifying game helper threads as background | Observe-only process/cgroup inventory before writes |
| Decky UI | ServerAPI to Python backend to service API | Toggle modes, show telemetry, launch guided A/B, expose expert overrides | Direct UI writes can leave unsafe state or conflict with tools | Plugin prototype with read-only service calls first |
| Existing handheld tools | PowerTools, SimpleDeckyTDP, HHD/adjustor | Borrow UX patterns and detect controller conflicts | Double controllers fighting TDP/EPP/frequency | Conflict detection and explicit ownership display |
| sched_ext | `/sys/kernel/sched_ext`, scx scheduler experiments | Future optional low-latency/background-idle scheduler lane | Kernel support and ABI instability | Separate guarded experiment only when enabled |

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

## Current Implementation Slices

- The guarded profiler accepts an explicit `PROFILE_GAME_POWER_FPS_TARGET`
  override and stores `fps_target`, `target_frame_ms`,
  `avg_fps_target_ratio`, and `fps_target_met` in every run summary.
- When the explicit override is absent, the wrapper makes a best-effort
  read-only pass over live `/proc/*/cmdline` entries whose executable name
  contains `gamescope` and parses the focused `-r` frame-rate limit before the
  `--` command separator. The result is stored in `fps-target.discovery.json`
  with `fps_target_source` and confidence metadata.
- Profile aggregation treats FPS target as part of the experiment context, so
  40 FPS, 45 FPS, 60 FPS, and uncapped runs do not share one median bucket.
- The comparison gate now has a target-sustained power-saving path: if baseline
  and candidate both keep average FPS at or above 98% of the target, low/p99
  pacing does not regress beyond the rejection guard, restore is exact, and the
  candidate reduces package watts by at least 5%, the candidate can be accepted
  even when raw FPS does not increase.
- The guarded profiler now emits `cpu-topology.json` for each run by reading
  `/sys/devices/system/cpu`, CPU topology, and CPUFreq policy files. The
  summarizer uses it with `thread-affinity.jsonl` and
  `thread-schedstat.jsonl` to write
  `affinity-advice.json`, an observe-only advisor that ranks hot thread roles,
  preferred latency CPUs, migration harm, runqueue wait, and explicit no-write
  reasons.
- `affinity-advice.json` now includes `role_candidates`. Each role key combines
  cgroup role and normalized thread name, for example
  `foreground-game:worker-thread`, then aggregates CPU time, migration count,
  runqueue wait, seen CPUs, preferred CPU overlap, and suggested action across
  matching TIDs. This is the first cross-run unit for automatic affinity A/B
  analysis.
- The aggregate CLI now reads sibling `affinity-advice.json` files next to each
  included `summary.json` and emits per-policy role stability summaries. This
  lets repeated A/B reports show both the policy-level FPS/power result and the
  scheduler roles that were consistently latency-hot during those runs.
- The aggregate CLI now also reads sibling `background-shaping.json` files and
  emits per-policy background/helper cgroup stability summaries plus
  `background_shaping_experiment_plan`. A background plan is marked
  `ready-for-guarded-experiment` only when repeated controlled runs, restore
  checks, cgroup CPU-controller snapshot coverage, a better policy verdict, and
  stable helper-cgroup evidence all pass. The write policy remains disabled.
- The guarded profiler now emits `thread-schedstat.jsonl` for each run by
  sampling read-only `/proc/<pid>/task/<tid>/schedstat` for foreground Steam
  app cgroups. This is the first automatic-affinity latency signal because it
  measures how long a hot thread waited to run, not only whether it migrated.
- The guarded profiler now emits `process-cgroups.jsonl` for each run by
  sampling read-only `/proc/<pid>/cgroup`, `/proc/<pid>/comm`, and
  `/proc/<pid>/stat` data. The summarizer writes `background-shaping.json`,
  an observe-only advisor that ranks non-foreground Steam helper,
  gamescope/mangoapp, user, and system cgroups before any foreground affinity
  experiment is considered.
- This is still only the first automatic FPS-target source, not a proven
  SteamOS target oracle. The remaining work is to validate it against the Steam
  client setting, gamescope state, gamescopectl, stats pipe, and MangoHud on a
  live game session.

## Research Gaps

- Identify the most reliable SteamOS source for FPS target:
  Steam client setting, gamescope state, gamescopectl, stats pipe, or MangoHud.
- Determine whether the target handheld exposes enough CPU capacity/topology
  data to distinguish P-core, E-core, and any LP E-core policy domains.
- Measure per-policy frequency, package/core/uncore watts, and FPS outcome
  across TDP values to build an empirical efficiency curve.
- Investigate whether gamescope stats can provide frametime or app-present
  timestamps with lower latency than MangoHud CSV summaries.
- Determine which per-thread scheduler signals are available without invasive
  kernel changes beyond the current `/proc/<pid>/task/<tid>/sched` and
  `/proc/<pid>/task/<tid>/schedstat` capture: tracefs scheduler events,
  `perf sched`, eBPF, or sched_ext monitor output.
- Build a migration/run-queue-delay profile for foreground games and correlate
  it with p99 frame-time spikes before trying any hard affinity.
- Evaluate cgroup background controls before foreground CPU caps.
- Evaluate whether memory bandwidth or IO pressure explains 1% low drops in
  scenes where package power is already near PL1.

## Current Design Direction

The recommended direction is a generic FPS-target, topology-aware governor:

- activation is game-scoped,
- objective is target frame-time and pacing, not maximum raw FPS,
- primary controls are EPP and cgroup/uclamp,
- automatic thread-affinity work starts as observe-only hotspot detection and
  soft compact placement, not fixed per-game pinning,
- CPU max frequency caps are measured variants, not default behavior,
- CPU topology affects every control choice,
- AppID is an artifact grouping key and optional cache key, not a hard-coded
  policy rule,
- every experiment must include repeated A/B runs, exact restore evidence, and
  low-percentile frame-time acceptance gates.
