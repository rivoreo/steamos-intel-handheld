# Release Process

This is the operator runbook for publishing the Arch/SteamOS packages and the
`rivoreo-steamos` pacman repository for this project.

Use it when creating a hidden release candidate, promoting a stable release,
debugging the release workflow, or explaining how users install the public
repository. Agent-oriented release guidance also lives in
`.codex/skills/arch-release-publisher`.

## Release Channels

Stable tags: `vX.Y.Z`

- run repository validation through GitHub Actions
- build all Arch packages in `archlinux:base-devel`
- verify the signed pacman repository artifact before deployment
- sign package artifacts and repository metadata with the protected release key
- deploys GitHub Pages with the public pacman repository

Candidate tags: `vX.Y.Z-rc.N`

- run the same validation and package/repository build path
- run the same `verify-repo-artifact` gate
- upload the `signed-pacman-repository` artifact for inspection
- skips `deploy-pages`
- may use a short-lived candidate signing key when protected signing secrets are
  absent

Ordinary pushes and pull requests are validation only. They do not sign release
packages and do not deploy GitHub Pages.

## Required Stable Secrets

Stable releases require the protected signing secrets:

- `ARCH_REPO_GPG_PRIVATE_KEY`: ASCII-armored private key for the Rivoreo package
  signing key.
- `ARCH_REPO_GPG_PASSPHRASE`: passphrase for the private key.
- `ARCH_REPO_GPG_KEY_ID`: full expected fingerprint for the imported key.

Candidate releases can use the candidate signing fallback for CI validation, but
stable releases require the protected signing secrets. Do not treat a candidate
fallback artifact as a public repository.

## What CI Builds

The `Arch Package Release` workflow in `.github/workflows/arch-release.yml`
builds:

- `steamos-intel-handheld`
- `steamos-intel-handheld-mangoapp`
- `steamos-intel-handheld-mangoapp-debug`
- `rivoreo-keyring`
- `rivoreo-steamos-repo`

The main `steamos-intel-handheld` package includes the TDP service, the
`steamos-intel-handheld-ec-control` CLI, the restore CLI and restore systemd
service, the MSI Claw 8 AI+ gamescope display profile and session hooks, and the
Decky Loader charge-limit plugin runtime under
`/home/deck/homebrew/plugins/steamos-intel-handheld-ec`.

The restore payload includes canonical managed artifacts under
`/opt/steamos-intel-handheld/share/etc-artifacts`, a durable
`/etc/systemd/system/steamos-intel-handheld-restore.service` anchor, and a
durable power-control unit copy. It repairs project-owned `/etc` files after
SteamOS overlay rotations. It does not package WireGuard private configuration;
`/etc/wireguard/rncn-steamdeck.conf` is a health-check-only artifact.

Decky Loader is optional for the backend service and CLI. The Steam UI Charge Limit panel requires Decky Loader, and the installer reports whether Decky Loader was detected. Missing Decky Loader must remain a warning, not a package
installation failure.

The release build script also creates signed `rivoreo-steamos` repository
metadata, and package versions derive from `pyproject.toml`; before building the
main package, CI syncs `packaging/arch/PKGBUILD` to that version and refreshes
checksums with `updpkgsums`.

The `steamos-intel-handheld-mangoapp` package uses the patched MangoHud
submodule. CI builds the `mangoapp` executable on Linux x86_64 in a SteamOS
rootfs chroot extracted from Valve's recovery image, then packages that binary
separately so users do not fall back to the unpatched system MangoHud. The QEMU
VM path is for local macOS or non-Linux development and should not be used as
the release CI path.

For GitHub Pages compatibility, repo aliases `.db`, `.files`, and `.sig` are
regular files, not symlinks.

The `verify-repo-artifact` job downloads the generated
`signed-pacman-repository` artifact before any Pages deployment. It verifies the
repo database aliases, package and database signature pairs, published signing
key fingerprint, package metadata, expected package contents, HTTPS-only repo
configuration, restore service payload, and `mangoapp` payload path. It imports
the artifact public key and runs `gpg --batch --verify` for every generated
`.sig` file.

## Before Tagging

1. Confirm the version in `pyproject.toml` matches the intended stable base.
   For example, `0.1.0` permits `v0.1.0` and `v0.1.0-rc.N`.
2. Run the local harness:

   ```bash
   PYTHON=.venv/bin/python scripts/check-local.sh
   ```

3. Review the release workflow and package changes:

   ```bash
   git diff -- .github/workflows/arch-release.yml scripts/build-arch-release-repo.sh packaging/arch docs/release-process.md
   ```

4. If release automation changed, publish a hidden release candidate before a
   stable tag.

## Hidden Candidate Release

Use a candidate tag to validate the full package build and repository artifact
without publishing GitHub Pages:

```bash
git tag -a v0.1.0-rc.5 -m "v0.1.0-rc.5"
git push origin v0.1.0-rc.5
```

Watch the workflow:

```bash
gh run list --repo rivoreo/steamos-intel-handheld --workflow "Arch Package Release" --limit 5
gh run watch <run-id> --repo rivoreo/steamos-intel-handheld --exit-status
```

If the run fails, read only the failed logs first:

```bash
gh run view <run-id> --log-failed --repo rivoreo/steamos-intel-handheld
```

Expected candidate result:

- `validate`: success
- `build-mangoapp`: success
- `build-repo`: success
- `verify-repo-artifact`: success
- `deploy-pages`: skipped
- `signed-pacman-repository`: uploaded artifact

## GitLab Validation Artifact Dry Run

When a GitLab pipeline is used for package validation, download the
`arch:repository` artifact and run:

```bash
scripts/verify-gitlab-pacman-artifact.sh /path/to/downloaded/artifact
```

This verifies repository aliases and main package contents. It does not replace
the signed GitHub release artifact gate because GitLab artifacts are
validation-only and unsigned.

Check the artifact exists:

```bash
gh api repos/rivoreo/steamos-intel-handheld/actions/runs/<run-id>/artifacts \
  --jq '.artifacts[] | select(.name=="signed-pacman-repository") | {name, expired, size_in_bytes}'
```

Download and inspect the artifact shape when needed:

```bash
gh run download <run-id> \
  --repo rivoreo/steamos-intel-handheld \
  --name signed-pacman-repository \
  --dir /tmp/rivoreo-rc
find /tmp/rivoreo-rc -maxdepth 4 -type f | sort
```

Expected files include:

- `key/fingerprint.txt`
- `key/rivoreo.gpg`
- `rivoreo-steamos/os/x86_64/*.pkg.tar.zst`
- `rivoreo-steamos/os/x86_64/*.pkg.tar.zst.sig`
- `rivoreo-steamos/os/x86_64/steamos-intel-handheld-mangoapp-*.pkg.tar.zst`
- `rivoreo-steamos/os/x86_64/steamos-intel-handheld-mangoapp-*.pkg.tar.zst.sig`
- `rivoreo-steamos/os/x86_64/steamos-intel-handheld-mangoapp-debug-*.pkg.tar.zst`
- `rivoreo-steamos/os/x86_64/steamos-intel-handheld-mangoapp-debug-*.pkg.tar.zst.sig`
- the main `steamos-intel-handheld` package contains
  `/etc/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua`
  and the matching `gamescope-session.service` user hooks
- the main `steamos-intel-handheld` package contains
  `/usr/bin/steamos-intel-handheld-restore-etc`,
  `/etc/systemd/system/steamos-intel-handheld-restore.service`, and
  `/opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml`
- the `steamos-intel-handheld-mangoapp` package contains
  `/opt/steamos-intel-handheld/share/etc-artifacts/manifest.d/10-mangoapp.toml`
- `rivoreo-steamos/os/x86_64/rivoreo-steamos.db`
- `rivoreo-steamos/os/x86_64/rivoreo-steamos.db.sig`
- `rivoreo-steamos/os/x86_64/rivoreo-steamos.files`
- `rivoreo-steamos/os/x86_64/rivoreo-steamos.files.sig`

## Stable Release

Only create the stable tag after a hidden candidate has validated the release
path and the protected signing secrets are configured:

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

Watch the workflow:

```bash
gh run list --repo rivoreo/steamos-intel-handheld --workflow "Arch Package Release" --limit 5
gh run watch <run-id> --repo rivoreo/steamos-intel-handheld --exit-status
```

Expected stable result:

- `validate`: success
- `build-mangoapp`: success
- `build-repo`: success
- `verify-repo-artifact`: success
- `deploy-pages`: success
- public repository served from
  `https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos/os/x86_64`

After the Pages deployment, users install from the public bootstrap path:

```bash
curl -fsSL https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos/bootstrap.sh | sudo bash
```

Users should not install from hidden release-candidate artifacts. Those artifacts
are for CI validation and operator inspection only.

## First Candidate Failure Modes

The first hidden candidate sequence exposed these concrete failure modes. Keep
them in mind when changing the workflow:

- `v0.1.0-rc.1`: checkout failed inside the Arch container until the workflow
  added the `Install checkout dependencies` step and installed `git` before
  `actions/checkout`.
- `v0.1.0-rc.2`: signing failed with missing signing secrets; hidden candidates
  now have a candidate signing fallback, while stable releases still fail
  clearly without protected secrets.
- `v0.1.0-rc.3`: package build failed because the Python build backend
  `setuptools.build_meta` was unavailable; `python-setuptools` is now declared
  for Arch builds.
- `v0.1.0-rc.4`: hidden candidate validation succeeded. The run uploaded the
  `signed-pacman-repository` artifact and skipped Pages deployment.
- `v0.1.0-rc.5`: cancelled after the QEMU/SSH `build-mangoapp` path spent about
  19 minutes waiting on a VM that never exposed SSH; release CI now uses the
  Linux SteamOS rootfs chroot path instead.
- `v0.1.0-rc.6`: rootfs/chroot path reached SteamOS `pacman`, then failed
  `CheckSpace` because the chroot root was not a mount point; the helper now
  bind-mounts the rootfs onto itself before entering chroot.
- `v0.1.0-rc.8`: hidden candidate validation succeeded, including the patched
  `mangoapp` build and signed repository artifact upload; release CI now has a
  dedicated `verify-repo-artifact` gate for package contents, repository
  metadata, HTTPS repo configuration, and detached signatures before Pages
  deployment.

## Reporting A Release

When reporting a candidate or stable release result, include:

- tag name
- commit SHA
- GitHub Actions run ID and URL
- `validate`, `build-mangoapp`, `build-repo`, `verify-repo-artifact`, and
  `deploy-pages` job results
- artifact name for candidates, or public repository URL for stable releases
- whether protected signing secrets or the candidate signing fallback were used
