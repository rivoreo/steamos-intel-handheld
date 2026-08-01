import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO_BASE = "https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos"
PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
SITE_INDEX = ROOT / "site/index.html"
BOOTSTRAP = ROOT / "site/rivoreo-steamos/bootstrap.sh"
BOOTSTRAP_INSTALL_COMMAND = (
    "pacman -S --needed rivoreo-keyring rivoreo-steamos-repo "
    "steamos-intel-handheld steamos-intel-handheld-mangoapp"
)


def read_site_translations() -> dict[str, dict[str, str]]:
    index = SITE_INDEX.read_text()
    start = index.index("const TRANSLATIONS = ") + len("const TRANSLATIONS = ")
    end = index.index(";\n\n      const STORAGE_KEY", start)
    return json.loads(index[start:end])


def test_pages_workflow_deploys_docs_but_never_over_a_published_repository() -> None:
    workflow = PAGES_WORKFLOW.read_text()
    assert "Static Site Check" in workflow
    assert "cp -R site/. _site/" in workflow
    assert "test -f _site/index.html" in workflow
    # Documentation changes reach the public site without cutting a release.
    assert "actions/deploy-pages" in workflow
    assert "pages: write" in workflow
    # But a Pages deployment replaces the whole site, so a site-only deploy must
    # refuse to run once the signed repository is live - otherwise it deletes the
    # package database and key out from under anyone who configured the repo.
    assert "Refuse to overwrite a published package repository" in workflow
    assert "rivoreo-steamos.db.tar.gz" in workflow
    assert "key/rivoreo.gpg" in workflow
    assert "exit 1" in workflow
    # Pull requests validate only.
    assert "github.event_name != 'pull_request'" in workflow


def test_pages_site_documents_project_repo_url() -> None:
    index = SITE_INDEX.read_text()
    assert f"{PUBLIC_REPO_BASE}/os/$arch" in index
    assert "https://holo.libz.so" not in index
    assert "http://" not in index
    assert "SigLevel = Required TrustedOnly" in index
    assert "SteamOS support for Intel handhelds" in index
    assert "What it is" in index
    assert "What it can do" in index
    assert "Why it exists" in index
    assert "How to install" in index


def test_pages_site_explains_capabilities_and_active_release_state() -> None:
    index = SITE_INDEX.read_text()
    # The capability cards name what the user gets, not the internal components
    # that deliver it. A visitor deciding whether to install should be able to
    # tell what changes on their handheld.
    assert "Game Power" in index
    assert "Charge Limit" in index
    assert "Working power sensors" in index
    # And the page has to say how to use them, not only how to install them.
    assert 'id="usage"' in index
    assert "Steam quick access menu" in index
    # The page must not advertise an install path that does not work. The signed
    # repository is not published, so the one-line bootstrap 404s at its first
    # download, and saying otherwise sends people to a broken command.
    assert "Package repository not published yet" in index
    assert "Install from source for now" in index
    assert "scripts/install-on-device.sh" in index
    assert "Repository active" not in index
    assert "Install channel open" not in index
    assert "Safe placeholder" not in index
    assert "exits without changing the system" not in index


def test_pages_site_explains_stable_install_and_candidate_release_flow() -> None:
    index = SITE_INDEX.read_text()
    assert "No stable release has been published to the package repository yet" in index
    assert "Release candidates build and verify signed artifacts without deploying them" in index
    assert (
        "Install from source until the repository goes live"
        in index
    )
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
    assert "Intel 掌機" in zh_tw_text
    assert "套件庫" in zh_tw_text
    assert "套件庫尚未發佈" in zh_tw_text
    assert "從原始碼安裝" in zh_tw_text
    assert "簽名套件庫" in zh_tw_text
    assert "候選版本" in zh_tw_text
    assert "但不會部署" in zh_tw_text
    assert "裝置" in zh_tw_text
    # Names the two panels, in the words the panels themselves use.
    assert "遊戲電力" in zh_tw_text
    assert "充電上限" in zh_tw_text
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


def test_pages_site_uses_https_project_pages_domain_not_custom_domain() -> None:
    assert not (ROOT / "site/CNAME").exists()


def test_active_bootstrap_configures_signed_repo() -> None:
    bootstrap = BOOTSTRAP.read_text()
    assert BOOTSTRAP_INSTALL_COMMAND in bootstrap
    assert f'REPO_BASE_URL:-{PUBLIC_REPO_BASE}' in bootstrap
    assert "https://holo.libz.so" not in bootstrap
    assert "http://" not in bootstrap
    assert "signed package database has not been published" not in bootstrap
    assert "exit 1" not in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap
