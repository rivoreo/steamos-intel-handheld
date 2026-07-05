# Game Power V6 Final Research Ledger

Date: 2026-07-05

Status: research input for V6 final Plan Review. This file is intentionally
more conservative than the product ambition: only evidence-backed, reversible
changes may become default behavior.

## Objective

Finish one coherent V6 final, not a chain of V6b/V6c/V6d patches:

- make the scheduler's learning model honest and inspectable,
- keep the default automatic governor safe across games and TDP levels,
- improve the profiler and Decky product surface enough to close the sampling
  confusion loop,
- leave aggressive controls as guarded experiments until A/B evidence supports
  enabling them.

## Source Ledger

Primary sources and direct implications:

- Linux `uclamp`: `cpu.uclamp.min/max` are scheduler hints that can influence
  schedutil frequency selection, and the kernel docs explicitly discuss a
  perceived-FPS feedback loop for games. Implication: uclamp is the preferred
  future fine-grained control, but only after a foreground/background cgroup
  owner and restore contract are proven.
  Source: https://docs.kernel.org/scheduler/sched-util-clamp.html
- Linux cgroup v2: `cpuset.cpus.partition=isolated` disables scheduler load
  balancing and requires careful task distribution. Implication: hard isolated
  cpusets are not a default V6 control.
  Source: https://docs.kernel.org/admin-guide/cgroup-v2.html
- Linux `sched_ext`: BPF schedulers can be safely unloaded and are useful for
  experimentation, but they require kernel support and a larger safety envelope.
  Implication: scx/sched_ext remains research/prototype only for this package.
  Source: https://docs.kernel.org/scheduler/sched-ext.html
- Linux `intel_pstate`: HWP/EPP and hybrid CPU support exist, but policy is
  still mediated by scheduler and platform behavior. Implication: default V6
  should keep EPP as the only live write unless stronger evidence supports CPU
  caps or cgroup shaping.
  Source: https://docs.kernel.org/admin-guide/pm/intel_pstate.html
- Linux powercap/RAPL: package/core/uncore constraints and energy counters are
  the right shared-power ground truth. Implication: continue to classify package
  pressure before acting; do not infer CPU/GPU contention from FPS alone.
  Source: https://docs.kernel.org/power/powercap/powercap.html
- Android ADPF PerformanceHintManager: target duration plus actual duration is
  the platform pattern for game loops. Implication: FPS target and frametime
  percentile must become the control objective when live telemetry is present.
  Source: https://developer.android.com/reference/android/os/PerformanceHintManager.Session
- Android Game Mode: platform-level performance and battery modes can change
  game behavior and FPS targets. Implication: AppID is valid for grouping and
  history, but the policy must still be telemetry-driven.
  Source: https://developer.android.com/games/optimize/adpf/gamemode/gamemode-api
- Microsoft QoS/EcoQoS: foreground/high QoS and background/efficient QoS map
  onto core selection and power management. Implication: background helper
  shaping is the right long-term direction, but should be service-owned and
  reversible, not user-exposed per-core frequency tuning.
  Source: https://learn.microsoft.com/en-us/windows/win32/procthread/quality-of-service
- Feral GameMode: game-scoped Linux host policy is established practice and
  includes CPU governor, niceness, scheduler, GPU performance, pinning/parking,
  and custom scripts. Implication: V6 should preserve temporary activation and
  exact restore semantics.
  Source: https://github.com/FeralInteractive/gamemode
- MangoHud: FPS logging can produce benchmark artifacts and percentile
  summaries. Implication: use it for profiler evidence and optional live
  frame-source input, but do not depend on it as the only daemon truth source.
  Source: https://github.com/flightlessmango/MangoHud
- Gamescope: SteamOS's game compositor owns virtual resolution and frame-rate
  limit surface. Implication: gamescope/Steam target is the best future FPS
  target source; manual target and MangoHud CSV remain fallback/profiler inputs.
  Source: https://github.com/ValveSoftware/gamescope
- SimpleDeckyTDP and PowerTools: the market already has manual per-game TDP,
  EPP, boost, SMT, CPU/GPU frequency, and persisted per-game settings.
  Implication: our Decky plugin must expose a simple automatic scheduler state,
  not duplicate dangerous low-level knobs.
  Sources: https://github.com/aarron-lee/SimpleDeckyTDP and
  https://github.com/NGnius/PowerTools
- Handheld Daemon: handheld stacks often combine TDP, fan curves, controller
  enablement, and overlays. Implication: Game Power must coexist with other
  device services and clearly state when another controller may own the same
  knobs.
  Source: https://github.com/hhd-dev/hhd
- SysScale: SoC shared-power allocation across CPU/GPU/memory/IO is a real
  system problem under a fixed power budget. Implication: V6 should track
  package/core/uncore and avoid CPU-only narratives.
  Source: https://arxiv.org/abs/2005.07613
- Variable frame timing research: smoothness depends on frame-time variation,
  not just average frame rate. Implication: A/B acceptance must include low
  percentile and percentile frametime, not only average FPS.
  Source: https://arxiv.org/abs/2306.01691
- Affinity Tailor and UFS: recent research favors userspace controllers that
  provide hints, compact CPU sets, and background isolation, rather than
  blind hard affinity. Implication: V6 can add advisor/experiment support, but
  default hard thread affinity is not justified by current evidence.
  Sources: https://arxiv.org/abs/2604.27915 and https://arxiv.org/abs/2605.02377

## Current Device Evidence

Last local profile artifact inspected:
`.cache/game-power/v6-profiles-rerun`.

Observed results:

- At 12W, `gpu-priority` was close to neutral on average FPS and slightly better
  on 1% low in the captured scene: baseline-before average 42.1 FPS, 1% low
  34.11 FPS; candidate average 42.2 FPS, 1% low 35.57 FPS.
- At 30W, `gpu-priority` did not help and worsened the captured 1% low:
  baseline-before average 60.3 FPS, 1% low 51.02 FPS; candidate average
  59.9 FPS, 1% low 44.60 FPS.
- The 30W candidate made no live writes because samples classified as
  `not-package-bound`; this is correct behavior, not a performance win.
- Foreground hot-thread migration deltas were zero in the inspected samples.
  Hard affinity is therefore not evidence-backed for default activation.
- Background `plugin_loader.service` repeatedly appeared as a large CPU consumer
  during profile windows. This supports a guarded background-shaping experiment,
  not default daemon writes.

## V6 Final Decisions

Default behavior to implement:

- Keep packaged service default automatic mode enabled, but default writes remain
  EPP-only unless existing CPU-cap gating explicitly fires.
- Make AppID/TDP/power-source/topology/runtime keyed learning visible in the
  runtime snapshot and Decky UI.
- Prevent FPS-targetless sessions from promoting a reusable hint. A targetless
  session may still contribute local observation, but it must not shorten
  future activation hysteresis unless an explicit future target-independent
  validation path exists.
- Expose sampling and retention truth: current session sample count, minimum
  samples/sessions, whether a hint exists, whether it was used, whether it is
  disabled by contradiction, and whether this session can be reused next launch.
- Keep user controls product-level only: Automatic, View data only, Off,
  refresh/probe, and defaults. Do not expose P-core/E-core frequency, thresholds,
  uclamp, cpu.weight, or affinity knobs.
- Add an FPS target source contract. Automatic discovery should prefer the
  SteamOS/gamescope frame limiter when a stable source is present. A manual FPS
  target override is a valid product-level control because it defines the
  scheduler objective; it is not a low-level measured tuning constant. When the
  target cannot be discovered and no manual target exists, runtime state must
  say target unknown and target-aware learning must stay disabled.
- The Decky manual target control may be a simple FPS slider with coarse,
  product-safe steps. It must write only the runtime target override, never
  P-core/E-core frequency, CPU cap thresholds, cgroup shaping values, PL2/Tau,
  or affinity settings.

Guarded experiment only:

- Background helper shaping with cgroup `cpu.weight` or `cpu.uclamp.max`.
- Foreground cgroup uclamp target-aware boost.
- Thread-affinity advisor.
- sched_ext/scx experiments.

Explicitly rejected for V6 final default:

- hard thread affinity,
- isolated cpuset partitions,
- sched_ext/scx default enablement,
- default CPU max-frequency caps,
- any claim that the packaged service is fully FPS-target-aware without a live
  target/frame telemetry source.

## Acceptance Criteria

The V6 final implementation is complete only when:

- Plan Review passes with adversarial lanes covering scheduler safety, product
  semantics, testability, upstreamability, and performance evidence.
- TDD covers the targetless hint promotion guard and runtime learning snapshot.
- Decky copy explains the difference between Automatic, View data only, and Off
  without exposing dangerous knobs.
- Focused tests and the required harness sweep pass.
- The current build is deployed to `root@10.100.0.19`.
- Device verification covers `verify-on-device`, `verify-game-power-on-device`,
  and a fresh profile/A-B run including 12W.
- Code Review runs after implementation and before commit.
- Changes are committed and pushed.
