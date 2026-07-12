## Summary

- 

## TDD evidence

Changes to maintained repeatable behavior follow `docs/tdd-workflow.md`; use its
canonical scope to decide whether RED/GREEN applies.

Verification remains independent and required even when RED/GREEN is not applicable.

RED evidence:

```text
paste the focused failing test output here
```

GREEN evidence:

```text
paste the same focused test passing here
```

Verification evidence:

```text
paste scripts/check-local.sh output here
```

Device evidence, if hardware-facing behavior changed:

```text
paste scripts/verify-on-device.sh output or explain why device verification is not required
```

## Checklist

- [ ] RED evidence, or N/A for exempt work with the reason in the summary.
- [ ] GREEN evidence, or N/A for exempt work with the reason in the summary.
- [ ] `scripts/check-local.sh` passes.
- [ ] Device-facing changes include device harness output.
- [ ] New hardware support includes `scripts/collect-device-info.sh` evidence.
