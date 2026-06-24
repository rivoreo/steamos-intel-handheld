import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
SITE_INDEX = ROOT / "site/index.html"
BOOTSTRAP = ROOT / "site/rivoreo-steamos/bootstrap.sh"


def read_site_translations() -> dict[str, dict[str, str]]:
    index = SITE_INDEX.read_text()
    start = index.index("const TRANSLATIONS = ") + len("const TRANSLATIONS = ")
    end = index.index(";\n\n      const STORAGE_KEY", start)
    return json.loads(index[start:end])


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
    assert "Packages not released" in index
    assert "Not installable yet" in index
    assert "Packages pending" not in index
    assert "Pages live" not in index
    assert "Website live" not in index
    assert "Safe placeholder" not in index
    assert "exits without changing the system" in index


def test_pages_site_has_visible_brand_mark_and_language_switcher() -> None:
    index = SITE_INDEX.read_text()
    brand_start = index.index('<span class="brand-mark"')
    brand_end = index.index("</span>", brand_start)
    brand_markup = index[brand_start:brand_end]
    assert "<svg" in brand_markup
    assert 'class="handheld-body"' in brand_markup
    assert 'class="chip-core"' in brand_markup
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
    assert "針對 Intel 掌機的 SteamOS 支援層" in index


def test_pages_site_locale_dictionaries_have_matching_keys() -> None:
    translations = read_site_translations()
    assert set(translations) == {"en", "zh-CN", "zh-TW"}
    english_keys = set(translations["en"])
    assert english_keys == set(translations["zh-CN"])
    assert english_keys == set(translations["zh-TW"])


def test_pages_site_uses_taiwan_zh_tw_wording() -> None:
    zh_tw_text = "\n".join(read_site_translations()["zh-TW"].values())
    assert "針對 Intel 掌機的 SteamOS 支援層" in zh_tw_text
    assert "套件庫" in zh_tw_text
    assert "套件尚未釋出" in zh_tw_text
    assert "尚不可安裝" in zh_tw_text
    assert "裝置" in zh_tw_text
    assert "輸出套件狀態" in zh_tw_text
    assert "頁面已上線" not in zh_tw_text
    assert "安全佔位" not in zh_tw_text
    assert "面向" not in zh_tw_text
    assert "軟體源" not in zh_tw_text
    assert "發布" not in zh_tw_text
    assert "設備" not in zh_tw_text
    assert "列印" not in zh_tw_text


def test_pages_site_does_not_treat_hong_kong_or_macau_as_zh_tw() -> None:
    index = SITE_INDEX.read_text()
    assert 'locale === "zh-hk"' not in index
    assert 'locale === "zh-mo"' not in index
    assert 'locale.startsWith("zh-hant-")' not in index


def test_pages_site_declares_custom_domain() -> None:
    cname = (ROOT / "site/CNAME").read_text()
    assert cname.strip() == "holo.libz.so"


def test_placeholder_bootstrap_exits_before_packages_exist() -> None:
    bootstrap = BOOTSTRAP.read_text()
    assert "signed packages" in bootstrap
    assert "exit 1" in bootstrap
    assert "pacman -S" not in bootstrap
    assert "steamos-readonly disable" not in bootstrap
