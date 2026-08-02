# Headless delegation prompts

A headless task should be self-contained because the final response is its
return value. Include:

- the concrete objective and bounded surface;
- inputs or repository locations it must inspect;
- important authority and side-effect boundaries;
- acceptance evidence and desired return contents;
- the condition that genuinely requires escalation.

For frontier agents, keep this compact and allow the implementation path to
react to repository evidence. Do not add fixed tool order, chain-of-thought,
progress cadence, or multiple self-review rounds.

For a weaker executor, add only the scaffolding that addresses observed gaps:
ordered steps, examples, explicit edge-case branches, output schema, and stop
conditions.

Delegation is useful for independent work or scrutiny. It is not a requirement,
does not expand authority, and should not be used when reloading context costs
more than doing the small task directly.
