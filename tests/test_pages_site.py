import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
SITE_INDEX = ROOT / "site/index.html"
BOOTSTRAP = ROOT / "site/rivoreo-steamos/bootstrap.sh"
BOOTSTRAP_INSTALL_COMMAND = (
    "pacman -S --needed rivoreo-keyring rivoreo-steamos-repo steamos-intel-handheld"
)


def read_site_translations() -> dict[str, dict[str, str]]:
    index = SITE_INDEX.read_text()
    start = index.index("const TRANSLATIONS = ") + len("const TRANSLATIONS = ")
    end = index.index(";\n\n      const STORAGE_KEY", start)
    return json.loads(index[start:end])


def test_pages_workflow_validates_static_site_without_deploying() -> None:
    workflow = PAGES_WORKFLOW.read_text()
    assert "Static Site Check" in workflow
    assert "cp -R site/. _site/" in workflow
    assert "test -f _site/index.html" in workflow
    assert "actions/upload-pages-artifact" not in workflow
    assert "actions/deploy-pages" not in workflow
    assert "pages: write" not in workflow


def test_pages_site_documents_project_repo_url() -> None:
    index = SITE_INDEX.read_text()
    assert "https://holo.libz.so/rivoreo-steamos/os/$arch" in index
    assert "SigLevel = Required TrustedOnly" in index
    assert "SteamOS support for Intel handhelds" in index
    assert "What it is" in index
    assert "What it can do" in index
    assert "Why it exists" in index
    assert "How to install" in index


def test_pages_site_explains_capabilities_and_active_release_state() -> None:
    index = SITE_INDEX.read_text()
    assert "SteamOS Manager TDP remote" in index
    assert "Intel RAPL power path" in index
    assert "MangoHud sensor access" in index
    assert "Repository active" in index
    assert "Install channel open" in index
    assert "signed package repository is published through GitHub Actions" in index
    assert "Repository not activated" not in index
    assert "Install channel not open" not in index
    assert "signed package database has not been published to Pages" not in index
    assert "Packages not released" not in index
    assert "Packages pending" not in index
    assert "Safe placeholder" not in index
    assert "exits without changing the system" not in index


def test_pages_site_explains_stable_install_and_candidate_release_flow() -> None:
    index = SITE_INDEX.read_text()
    assert "Stable tags update the public pacman repository" in index
    assert "release-candidate tags build signed artifacts without deploying Pages" in index
    assert "Users install from holo.libz.so after a stable release" in index
    assert "Maintainers inspect candidate artifacts in GitHub Actions" in index


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
    assert "套件庫已啟用" in zh_tw_text
    assert "可以安裝" in zh_tw_text
    assert "簽名套件庫" in zh_tw_text
    assert "候選版本" in zh_tw_text
    assert "不會部署 GitHub Pages" in zh_tw_text
    assert "裝置" in zh_tw_text
    assert "輸出套件狀態" not in zh_tw_text
    assert "套件尚未釋出" not in zh_tw_text
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


def test_active_bootstrap_configures_signed_repo() -> None:
    bootstrap = BOOTSTRAP.read_text()
    assert BOOTSTRAP_INSTALL_COMMAND in bootstrap
    assert "signed package database has not been published" not in bootstrap
    assert "exit 1" not in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap
