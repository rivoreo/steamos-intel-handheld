# Package Repository

This repository uses GitHub Pages as the static hosting endpoint for the future
`rivoreo-steamos` pacman repository.

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

The Pages deployment is live before package publication so the URL can be
validated early. Until signed packages and repository databases are published,
the bootstrap script exits without changing the system.

DNS should point the subdomain to GitHub Pages with:

```text
holo.libz.so.  CNAME  rivoreo.github.io.
```

When the package publisher is implemented, it should write regular files for
`rivoreo-steamos.db`, `rivoreo-steamos.files`, and their signatures. Do not use
symlinks in the Pages artifact.
