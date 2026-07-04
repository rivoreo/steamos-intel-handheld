# Plan Review Report: Game Power V5 FPS-Target-Aware Governor

## Summary

| Reviewer | Role | Score | Verdict |
| --- | --- | ---: | --- |
| A | Architecture & Feasibility | +1 | APPROVE WITH CONSTRAINTS |
| B | Completeness & Risk | +1 | APPROVE WITH NOTES |
| C | Quality & Conventions | +1 | APPROVE |
| E | Product & Value | +1 | APPROVE WITH CONSTRAINTS |

**Overall Result**: APPROVED AFTER SUB-AGENT BLOCKER FIXES
**Review Iterations**: 1 local reviewer sweep + 2 sub-agent sweeps, budget 3/5
**Harness Status**: passed
**Active Reviewers**: A, B, C, E. Reviewer D not active because V5 does not
change Decky UI or interaction flows.
**Findings**: 3 sub-agent blockers found in the first plan draft; blockers
addressed in the revised spec; second architecture sub-agent found no remaining
plan blockers
**User Checkpoints**: intake asked no. The user explicitly authorized a V5
hands-on iteration after Plan Review.

## Bounded Surface

Reviewed artifact:

- `docs/superpowers/specs/2026-07-04-game-power-v5-fps-target-aware-design.md`

Implementation surface allowed by the reviewed plan:

- `src/steamos_intel_handheld/game_power.py`
- `scripts/profile-game-power-on-device.sh`
- focused tests in `tests/test_game_power.py` and harness contract tests if the
  script surface changes

Out of scope:

- Decky UI
- default CPU max-frequency caps
- sched_ext
- thread affinity
- claiming FPS-target-aware behavior for the installed service without a live
  frame-performance source

## Key Strengths

- The plan correctly identifies that current runtime samples have FPS target
  metadata but no current FPS or frame pacing telemetry.
- The decision gate requires both average FPS headroom and p95 frametime proof,
  avoiding a single-row FPS spike from disabling EPP in the 12W below-target
  scene.
- Missing or malformed frame-performance telemetry keeps V4 behavior, making
  the new policy self-disabling outside verified capture paths.
- The controlled profiler already has a live MangoHud CSV stream, so the first
  implementation can be validated on real hardware without inventing a daemon
  global file-discovery mechanism.

## Issue Ledger

```json
{
  "schemaVersion": "lunatalk.review-loop.v2",
  "reviewType": "plan",
  "iteration": 1,
  "budget": {"maxSweeps": 5, "sweepsUsed": 2},
  "harnessStatus": "passed",
    "reason": "sub-agent blockers were addressed and a follow-up architecture reviewer found no remaining plan blockers",
    "reasonCode": "converged_after_revision",
  "activeReviewers": ["A", "B", "C", "E"],
  "surfaceId": "game-power-v5-fps-target-aware-design",
  "scores": {"A": 1, "B": 1, "C": 1, "E": 1},
  "convergence": {
    "openBlockers": 0,
    "confirmedFindings": 6,
    "refutedFindings": 3,
    "unresolvedDecisionItems": 0,
    "unresolvedEvidenceItems": 0,
    "newBlockers": 0,
    "reopenedBlockers": 0,
    "latentMissedStreak": 0,
    "novelIssuesBySource": {
      "revision_introduced": 0,
      "latent_missed": 0,
      "scope_expansion": 0,
      "unsupported": 0
    },
    "maxMaterialRevisionAttempts": 2,
    "heldOutSweepsUsed": 2,
    "plateauDetected": false,
    "reviewProcessDefect": false
  },
  "userCheckpoints": {
    "intakeAsked": false,
    "decisionAsked": false,
    "recordedAssumptions": [
      "V5 is allowed to improve profiler/CLI runtime behavior before solving packaged-daemon FPS discovery.",
      "The packaged service must remain behavior-compatible with V4 when no live frame-performance source exists."
    ]
  },
  "ledger": [
    {
      "dedupeKey": "target-alone-is-not-runtime-evidence",
      "severity": "critical",
      "status": "addressed",
      "verification": "verified_against_code",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "GamePowerSample currently has frame_target but no frame_performance. Controller decisions only use AppID, RAPL, and fdinfo.",
      "finding": "A target-aware governor cannot infer target satisfaction from fps_target metadata alone.",
      "recommendation": "Add explicit FramePerformanceTelemetry and gate only when high-confidence live data is present.",
      "disposition": "Addressed by the design: target-satisfied logic is disabled unless frame-performance telemetry is present."
    },
    {
      "dedupeKey": "single-row-fps-spikes-can-disable-helpful-12w-policy",
      "severity": "major",
      "status": "addressed",
      "verification": "verified_against_profile_artifacts",
      "reviewer": "B",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "12W MangoHud rows contain short spikes above 40 FPS, but no 20-row window satisfies avg>=42 and p95<=28.75ms.",
      "finding": "Using instantaneous FPS or only avg>=target would incorrectly restore in below-target low-TDP scenes.",
      "recommendation": "Require windowed average headroom and p95 frametime proof.",
      "disposition": "Addressed by the 20-row window, 105% FPS headroom, and 115% p95 frametime threshold."
    },
    {
      "dedupeKey": "packaged-service-has-no-live-fps-source",
      "severity": "major",
      "status": "confirmed_non_blocking",
      "verification": "verified_against_code",
      "reviewer": "E",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 0,
      "novelIssueSource": "none",
      "evidence": "power_control.build_game_power_governor constructs SystemGamePowerObserver without FPS/frametime source.",
      "finding": "The installed service cannot yet perform target-aware retreat unless a live frame-performance source is wired in.",
      "recommendation": "Do not claim service-level FPS-target awareness in V5; validate profile/CLI behavior and keep service V4-compatible.",
      "disposition": "Non-blocking because the reviewed goal scopes V5 behavior to paths with telemetry and explicitly forbids overclaiming service behavior."
    },
    {
      "dedupeKey": "mangohud-csv-race-at-capture-start",
      "severity": "major",
      "status": "addressed",
      "verification": "subagent_verified_against_script",
      "reviewer": "subagent-test-risk",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "none",
      "evidence": "profile-game-power-on-device.sh starts MangoHud logging before running the governor, but the CSV may appear after a delay.",
      "finding": "Passing a stale or missing CSV would either disable V5 or read the wrong session.",
      "recommendation": "Wait for a CSV newer than mangohud.start; if none appears quickly, omit the arg and record V4-compatible fallback.",
      "disposition": "Addressed in revised spec by requiring marker timing, non-summary fresh CSV selection, minimum valid rows, and fallback artifacts."
    },
    {
      "dedupeKey": "live-csv-runtime-path-not-closed",
      "severity": "critical",
      "status": "addressed",
      "verification": "subagent_verified_against_script",
      "reviewer": "subagent-test-risk",
      "firstSeenIteration": 2,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "latent_missed",
      "evidence": "The original plan required passing --frame-performance-csv, while the current script only collects MangoHud CSV after the governor exits.",
      "finding": "The first plan could produce passing unit tests while the real profiler still ran V4 because the live CSV path was never wired.",
      "recommendation": "Wire fresh live CSV discovery before launching steamos-intel-handheld-game-power.",
      "disposition": "Addressed in revised spec and made an implementation acceptance criterion."
    },
    {
      "dedupeKey": "runtime-contract-must-prove-v5-active",
      "severity": "critical",
      "status": "addressed",
      "verification": "subagent_verified_against_contract_surface",
      "reviewer": "subagent-test-risk",
      "firstSeenIteration": 2,
      "lastSeenIteration": 2,
      "materialRevisionAttempts": 1,
      "novelIssueSource": "latent_missed",
      "evidence": "Existing runtime contract only validates FPS target metadata, not frame-performance fields or fps-target-satisfied classifications.",
      "finding": "A profiler run could pass while V5 target-aware behavior never executed.",
      "recommendation": "Extend runtime contract to require frame-performance fields and fps-target-satisfied evidence when target-aware behavior is expected.",
      "disposition": "Addressed in revised spec and implementation notes."
    },
    {
      "dedupeKey": "csv-reader-must-not-read-unbounded-files",
      "severity": "minor",
      "status": "confirmed_non_blocking",
      "verification": "quality_review",
      "reviewer": "C",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 0,
      "novelIssueSource": "none",
      "evidence": "MangoHud CSV can grow during long captures.",
      "finding": "A naive read-all parser could become expensive outside short profile runs.",
      "recommendation": "Parse into a bounded deque of the last N valid rows.",
      "disposition": "Non-blocking implementation constraint."
    },
    {
      "dedupeKey": "target-satisfied-should-use-existing-restore-hysteresis",
      "severity": "major",
      "status": "refuted",
      "verification": "verified_against_design",
      "reviewer": "A",
      "firstSeenIteration": 1,
      "lastSeenIteration": 1,
      "materialRevisionAttempts": 0,
      "novelIssueSource": "none",
      "evidence": "Design routes target-satisfied samples through the existing negative-sample path.",
      "finding": "Target-satisfied detection might abruptly restore CPU policy and cause oscillation.",
      "recommendation": "Use existing restore hysteresis instead of immediate restore.",
      "disposition": "Refuted as a blocker: design already uses the existing negative sample and restore hysteresis path."
    },
    {
      "dedupeKey": "follow-up-architecture-review-no-blockers",
      "severity": "info",
      "status": "confirmed_non_blocking",
      "verification": "subagent_architecture_review",
      "reviewer": "subagent-architecture",
      "firstSeenIteration": 3,
      "lastSeenIteration": 3,
      "materialRevisionAttempts": 0,
      "novelIssueSource": "none",
      "evidence": "Follow-up architecture reviewer checked the revised spec and plan review and found no plan blocker.",
      "finding": "Revised live CSV discovery, stale protection, runtime contract, and restore-hysteresis design are sufficient for implementation.",
      "recommendation": "Proceed, while preserving the boundary that packaged daemon behavior remains V4-compatible without a live FPS source.",
      "disposition": "Proceed to implementation."
    }
  ],
  "nextAction": {
    "type": "proceed",
    "summary": "Proceed to TDD implementation with live CSV discovery, stale protection, and runtime contract proof."
  }
}
```

## Implementation Notes

- Keep thresholds hardcoded in `GamePowerConfig` for now. They are scientific
  policy constants, not user-facing knobs.
- Add tests before implementation:
  - above-target stable frame telemetry suppresses activation
  - below-target or poor-pacing telemetry still activates EPP
  - missing telemetry preserves V4
  - active controller restores after target-satisfied hysteresis
  - JSONL includes frame-performance fields
  - MangoHud CSV parser uses the last valid window and handles partial files
- Device validation must explicitly state whether the packaged service used
  target-aware telemetry. If not, only claim V4-compatible service deployment and
  profile/CLI V5 behavior.
- Controlled profile validation must prove that V5 ran rather than merely
  falling back to V4: 22W should contain `fps-target-satisfied` with high
  confidence frame telemetry; 12W should retain `gpu-priority-epp` and avoid
  false target-satisfied classifications.

## Final Recommendation

Proceed with implementation after the revised spec. The plan is valid as a V5
profiler/CLI runtime governor improvement and as a safe foundation for later
service-level FPS telemetry discovery. It must not be represented as a complete
packaged-daemon FPS-target-aware scheduler until the daemon has a live FPS
source.
