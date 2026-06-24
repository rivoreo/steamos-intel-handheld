# GitHub Pages Product Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the `holo.libz.so` Pages site into a polished product explanation page for `steamos-intel-handheld`.

**Architecture:** Keep the site as dependency-free static HTML in `site/index.html`, protected by pytest assertions in `tests/test_pages_site.py`. The page explains the product, capabilities, need, and installation path while preserving the existing GitHub Pages workflow and safe placeholder bootstrap.

**Tech Stack:** Static HTML/CSS, GitHub Pages, pytest, UIUX Pro Max design guidance.

---

### Task 1: Product Copy Tests

**Files:**
- Modify: `tests/test_pages_site.py`

- [ ] **Step 1: Add assertions for the four product questions**

Update `tests/test_pages_site.py` so `test_pages_site_documents_project_repo_url`
also checks:

```python
assert "SteamOS support for Intel handhelds" in index
assert "What it is" in index
assert "What it can do" in index
assert "Why it exists" in index
assert "How to install" in index
```

- [ ] **Step 2: Add assertions for capabilities and safety copy**

Add a new test:

```python
def test_pages_site_explains_capabilities_and_pending_release_state() -> None:
    index = SITE_INDEX.read_text()
    assert "SteamOS Manager TDP remote" in index
    assert "Intel RAPL power path" in index
    assert "MangoHud sensor access" in index
    assert "Packages pending" in index
    assert "exits without changing the system" in index
```

- [ ] **Step 3: Run the focused test and confirm failure before implementation**

Run:

```bash
.venv/bin/python -m pytest tests/test_pages_site.py -q
```

Expected: new assertions fail because the current page is still a placeholder.

### Task 2: Redesign The Static Page

**Files:**
- Modify: `site/index.html`

- [ ] **Step 1: Replace placeholder copy with product page structure**

Implement `site/index.html` with:

- header: product name, status, GitHub link
- hero: `SteamOS support for Intel handhelds`
- install command block
- four answer cards: `What it is`, `What it can do`, `Why it exists`,
  `How to install`
- capability grid with the six capabilities from the spec
- install section with bootstrap and pacman repo stanza
- footer with GitHub Pages source and DNS note

- [ ] **Step 2: Apply UIUX Pro Max visual rules**

Use:

- dark technical palette
- clear grid
- accessible muted text
- code blocks with overflow handling
- no emoji icons
- no decorative orbs
- no layout-shifting hover states

- [ ] **Step 3: Run focused page tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_pages_site.py -q
```

Expected: all page tests pass.

### Task 3: Verify Locally And Commit

**Files:**
- Modify: `site/index.html`
- Modify: `tests/test_pages_site.py`
- Add: `.codex/skills/ui-ux-pro-max/**`
- Add: `docs/superpowers/specs/2026-06-24-pages-product-design.md`
- Add: `docs/superpowers/plans/2026-06-24-pages-product-redesign.md`

- [ ] **Step 1: Run the full local gate**

Run:

```bash
env PATH="$PWD/.venv/bin:$PATH" PYTHON="$PWD/.venv/bin/python" scripts/check-local.sh
```

Expected: all checks pass.

- [ ] **Step 2: Check staged whitespace**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 3: Commit**

Run:

```bash
git add .codex/skills/ui-ux-pro-max docs/superpowers/specs/2026-06-24-pages-product-design.md docs/superpowers/plans/2026-06-24-pages-product-redesign.md site/index.html tests/test_pages_site.py
git commit -m "feat(pages): redesign product landing page"
```

### Task 4: Push And Verify Pages

**Files:**
- Remote GitHub Pages deployment

- [ ] **Step 1: Push**

Run:

```bash
git push origin main
```

- [ ] **Step 2: Wait for CI and Pages workflows**

Run:

```bash
gh run list --repo rivoreo/steamos-intel-handheld --limit 5
gh run watch <pages-run-id> --repo rivoreo/steamos-intel-handheld --exit-status
```

Expected: CI and Pages succeed.

- [ ] **Step 3: Verify deployed content**

After DNS is configured for `holo.libz.so`, run:

```bash
curl -fsSL https://holo.libz.so/ | rg "SteamOS support for Intel handhelds|What it is|How to install"
```

Before DNS is configured, verify through the default Pages redirect or workflow
artifact state and report that DNS remains the external blocker.
