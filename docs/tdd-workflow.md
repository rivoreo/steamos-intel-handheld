# TDD Workflow

This repository requires test-driven development for production behavior
changes. The rule is deliberately simple:

No production behavior change may be merged without RED evidence.

## RED

Before editing production code, add or update the smallest test that describes
the missing behavior or regression. Run that focused test and keep the failing
output.

Acceptable RED evidence includes:

- pytest output showing the expected assertion failure
- a device-harness failure that reproduces the bug before the fix
- a documentation-policy test failure for harness or process changes

The failure must be meaningful. Import errors, typos, missing fixtures, and
environment failures are not RED evidence.

## GREEN

Make the smallest production change that satisfies the failing test. Do not
bundle unrelated refactors or additional behavior into the GREEN step.

Run the same focused test again and keep the passing output.

## VERIFY

After the focused test is green, run the local harness:

```bash
scripts/check-local.sh
```

For changes that touch install behavior, SteamOS Manager integration, RAPL
behavior, or hardware profiles, also run the device harness against a root SSH
target:

```bash
scripts/verify-on-device.sh root@192.168.128.214
```

## Pull request evidence

Every pull request that changes behavior must include:

- RED evidence: the focused test failing before the change
- GREEN evidence: the same focused test passing after the change
- Verification evidence: `scripts/check-local.sh` output
- Device evidence when hardware-facing behavior changed

Pure documentation-only changes may skip RED/GREEN evidence only when they do
not change repository policy, install behavior, packaging behavior, or hardware
support claims.
