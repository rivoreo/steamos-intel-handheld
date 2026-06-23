## Summary

- 

## TDD evidence

No production behavior change without a failing test first.

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

- [ ] Behavior changes have RED evidence.
- [ ] The same focused test has GREEN evidence.
- [ ] `scripts/check-local.sh` passes.
- [ ] Device-facing changes include device harness output.
- [ ] New hardware support includes `scripts/collect-device-info.sh` evidence.
