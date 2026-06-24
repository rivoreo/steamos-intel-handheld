# Arch Package CI Publisher Design

## Objective

Create a release-only GitHub Actions publisher that turns a tagged
`steamos-intel-handheld` release into a signed Arch pacman repository served
from the existing GitHub Pages site at:

```text
https://holo.libz.so/rivoreo-steamos/os/x86_64
```

This design refines the broader package repository design in
`docs/superpowers/specs/2026-06-24-arch-package-repository-design.md`. That
document describes the package ecosystem. This document defines the CI publishing
target that activates the public repository.

## Approved Direction

Use GitHub Actions as the public publisher.

The release flow is:

1. A maintainer pushes a version tag such as `v0.1.0`.
2. GitHub Actions builds Arch packages from that exact tag in a clean Arch
   container.
3. The workflow imports the CI signing key from protected GitHub secrets.
4. The workflow signs package artifacts and generates a signed
   `rivoreo-steamos` repository database with `repo-add`.
5. The workflow assembles a complete Pages artifact containing the static site,
   bootstrap endpoint, public key files, and pacman repository files.
6. GitHub Pages deploys the artifact to `holo.libz.so`.

Normal pushes and pull requests keep running tests and package validation only.
They must not publish or use release signing secrets.

## Current State

The repository already has:

- `packaging/arch/PKGBUILD` as the initial application package definition.
- `.gitlab-ci.yml` jobs that build package validation artifacts and an unsigned
  repository tree.
- `.github/workflows/pages.yml`, which deploys the static site from `site/`.
- `docs/package-repository.md`, which documents the future
  `rivoreo-steamos` repository URL.
- `site/index.html` and `site/rivoreo-steamos/bootstrap.sh`, which intentionally
  say the public repository is not active because a signed package database has
  not been published to Pages.

The missing piece is a release publisher that signs artifacts and promotes the
signed repository into the Pages artifact.

## CI Architecture

Add one release workflow, tentatively named `.github/workflows/arch-release.yml`.

The workflow has three jobs:

1. `validate`: runs the existing local harness and any package policy tests.
2. `build-repo`: runs in `archlinux:base-devel`, builds packages, signs them,
   creates repository metadata, and uploads an unsigned internal build log plus
   the signed repository tree as a workflow artifact.
3. `deploy-pages`: runs only after `build-repo` succeeds, merges `site/` with
   the generated repository tree, checks the artifact shape, and deploys to
   GitHub Pages.

The workflow trigger is:

```yaml
on:
  push:
    tags:
      - "v*.*.*"
  workflow_dispatch:
    inputs:
      tag:
        description: "Existing vX.Y.Z tag to publish"
        required: true
```

`workflow_dispatch` may publish only an existing `vX.Y.Z` version tag in this
repository. It must not publish an arbitrary branch, pull request ref, raw SHA,
or untagged commit.

## Package Build Contract

The CI publisher builds packages from the checked-out tag, not from mutable local
state.

The build job must:

- Check out the tagged commit with `submodules: recursive`.
- Install Arch build dependencies with pacman.
- Create a source archive for the tag or consume the release source archive in a
  reproducible way.
- Run `makepkg --cleanbuild --syncdeps --noconfirm`.
- Fail if the main `PKGBUILD` still uses `sha256sums=("SKIP")` for release
  sources.
- Build all packages required to make the repository self-hosting:
  `steamos-intel-handheld`, `rivoreo-keyring`, and `rivoreo-steamos-repo`.
- Treat the patched `steamos-intel-handheld-mangoapp` package as optional until
  its release packaging is ready. The first publisher may omit it, but the
  repository layout must allow adding it later without changing the URL scheme.

## Signing Model

The CI publisher uses one repository signing key dedicated to
`rivoreo-steamos`.

Required GitHub secrets:

- `ARCH_REPO_GPG_PRIVATE_KEY`: ASCII-armored private key.
- `ARCH_REPO_GPG_PASSPHRASE`: passphrase for the private key.
- `ARCH_REPO_GPG_KEY_ID`: expected key id or fingerprint.

The workflow must:

- Import the key into a temporary `GNUPGHOME`.
- Verify the imported fingerprint equals `ARCH_REPO_GPG_KEY_ID`.
- Sign each `.pkg.tar.zst` with detached signatures.
- Run `repo-add --sign` so the repository database has a detached signature.
- Include package signatures in the repository database when possible by keeping
  each `.sig` file beside its package before `repo-add`.
- Never print private key material, passphrases, or unmasked fingerprint secrets.

The public key is published under:

```text
site/rivoreo-steamos/key/rivoreo.gpg
site/rivoreo-steamos/key/fingerprint.txt
```

The bootstrap script pins the expected fingerprint and refuses to configure the
repo if the downloaded key does not match.

## Pages Artifact Contract

The deployed Pages artifact must contain the normal site and this repository
tree:

```text
_site/
  index.html
  rivoreo-steamos/
    index.html
    bootstrap.sh
    key/
      rivoreo.gpg
      fingerprint.txt
    os/
      x86_64/
        rivoreo-steamos.db
        rivoreo-steamos.db.sig
        rivoreo-steamos.db.tar.zst
        rivoreo-steamos.db.tar.zst.sig
        rivoreo-steamos.files
        rivoreo-steamos.files.sig
        rivoreo-steamos.files.tar.zst
        rivoreo-steamos.files.tar.zst.sig
        *.pkg.tar.zst
        *.pkg.tar.zst.sig
```

The `.db` and `.files` aliases must be regular files copied from the
corresponding `.tar.zst` files. They must not be symbolic links or hard links,
because GitHub Pages artifact upload does not reliably preserve pacman-style
repository aliases.

## Website And Bootstrap Handoff

Activating the publisher requires changing the static site and bootstrap script
in the same implementation series.

The site changes should:

- Replace "Repository not activated" messaging with an install-ready state only
  after the release workflow exists and can deploy signed repository files.
- Keep the pacman stanza:
  ```ini
  [rivoreo-steamos]
  SigLevel = Required TrustedOnly
  Server = https://holo.libz.so/rivoreo-steamos/os/$arch
  ```
- Tell users to review the bootstrap script before running it.

The bootstrap changes should:

- Remain idempotent.
- Require root.
- Verify the published key fingerprint before importing it.
- Initialize pacman's keyring when needed.
- Import and locally sign the Rivoreo key.
- Write `/etc/pacman.d/rivoreo-steamos.conf`.
- Add `Include = /etc/pacman.d/rivoreo-steamos.conf` to `/etc/pacman.conf` only
  when missing.
- Run `pacman -Sy`.
- Install `rivoreo-keyring`, `rivoreo-steamos-repo`, and
  `steamos-intel-handheld`.

## Verification Strategy

Automated verification must cover both publish safety and pacman usability.

Static tests:

- The release workflow triggers only on `v*.*.*` tags or an explicit
  `workflow_dispatch` tag input.
- No non-tag push path can access signing secrets or deploy Pages.
- Package repository docs and site text agree on the public URL.
- Bootstrap does not contain `SigLevel = Never`.
- The Pages artifact checks assert regular files for `rivoreo-steamos.db` and
  `rivoreo-steamos.files`.

CI runtime checks:

- `gpg --verify` succeeds for every package signature.
- `repo-add --verify` succeeds for the generated database.
- A temporary pacman root can add the generated repo and resolve
  `steamos-intel-handheld` with `pacman -Syp`.
- The generated Pages artifact contains `bootstrap.sh`, public key files, signed
  database files, package files, and package signatures.

Manual release checks:

- Install from `https://holo.libz.so/rivoreo-steamos/bootstrap.sh` on a SteamOS
  target.
- Reboot and verify the installed service state.
- Rerun bootstrap to prove idempotency.

## Failure Handling

If any validation, build, signing, or repository metadata check fails, the
workflow stops before deployment. A failed release tag should leave the previous
GitHub Pages deployment intact.

If deployment succeeds but hardware validation later finds a release-blocking
issue, the maintainer should publish a higher version or packaging `pkgrel`.
The CI publisher should not overwrite history in the public repository as its
primary rollback mechanism.

## Non-Goals

- Do not publish from ordinary branch pushes.
- Do not support `SigLevel = Never`.
- Do not make GitLab CI responsible for public deployment.
- Do not require AUR helpers or on-device package builds.
- Do not solve generic SteamOS package persistence beyond making bootstrap safe
  to rerun after SteamOS updates.

## Success Criteria

The design is implemented when:

- Pushing a `vX.Y.Z` tag can publish the signed repository to GitHub Pages.
- Ordinary pushes and pull requests cannot publish or access signing secrets.
- The Pages artifact contains regular-file pacman database aliases and detached
  signatures.
- Pacman can resolve and install `steamos-intel-handheld` from the published
  `rivoreo-steamos` repository with `SigLevel = Required TrustedOnly`.
- The website and bootstrap endpoint present the repository as active only after
  the signed repository is present.
- The release flow is documented enough that a maintainer can rotate the signing
  key by updating the key package, public key files, bootstrap fingerprint, and
  GitHub secrets in one planned release.
