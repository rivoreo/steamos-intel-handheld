# Package Repository

This repository uses GitHub Pages as the static hosting endpoint for the
`rivoreo-steamos` pacman repository scaffold.

Current custom domain:

```text
https://holo.libz.so/
```

GitHub Pages serves it from the project site
`https://rivoreo.github.io/steamos-intel-handheld/`.

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

The package definitions and Pages scaffold are in place. The public pacman
repository is not activated yet because the signed package database has not been
published to Pages. Until that database is published, the bootstrap script
reports repository status and exits without changing the system.

GitLab CI is the build path for package validation artifacts:

- `python:test` runs the repository's local harness.
- `arch:package` runs in `archlinux:base-devel`, builds a source snapshot from
  the current commit, and produces `.pkg.tar.zst` artifacts with `makepkg`.
- `arch:repository` consumes the package artifact, runs `repo-add`, and outputs
  a static repository tree under
  `.cache/pacman-repo/public/rivoreo-steamos/os/x86_64`.

Those CI artifacts prove that the Arch package and pacman repository metadata
can be generated for a given commit. They are not the same as activating the
public repository. Public activation still requires signing the packages and
database, then promoting the signed repository files into the GitHub Pages
artifact.

DNS should point the subdomain to GitHub Pages with:

```text
holo.libz.so.  CNAME  rivoreo.github.io.
```

When the signed package publisher is implemented, it should write regular files
for `rivoreo-steamos.db`, `rivoreo-steamos.files`, and their signatures. Do not
use symlinks in the Pages artifact.
