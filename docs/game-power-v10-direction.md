# Game Power V10 Direction: Demand-Shaped Power, Not Trimmed-at-the-Top

Date: 2026-07-06
Status: research + direction document. No implementation yet by design: the
user asked for the scheduling philosophy to be settled before any code.
Author: Fable 5 (designer), incorporating on-device V9 evidence and the
user's field report from the 2026-07-05/06 trial build.

## 1. What the field trial showed

User observations on the deployed V9 build (MSI Claw 8 AI+, 17 W slider):

1. Sporadic frame drops: a scene that should hold 60 FPS occasionally fell
   to 30-40 FPS for no visible reason.
2. Package power sits pinned at the 17 W TDP essentially all the time while
   a game runs — even in medium/low-load scenes — although average
   multi-core CPU power is visibly low. On battery this is waste; on AC it
   means constant loud fans.

The V9 S7 controlled A/B already contained the same signature, measured:
baseline and candidate both pinned at ~16.95 W package with CPU core power
only ~3.6 W and uncore ~7.5 W; the V9 CPU trim ladder moved ~0.2 W from
core to uncore and package power did not move at all.

## 1b. Quantified evidence from the S7 profiles (added 2026-07-06)

Re-analysis of the 9 controlled S7 runs (static scene, QAM 60 FPS cap,
17 W, battery) confirms the diagnosis with numbers:

- Package pinned at >= 94% of PL1 in **270 of 270 samples (100%)** across
  all baseline and candidate runs — in a light, frame-capped scene.
  Median split: package 16.8 W, CPU core only 3.5-3.65 W, uncore (iGPU)
  7.4-7.6 W. The pin is real and it is not the CPU.
- CPU-side trimming is a measured dead end for power: the V9 candidate
  moved core -0.19 W and uncore absorbed +0.20 W; net package change
  ~0.07 W (zero). Freed CPU watts became GPU frequency with no FPS
  benefit (already capped at 60) — direct evidence of the race-to-idle
  V²f waste and of why only GPU-envelope or package-budget actuators can
  produce battery savings on this platform.
- Ladder step S4 measurably hurts pacing even in a static scene: p95
  frame time 19.45 ms in the S4 window vs 18.63 ms baseline (+4.4%),
  while S0-S3 windows show no difference (18.7-18.9 ms both sides).
  Small sample (67 frames) but consistent with the candidate 1%-low
  medians. In an interactive heavy scene this plausibly grows into the
  user-observed 30-40 FPS dips (H1). Consequence: S4 (and possibly S3)
  should be dropped or verdict-gated on battery; trim the GPU instead.
- The single worst frame (24.3 ms) occurred at t=12.5 s under S0 (no trim
  active), so the ladder is not the only tail source; the interactive
  drop forensics (P6) still needs the on-device journal from the user's
  play session, which these static captures cannot reproduce.

## 2. Root cause analysis

### 2.1 Why the package pins at TDP even at medium load

RAPL PL1 is a *ceiling*, and the SoC races to whatever ceiling exists as
long as any agent demands it. On this platform the demanding agent is the
iGPU: the xe/GuC SLPC frequency controller ramps GT clocks toward max
(1950 MHz) whenever there is GPU work and power budget, regardless of
whether the extra frequency contributes to the frame deadline. Two regimes:

- Uncapped rendering: every saved watt elsewhere becomes more frames above
  target — pure waste (V9's S7 result: core watts freed, uncore absorbed
  them, FPS unchanged).
- Capped rendering (QAM 60 FPS limit): the GPU still race-to-idles each
  frame at high frequency/voltage. Because power scales ~V²f, finishing a
  frame at 1950 MHz and idling is measurably less efficient than pacing
  the same frame at, say, 1100 MHz that just meets the deadline. The
  budget the GPU burns keeps package at PL1 through most of the frame.

V9's blind spot is structural: every actuator it owns is CPU-side (EPP,
CPU frequency caps, cgroup shaping, affinity). It never touches the GPU
frequency envelope, never touches the frame limiter, and never lowers the
package budget itself. So "at target" could only ever convert CPU slack
into GPU boost — the opposite of what a battery-powered handheld wants at
medium load.

Independent confirmation this is the right lever set: Intel's own Windows
driver 32.0.101.6734 fixed Lunar Lake handheld gaming (MSI Claw 8 AI+
explicitly) by making CPU power management less greedy so the iGPU
performs better *within the same 17 W*, and Linux xe gained a GuC SLPC
"power saving" profile (conservative ramp thresholds + waitboost disable)
in kernel 6.15. Both vendors converged on: shape the demand, don't just
redistribute a pinned budget.

### 2.2 Why frames drop to 30-40 sporadically

Hypotheses ranked by likelihood; each gets a validation probe (section 6):

- H1 Control-loop timescale mismatch. The governor polls at 2 s and phase
  commits take 1-3 ticks. At 60 FPS a 2-6 s reaction window is 120-360
  frames. If the ladder had trimmed to S3/S4 (P-cores capped 4.0/3.0 GHz)
  and the scene spiked (streaming, shader compile, combat burst), the game
  is CPU-starved for seconds before release. A frame-scale QoS problem
  cannot be governed by a seconds-scale loop alone.
- H2 EPP balance_power on E-cores parks wakeup latency. Ladder S1/S2 set
  balance_power globally; intel_pstate HWP then ramps lazily. A bursty
  render thread waking on a low-EPP core misses several frames before HWP
  catches up. This is the exact "boost fast" gap the user pointed at.
- H3 Loading/phase misclassification releasing and re-climbing (thrash),
  observable in the decision JSONL as phase flapping.
- H4 The trial may have run gpu-priority (not target-balance) if the
  drop-in was not applied; gpu-priority's EPP hysteresis has the same H2
  shape. The JSONL on the device will say which mode ran.

None of these is fixed by more CPU trimming. H1/H2 need a *boost path*
that reacts in tens of milliseconds, which no 2 s userspace poll can do —
that is what HWP dynamic boost, uclamp floors, and compositor-fed hints
are for.

## 3. The philosophy shift (what the final Output is)

V1-V9 asked: "given the TDP the user set, how do we split it well?"
V10 must ask: **"what is the minimum energy that delivers the user's
target frame experience right now, and how fast can we get power there —
in both directions?"**

Concretely, the output contract per user situation:

- On battery: target FPS held with a p95 guard band, at the lowest package
  power the scene permits. Package power should *follow demand* — 8 W in a
  menu, 11 W walking through a village, 17 W only in combat. The TDP
  slider becomes a ceiling, not an operating point. Success metric:
  Wh per gaming hour at equal pacing, not FPS.
- On AC: two user intents. "Quiet": same demand-following behavior with a
  slightly higher guard band (fan noise is the constraint — power follows
  need, not the wall). "Performance": current behavior (ceiling
  operation) is acceptable; headroom parks above target as pacing margin.
- Always, both modes: transient bursts (loading, shader compile, combat
  entry) get *immediate* full-ceiling boost, released promptly when the
  burst ends. Boost latency budget: one frame period, not one poll tick.

This is the phone/Apple model the user referenced: race up instantly on
demand, decay promptly after the work retires, never hold frequency (or
package budget) hot without a consumer. Android formalizes the same loop
as ADPF performance-hint sessions (target duration vs actual duration
feedback) and platform FPS throttling for battery; AMD (Radeon Chill) and
NVIDIA (BatteryBoost) both ship "frame-target ⇒ power savings" as their
battery gaming story. We are not inventing a philosophy; we are late to
it.

## 4. The V10 actuator hierarchy (system-level, not one file)

Ordered by leverage. The first three are new surfaces V9 does not touch —
this is explicitly not a game_power.py-only plan.

### A1. Frame limiter as a first-class actuator (compositor)

Rendering above target is the single largest waste. gamescope exposes a
runtime limiter (`gamescopectl debug_set_fps_limit N`, plus the SteamOS
QAM per-game limit that drives the same mechanism). V10 policy:

- Read and honor the QAM/gamescope limit as the FPS target (V9 already
  discovers `-r`; extend to the runtime convar state).
- When the user has no limit set and mode is battery/quiet: propose or
  apply (user-consented, Decky toggle) a limiter at the detected target.
- Never fight the user's explicit setting.

Ownership note: this writes through gamescope's own control channel (as
our display workaround already does), so restore semantics are clean.

### A2. GPU frequency governor (xe sysfs + SLPC profile)

The missing half of the V6-V9 story. Controls verified present on the
device: `/sys/class/drm/card0/device/tile0/gt{0,1}/freq0/{min_freq,max_freq}`
(rp0 1950, rpe 800, rpn 100). Kernel 6.16 may also expose the GuC SLPC
power-profile knob (probe needed).

- BELOW_TARGET_GPU_BOUND: raise `min_freq` floor (V9 already has this as
  a profiler lane) — boost path.
- AT/ABOVE_TARGET: cap `max_freq` stepwise down while p95 guard holds —
  the GPU analogue of the V9 CPU ladder, and on this evidence the far
  more effective one. Pacing at the lowest frequency that meets the
  deadline beats race-to-idle at 1950 MHz on V²f grounds.
- If the SLPC power-saving profile is exposed: prefer it (vendor-tuned
  conservative ramping + waitboost off) as the coarse mode, with min/max
  as fine trim.

### A3. Dynamic package budget (soft PL1 below the slider)

We uniquely own the TDP backend — no other handheld tool can do this
cleanly. Add a governor-driven *soft PL1*: `effective_PL1 =
min(user_slider, demand_estimate + guard)`, floor at 8 W, always
restorable to the slider value, PL2/Tau untouched (bursts still get full
boost). Decay stepwise (e.g. 1 W per qualifying window) while target+p95
hold; snap back to the slider ceiling *instantly* on a miss, a phase
change to LOADING, or any spike signal. This directly implements "don't
hold 17 W in a menu" and is the AMD STAPM/fast-slow-limit shape done in
userspace. EC mirroring follows the existing guarded path.

Safety: soft PL1 is a *reduction-only* overlay under the user's slider;
SteamOS Manager remains the owner of the user-visible contract.

### A4. CPU-side boost path (fix the "boost fast" half)

- Loading/spike boost exists in V9 but at 2 s granularity. Add the fast
  lane (section 5) and drive: EPP performance on P-cores, uclamp.min
  floor on the foreground latency-hot color (A-role from the ledger),
  and `hwp_dynamic_boost=1` evaluation (currently 0 on the device —
  probe whether enabling it improves wake ramps under balance_power).
- The existing V9 trim ladder stays, but subordinated: it only runs when
  A1-A3 already hold the demand shape, and its S3/S4 frequency caps are
  candidates for *removal* on battery if A2+A3 prove sufficient (caps are
  the prime H1 stutter suspect).

### A5. Existing V9 machinery (kept, repositioned)

Thread coloring, verdict ledger, background shaping, phase machine,
telemetry contracts, evidence gates: all stay. The phase machine gains
the demand estimator; the verdict ledger gates A2/A3 aggressiveness per
context exactly as it gates today's lanes.

## 5. Control-loop redesign: two rates + a real frame feed

- Slow lane (existing, 2 s): phase classification, trim/decay decisions,
  coloring, telemetry.
- Fast lane (new, 100-250 ms, event-preferred): boost decisions only.
  Watches frame-time spikes and foreground runqueue pressure; on trigger
  it releases all trims, snaps soft-PL1 to ceiling, floors GPU freq, and
  boosts EPP — within one or two frame periods. Boost is always safe
  (never below user intent), so the fast lane needs no evidence gate.
- Frame feed: the standing V9 gap (daemon has no runtime frame data
  outside profiler logging sessions) becomes blocking for V10, because
  both the limiter decision and the decay guard need live frame times.
  Options, preferred first:
  1. Extend our mangoapp fork (we already ship it and it already consumes
     the gamescope stats pipe) to export a compact rolling frame summary
     (avg/p95/last-frame-ms, ~1 Hz + spike events) to a runtime file or
     socket the daemon reads. Small patch, fully under our control,
     upstreamable later.
  2. gamescope stats/ctl surface directly, if enumeration shows a
     readable frame-time counter that does not steal mangoapp's pipe.
  3. MangoHud CSV sessions (today's mechanism) stay the profiler path.

## 6. Validation plan before any build (device probes, ~1 day)

P1. Reproduce the pin: game at QAM 60-cap in a low-load scene; record
    package/core/uncore W + GT act_freq at 1 Hz for 5 min. Expected: GT
    frequency high, package ≈ PL1. This is the baseline artifact.
P2. GPU cap sweep: same scene, step `max_freq` 1950→800 in ~200 MHz
    steps, 60 s each; record FPS/p95/package W. Hypothesis: a wide
    plateau where pacing holds and package drops 2-5 W. This directly
    quantifies A2's value and picks the cap ladder steps.
P3. Soft-PL1 sweep: same scene, step PL1 17→9 W; find the knee where
    p95 breaks. Quantifies A3 and the guard band.
P4. Limiter check: `gamescopectl debug_set_fps_limit` efficacy and
    interaction with the QAM slider on this SteamOS build.
P5. SLPC probe: does 6.16-valve expose the GuC power profile knob? Does
    `hwp_dynamic_boost=1` change wake ramp behavior under balance_power?
P6. Frame-drop forensics for H1-H4: pull the trial JSONL from the device
    journal/runtime dir, align drop timestamps with phase/ladder/EPP
    decisions; separately reproduce a combat/loading burst with the
    ladder pinned at S4 vs S0.

Each probe is observe-only or trivially restorable, uses the existing
profiler evidence discipline, and produces the numbers that size the V10
ladders (instead of guessing constants).

## 7. Deliverable slices after validation (sketch, evidence-gated as ever)

S1 mangoapp frame-feed export + daemon fast lane (boost-only).
S2 GPU ladder (A2) with p95 guard + profiler A/B; battery persona first.
S3 Soft-PL1 governor (A3) under the TDP backend, reduction-only overlay.
S4 Frame-limiter integration (A1) with Decky consent UX.
S5 Persona wiring (battery/AC-quiet/AC-performance) + QAM/HHD conflict
   detection; CPU cap steps re-evaluated (possibly dropped on battery).
S6 Controlled device evidence at 12/17 W across two scenes (low-load and
   heavy), verdict export, rollout gate as in V9.

## 8. What this changes beyond src/steamos_intel_handheld/game_power.py

- external/MangoHud fork: mangoapp frame summary export (new patch).
- power_control.py TDP backend: soft-PL1 overlay API (reduction-only).
- New xe GPU actuator module + guarded writer/restore + evidence files.
- gamescope control-channel integration (share plumbing with the display
  workaround service).
- game_power_profile.py: probes P1-P5 as capture modes; GPU/soft-PL1
  ladder policies; persona-aware aggregates.
- Decky plugin: persona selector (battery/quiet/performance), limiter
  consent toggle, live package-power vs demand display.
- docs/design.md, README, harness guarded checks for the new lanes.

## 9. Open questions (to answer with P1-P6 data, not opinion)

- Is the GPU cap plateau wide enough at 17 W that a static per-scene cap
  suffices, or does it need continuous adjustment? (Static is simpler and
  less oscillation-prone.)
- Can soft-PL1 alone deliver most of the battery win without touching GPU
  freq (RAPL naturally squeezes SLPC)? If yes, A3 before A2.
- Does the fast lane need kernel help (uclamp via sched_ext later), or do
  EPP+PL1 snaps at 100-250 ms suffice for the observed drop pattern?
- How do we coexist with Intel's own future Linux equivalent of the 6734
  driver behavior if it lands in xe/SLPC?
