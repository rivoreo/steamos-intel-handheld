---
name: game-power-scheduler
description: Product and behavior boundaries for the Game Power governor on Intel handhelds - frame-target-driven power scheduling, actuator priority, pacing guards, and evidence rules. Use when changing game_power*.py, the TDP backend, GPU or soft-PL1 actuators, phase/ladder logic, frame-target detection, or when tuning scheduler constants.
---

# Game Power scheduler

The question this scheduler answers is **"what is the minimum energy that
delivers the user's target frame experience right now, and how fast can we get
power there - in both directions?"**

It is not "given the TDP the user set, how do we split it well?". That was V1-V9
and it structurally cannot save power: every watt freed on one side is absorbed
by the other. See `docs/game-power-v10-direction.md` for the full derivation and
`docs/game-power-v10-framework-plan.md` for the contracts.

## Success metric

Watt-hours per gaming hour **at equal pacing**. Not FPS, not package watts alone.
A change that saves power while pacing degrades is a regression. A change that
improves pacing at equal power is a win even with no energy saving.

## Actuator priority

Shape demand before squeezing the ceiling:

1. **Frame limiter** - rendering above target is the largest single waste.
2. **GPU frequency ceiling** - in *light* scenes the iGPU races to max clock
   regardless of whether it helps the frame deadline, and pacing at the lowest
   clock that meets the deadline beats that on V²f grounds. In heavy scenes it
   does not: measured 97-98% render utilisation with zero C6, where the clock is
   high because the work is real and capping only costs frames. Do not assume the
   race-to-idle story universally.
3. **Soft PL1** - a reduction-only overlay *under* the user's slider.
4. **CPU-side (EPP, cgroup, affinity)** - smallest leverage on this platform.

Neither the GPU ceiling nor the package budget works alone, and this is the most
easily-got-wrong part of the design:

- **A GPU cap on its own does not reduce package power.** Over a 145-sample live
  session, graphics power against package power correlated only -0.138 while
  graphics against CPU power was -0.659: the CPU re-spends what the GPU gives up.
  The budget is the closure that makes the cap stick, not a second-order trim
  behind it. So the ladder establishes the budget first and pairs the cap onto it.
- **But do not drive PL1 deep as the primary lever.** RAPL is back-pressure, not
  demand shaping; pushed toward the knee it makes the GPU controller hunt and the
  frame rate swing (measured: 17 W gave 51-60 FPS with GT swinging 1250-1950 MHz).
  A mild budget that leads the cap is fine; a deep one that substitutes for it is
  not.

There is also a fixed ~7.0 W uncore/fabric floor that none of these actuators
touch - 35% of a 20 W package. It bounds what any of this can save.

Lanes in the trim ladder are **interleaved, not grouped**. The sequence is
strictly cumulative, so a rung the scene cannot sustain also strands every rung
behind it.

## What GPU utilisation is and is not for

The fdinfo render-busy signal does not exist on this platform; the xe PMU
replaces it. Two things follow from the measurements:

- **Do not use utilisation to choose a frequency.** GT frequency against
  utilisation correlated -0.916: frequency is the cause and utilisation the
  effect, since a higher clock finishes the same frame sooner. SLPC already runs
  that loop and lands utilisation in 0.80-0.97 for most of a session. A
  utilisation-driven frequency controller would re-derive SLPC's own.
- **Do use it to detect over-capping.** Utilisation pinned at the ceiling means
  no slack is left and frames are about to slip - a leading indicator where p95
  is a lagging one. Measured: at/above ~0.97 the session ran 56.2 FPS against a
  60 target with p95 19.7 ms; the 0.80-0.97 band held 59.6-59.9 at p95 17.9 ms.
- `gt-c6-residency` is inert during gameplay (0 ms in all 145 samples). The GT
  does not enter deep idle between frames, so it cannot detect finishing early.

## Guards are regression guards

Never gate on an absolute ideal. A healthy 60 FPS scene routinely paces at p95
18-20 ms against a 16.67 ms target; a guard set at `target x ratio` breaches
before the ladder ever climbs and the whole feature becomes a no-op.

- Learn the scene's **unconstrained** baseline (samples taken while nothing of
  ours is applied), then allow `max(target x ratio, baseline x regression)`.
- The same budget must feed the phase classifier *and* the ladder. If only one
  of them is baseline-aware, the phase machine flaps instead.
- Judge avg_fps loosely and pacing (p95) strictly. A game pinned at its own cap
  averages just under target with real noise; that is not a miss.

## Persona defaults are a product decision, not a tuning artifact

- **Battery: balanced (Max-Q).** Demand shaping is active. Hold the frame target
  at the lowest power the scene needs.
- **Plugged in: performance release.** Users expect a handheld to get *more*
  capable on the wall, so AC does not trim by default. The demand-shaping lanes
  being inactive here is intended, not a bug to fix.
- **Quiet is opt-in.** A user who wants low fan noise while plugged in selects it
  explicitly; we never infer it.

Do not "fix" AC by enabling trims there. If a change only shows a benefit by
making plugged-in operation quieter or slower by default, it is out of bounds.

## Frame cap authority

The frame target is two separable things and the distinction matters: what the
scheduler *aims at*, and whether we *write the real cap* so the game stops
rendering above it. Aiming without capping leaves the largest waste untouched.

- **On battery, Auto writes the real cap** through gamescope's control channel.
- **Plugged in we never write it** - that is the performance-release position -
  unless the user selected quiet.
- The user's own QAM limit is a separate, higher layer. Our write is an overlay;
  clearing it returns to their setting. Never overwrite user intent.
- **We do not touch the refresh rate.** Follow whatever the system does with it
  for the current app. Auto targets are exact divisors of the panel rate, so
  frame intervals are already even without a modeset.

## Auto target estimation

- Candidates are **exact divisors of the current panel refresh rate** (at 120 Hz:
  60 / 40 / 30). There is no working VRR on the reference panel, so a non-divisor
  cap gives permanently uneven frame intervals.
- **Lowering the cap only fixes sustained throughput shortfalls. It does nothing
  for transient hitches** - an asset-streaming or shader-compile stall happens
  regardless of the cap, so capping just renders fewer frames between the same
  hitches. Pure loss. Separating these two is the whole job of the estimator.
- Therefore judge with **two different percentiles**: a *high* one decides
  whether the target is reachable at all (if the scene's good moments still land
  on target, the misses are transient - leave it alone), and a *low* one decides
  where to cap once it is genuinely capability-limited.
- "Sustained" means **repeatedly short across a window**, not short without
  interruption. A consecutive-only counter resets on every good sample and never
  fires on exactly the bouncing frame rate users complain about.
- Drop only on a material shortfall, with nothing of ours applied. A near-miss -
  a scene at ~97% of target - is the power scheduler's job. Never spend a whole
  divisor rung on it.
- Climb back only on a long, decisive win: clear headroom past the next rung up
  with margin, held for minutes. The rungs are ~50% apart, which is most of the
  anti-oscillation on its own.
- Re-evaluate on user-caused context changes (power source, profile, launch) -
  moments where a change is already expected. Never mid-load or mid-boost.
- A cap change is something the player *feels*. The metric to minimise is how
  often they notice it happening, so cap the number of changes per session.

## Persist choices, not derived state

- The user's explicit selections (power profile, whether the frame target is
  manual and what they set) survive reboot.
- Anything we computed - detected limits, learned baselines, estimated
  achievable frame rates - is re-derived and must never be frozen into a setting
  the user then has to undo.
- A setting the user never chose must not outlive the session that created it.

## Asymmetric timing

- **Give power back fast**: one tick, no confirmation needed. Boost is always
  safe because it only removes our own reductions.
- **Take power away slowly**: climb one rung at a time with a hold period.
- **Penalise only confirmed misses**: a single noisy sample must not reset the
  ladder or burn an anti-oscillation backoff. Require consecutive breaches.
- Unconfirmed blips hand power back but **keep the ladder position**, so
  returning to target resumes the rung instead of re-climbing from zero.

## Frame target detection

Priority order, and never invent a value:

1. User override from the runtime control file.
2. `GAMESCOPE_FPS_LIMIT` root-window atom - this is the live QAM per-game limit.
   Read it demoted to the session user with `setpriv` (not `runuser`: PAM logs
   two journal lines per call) and cache it; the value only changes on user
   action.
3. gamescope argv (`-r`, `--framerate-limit`) as a fallback. On current SteamOS
   builds these flags are absent, so argv alone detects nothing.

No target means degrade to NO_TARGET, not a guessed default.

## Boundaries

- **Reduction-only under user intent.** No actuator may exceed what the user
  set (TDP slider, QAM limit, refresh rate). Boost means removing our own
  reductions, never overclocking past user state.
- **Snapshot / restore / fail-closed for every writer**, with readback-verified
  restore. A fail-closed latch must stop further writes, not retry blindly.
- SteamOS Manager stays the owner of the user-visible TDP contract; soft PL1 is
  an overlay beneath it and PL2/Tau keep deriving from the user's slider so
  bursts stay full.
- **Every tunable is a config field, never a literal.** Constants get tuned on
  device against live telemetry.
- Telemetry must match reality: if a snapshot says a cap is applied, sysfs must
  agree. Audit this directly rather than trusting the decision log.
- Local test success is never device evidence. See
  [[game-power-device-evidence]] for what each layer licenses.
- Do not add user-facing surface here; the panel has its own boundary in
  [[decky-panel-ux]].
