# Prompt rewrite protocol

1. Record the target capability, execution surface, objective, and observed
   failure.
2. Map every old instruction to a real requirement, prior incident, or measured
   gap. Delete instructions with no support.
3. Consolidate duplicates and contradictions.
4. Replace process control with outcome language:
   - intent and success state;
   - scope and authority;
   - observable evidence;
   - genuine pause or escalation conditions.
5. Keep task data separate from stable policy. Add delimiters only when the
   interface or data ambiguity needs them.
6. Validate in proportion to impact:
   - obvious low-risk edit: static inspection plus a positive and negative case;
   - reused/high-impact prompt: representative A/B cases;
   - unstable behavior: multiple samples only after variance is observed.

Do not claim improvement from prompt length alone. Measure task success,
precision/recall, latency, token use, or cost as appropriate.
