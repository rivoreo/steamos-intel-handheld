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
    assert "Packages pending" in index
    assert "exits without changing the system" in index


def test_pages_site_declares_custom_domain() -> None:
    cname = (ROOT / "site/CNAME").read_text()
    assert cname.strip() == "holo.libz.so"


def test_placeholder_bootstrap_exits_before_packages_exist() -> None:
    bootstrap = BOOTSTRAP.read_text()
    assert "signed packages" in bootstrap
    assert "exit 1" in bootstrap
    assert "pacman -S" not in bootstrap
    assert "steamos-readonly disable" not in bootstrap
