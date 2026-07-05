# Game Power Thread Coloring Research

Date: 2026-07-05

Status: research report after V7/V8 implementation and on-device profiling.
This is not an implementation plan. It is the evidence-backed direction for a
future V9 scheduler iteration.

## Executive Summary

"Thread coloring" should be treated as a practical design model, not as a
promise that user space can infer game engine logic. The useful interpretation
is:

- classify observed threads and cgroups into stable runtime roles,
- assign each role a "color" that maps to scheduler intent,
- translate the color into the least invasive Linux control that can be
  verified and restored,
- keep hard per-thread affinity as a guarded experiment until repeated
  frame-time evidence proves it helps.

The V8 run proved that automatic foreground affinity can be applied and restored
cleanly on the MSI Claw test target. It did not prove a performance win. In the
2026-07-05 profiles for AppID `1903340`, V8 wrote two `Foreground Work` threads
to CPUs `2,3` and restored them to `0-7` at 12 W, 17 W, and 30 W, but all
aggregate verdicts stayed `inconclusive`.

The next effective iteration should therefore move from "pin this role" to
"color roles, score risk, then choose an actuator." In practice, V9 should
default to observing and learning role colors, prefer cgroup-level soft controls
for background/helper pressure, and only enable per-thread hard affinity after
an AppID/scene/TDP policy has repeated controlled evidence.

## Source Base

Primary sources reviewed:

- Linux `sched_setaffinity(2)`:
  https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html
- Linux `taskset(1)`:
  https://man7.org/linux/man-pages/man1/taskset.1.html
- Linux cgroup v2 CPU, pressure, cpuset, and uclamp interfaces:
  https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux utilization clamping:
  https://docs.kernel.org/scheduler/sched-util-clamp.html
- Linux `uclampset(1)`:
  https://man7.org/linux/man-pages/man1/uclampset.1.html
- Linux CPU topology sysfs:
  https://docs.kernel.org/admin-guide/cputopology.html
- Linux scheduler statistics:
  https://docs.kernel.org/scheduler/sched-stats.html
- Linux Energy Aware Scheduling:
  https://docs.kernel.org/scheduler/sched-energy.html
- Intel Hardware Feedback Interface:
  https://docs.kernel.org/arch/x86/intel-hfi.html
- Linux sched_ext:
  https://docs.kernel.org/scheduler/sched-ext.html
- sched-ext scheduler collection:
  https://github.com/sched-ext/scx
- Feral GameMode:
  https://github.com/FeralInteractive/gamemode
- Decky PowerTools:
  https://github.com/NGnius/PowerTools
- Affinity Tailor:
  https://arxiv.org/abs/2604.27915

## What The Sources Support

### 1. Hard affinity is real but dangerous as a default

Linux exposes per-thread affinity through `sched_setaffinity(2)`. The affinity
mask limits the CPUs a thread is eligible to run on. The kernel may additionally
intersect the requested mask with cpuset/cgroup restrictions. `taskset(1)` is
the practical command-line interface used by the V8 profiler.

Implication for this project:

- Per-TID affinity is a valid actuator.
- It must be treated as thread-local and transient.
- Restore evidence is mandatory because game worker TIDs can disappear or
  change role.
- The controller must record both the requested mask and the observed effective
  mask.

V8 already implements this minimum evidence shape:

- `foreground-affinity-writes.json`
- `foreground-affinity-restore.json`
- `foreground_affinity_valid_evidence`
- hot-thread samples showing whether the target role actually ran inside the
  requested mask.

The missing piece is not the primitive. The missing piece is proof that hard
affinity improves pacing for a stable role.

### 2. Soft locality is better than static partitioning

The Affinity Tailor paper is the closest match to the "thread coloring" idea.
Its core point is that normal load balancing spreads work, which can weaken
cache and locality behavior, while strict CPU partitioning can waste capacity.
Their design uses demand-sized compact CPU sets as affinity hints rather than
hard partitions.

Implication for this project:

- The best long-term model is not "pin game thread X to CPU Y".
- The better model is "role A prefers this compact CPU set but can escape when
  the system needs capacity."
- Linux user space does not currently provide a direct "soft affinity hint"
  equivalent to Affinity Tailor for CFS, so we approximate it through staged
  controls:
  - observe-only coloring,
  - background/helper cgroup shaping,
  - foreground uclamp/nice/EPP hints where safe,
  - guarded hard affinity only after evidence gates pass,
  - sched_ext as a future route for true soft steering.

### 3. cgroup controls are safer for background and helper roles

cgroup v2 exposes CPU bandwidth (`cpu.max`), CPU pressure (`cpu.pressure`),
uclamp files (`cpu.uclamp.min`, `cpu.uclamp.max`), weights, and cpuset
partitioning. Utilization clamping can protect minimum utilization or cap
maximum utilization at cgroup scope.

Implication for this project:

- Background/helper shaping should usually be cgroup-level, not TID-level.
- `cpu.weight` / `cpu.uclamp.max` are better first actuators for Steam helpers,
  overlay helpers, plugin workers, and noisy user/session helpers.
- `cpuset.cpus.partition=isolated` is too heavy for normal handheld gameplay.
  The kernel docs explicitly frame isolated partitions as requiring careful task
  distribution.
- Any cgroup write must snapshot and restore the original files, because Decky,
  Steam, GameMode, HHD, gamescope, and system services can coexist.

### 4. Topology must be a first-class input

Linux exports topology through sysfs: siblings, core masks, cluster/die/package
IDs where available. Intel HFI can provide per-CPU performance and energy
efficiency capability information that Linux can use for task placement on
supported hardware.

Implication for this project:

- A "color" cannot map to fixed CPU IDs globally.
- A color maps to a topology-derived CPU set:
  - latency-preferred compact set,
  - throughput spread set,
  - efficiency/preferred background set,
  - avoid-SMT-sibling set if sibling contention is observed,
  - avoid-compositor set if gamescope or overlay contention is observed.
- The profiler should store CPU sets as topology facts plus resolved CPU lists.
  The resolved list is evidence for a given machine, not a portable policy.

### 5. schedstat is a strong coloring signal

Linux scheduler statistics expose CPU runtime, runqueue wait time, and number of
timeslices at `/proc/<pid>/schedstat`. The current profiler already collects
per-thread schedstat deltas.

Implication for this project:

- Role coloring should be based on deltas, not static names alone.
- Useful signals:
  - CPU time delta,
  - runqueue wait ratio,
  - runqueue wait per slice,
  - CPUs seen,
  - affinity mask changes,
  - cgroup role,
  - comm stability across runs,
  - foreground AppID coverage,
  - frame-time impact after intervention.

### 6. sched_ext is the real future for soft thread coloring

sched_ext lets BPF schedulers implement custom scheduling policies and can run
partially or system-wide depending on how the scheduler is loaded. The scx
project explicitly frames sched_ext as a way to iterate on scheduling strategies
rapidly, and upstream Linux has sched_ext support starting with kernel 6.12.

Implication for this project:

- V9 should not depend on sched_ext, because SteamOS target state and scheduler
  availability vary.
- But V9 should shape data contracts so a future sched_ext controller can reuse
  them:
  - AppID,
  - role color,
  - topology compact set,
  - demand estimate,
  - escape allowance,
  - frame-time verdict.

## Current Device Evidence From V7/V8

Target:

- `root@10.100.0.19`
- AppID `1903340`
- controlled MangoHud/mangoapp profiles
- 30 FPS manual target
- TDP points: 12 W, 17 W, 30 W

V7 `gpu-priority` scan:

| TDP | Baseline FPS | Candidate FPS | Baseline 1% Low | Candidate 1% Low | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| 12 W | 19.8 | 19.9 | 18.407 | 18.587 | inconclusive |
| 17 W | 25.95 | 25.9 | 24.513 | 24.640 | inconclusive |
| 30 W | 28.55 | 28.5 | 27.108 | 26.764 | inconclusive |

V8 `gpu-priority-affinity` scan:

| TDP | Baseline FPS | Candidate FPS | Baseline 1% Low | Candidate 1% Low | Affinity Evidence | Verdict |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 12 W | 19.9 | 19.9 | 18.598 | 18.554 | 1/1 valid | inconclusive |
| 17 W | 25.9 | 25.9 | 24.501 | 24.731 | 1/1 valid | inconclusive |
| 30 W | 28.45 | 28.5 | 26.937 | 27.182 | 1/1 valid | inconclusive |

Interpretation:

- The current game/scene is not reaching 30 FPS even at 30 W.
- V8 proves the mechanism can write and restore foreground role affinity.
- V8 does not prove this role should be pinned by default.
- The role `foreground-game:foreground-work` is stable enough for an experiment,
  but not yet stable enough for an automatic performance policy.

## Proposed Thread Color Model

The next iteration should introduce a color ledger. A color is a role-level
classification plus a preferred control envelope.

### Color A: foreground-latency-hot

Purpose:

- protect a role that has high frame-time impact or high runqueue wait while the
  game is below target.

Candidate signals:

- foreground game cgroup,
- stable comm/role across controlled runs,
- high runqueue wait ratio or wait per slice,
- high CPU time delta near frame work,
- candidate action improves 1% low or p99 frametime.

Preferred actuators:

- observe first,
- uclamp/min or EPP only when available and scoped,
- guarded compact affinity only after repeated BETTER verdicts.

Do not default:

- hard pinning on one run,
- pinning roles that are just busy but not frame-latency relevant.

### Color B: foreground-throughput-wide

Purpose:

- avoid over-constraining worker pools that benefit from scheduler freedom.

Candidate signals:

- many worker threads,
- high CPU time but low runqueue wait,
- CPUs seen across the full package,
- no low-percentile improvement from compact affinity.

Preferred actuators:

- no hard affinity,
- protect package/TDP budget through global game-power governor,
- consider CPU cap only when the game is GPU/package-bound and low-percentile
  metrics do not regress.

### Color C: compositor-and-overlay-sensitive

Purpose:

- avoid damaging gamescope, mangoapp, overlay, audio, and input paths that feed
  visible frame pacing.

Candidate signals:

- gamescope cgroups,
- mangoapp,
- pipewire/wireplumber,
- inputplumber,
- Decky overlay paths.

Preferred actuators:

- observe,
- never hard-pin by default,
- only soft-shape if a repeated profile proves they are background noise and
  frame pacing improves.

### Color D: background-helper-shapable

Purpose:

- reduce CPU interference from Steam helpers, plugin workers, launchers, and
  unrelated user-session helpers.

Candidate signals:

- non-foreground-game cgroup,
- CPU time above threshold,
- not compositor/audio/input critical,
- cgroup appears in restore snapshot,
- repeated run coverage.

Preferred actuators:

- `cpu.weight`,
- `cpu.uclamp.max`,
- possibly `cpu.max` for explicit experiments.

This is the safest near-term control surface because it avoids guessing game
engine thread semantics.

### Color E: unknown-or-unstable

Purpose:

- prevent the controller from overfitting.

Candidate signals:

- appears in one run only,
- TID changes without stable role identity,
- cgroup not present in restore snapshot,
- no frame-time verdict,
- mixed power source or uncontrolled capture.

Preferred actuators:

- observe only.

## Recommended V9 Direction

V9 should be named and scoped as a "thread coloring advisor", not as an
automatic hard-affinity scheduler.

Implementation shape:

1. Add a color ledger to profile aggregate output.
2. Compute role colors from existing samples:
   - `thread-affinity.jsonl`,
   - `thread-schedstat.jsonl`,
   - `process-cgroups.jsonl`,
   - `restore-affinity.json`,
   - CPU topology,
   - FPS/low-percentile comparison.
3. Emit candidate controls by color:
   - background cgroup shaping for Color D,
   - guarded affinity for Color A only after repeated positive evidence,
   - observe-only for Colors B/C/E.
4. Keep Decky UI explainable:
   - show current role colors,
   - show whether a candidate is observe-only, guarded, or rejected,
   - show why no action was taken.
5. Cache only verdicts, not raw CPU IDs:
   - AppID,
   - TDP,
   - FPS target,
   - scene evidence,
   - kernel/build fingerprint,
   - topology fingerprint,
   - role color,
   - selected actuator.

## Acceptance Gates For Any Automatic Actuator

Hard foreground affinity can move from profiler-only to automatic only if all
of these are true:

- controlled capture,
- stable power source,
- repeated runs, recommended minimum 3 paired runs,
- exact restore evidence in every candidate run,
- foreground role coverage across runs,
- no partial write failures,
- median average FPS does not regress,
- median 1% low or p99 frametime improves enough to pass the repo's comparison
  threshold,
- no thermal pairing mismatch,
- no FPS target/pacing regression.

Background cgroup shaping can move earlier because it is less semantically
fragile, but still needs:

- cgroup restore snapshot coverage,
- repeated cgroup presence,
- clear non-critical helper classification,
- positive controlled comparison,
- exact restore.

## Decision

Do not ship V8 hard foreground affinity as a default performance policy based on
the current data. Ship or keep it as a guarded profiler mechanism and evidence
generator.

The next valuable work is V9 thread coloring:

- role classification first,
- topology-aware color assignment second,
- soft/background actuators before hard foreground affinity,
- repeated frame-time evidence before automatic enablement.

This aligns with the best source-backed theory: compact locality can help, but
hard partitioning and blind per-thread pinning are too brittle for a generic
game scheduler.
