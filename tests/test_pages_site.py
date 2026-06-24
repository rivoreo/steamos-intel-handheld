import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
SITE_INDEX = ROOT / "site/index.html"
BOOTSTRAP = ROOT / "site/rivoreo-steamos/bootstrap.sh"


def test_pages_workflow_deploys_static_site_with_actions() -> None:
    workflow = PAGES_WORKFLOW.read_text()
    assert "actions/configure-pages@v5" in workflow
    assert "actions/upload-pages-artifact@v4" in workflow
    assert "actions/deploy-pages@v4" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "path: _site" in workflow


def test_pages_site_documents_project_repo_url() -> None:
    index = SITE_INDEX.read_text()
    assert "https://holo.libz.so/rivoreo-steamos/os/$arch" in index
    assert "SigLevel = Required TrustedOnly" in index
    assert "SteamOS support for Intel handhelds" in index
    assert "What it is" in index
    assert "What it can do" in index
    assert "Why it exists" in index
    assert "How to install" in index


def test_pages_site_explains_capabilities_and_pending_release_state() -> None:
    index = SITE_INDEX.read_text()
    assert "SteamOS Manager TDP remote" in index
    assert "Intel RAPL power path" in index
    assert "MangoHud sensor access" in index
    assert "Pages live" in index
    assert "Packages not published" in index
    assert "Packages pending" not in index
    assert "exits without changing the system" in index


def test_pages_site_has_visible_brand_mark_and_language_switcher() -> None:
    index = SITE_INDEX.read_text()
    brand_start = index.index('<span class="brand-mark"')
    brand_end = index.index("</span>", brand_start)
    brand_markup = index[brand_start:brand_end]
    assert "<svg" in brand_markup
    assert "aria-hidden=\"true\"" in brand_markup
    assert 'class="language-switcher"' in index
    assert 'data-language-option="en"' in index
    assert 'data-language-option="zh-CN"' in index
    assert 'data-language-option="zh-TW"' in index


def test_pages_site_embeds_supported_locales() -> None:
    index = SITE_INDEX.read_text()
    assert "const TRANSLATIONS" in index
    assert "navigator.languages" in index
    assert "localStorage" in index
    assert '"zh-CN"' in index
    assert '"zh-TW"' in index
    assert "SteamOS support for Intel handhelds" in index
    assert "面向 Intel 掌机的 SteamOS 支持层" in index
    assert "面向 Intel 掌機的 SteamOS 支援層" in index


def test_pages_site_locale_dictionaries_have_matching_keys() -> None:
    index = SITE_INDEX.read_text()
    start = index.index("const TRANSLATIONS = ") + len("const TRANSLATIONS = ")
    end = index.index(";\n\n      const STORAGE_KEY", start)
    translations = json.loads(index[start:end])
    assert set(translations) == {"en", "zh-CN", "zh-TW"}
    english_keys = set(translations["en"])
    assert english_keys == set(translations["zh-CN"])
    assert english_keys == set(translations["zh-TW"])


def test_pages_site_declares_custom_domain() -> None:
    cname = (ROOT / "site/CNAME").read_text()
    assert cname.strip() == "holo.libz.so"


def test_placeholder_bootstrap_exits_before_packages_exist() -> None:
    bootstrap = BOOTSTRAP.read_text()
    assert "signed packages" in bootstrap
    assert "exit 1" in bootstrap
    assert "pacman -S" not in bootstrap
    assert "steamos-readonly disable" not in bootstrap
