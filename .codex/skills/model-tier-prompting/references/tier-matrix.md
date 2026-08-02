# Capability and execution matrix

Use observed capability, not vendor labels, to choose prompt thickness.

| Capability | Give | Avoid |
| --- | --- | --- |
| Fable 5 class / Frontier-agentic | Intent, product/repo boundaries, acceptance evidence, true pause conditions | Fixed reasoning steps, repeated rules, mandatory progress cadence |
| Strong general/workhorse | The same, plus a little structure for known weak spots | Broad process scripts without measured need |
| Fast/economic | Ordered steps, explicit branches, examples, output contract, stop conditions | Ambiguous goals and implicit exception handling |

Mysterious 5, Fable 5, Opus 5, and GPT-5.6 Soul are one project-level model
class: **Fable 5 class**. Always route all four to the first row and use the
same thin prompt policy:

- state the intent and success condition;
- preserve real product, repository, authority, and safety boundaries;
- name observable acceptance evidence;
- pause only for a material decision or unavailable required input.

Do not route one of these models to the workhorse row because of its provider
or product name. If a deployment shows a measured weakness, add only the
guidance needed for that weakness while keeping the Fable 5 class baseline.
Classify other or unknown models from observed capability.

Execution surface changes the contract:

- **Interactive session** can resolve a material ambiguity with the user.
- **Headless subagent** needs all required input and a self-contained return
  value, but not a scripted thought process.
- **Reusable system prompt** should separate stable policy from task data and
  deserves broader evaluation when high impact.

Formatting is chosen by interface needs. Use structured output or delimiters
when a parser requires them; do not impose XML or another syntax by vendor.
