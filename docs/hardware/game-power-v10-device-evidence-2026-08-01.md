# Game Power V10 device evidence, 2026-07-31/08-01

Closes probes P1, P2, P3 and P5 from `docs/game-power-v10-direction.md` section 6,
which were still marked pending. Also records findings that were not in the probe
plan because we did not know to look for them.

Everything below is measured on hardware. Where a number is inferred rather than
measured, it says so.

## Conditions

- MSI Claw 8 AI+, kernel `6.16.12-valve24.5-1-neptune-616`, `xe` driver.
- Panel: CSW PN8007QB1-2, 1920x1200, EDID range 48-120 Hz, **no `vrr_capable`**
  on the eDP connector. Refresh changes happen by discrete modeset, which is why
  the shipped gamescope profile generates a modeline per Hz value.
- Live workload: a real game session (base-building, continuous rendering), QAM
  frame limit 60, PL1 24 W unless stated.
- Frame data from the mangoapp feed at ~2 Hz; power from RAPL package/core/uncore.
- Sweeps restore prior state via a shell trap; every restore was verified.

Caveat that applies throughout: the scene changed during the session because a
person was playing. Comparisons within one sweep are sound; comparisons across
sweeps taken minutes apart are not, and are not made.

## P1 - the package pin, and where it does not hold

In a light, frame-capped scene the earlier V9 finding reproduced: GT `act_freq`
pinned at 1950 MHz (= `rp0`) while holding 60 FPS, package near PL1.

**But this is scene-specific, and the original direction document overstated it.**
Later in a heavy scene the same panel read 1600-1850 MHz with the render engine
97-98% busy and zero C6 residency. There is no race-to-idle waste to reclaim
there; the clock is high because the work is real.

So "the package pins at TDP and it is the GPU racing" is true of light scenes and
false of heavy ones. Any policy that assumes it universally will cost frames.

## P2 - GPU frequency sweep

22 s per step, 60 FPS cap, 24 W.

| `max_freq` | avg FPS | package W | graphics W | p95 ms |
|---|---|---|---|---|
| 1950 | 60.0 | 22.1 | 10.6 | 19.6 |
| 1600 | 60.0 | 22.1 | 8.9 | 19.9 |
| **1400** | **59.8** | **20.6** | **8.0** | **18.2** |
| 1200 | 50 | 18.6 | 6.7 | 22 |
| 1000 | 43 | 17.0 | 5.4 | 26 |
| 900 | 36 | 15.3 | 4.6 | 31 |
| 800 | 33 | 12.3 | 4.0 | 33 |

Two results:

- There is a plateau down to ~1400 MHz and a cliff immediately below it. 1600
  gives no package saving at all despite 1.7 W less graphics power.
- **p95 improves as the clock comes down** (19.6 to 18.2 ms). Lower, steadier
  clocks pace better than sprint-and-idle. Capping the GPU is not purely a
  power-for-frames trade.

## P3 - PL1 sweep

| PL1 | behaviour |
|---|---|
| 24 W | 60 FPS |
| 20 W | 60 FPS |
| 17 W | **51-60 FPS oscillating**, GT swinging 1250-1950 MHz |
| 14 W | 47 FPS |
| 12 W | 41 FPS |
| 10 W | 34 FPS |

RAPL is back-pressure, not demand shaping. Driving PL1 down first makes the GPU
controller hunt and the frame rate swing. This is the evidence for shaping demand
first and lowering the ceiling onto the shaped demand, never the reverse.

## Controlled A/B, with bookends

25 s per configuration. The two stock runs bracket the others and agree to
0.02 W, so the scene was stable across this set.

| configuration | package | CPU | graphics | rest | avg FPS | p95 ms |
|---|---|---|---|---|---|---|
| A stock 1950 / 24 W | 23.78 | 5.53 | 11.23 | 7.02 | 59.97 | 19.21 |
| B 1400 / 24 W | 22.20 | 6.63 | 8.46 | 7.11 | 59.07 | 18.16 |
| C 1400 / 18 W | 21.29 | 5.85 | 8.36 | 7.08 | 58.95 | 18.19 |
| **D 1500 / 20 W** | **20.14** | 4.48 | 8.59 | 7.07 | **59.69** | **17.89** |
| E stock 1950 / 24 W | 23.80 | 5.66 | 11.13 | 7.01 | 59.88 | 19.62 |

Best operating point found: **−3.64 W package (−15%) with p95 7% better and FPS
within 0.3** of stock.

Two findings not in the probe plan:

**Capping the GPU alone leaks about 40% of its saving into the CPU.** A to B took
2.77 W out of graphics but only 1.58 W out of the package, because the CPU took
1.10 W back. A to D saved 3.64 W because the package budget stopped the leak.
So soft PL1 is not a second-order trim behind the GPU ladder - it is the closure
that prevents the GPU saving from being re-spent. Order still matters (P3 above),
but both are needed.

**There is a fixed floor of about 7.0 W.** "Rest" (uncore/fabric/SoC, i.e.
package minus core minus graphics) reads 7.01-7.11 W across every configuration
and does not move. At a 20 W package that is 35% of the budget and nothing in
this scheduler's reach touches it. It bounds how much any of this can ever save.

## P5 - CPU-side probes

- `hwp_dynamic_boost` is `0` and has never been enabled. Not evaluated.
- No GuC SLPC power-profile knob is exposed under `xe` on this kernel; the
  direction document listed this as needing a probe. It is absent.
- EPP is `balance_power` on all eight policies at rest. Under boost the governor
  moves some to `performance`/`balance_performance`, observed in a 200-sample
  audit.

## GPU utilisation: fdinfo is absent, the PMU is not

`render_busy` reads `None` in every snapshot. A sweep of every process on the
device found **no fdinfo carrying `drm-engine`** anywhere, so the signal is
structurally unavailable and the three thresholds keyed on it are unreachable.

The xe PMU (`xe_0000_00_02.0`) exposes what is needed:

```
engine-active-ticks / engine-total-ticks   -> real render utilisation
gt-c6-residency                            -> race-to-idle detector
gt-actual-frequency, gt-requested-frequency
```

Sample from the heavy scene: `render_busy 0.97-0.98`, `c6 0 ms`, `1600-1850 MHz`.

This pair answers a question p95 cannot - *whether there is anything to take*,
before trying to take it. p95 only reports afterwards that pacing broke. A high
clock with non-zero C6 is reclaimable; a high clock with zero C6 is not.

## Input-idle frame cap

Measured over a 78-sample window on battery with the cap engaged:

| state | package | FPS |
|---|---|---|
| 60 FPS, same scene | ~23.9 W | ~59 |
| 30 FPS idle cap | **17.58 W** | 29.95 |

**−6.3 W, −26%**, in a state where by definition nobody is looking. The user's
own QAM limit read 60 throughout and was never rewritten.

`GAMESCOPE_INPUT_COUNTER` is **not** a usable input signal: Steam Input grabs the
physical pad and re-emits on a virtual one, which the compositor never sees. The
atom stayed frozen through active play. Measured: 8 s of play produces 52 events
on `/dev/input/event21` (the virtual pad) and 0 on the atom, and 0 on the
physical controller because it is grabbed.

## Telemetry integrity

200-sample audit comparing what the runtime snapshot claimed against sysfs:
**zero mismatches** on GPU frequency caps and soft PL1, and no stale caps left
behind. The decision log can be trusted about what was applied.

## Still unverified

- The Auto frame cap firing end-to-end from a *capability-limited* scene (as
  opposed to the idle path, which is verified). Needs battery plus a scene that
  genuinely cannot hold the target.
- Whether refresh-rate reduction saves meaningful power. Not measured. The
  current position is to follow the system's per-app setting and not touch it;
  Auto targets are exact refresh divisors so frame intervals are even without a
  modeset.
- `hwp_dynamic_boost=1` effect on wake ramps under `balance_power`.
- Utilisation-driven GPU frequency selection. The signal is now published but no
  policy consumes it, and the light-versus-heavy comparison that would size it
  has not been run.
