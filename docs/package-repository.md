# Package Repository

This repository uses GitHub Pages as the static hosting endpoint for the
`rivoreo-steamos` pacman repository.

Current custom domain:

```text
https://holo.libz.so/
```

Pacman repository URL:

```ini
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = https://holo.libz.so/rivoreo-steamos/os/$arch
```

Bootstrap URL:

```bash
curl -fsSL https://holo.libz.so/rivoreo-steamos/bootstrap.sh | sudo bash
```

The public release path is the GitHub Actions release publisher in
`.github/workflows/arch-release.yml`. Stable `vX.Y.Z` tags build signed packages
and deploy GitHub Pages. Release-candidate tags such as `vX.Y.Z-rc.N` run the
same signed package and repository build, then stop after uploading the signed
repository artifact; they do not deploy GitHub Pages. Ordinary pushes and pull
requests validate the repository through CI, but ordinary pushes cannot sign
packages or deploy GitHub Pages.

When the protected release signing secrets are not configured, hidden
release-candidate runs generate a short-lived candidate signing key so the
package build, repository metadata, and artifact upload path can still be
validated; stable releases require the protected signing secrets.

Required GitHub secrets:

- `ARCH_REPO_GPG_PRIVATE_KEY`: ASCII-armored private key for the Rivoreo package
  signing key.
- `ARCH_REPO_GPG_PASSPHRASE`: passphrase for the private key.
- `ARCH_REPO_GPG_KEY_ID`: full expected fingerprint for the imported key.

Release flow:

1. Push a stable version tag such as `v0.1.0`, or a hidden validation tag such
   as `v0.1.0-rc.1`.
2. The `validate` job runs the local harness with recursive submodules.
3. The `build-repo` job runs in `archlinux:base-devel`, imports the protected
   signing key, builds packages with `makepkg`, signs package artifacts, and
   creates signed `rivoreo-steamos` repository metadata with `repo-add`.
4. Stable tags run the `deploy-pages` job, which assembles the static site,
   rendered bootstrap script, public key files, and signed pacman repository into
   the Pages artifact.
5. Release-candidate tags such as `vX.Y.Z-rc.N` do not deploy GitHub Pages; use
   the signed repository artifact attached to the workflow run for inspection.
6. GitHub Pages serves stable results from `https://holo.libz.so/`.

GitLab CI remains the build path for validation artifacts:

- `python:test` runs the repository's local harness.
- `arch:package` runs in `archlinux:base-devel`, builds a source snapshot from
  the current commit, refreshes package checksums with `updpkgsums`, and
  produces `.pkg.tar.zst` artifacts with `makepkg`.
- `arch:repository` consumes the package artifact, runs `repo-add`, and outputs
  a static repository tree under
  `.cache/pacman-repo/public/rivoreo-steamos/os/x86_64`.

Those validation artifacts prove that package and repository metadata can be
generated for a given commit. They are not the public release path and do not
use signing secrets.

DNS should point the subdomain to GitHub Pages with:

```text
holo.libz.so.  CNAME  rivoreo.github.io.
```

The publisher writes regular files for `rivoreo-steamos.db`,
`rivoreo-steamos.files`, and their signatures. Do not use symlinks in the Pages
artifact.
