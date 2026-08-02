---
name: arch-release-publisher
description: Publish or validate this project's Arch/SteamOS package repository. Use for release candidates, stable vX.Y.Z releases, arch-release.yml, signed pacman repository artifacts, GitHub Pages deployment, signing secrets, repository docs, or rivoreo-steamos install instructions.
---

# Arch release publisher

Use `docs/release-process.md` as the operational source of truth. This skill
keeps the channel and evidence boundaries visible without duplicating that
runbook.

## Classify the action

- An ordinary branch push is not a package publication.
- A hidden release candidate validates build/repository shape but is not a
  stable user channel.
- A stable `vX.Y.Z` release requires the documented signing, artifact
  verification, and Pages deployment path.

Never present a candidate signature, locally assembled repository, or
development artifact as a stable signed release.

## Contracts to preserve

- `arch-release.yml` validates before building and makes `deploy-pages` depend
  on `verify-repo-artifact`.
- Stable publishing uses the configured signing secrets and produces the signed
  pacman database/package shape described by the runbook.
- User bootstrap instructions use the stable HTTPS repository and do not expose
  hidden candidate URLs, local paths, private hosts, or unsigned shortcuts.
- MangoHud/mangoapp release artifacts must come from the documented SteamOS
  rootfs path; a generic host build is not release-parity evidence.

## Work from evidence

Inspect the actual workflow, release docs, tags, and artifacts relevant to the
request. Run focused local checks while editing. Use the repo closure suite for
release readiness, and run guarded artifact/signing/publishing checks only when
the task reaches those boundaries and the user has authorized the side effect.

A write request must identify the channel and target. After publishing or
editing release state, read back the tag/release, artifact verification, and
deployment result before claiming success.

Report local, candidate, stable, signing, artifact, and Pages evidence as
separate layers. Missing secrets, network access, or deployment authority are
blockers to the corresponding claim, not reasons to simulate success.
