---
name: arch-release-publisher
description: Repo-specific workflow for publishing or validating this project's Arch/SteamOS package repository. Use when working on hidden release candidates, stable vX.Y.Z releases, GitHub Actions arch-release.yml, signed pacman repository artifacts, GitHub Pages deployment, release signing secrets, package repository docs, or user install instructions for rivoreo-steamos.
---

# Arch Release Publisher

## Overview

Use this skill to keep the release workflow consistent across humans, CI, and
future agent runs. The authoritative human runbook is
`docs/release-process.md`.

## Core Rule

Read `docs/release-process.md` before changing release behavior, tagging a
release, reporting release status, or editing user install instructions.

## Release Decision

Choose the channel before running commands:

- Stable release: use `vX.Y.Z` only when protected signing secrets are configured
  and a hidden release candidate has already validated the path.
- Hidden release candidate: use `vX.Y.Z-rc.N` to validate package builds,
  repository metadata, signing, and artifact upload without publishing Pages.
- Ordinary push or pull request: treat as validation only. It cannot publish the
  signed pacman repository.

Do not tag or push a stable release when the only available signing path is the
candidate signing fallback.

## Release Facts To Preserve

- Stable `vX.Y.Z` releases deploy GitHub Pages through `deploy-pages`.
- Hidden `vX.Y.Z-rc.N` releases upload `signed-pacman-repository` and skip
  `deploy-pages`.
- Hidden release candidates may generate a short-lived candidate signing key.
- Stable releases require `ARCH_REPO_GPG_PRIVATE_KEY`,
  `ARCH_REPO_GPG_PASSPHRASE`, and `ARCH_REPO_GPG_KEY_ID`.
- Users install only from the public stable bootstrap URL, not from hidden
  release-candidate artifacts.

## Files To Inspect

For release behavior changes, inspect:

- `.github/workflows/arch-release.yml`
- `scripts/build-arch-release-repo.sh`
- `scripts/assemble-arch-release-pages.sh`
- `packaging/arch/`
- `site/rivoreo-steamos/bootstrap.sh`
- `docs/package-repository.md`
- `docs/release-process.md`
- `tests/test_arch_release_workflow.py`
- `tests/test_release_documentation.py`

## Operator Workflow

1. Read `docs/release-process.md`.
2. Verify the intended tag matches the version in `pyproject.toml`.
3. Run the local harness before tagging.
4. Prefer a hidden release candidate after any release workflow change.
5. Watch the GitHub Actions run until it reaches a final state.
6. For candidates, verify the `signed-pacman-repository` artifact exists and
   `deploy-pages` was skipped.
7. For stable releases, verify `deploy-pages` succeeded and report the public
   repository URL.

## Verification

Use the targeted release/documentation tests while editing this workflow:

```bash
.venv/bin/python -m pytest tests/test_arch_release_workflow.py tests/test_release_documentation.py
```

Before reporting completion, run the repository harness when feasible:

```bash
PYTHON=.venv/bin/python scripts/check-local.sh
```

Validate this skill after editing it:

```bash
python3 .codex/skills/skill-creator/scripts/quick_validate.py .codex/skills/arch-release-publisher
```

That validator imports PyYAML. If the active environment does not provide it,
report the dependency gap and run an equivalent frontmatter/name/description
structure check instead of editing unrelated project dependencies.

## Report Shape

When reporting a release or release-documentation change, include:

- tag and commit SHA, if a tag was pushed
- GitHub Actions run ID and URL, if a run was observed
- `validate`, `build-repo`, and `deploy-pages` results
- artifact name for candidates, or public repo URL for stable releases
- tests and validation commands actually run
