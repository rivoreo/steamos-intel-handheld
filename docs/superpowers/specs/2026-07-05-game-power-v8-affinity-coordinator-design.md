# Game Power V8 Affinity Coordinator Design

## Context

The current Game Power scheduler does not apply thread affinity. It applies EPP
and optional CPU frequency caps, while the profiler records thread-affinity,
thread-schedstat, CPU topology, process cgroups, and restore snapshots. V7 added
evidence readiness so stale or incomplete telemetry does not look authoritative.

The next useful step is not game-specific manual tuning. The scheduler cannot
know engine internals, and per-game profile maintenance would not scale. V8
should turn existing samples into a guarded, repeatable affinity experiment:
identify stable foreground thread roles, bind those roles to compact latency
CPUs during a controlled profiler run, restore the original masks, and let the
existing A/B comparison decide whether the captured scene improved.

## Research Basis

- Linux CPU affinity is a per-thread mask. `sched_setaffinity(2)` can restrict a
  thread to a CPU set and migration happens immediately if the thread is running
  outside the new mask.
- Linux cgroup v2 cpuset is a hard boundary. `cpuset.cpus` requests CPUs for all
  tasks in a cgroup, but the effective CPUs are still constrained by the parent.
- Linux util clamp is a scheduler hint path, not a hard binding path. Kernel docs
  explicitly frame it as user-space assisted power/performance management that
  works best with feedback loops.
- GameMode already exposes CPU core pinning/parking as a Linux gaming precedent,
  but it is configuration-driven and platform-detected rather than evidence-gated
  from this repo's profile artifacts.
- Recent scheduling research supports the product direction: spatial locality is
  useful, but strict static partitions can hurt bursty workloads. The closest
  practical shape for this repo is compact, evidence-gated, quickly restorable
  affinity experiments rather than permanent hard partitions.

## Decision

Yes, this is worth doing, but only as a guarded profile-stage automation first.
The implementation should not add always-on daemon writes yet.

V8 will add a foreground role affinity writer to the profiler CLI and wire it
into `scripts/profile-game-power-on-device.sh` as a new controlled policy
variant. The wrapper consumes an aggregate JSON report or a raw
`affinity_experiment_plan`, selects the first ready candidate, and applies it
automatically in the next profile run. The wrapper does not accept free-form
role/cpu debug environment variables because those would be interpolated into
the remote SSH command. Manual role/cpu control remains available only through
the Python CLI subcommand for explicit local or already-remote debugging.

The writer consumes the existing `restore-affinity.json` snapshot and a
role-level candidate from the experiment plan; it does not trust stale raw TIDs
from previous launches. It recomputes the current role key from the live
snapshot, revalidates the current `/proc/<pid>/task/<tid>` identity before each
write, applies the compact CPU mask only to matching foreground-game threads,
records every write, then restores the original masks after the run.

The write artifact is part of the evidence model. A `gpu-priority-affinity` run
is valid candidate evidence only if at least one current foreground role thread
was written, no write failed after partial application, and restore verification
returned clean. A zero-write or failed-write run exits before sampling or is
marked not comparable.

## Automation Model

1. The profiler continues sampling per-thread CPU time, migrations, current CPU,
   affinity masks, runqueue wait, and cgroup role.
2. `aggregate` continues promoting per-run TIDs into stable role keys such as
   `foreground-game:worker-thread`.
3. A role becomes eligible only when repeated controlled A/B runs show:
   controlled capture mode, clean restore evidence, enough repeated runs,
   stable role coverage, foreground-game scope, and meaningful migration or
   runqueue-wait harm.
4. The aggregate plan computes a compact hard-affinity mask for the candidate.
   The mask must be wide enough for the stable role's observed thread count and
   must stay within the current effective/allowed CPU set. Single-CPU masks are
   rejected for multi-thread roles.
5. The next profiler invocation points
   `PROFILE_GAME_POWER_AFFINITY_PLAN_JSON` at the aggregate report or plan JSON.
   The wrapper copies that artifact to the target and resolves the first ready
   candidate.
6. The new guarded writer applies a compact CPU mask to current matching role
   threads during a device profiler run.
7. The run records `foreground-affinity-writes.json` and
   `foreground-affinity-restore.json`.
8. Summary and aggregate logic carry foreground-affinity evidence fields. Runs
   without successful apply+restore are not accepted as valid affinity evidence.
   If the candidate is not better, the automation stops at "not justified".

## Safety Rules

- No daemon default writes.
- No Decky "apply affinity" button.
- No raw TID reuse across launches.
- No non-foreground role writes.
- No empty or malformed CPU masks.
- No zero-write affinity run may be summarized as valid candidate evidence.
- No write without an already captured restore snapshot.
- Every write and restore must tolerate missing `taskset`, exited threads, and
  partial failures by writing diagnostics and failing closed.
- If restore fails or mismatches, the profile wrapper marks the run failed.
- Documentation must say local tests prove only contract behavior; real
  performance claims require `game-power-profile-device`.

## User-Facing Outcome

After V8, the repo can run a controlled policy such as
`gpu-priority-affinity` in the device profiler. That policy is generated from
existing sampling evidence and produces reviewable write/restore artifacts. It
is an automated performance coordination mechanism for profiler A/B, not yet a
permanent scheduler feature.
