import json
import re
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


def test_pages_site_points_only_at_the_signed_project_repository() -> None:
    index = SITE_INDEX.read_text()
    assert f"{PUBLIC_REPO_BASE}/os/$arch" in index
    assert "https://holo.libz.so" not in index
    assert "http://" not in index
    # Anyone who copies the repository stanza off this page must copy the
    # signature requirement with it.
    assert "SigLevel = Required TrustedOnly" in index


def test_pages_site_leads_with_the_user_facing_promise() -> None:
    index = SITE_INDEX.read_text()
    # A first-time visitor has to learn what this is before anything else, and
    # the answer is a result on their handheld, not a description of the
    # repository that ships it.
    assert "Longer battery. Steadier frame rates." in index
    assert "MSI Claw 8 AI+" in index
    assert "No account, no telemetry" in index
    # The two things a visitor installs, named as the panels name themselves.
    assert "Game Power" in index
    assert "Charge Limit" in index
    assert "Working power sensors" in index


def test_pages_site_shows_the_real_panels_and_never_a_mock_up() -> None:
    index = SITE_INDEX.read_text()
    # This is a product whose entire surface is two Steam panels. A page that
    # never shows one leaves the visitor guessing what they are installing.
    for shot in ("panel-game-power.png", "panel-charge-limit.png"):
        asset = ROOT / "site/assets" / shot
        assert asset.is_file(), shot
        assert asset.stat().st_size > 20_000, shot
        assert f'src="assets/{shot}"' in index
    # Nothing on the page may be a drawing of the product dressed as a capture
    # of it. These class names belonged to hand-built panel replicas.
    for mock_marker in ('class="device"', 'class="panel-status"', 'class="panel-row"'):
        assert mock_marker not in index, mock_marker
    # And the page has to say where the panels appear after installing, not only
    # how to install them.
    assert 'id="usage"' in index
    assert "Steam quick access menu" in index
    assert "Decky plug icon" in index


def test_pages_site_screenshots_carry_translated_alt_text_and_captions() -> None:
    index = SITE_INDEX.read_text()
    translations = read_site_translations()
    # A screenshot with no alt text is invisible to a screen reader, and an
    # untranslated caption undoes the point of shipping three locales.
    assert index.count("data-i18n-alt=") == 2
    for key in ("shot.power.alt", "shot.power.caption", "shot.charge.alt", "shot.charge.caption"):
        for locale in ("en", "zh-CN", "zh-TW"):
            assert translations[locale][key].strip(), (locale, key)
        assert translations["zh-TW"][key] != translations["en"][key], key
        assert translations["zh-CN"][key] != translations["en"][key], key
    # Both captions must name the device the capture came from, so a reader can
    # tell a real screenshot from an illustration.
    assert translations["en"]["shot.power.caption"].count("MSI Claw 8 AI+") == 1
    assert translations["en"]["shot.charge.caption"].count("MSI Claw 8 AI+") == 1


def test_pages_site_quotes_only_measurements_that_were_actually_taken() -> None:
    index = SITE_INDEX.read_text()
    assert "-26%" in index
    assert "-15%" in index
    assert "0.22 W" in index
    # Every headline number must be attributed to a real device and a published
    # reading, so nobody reads them as a marketing estimate.
    assert "Measured on an MSI Claw 8 AI+" in index
    assert "published in the repository" in index


def test_pages_site_does_not_advertise_an_install_path_that_does_not_work() -> None:
    index = SITE_INDEX.read_text()
    # The signed repository is not published, so the bootstrap one-liner 404s at
    # its first download. Presenting it as ready sends people to a broken
    # command; the source install is the one that works today.
    assert "Not published yet" in index
    assert "not live yet" in index
    assert "Install from source" in index
    assert "scripts/install-on-device.sh" in index
    assert "Repository active" not in index
    assert "Install channel open" not in index
    assert "Safe placeholder" not in index
    assert "exits without changing the system" not in index


def test_pages_site_answers_the_questions_that_stop_people_installing() -> None:
    index = SITE_INDEX.read_text()
    assert "Which handhelds does this support?" in index
    assert "Can it damage my handheld?" in index
    assert "Will a SteamOS update undo it?" in index
    assert "Does it send any data anywhere?" in index
    # The safety answer must state the actual guarantee the code makes, not a
    # reassuring paraphrase of it.
    assert "never raises them" in index
    assert "records the original state" in index


def test_pages_site_has_visible_brand_mark_and_language_switcher() -> None:
    index = SITE_INDEX.read_text()
    brand_start = index.index('<span class="brand-mark"')
    brand_end = index.index("</span>", brand_start)
    brand_markup = index[brand_start:brand_end]
    assert "<svg" in brand_markup
    assert 'class="handheld-body"' in brand_markup
    assert 'class="chip-core"' in brand_markup
    assert 'aria-hidden="true"' in brand_markup
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


def test_pages_site_locale_dictionaries_have_matching_keys() -> None:
    translations = read_site_translations()
    assert set(translations) == {"en", "zh-CN", "zh-TW"}
    english_keys = set(translations["en"])
    assert english_keys == set(translations["zh-CN"])
    assert english_keys == set(translations["zh-TW"])


def test_every_translated_key_is_used_and_every_used_key_is_translated() -> None:
    index = SITE_INDEX.read_text()
    markup_keys = set(re.findall(r'data-i18n(?:-alt)?="([^"]+)"', index))
    english_keys = set(read_site_translations()["en"])
    # A key in the markup with no entry leaves the element stuck in English when
    # the visitor switches language, which is exactly the failure a reader
    # notices first.
    assert markup_keys - english_keys == set()
    # Keys applied by the script rather than by a data-i18n attribute.
    assert english_keys - markup_keys == {"meta.title", "meta.description"}


def test_pages_site_uses_taiwan_zh_tw_wording() -> None:
    zh_tw_text = "\n".join(read_site_translations()["zh-TW"].values())
    assert "Intel 掌機" in zh_tw_text
    assert "套件庫" in zh_tw_text
    assert "從原始碼安裝" in zh_tw_text
    assert "簽名套件庫" in zh_tw_text
    assert "尚未發佈" in zh_tw_text
    assert "原廠韌體" in zh_tw_text
    # Names the two panels, in the words the panels themselves use.
    assert "遊戲電力" in zh_tw_text
    assert "充電上限" in zh_tw_text
    # Mainland vocabulary and orthography that a Taiwanese reader would flag.
    assert "面向" not in zh_tw_text
    assert "軟體源" not in zh_tw_text
    assert "發布" not in zh_tw_text
    assert "設備" not in zh_tw_text
    assert "列印" not in zh_tw_text
    assert "帧" not in zh_tw_text
    assert "屏幕" not in zh_tw_text
    assert "菜單" not in zh_tw_text


def test_pages_site_chinese_punctuation_follows_chinese_convention() -> None:
    # Prose sentences close with a full stop; headings, labels, buttons and list
    # items do not. Punctuating them like English sentences is the tell that the
    # copy was translated clause by clause instead of written.
    prose_suffixes = (".description", ".lead", ".note", ".text", ".sub")
    translations = read_site_translations()
    for locale in ("zh-CN", "zh-TW"):
        for key, value in translations[locale].items():
            assert not value.endswith("."), (locale, key)
            if key.endswith(prose_suffixes) or re.fullmatch(r"faq\.a\d+", key):
                continue
            assert not value.endswith("。"), (locale, key)


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
    assert f"REPO_BASE_URL:-{PUBLIC_REPO_BASE}" in bootstrap
    assert "https://holo.libz.so" not in bootstrap
    assert "http://" not in bootstrap
    assert "signed package database has not been published" not in bootstrap
    assert "exit 1" not in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap
