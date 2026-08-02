# Prompt anti-patterns

## Reasoning extraction

Avoid requests to reveal chain-of-thought or narrate every reasoning step. Ask
for conclusions, evidence, assumptions, and concise rationale.

## Process micromanagement

Fixed file order, tool cadence, progress intervals, and repeated self-checks can
prevent a frontier agent from pivoting when the repository contradicts the
prompt.

## Universal ceremony

Do not require TDD, subagents, graders, held-out sweeps, or fixed iteration
counts for every task. Attach them to a stable behavior, measured uncertainty,
or high-impact risk.

## Recall suppression

"Only report issues when absolutely certain" and severity-only wording can hide
real defects. Ask for actionable findings with evidence and calibrated
confidence; filter downstream if necessary.

## Vendor stereotypes

Do not treat all GPT, Claude, or open-source models as one capability tier or
force a vendor-specific prompt syntax. Classify the actual model and surface.

## Repetition and contradiction

Stating the same rule in several places increases tokens and can make precedence
unclear. Keep one authoritative instruction for each constraint.

## Context anxiety

Do not tell a long-horizon agent to rush because context is running low. Give
durable state or handoff requirements only when the execution environment
actually needs them.
