import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO_BASE = "https://rivoreo.github.io/steamos-intel-handheld/rivoreo-steamos"
PAGES_WORKFLOW = ROOT / ".github/workflows/pages.yml"
SITE_INDEX = ROOT / "site/index.html"
BOOTSTRAP = ROOT / "site/rivoreo-steamos/bootstrap.sh"
BOOTSTRAP_INSTALL_COMMAND = (
    "rivoreo-keyring rivoreo-steamos-repo "
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
    assert "key/rivoreo.gpg" in workflow
    assert "exit 1" in workflow
    # The guard has to probe files this project actually publishes. It used to
    # look for rivoreo-steamos.db.tar.gz, which repo-add never produces, so it
    # reported an empty repository no matter what was live.
    assert "os/x86_64/rivoreo-steamos.db" in workflow
    assert "rivoreo-steamos.db.tar.gz" not in workflow
    # Pull requests validate only.
    assert "github.event_name != 'pull_request'" in workflow


def test_pages_site_points_only_at_the_signed_project_repository() -> None:
    index = SITE_INDEX.read_text()
    assert PUBLIC_REPO_BASE in index
    assert "https://holo.libz.so" not in index
    assert "http://" not in index
    # Wherever the page mentions the package repository it must also state that
    # only signed packages can install from it, so nobody reads the one-line
    # bootstrap as "pipe an unverified script at your system".
    assert "SigLevel = Required TrustedOnly" in index
    # Every command the page tells someone to pipe into a shell must come from
    # a host this project controls.
    for piped in re.findall(r"curl -fsSL (\S+)", index):
        assert piped.startswith(
            ("https://rivoreo.github.io/steamos-intel-handheld/",
             "https://raw.githubusercontent.com/rivoreo/steamos-intel-handheld/"),
        ), piped


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


def test_pages_site_credits_the_whole_package_not_only_the_two_panels() -> None:
    """The two panels are the visible part, but a visitor also inherits the
    display fixes and the update survival without asking for them. A page that
    lists only the panels undersells what actually gets installed.

    The Steam performance-menu TDP slider is claimed here only because it now
    works: Valve's device profile for this board declares no TDP method, and the
    restore service composes one that does."""
    index = SITE_INDEX.read_text()
    # Verified end to end on hardware before it went on the page: setting 22W
    # through steamosctl moved RAPL PL1 from 12000000 to 22000000 and back.
    assert "The TDP slider Steam already has" in index
    # The rest of the payload a visitor inherits without asking for it.
    assert "1920×1200" in index
    assert "48 to 120 Hz" in index
    assert "See what your settings actually cost" in index
    assert "A SteamOS update will not undo it" in index


def test_pages_site_shows_the_real_panels_and_never_a_mock_up() -> None:
    index = SITE_INDEX.read_text()
    # This is a product whose entire surface is two Steam panels. A page that
    # never shows one leaves the visitor guessing what they are installing.
    for shot in ("panel-game-power.png", "panel-charge-limit.png", "panel-steam-tdp.png"):
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
    assert index.count("data-i18n-alt=") == 3
    for key in (
        "shot.power.alt", "shot.power.caption",
        "shot.charge.alt", "shot.charge.caption",
        "shot.steam.alt", "shot.steam.caption",
    ):
        for locale in ("en", "zh-CN", "zh-TW"):
            assert translations[locale][key].strip(), (locale, key)
        assert translations["zh-TW"][key] != translations["en"][key], key
        assert translations["zh-CN"][key] != translations["en"][key], key
    # Both captions must name the device the capture came from, so a reader can
    # tell a real screenshot from an illustration.
    for key in ("shot.power.caption", "shot.charge.caption", "shot.steam.caption"):
        assert translations["en"][key].count("MSI Claw 8 AI+") == 1, key


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
    # The signed repository is published now, so the bootstrap one-liner is the
    # recommended path and nothing may still describe it as unavailable.
    assert "Not published yet" not in index
    assert "not live yet" not in index
    assert "cannot work today" not in index
    assert "bootstrap.sh | sudo bash" in index
    # The other two paths stay, described as what they are.
    assert "scripts/install.sh | sudo bash" in index
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
    assert "簽名套件" in zh_tw_text
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


def _is_heading_or_label(key: str) -> bool:
    """Headings, buttons, tags and nav items - anything that is a name for
    something rather than a sentence about it."""
    return (
        key.endswith((".title", ".tag", ".alt", ".name"))
        or key.startswith(("nav.", "code.", "footer.", "stat."))
        or key in {"hero.eyebrow", "hero.primaryCta", "hero.secondaryCta"}
    ) and not key.endswith((".note",))


def test_pages_site_chinese_punctuation_follows_chinese_convention() -> None:
    # A Latin full stop never closes a Chinese sentence, and a Chinese heading
    # never takes a full stop at all. Both are the signature of copy translated
    # clause by clause rather than written.
    translations = read_site_translations()
    for locale in ("zh-CN", "zh-TW"):
        for key, value in translations[locale].items():
            assert not value.endswith("."), (locale, key)
            if _is_heading_or_label(key):
                assert not value.endswith("。"), (locale, key)


def test_pages_site_bullets_are_punctuated_the_same_way_throughout() -> None:
    """Half the list items ending in a full stop and half not is the kind of
    thing a reader registers as sloppy without being able to name."""
    translations = read_site_translations()
    for locale, close in (("en", "."), ("zh-CN", "。"), ("zh-TW", "。")):
        bullets = {k: v for k, v in translations[locale].items() if re.search(r"\.b\d+$", k)}
        assert len(bullets) >= 9, locale
        closed = {k for k, v in bullets.items() if v.endswith(close)}
        assert closed == set(bullets), (locale, sorted(set(bullets) - closed))


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
    # The documented invocation pipes this script into bash, so bash is reading
    # it from stdin and any interactive pacman prompt would be answered with the
    # next lines of the script itself.
    assert "pacman -Sy --noconfirm" in bootstrap
    assert "--needed --noconfirm" in bootstrap
    assert f"REPO_BASE_URL:-{PUBLIC_REPO_BASE}" in bootstrap
    assert "https://holo.libz.so" not in bootstrap
    assert "http://" not in bootstrap
    assert "signed package database has not been published" not in bootstrap
    assert "exit 1" not in bootstrap
    assert "SigLevel = Required TrustedOnly" in bootstrap


def test_pages_site_install_command_matches_what_the_script_actually_needs() -> None:
    """The installer writes to /opt and /etc/systemd/system, so it needs root.
    A homepage that prints the wrong SSH user hands every first-time visitor a
    permission error on the very first command."""
    script = (ROOT / "scripts/install-on-device.sh").read_text()
    usage = re.search(r"Usage: \$0 (\w+)@", script)
    assert usage, "install-on-device.sh no longer documents its own usage"
    user = usage.group(1)
    index = SITE_INDEX.read_text()
    assert f"scripts/install-on-device.sh {user}@" in index
    # And the prerequisite has to be stated next to that command, not implied.
    for locale in ("en", "zh-CN", "zh-TW"):
        assert user in read_site_translations()[locale]["install.dev.note"], locale


def test_pages_site_says_which_machine_each_command_runs_on() -> None:
    """A handheld running SteamOS is also a desktop computer, so "run this on
    your computer" tells a reader nothing. Every command has to name the
    machine unambiguously."""
    translations = read_site_translations()
    for locale in ("en", "zh-CN", "zh-TW"):
        block = translations[locale]
        # The one-liner runs on the handheld, and says so along with how to get
        # a terminal there at all.
        assert block["code.onHandheld"] != block["code.onDevMachine"], locale
        for key in ("install.oneline.text", "code.onHandheld"):
            assert block[key] != block.get("install.dev.text"), (locale, key)
    # English is checked literally; the point is the words, not the structure.
    english = translations["en"]
    # The recommended path is the one that must spell out how to get a terminal
    # on the handheld at all.
    assert "Desktop Mode" in english["install.package.text"]
    assert "Konsole" in english["install.package.text"]
    assert "on the handheld" in english["install.oneline.text"]
    assert "on the handheld" in english["code.onHandheld"]
    assert "a second machine" in english["install.dev.text"]
    assert "not the handheld" in english["install.dev.text"]
    # The phrase that caused the ambiguity in the first place.
    assert "on your computer, not on the handheld" not in SITE_INDEX.read_text()


def test_pages_guard_probes_the_filenames_repo_add_actually_produces() -> None:
    """The guard is the only thing standing between a documentation push and
    the deletion of a live package repository, so the names it probes must be
    the ones the release build writes."""
    build = (ROOT / "scripts/build-arch-release-repo.sh").read_text()
    workflow = PAGES_WORKFLOW.read_text()

    produced = {
        line.split("/")[-1]
        for line in re.findall(r'"\$repo_out/(rivoreo-steamos\.db[^"]*)"', build)
    }
    assert "rivoreo-steamos.db" in produced, produced
    probed = set(re.findall(r"os/x86_64/([A-Za-z0-9._-]+)", workflow))
    assert probed, "guard probes no repository file at all"
    assert probed <= produced, (probed - produced, produced)


def test_pages_site_recommends_the_packaged_install_first() -> None:
    """Whichever path the page puts first is the one most people will run, so
    it has to be the one that brings updates with it."""
    index = SITE_INDEX.read_text()
    tags = re.findall(r'data-i18n="install\.(\w+)\.tag"', index)
    assert tags[0] == "package", tags
    # And the recommended card is the one carrying the signature requirement.
    package_card = index[index.index('data-i18n="install.package.tag"') :]
    package_card = package_card[: package_card.index("</div>\n            <div class=")]
    assert "bootstrap.sh" in package_card


def test_inline_html_matches_the_english_dictionary() -> None:
    """Every translated node carries English text inline as well as in the
    dictionary. A visitor with JavaScript disabled sees the inline copy, so the
    two drifting apart publishes text nobody reviewed - which is exactly how
    "the repository is not live yet" survived the repository going live."""
    import html as html_mod

    index = SITE_INDEX.read_text()
    english = read_site_translations()["en"]
    body = index[: index.index("const TRANSLATIONS = ")]

    pattern = re.compile(
        r'<(?P<tag>h1|h2|h3|p|span|li|a|figcaption)\b[^>]*?data-i18n="(?P<key>[^"]+)"[^>]*?>'
        r"(?P<inner>.*?)</(?P=tag)>",
        re.DOTALL,
    )
    checked = 0
    for match in pattern.finditer(body):
        key, inner = match.group("key"), match.group("inner")
        if key not in english or "<" in inner:
            continue
        assert html_mod.unescape(inner) == english[key], key
        checked += 1
    assert checked >= 40, checked


def test_bootstrap_initialises_a_keyring_that_exists_but_does_not_work() -> None:
    """SteamOS ships /etc/pacman.d/gnupg without a usable keyring inside it.
    Testing for the directory skips the init that is actually needed, and every
    later pacman-key call fails - on the exact command the homepage recommends
    first. Measured on a stock device: the directory was present and
    `pacman-key --list-keys` failed."""
    bootstrap = BOOTSTRAP.read_text()
    assert "pacman-key --init" in bootstrap
    assert "if [ ! -d /etc/pacman.d/gnupg ]" not in bootstrap
    assert "pacman-key --list-keys" in bootstrap


def test_rendered_bootstrap_gets_past_its_own_fingerprint_guard() -> None:
    """The renderer substitutes the placeholder globally, so a guard written in
    terms of that placeholder is rewritten along with the value and compares the
    fingerprint against itself. Every published copy exited immediately with
    "bootstrap was not rendered with a Rivoreo signing key fingerprint" - on the
    command the homepage recommends first. Render it the way the release does
    and prove it reaches the next check."""
    import re as _re
    import subprocess
    import tempfile

    assembler = (ROOT / "scripts/assemble-arch-release-pages.sh").read_text()
    substitution = _re.search(r'sed "s/(__RIVOREO_KEY_FINGERPRINT__)/\$(\w+)/g"', assembler)
    assert substitution, "release renderer no longer substitutes the placeholder as expected"

    fingerprint = "1234567890ABCDEF1234567890ABCDEF12345678"
    rendered = BOOTSTRAP.read_text().replace("__RIVOREO_KEY_FINGERPRINT__", fingerprint)
    assert fingerprint in rendered

    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(rendered)
        path = handle.name

    # Run as the current (non-root) user: it must fail on the root check, which
    # comes first, and never on the fingerprint guard.
    result = subprocess.run(["bash", path], capture_output=True, text=True, check=False)
    assert "not rendered with a Rivoreo signing key" not in result.stderr, result.stderr
    assert "run as root" in result.stderr, result.stderr


def test_bootstrap_rejects_an_unrendered_or_malformed_fingerprint() -> None:
    """The guard still has to catch a release that forgot to render it."""
    import subprocess
    import tempfile

    unrendered = "__RIVOREO_KEY_FINGERPRINT__"
    not_hex = "ZZZZ567890ABCDEF1234567890ABCDEF12345678"
    for bad in (unrendered, "", "DEADBEEF", not_hex):
        rendered = BOOTSTRAP.read_text().replace("__RIVOREO_KEY_FINGERPRINT__", bad)
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
            handle.write(rendered)
            path = handle.name
        result = subprocess.run(
            ["bash", path], capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "EUID": "0"},
        )
        assert "not rendered with a Rivoreo signing key" in result.stderr, (bad, result.stderr)


def test_bootstrap_overwrite_is_scoped_to_paths_this_project_owns() -> None:
    """A machine set up with scripts/install.sh has unowned files exactly where
    the packages want to write, so pacman aborts the whole transaction. Allowing
    the overwrite is right, but only for our own paths: a blanket --overwrite
    would let pacman silently take over files belonging to someone else."""
    bootstrap = BOOTSTRAP.read_text()
    overwrites = re.findall(r"--overwrite '([^']+)'", bootstrap)
    assert overwrites, "no overwrite scope declared"
    for glob in overwrites:
        assert glob.startswith((
            "/opt/steamos-intel-handheld/",
            "/home/deck/homebrew/plugins/steamos-intel-handheld-",
            "/etc/systemd/system/steamos-intel-handheld-",
            "/etc/systemd/user/steamos-intel-handheld-",
            "/etc/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf",
            "/etc/systemd/user/gamescope-session.service.wants/steamos-intel-handheld-",
            "/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf",
            "/etc/dbus-1/system.d/org.rivoreo.",
            "/etc/gamescope/scripts/00-steamos-intel-handheld/",
            "/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg",
        )), glob
    # Every path the packages actually install must be covered, or a source
    # install still cannot move to packages.
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    # Directories are created, not written over, so they never conflict.
    etc_targets = {
        target
        for target in re.findall(r'"\$pkgdir(/etc/[^"]+)"', pkgbuild)
        if f'install -d -m 0755 "$pkgdir{target}"' not in pkgbuild
    }
    assert etc_targets
    import fnmatch
    for target in etc_targets:
        assert any(fnmatch.fnmatch(target, glob) for glob in overwrites), target
    # The catch-all forms that would defeat the point.
    for reckless in ("*", "/*", "/usr/*", "/etc/*"):
        assert reckless not in overwrites, reckless


def test_page_is_usable_without_a_mouse_and_without_motion() -> None:
    """Two things the page shipped without. A keyboard user had no focus ring
    anywhere, so there was no way to see what was selected; and the page scrolled
    smoothly regardless of the visitor's reduced-motion setting, which for some
    people is a vestibular trigger rather than a flourish."""
    index = SITE_INDEX.read_text()
    assert ":focus-visible" in index
    assert "outline:" in index
    assert "@media (prefers-reduced-motion: reduce)" in index
    # Smooth scrolling has to be switched back off, not merely declared once.
    assert index.count("scroll-behavior") >= 2


def test_alt_text_does_not_repeat_the_caption_beside_it() -> None:
    """A screen reader reads the image description and then the caption. Saying
    the same thing twice wastes the listener's time; the alt text should carry
    what the picture shows and the caption what it means."""
    translations = read_site_translations()["en"]
    for key in ("shot.power", "shot.charge", "shot.steam"):
        alt = set(re.findall(r"[a-z0-9+]+", translations[f"{key}.alt"].lower()))
        caption = set(re.findall(r"[a-z0-9+]+", translations[f"{key}.caption"].lower()))
        filler = {"a", "an", "the", "on", "at", "in", "of", "and", "with", "is", "to", "its"}
        overlap = (alt & caption) - filler
        # Some repetition is unavoidable - describing a panel needs the word
        # "panel". What matters is that the alt is not mostly the caption again.
        assert len(overlap) / max(len(alt - filler), 1) < 0.35, (key, sorted(overlap))
        # And the alt has to actually describe something.
        assert len(translations[f"{key}.alt"]) > 40, key


def test_language_buttons_are_big_enough_to_hit() -> None:
    """Three 24-pixel-tall buttons in a row is a target most thumbs miss."""
    index = SITE_INDEX.read_text()
    block = index[index.index(".language-option {") :]
    block = block[: block.index("}")]
    assert "min-height: 40px" in block
    assert "min-width: 44px" in block


def test_language_buttons_announce_what_they_switch_to() -> None:
    """The visible labels are single glyphs, so a screen reader announces "EN",
    "简", "繁" and nothing else. The accessible name has to say the language."""
    index = SITE_INDEX.read_text()
    for label in ('aria-label="English"', 'aria-label="简体中文"', 'aria-label="繁體中文"'):
        assert label in index, label
    # aria-pressed marks which one is active; without it there is no way to hear
    # which language is currently selected. Count the buttons, not the CSS
    # selector that styles them.
    buttons = re.findall(r"<button[^>]*data-language-option[^>]*>", index)
    assert len(buttons) == 3
    assert all("aria-pressed=" in button for button in buttons)
    assert all("aria-label=" in button for button in buttons)
