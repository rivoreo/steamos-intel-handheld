import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITLAB_CI = ROOT / ".gitlab-ci.yml"
PACKAGE_DOCS = ROOT / "docs/package-repository.md"
SOURCE_ARCHIVE_RE = re.compile(
    r"steamos-intel-handheld-[0-9]+[.][0-9]+[.][0-9]+(?:[-.][A-Za-z0-9._]+)?[.]tar[.]gz"
)


def read_gitlab_ci() -> str:
    assert GITLAB_CI.exists(), ".gitlab-ci.yml must define the package build pipeline"
    return GITLAB_CI.read_text()


def _line_index_containing(text: str, *needles: str) -> int:
    for index, line in enumerate(text.splitlines()):
        if all(needle in line for needle in needles):
            return index
    raise AssertionError(f"Could not find line containing: {needles}")


def test_gitlab_ci_builds_arch_package_in_arch_container() -> None:
    ci = read_gitlab_ci()
    assert "image: archlinux:base-devel" in ci
    assert "stage: package" in ci
    assert "makepkg --cleanbuild" in ci
    assert "PKGDEST=" in ci
    assert ".cache/arch-packages/*.pkg.tar.zst" in ci


def test_gitlab_ci_uses_non_root_makepkg_builder() -> None:
    ci = read_gitlab_ci()
    assert "useradd -m builder" in ci
    assert "chown -R builder:builder" in ci
    assert "su builder -c" in ci


def test_gitlab_ci_builds_current_commit_source_snapshot() -> None:
    ci = read_gitlab_ci()
    assert "tar --exclude=.git" in ci
    assert "--transform \"s,^,steamos-intel-handheld-${PKGVER}/,\"" in ci
    assert "/tmp/steamos-intel-handheld-${PKGVER}.tar.gz" in ci
    assert "packaging/arch/steamos-intel-handheld-${PKGVER}.tar.gz" in ci
    assert "updpkgsums" in ci


def test_gitlab_ci_derives_arch_package_version_from_pyproject() -> None:
    ci = read_gitlab_ci()
    assert "PKGVER=" in ci
    assert "pyproject.toml" in ci
    assert "tomllib" in ci
    assert '["project"]["version"]' in ci


def test_gitlab_ci_uses_pkgver_for_arch_source_archive_paths() -> None:
    ci = read_gitlab_ci()
    assert not SOURCE_ARCHIVE_RE.findall(ci)
    assert "steamos-intel-handheld-0.1.0.tar.gz" not in ci
    assert "steamos-intel-handheld-${PKGVER}.tar.gz" in ci
    assert "chown builder:builder" in ci
    assert "packaging/arch/steamos-intel-handheld-${PKGVER}.tar.gz" in ci


def test_gitlab_ci_syncs_pkgbuild_pkgver_before_refreshing_checksums() -> None:
    ci = read_gitlab_ci()
    sync_line = _line_index_containing(
        ci,
        "sed -i",
        "pkgver=${PKGVER}",
        "packaging/arch/PKGBUILD",
    )
    updpkgsums_line = _line_index_containing(ci, "updpkgsums")

    assert sync_line < updpkgsums_line


def test_gitlab_ci_builds_pacman_repository_artifact() -> None:
    ci = read_gitlab_ci()
    assert "stage: repository" in ci
    assert "repo-add" in ci
    assert "rivoreo-steamos.db.tar.zst" in ci
    assert "rivoreo-steamos.files" in ci
    assert ".cache/pacman-repo/public" in ci


def test_gitlab_ci_replaces_repo_add_symlinks_with_regular_files() -> None:
    ci = read_gitlab_ci()
    assert "rm -f rivoreo-steamos.db rivoreo-steamos.files" in ci
    assert "cp rivoreo-steamos.db.tar.zst rivoreo-steamos.db" in ci
    assert "cp rivoreo-steamos.files.tar.zst rivoreo-steamos.files" in ci


def test_package_repository_docs_describe_gitlab_ci_artifacts() -> None:
    docs = PACKAGE_DOCS.read_text()
    assert "GitLab CI" in docs
    assert "validation artifacts" in docs
    assert "arch:package" in docs
    assert "arch:repository" in docs
    assert ".pkg.tar.zst" in docs
    assert "repo-add" in docs
