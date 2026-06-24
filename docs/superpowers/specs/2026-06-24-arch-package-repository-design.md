# Arch/SteamOS Package Repository Design

## Objective

Publish `steamos-intel-handheld` as installable Arch-style packages and host a
small Rivoreo pacman repository so SteamOS and Arch-based handheld users can
install and upgrade the support layer without cloning the repository or running
the SSH development installer.

## Current State

The repository already has a packaging draft at `packaging/arch/PKGBUILD`. It
builds the Python wheel and installs the systemd unit, D-Bus policy, SteamOS
Manager remote config, and license. It is not release-ready yet because it uses
`sha256sums=("SKIP")`, has no package install hook, has no automated package
build validation, and is not connected to a published pacman repository.

The current development installer copies source files to
`/opt/steamos-intel-handheld` and writes system configuration over root SSH. The
package path should preserve that software-owned `/opt/steamos-intel-handheld`
runtime location for SteamOS compatibility while allowing pacman to own the
installation.

## External Packaging Facts

- Arch package metadata lives in `PKGBUILD`; `makepkg` consumes it and builds
  packages that pacman can install.
- A custom binary repository is a directory of `.pkg.tar.zst` packages plus a
  repository database generated with `repo-add`.
- Pacman repository sections are configured in `pacman.conf` with a unique
  section name, a `Server = ...` URL, and repository-specific `SigLevel`.
- Pacman package and repository signatures should be rooted in pacman's keyring,
  managed through `pacman-key` or a keyring package.

References:

- <https://man.archlinux.org/man/PKGBUILD.5>
- <https://man.archlinux.org/man/makepkg.8>
- <https://man.archlinux.org/man/repo-add.8>
- <https://man.archlinux.org/man/pacman.conf.5>
- <https://man.archlinux.org/man/pacman-key.8>

## Target Architecture

Use a signed binary pacman repository as the primary distribution path. Keep AUR
support optional and secondary because SteamOS handhelds should not need local
build dependencies or on-device compilation.

The distribution system has four layers:

1. **Package definitions** in `packaging/arch/`, including the application
   package, keyring package, repository-config package, and optional patched
   `mangoapp` package while the MangoHud change is not upstream.
2. **Build automation** that runs `makepkg` in a clean Arch container or chroot,
   signs packages, and stages them in a local repository directory.
3. **Repository publication** that runs `repo-add`, signs the repository
   database, and uploads static files to HTTPS hosting.
4. **SteamOS bootstrap** that imports the signing key, adds the repo, handles
   SteamOS read-only rootfs, installs packages, enables services, and verifies
   the result.

## Repository Layout

Publish a static tree like this:

```text
public/
  rivoreo-steamos/
    key/
      rivoreo.gpg
      rivoreo.gpg.sig
      fingerprint.txt
    bootstrap.sh
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
        rivoreo-keyring-<version>-<rel>-any.pkg.tar.zst
        rivoreo-keyring-<version>-<rel>-any.pkg.tar.zst.sig
        rivoreo-steamos-repo-<version>-<rel>-any.pkg.tar.zst
        rivoreo-steamos-repo-<version>-<rel>-any.pkg.tar.zst.sig
        steamos-intel-handheld-<version>-<rel>-any.pkg.tar.zst
        steamos-intel-handheld-<version>-<rel>-any.pkg.tar.zst.sig
        steamos-intel-handheld-mangoapp-<version>-<rel>-x86_64.pkg.tar.zst
        steamos-intel-handheld-mangoapp-<version>-<rel>-x86_64.pkg.tar.zst.sig
```

Use the repository name `rivoreo-steamos` and the pacman server URL:

```ini
[rivoreo-steamos]
SigLevel = Required TrustedOnly
Server = https://repo.rivoreo.com/$repo/os/$arch
```

The `any` architecture packages are published inside the `x86_64` repository
database because SteamOS handheld targets are currently x86_64.

## Packages

### `rivoreo-keyring`

Purpose: install Rivoreo's package signing public key and trust metadata.

Files:

- `/usr/share/pacman/keyrings/rivoreo.gpg`
- `/usr/share/pacman/keyrings/rivoreo-trusted`
- `/usr/share/pacman/keyrings/rivoreo-revoked`

This package lets users move from bootstrap-installed trust to normal pacman
keyring maintenance.

### `rivoreo-steamos-repo`

Purpose: install the repository include file and keep the repo configuration
owned by pacman after bootstrap.

Files:

- `/etc/pacman.d/rivoreo-steamos.conf`

The bootstrap script still needs to add an `Include` line to `/etc/pacman.conf`
when it is missing, because a package from this repo cannot be installed until
the repo is configured once.

### `steamos-intel-handheld`

Purpose: install the Python TDP provider, system service, D-Bus policy, SteamOS
Manager remote registration, and support helpers.

Runtime ownership:

- Executables under `/opt/steamos-intel-handheld/bin/`
- Python package under `/opt/steamos-intel-handheld/src/` or a wheel install
  path that preserves the existing systemd unit contract
- State under `/var/lib/steamos-intel-handheld/`
- Systemd unit under `/usr/lib/systemd/system/`
- D-Bus policy under `/etc/dbus-1/system.d/`
- SteamOS Manager remote under `/etc/steamos-manager/remotes.d/`

The package install hook should run `systemctl daemon-reload`, reload D-Bus
configuration best-effort, and print the enable command. The bootstrap script,
not the package hook, should enable and start services on SteamOS devices.

### `steamos-intel-handheld-mangoapp`

Purpose: temporary package for the patched `mangoapp` binary while the MangoHud
Intel integrated GPU power patch is not available in upstream SteamOS.

Runtime ownership:

- `/opt/steamos-intel-handheld/bin/mangoapp`
- `/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf`

This package should be optional and documented as temporary. Once MangoHud
upstream and SteamOS include the fix, users should be able to remove it without
removing `steamos-intel-handheld`.

## Build And Release Flow

1. Create a signed git tag such as `v0.1.0`.
2. Build packages from the tag in an Arch Linux builder:
   ```bash
   makepkg --cleanbuild --syncdeps --sign --needed
   ```
3. Stage packages in `public/rivoreo-steamos/os/x86_64/`.
4. Generate and sign the repository database:
   ```bash
   repo-add --sign --verify public/rivoreo-steamos/os/x86_64/rivoreo-steamos.db.tar.zst \
     public/rivoreo-steamos/os/x86_64/*.pkg.tar.zst
   ```
5. Publish the static tree to HTTPS hosting.
6. Smoke-test from a clean SteamOS VM or device with:
   ```bash
   pacman -Sy
   pacman -S --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld
   ```

For `steamos-intel-handheld-mangoapp`, build the binary in the SteamOS QEMU
environment documented by `.codex/skills/steamos-qemu-build-env` and
`docs/steamos-qemu-build-env.md`, then package that binary separately.

## SteamOS Bootstrap Flow

The bootstrap script should be idempotent and safe to rerun after SteamOS OTA:

```bash
curl -fsSL https://repo.rivoreo.com/rivoreo-steamos/bootstrap.sh | sudo bash
```

Flow:

1. Detect SteamOS by checking for `steamos-readonly`, `/etc/os-release`, or
   SteamOS-specific services.
2. If `steamos-readonly` exists, disable read-only mode before mutating pacman
   configuration or installing packages.
3. Download `key/rivoreo.gpg` and verify its fingerprint against the fingerprint
   embedded in the bootstrap script.
4. Initialize pacman's keyring when needed.
5. Import and locally sign the Rivoreo key.
6. Write `/etc/pacman.d/rivoreo-steamos.conf`.
7. Add `Include = /etc/pacman.d/rivoreo-steamos.conf` to `/etc/pacman.conf` if
   it is not already present.
8. Run `pacman -Sy`.
9. Install `rivoreo-keyring`, `rivoreo-steamos-repo`, and
   `steamos-intel-handheld`.
10. Enable and start `steamos-intel-handheld-power-control.service`.
11. Restart SteamOS Manager in the deck user session when the user bus is
    active.
12. Run or print the real-device verification command:
    `scripts/verify-on-device.sh root@<host>` for development, or a packaged
    `steamos-intel-handheld-verify` command once available.

For ordinary Arch Linux users, the bootstrap should skip SteamOS-only service
restarts and print that the SteamOS Manager remote is only useful on SteamOS.

## Upgrade Strategy

- Use upstream project versions from `pyproject.toml` and git tags.
- Reset `pkgrel` to `1` for each new upstream `pkgver`.
- Increment `pkgrel` for packaging-only fixes.
- Keep package signatures and repository database signatures required.
- Keep bootstrap idempotent so users can repair pacman config after SteamOS OTA.
- Treat SteamOS OTA persistence as a hardware validation requirement. The first
  release must document whether `/opt`, `/etc`, pacman database state, and
  enabled services survive a SteamOS update on the target device.
- Maintain a rollback command:
  ```bash
  pacman -Rns steamos-intel-handheld steamos-intel-handheld-mangoapp
  ```

## Testing Strategy

Automated checks:

- Python tests continue to run through `scripts/check-local.sh`.
- Add packaging tests that assert `PKGBUILD` has no `SKIP` checksum, declares
  install hooks, declares backups for `/etc` files, and packages expected data
  assets.
- Build packages inside an Arch container or clean chroot in CI.
- Verify generated repo metadata with `repo-add --verify` and `pacman -Syp`
  against a temporary pacman root.

Manual / hardware checks:

- Install from the published repo on a SteamOS target.
- Verify TDP provider behavior with `scripts/verify-on-device.sh`.
- Reboot and verify service and MangoHud sensor behavior again.
- Apply or simulate a SteamOS OTA and rerun bootstrap plus verification.

## Security Requirements

- Do not document `SigLevel = Never` as an installation path.
- Pin the bootstrap signing-key fingerprint.
- Sign packages and repository databases.
- Store private signing keys outside the repository, preferably in an offline
  release environment or protected CI secret.
- Keep the bootstrap script short enough for users to audit before running.

## Non-Goals

- Do not mirror Valve or Arch system packages.
- Do not require AUR helpers.
- Do not claim official Valve support.
- Do not keep the patched `mangoapp` package after upstream SteamOS includes
  the MangoHud Intel GPU power support.
- Do not solve generic package persistence for every SteamOS customization; this
  repository only needs a repairable, documented install path for this support
  layer.

## Success Criteria

- A clean Arch builder can build signed packages from a release tag.
- The static repository can be added to pacman with `SigLevel = Required`.
- A SteamOS target can install `steamos-intel-handheld` from the repo.
- The package-owned service starts and passes the existing hardware verifier.
- Re-running the bootstrap after reboot or SteamOS OTA is safe and restores repo
  configuration when needed.
