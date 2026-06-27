# Package Repository

This repository uses GitHub Pages as the static hosting endpoint for the
`rivoreo-steamos` pacman repository.

Current GitHub Pages HTTPS endpoint:

```text
https://rivoreo.github.io/steamos-intel-handheld/
```

Pacman repository URL:

```ini
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos/os/$arch
```

Bootstrap URL:

```bash
curl -fsSL https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos/bootstrap.sh | sudo bash
```

The public release path is the GitHub Actions release publisher in
`.github/workflows/arch-release.yml`. Stable `vX.Y.Z` tags build signed packages
and deploy GitHub Pages. Release-candidate tags such as `vX.Y.Z-rc.N` run the
same signed package and repository build, then stop after uploading the signed
repository artifact; they do not deploy GitHub Pages. Ordinary pushes and pull
requests validate the repository through CI, but ordinary pushes cannot sign
packages or deploy GitHub Pages.

Detailed release operator runbook: `docs/release-process.md`. Repo-local agent
guidance for the same workflow lives in `.codex/skills/arch-release-publisher`.

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
3. The `build-mangoapp` job builds the patched MangoHud `mangoapp` binary on
   Linux x86_64 in a SteamOS rootfs chroot extracted from Valve's recovery
   image.
4. The `build-repo` job runs in `archlinux:base-devel`, imports the protected
   signing key, builds packages with `makepkg`, signs package artifacts, and
   creates signed `rivoreo-steamos` repository metadata with `repo-add`.
5. Stable tags run the `deploy-pages` job, which assembles the static site,
   rendered bootstrap script, public key files, and signed pacman repository into
   the Pages artifact.
6. Release-candidate tags such as `vX.Y.Z-rc.N` do not deploy GitHub Pages; use
   the signed repository artifact attached to the workflow run for inspection.
7. GitHub Pages serves stable results from `https://rivoreo.github.io/steamos-intel-handheld/`.

The main `steamos-intel-handheld` package also ships the battery charge-limit
CLI and the Decky Loader plugin runtime under
`/home/deck/homebrew/plugins/steamos-intel-handheld-ec`, so pacman updates carry
the Steam UI entry point as well as the backend.

Decky Loader is optional for the backend service and CLI. The Steam UI Charge Limit panel requires Decky Loader because Decky is the runtime that loads the
plugin from `/home/deck/homebrew/plugins/steamos-intel-handheld-ec`. The
installer reports whether Decky Loader was detected; missing Decky Loader does
not fail package installation.

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

After you download the GitLab CI artifact, run:

```bash
scripts/verify-gitlab-pacman-artifact.sh /path/to/downloaded/artifact
```

GitLab CI artifacts are validation-only and unsigned. Passing this dry run proves
package and repository shape, not public release trust.

The Pages artifact does not ship a `CNAME` file. Keep the public install path on
GitHub Pages' HTTPS project URL unless a custom domain has a valid HTTPS
certificate and redirects only to HTTPS.

The publisher writes regular files for `rivoreo-steamos.db`,
`rivoreo-steamos.files`, and their signatures. Do not use symlinks in the Pages
artifact.
