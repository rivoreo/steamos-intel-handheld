# GitHub Pages Product Page Design

## Objective

Redesign the `holo.libz.so` GitHub Pages site from a repository placeholder into
a concise product explanation page for `steamos-intel-handheld`.

## Source Skills

This design uses the copied `ui-ux-pro-max` skill from
`.codex/skills/ui-ux-pro-max`. Searches used:

- Product: developer tool / infrastructure package repository
- Style: minimal professional technical
- Typography: SaaS developer professional
- Color: SaaS developer tool
- Landing: minimal direct documentation product
- UX: accessibility contrast responsive
- Stack: html-tailwind layout responsive semantic

The resulting direction is a developer-tool landing page using Minimal & Direct
plus Swiss-style grid structure, a dark technical palette with blue focus, and a
code-forward installation block.

## Audience

- SteamOS users running Intel handheld PCs.
- Developers validating SteamOS Manager remotes, Intel RAPL behavior, and
  MangoHud sensor support.
- Future contributors deciding whether this repository is a support layer,
  package source, or upstream patch staging area.

## Content Requirements

The page must answer four product questions:

1. **What is it?** A SteamOS support layer for Intel handheld PCs, currently
   centered on SteamOS Manager TDP control and MangoHud sensor access.
2. **What can it do?** Install a remote TDP provider, prepare MangoHud CPU/GPU
   power sensor access, host package repository paths, and provide verification
   tooling.
3. **Why is it needed?** SteamOS is Arch-based but not a normal mutable Arch
   install; Intel handhelds need device-specific support without patching
   SteamOS Manager in place.
4. **How to install?** Show the `curl -fsSL https://holo.libz.so/... |
   sudo bash` command and the future pacman repo stanza.

The page must clearly state that packages are not published yet and that the
current bootstrap script exits without changing the system.

## Information Architecture

Use a single static HTML file at `site/index.html` with these sections:

1. **Header bar** with product name, status, GitHub link, and repo URL anchor.
2. **Hero** with:
   - eyebrow: `holo.libz.so / rivoreo-steamos`
   - headline: `SteamOS support for Intel handhelds`
   - short value proposition
   - install command block
   - status line: Pages live, packages pending
3. **Answer cards** for:
   - What it is
   - What it can do
   - Why it exists
   - How to install
4. **Capability grid** with concise technical capabilities:
   - SteamOS Manager TDP remote
   - Intel RAPL power path
   - MangoHud sensor access
   - Pacman repository hosting
   - QEMU build environment
   - Hardware verification harness
5. **Install section** with:
   - bootstrap command
   - pacman repo stanza
   - safety note that bootstrap exits until packages are signed
6. **Footer** with GitHub repo, default Pages source, and custom DNS record.

## Visual Direction

- Use dark syntax-inspired colors:
  - background: `#0F172A`
  - panel: `#111827`
  - elevated panel: `#172033`
  - primary: `#3B82F6`
  - text: `#F1F5F9`
  - muted: `#CBD5E1`
  - border: `#334155`
  - success: `#22C55E`
  - warning: `#F59E0B`
- Use system fonts with a technical feel:
  - UI: `IBM Plex Sans` if available, fallback to system sans
  - Code: `JetBrains Mono` if available, fallback to system monospace
- Avoid external font requests so the page remains fast and reliable on SteamOS.
- Avoid emoji icons. Use small text labels, borders, status dots, and inline SVG
  only if an icon is necessary.
- Do not use gradient-orb or decorative bokeh backgrounds. Subtle linear depth
  is acceptable only as a page background, not as a dominant purple/blue hero.

## UX Requirements

- Keep the first viewport useful: product name, purpose, and install command
  visible on desktop and mobile.
- Use responsive layout with no horizontal scroll at 320px.
- Use accessible contrast for all text and code blocks.
- Preserve keyboard focus styles on links and buttons.
- Code blocks must be copyable as plain text and wrap or scroll safely on
  narrow screens.
- Keep the page static and dependency-free.

## Success Criteria

- `site/index.html` answers the four product questions without needing the
  README.
- `tests/test_pages_site.py` checks the key product copy, custom domain,
  install command, repository stanza, and pending package safety note.
- `scripts/check-local.sh` passes.
- GitHub Pages deploys successfully and serves the redesigned page.
