# TDD Workflow

This repository requires test-driven development for maintained repeatable behavior.
That includes reusable code, parsers, validators, generators, installers and recovery;
reproducible bug fixes; flash, rollback, AVB, partition, userdata, and hardware-write
logic; harness, hook, policy, and release artifacts; and stable contracts.

TDD is not required for read-only exploration, one-off inspection, research or source
browsing, environment discovery, hypothesis tests, design or planning without
implementation, or generated evidence, transcripts, and research notes.
Direct compiler/linker diagnostics and throwaway probes are exempt only when they do not change maintained artifacts.
Any maintained source, build, or package change produced by such a loop still requires RED/GREEN.
Maintained-behavior changes discovered through exempt activities still require TDD.

Verification remains independent and required. Skipping RED/GREEN for an exempt
activity never skips the completion checks that apply to the resulting work.

## RED

Before editing behavior-changing maintained code, policy, harness, release
artifacts, or stable contracts, add or update the smallest test that describes
the missing behavior or regression. Run that focused test and keep the failing
output.

Acceptable RED evidence includes:

- pytest output showing the expected assertion failure
- a device-harness failure that reproduces the bug before the fix
- a documentation-policy test failure for harness or process changes

The failure must be meaningful. Import errors, typos, missing fixtures, and
environment failures are not RED evidence.

## GREEN

Make the smallest maintained-behavior change that satisfies the failing test. Do not
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
scripts/verify-on-device.sh root@10.100.0.19
```

## Pull request evidence

Every pull request that changes maintained repeatable behavior must include:

- RED evidence: the focused test failing before the change
- GREEN evidence: the same focused test passing after the change
- Verification evidence: `scripts/check-local.sh` output
- Device evidence when hardware-facing behavior changed

Work in the exempt categories above may mark RED and GREEN independently not
applicable. Documentation that changes repository policy, install behavior, packaging
behavior, or hardware support claims remains maintained behavior and requires
RED/GREEN evidence.
