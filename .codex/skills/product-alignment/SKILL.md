---
name: product-alignment
description: How this repo settles product direction and turns it into durable boundaries - the alignment loop, what counts as a decision versus a lookup, and how to sediment each feature into a boundary skill. Use before designing or redesigning a feature, when a decision affects user-visible behavior, when a plan needs stress-testing, or when writing or updating a boundary skill.
---

# Product alignment

Every feature gets a skill that states its **product boundary** and **behavior
boundary**. As long as later work honours those boundaries, direction holds even
as the implementation churns and the people (or models) doing the work change.

This is the mechanism that lets iteration be autonomous without drifting. It
only works if the boundaries are written down honestly, including the parts that
were decided *against*.

## Alignment is incremental by design

One pass never completes it. Expect to align, build, discover the next fork
during testing, and align again. Do not stall a whole feature waiting for total
clarity, and do not pretend a settled-sounding summary means every branch was
explored. Ship the part that is aligned, name the part that is not.

## The loop

1. **Look facts up. Never spend a question on something the environment can
   answer.** Filesystem, device, sysfs, docs, git history, upstream source. A
   question that a two-minute probe would have answered wastes the one resource
   the operator actually has: their attention.
2. **Put decisions to the operator**, batched, with a recommended answer and the
   trade-off spelled out. Decisions are theirs; recommendations are the value we
   add. A question with no recommendation is offloading work.
3. **When they defer a decision back**, make the call, state it explicitly, and
   name the criterion you used - so it can be vetoed rather than silently
   inherited.
4. **Feed findings back in.** If a lookup dissolves a question (an actuator turns
   out not to need the mechanism you assumed), say so and withdraw the question
   instead of asking it anyway.
5. **Do not build until the shape is agreed.** Then build, and let testing
   surface the next fork.

## Record why, not just what

A boundary with no reasoning gets re-litigated the next time someone reads the
code and finds it surprising. Each decision carries the evidence or the product
argument that produced it. Device measurements beat opinion; where a constant
came from a measurement, name the measurement.

## Record non-defects explicitly

The most expensive failure mode is an agent "fixing" a deliberate product
position. If something looks broken but is intended, write it down as intended
and say what the wrong fix would be. Examples that have already bitten:

- Demand shaping being inactive while plugged in is the AC performance-release
  position, not a bug.
- A user-facing surface being deliberately coarse (few, well-spaced options) is
  not an omission to be filled in with finer granularity.

## Boundaries

- Boundary skills describe **product and behavior limits**, not implementation
  detail. Implementation lives in code and in `docs/`; the skill says what must
  stay true about it.
- One skill per feature area, and it owns that area. Cross-reference sibling
  skills with `[[name]]` rather than restating their rules, so a boundary has
  exactly one home.
- Every project skill in `.codex/skills/` ships `evals/evals.json` with at least
  three behavior evals, including a near-miss that must **not** trigger the
  skill. Frontmatter is exactly `name` and `description`.
- Do not encode a decision as a boundary until the operator has actually agreed
  to it, or has explicitly delegated the call. Provisional direction belongs in
  `docs/`, not in a boundary skill.
- Never widen a boundary to make a failing change pass. Bring the conflict back
  as an alignment question.
- Current boundary skills: [[game-power-scheduler]], [[decky-panel-ux]].
