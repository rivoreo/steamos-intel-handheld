#!/usr/bin/env python3
from __future__ import annotations

import argparse
import stat
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Keep direct script execution working on older system Python.
    tomllib = None


GUARDED_COMMAND_TOKENS = (
    "ssh ",
    "scp ",
    "curl ",
    "gh ",
    "git push",
    "sudo ",
    "pacman",
    "mount",
    "chroot",
    "losetup",
    "qemu-system",
)
STALE_DEVICE_TARGET = "root@192.168.128.214"
CURRENT_DEVICE_TARGET = "root@10.100.0.19"
AGENT_FACING_DOCS = (
    "AGENTS.md",
    "README.md",
    "docs/ai-development-harness.md",
    "docs/tdd-workflow.md",
    "docs/steamos-qemu-build-env.md",
)
CANONICAL_PAYLOAD_ROOTS = (
    "data",
    "packaging",
)
LEGACY_INSTALL_PREFIXES = (
    "/opt/rivoreo",
    "/etc/rivoreo",
)
ALLOWED_TIERS = ("required", "guarded")
ALLOWED_EXPECTATIONS = ("pass", "blocked", "known-fail")
KNOWN_FAIL_FIELDS = ("failure_signature", "quarantine_reason", "quarantine_expires")


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_toml(path: Path) -> dict[str, Any]:
    text = read_text(path)
    if tomllib is not None:
        return tomllib.loads(text)
    return parse_simple_toml(text)


def parse_simple_toml(text: str) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    current_check: Optional[dict[str, Any]] = None  # noqa: UP045 - direct Python 3.9 use.
    checks: list[dict[str, Any]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[checks]]":
            current_check = {}
            checks.append(current_check)
            continue
        if "=" not in line:
            raise ValueError(f"Unsupported TOML line: {raw_line}")
        key, raw_value = (part.strip() for part in line.split("=", 1))
        target = current_check if current_check is not None else manifest
        target[key] = parse_simple_toml_value(raw_value)

    if checks:
        manifest["checks"] = checks
    return manifest


def parse_simple_toml_value(raw_value: str) -> Any:
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    if raw_value.startswith('"') and raw_value.endswith('"'):
        return raw_value[1:-1]
    if raw_value.startswith("[") and raw_value.endswith("]"):
        inner = raw_value[1:-1].strip()
        if not inner:
            return []
        values = []
        for part in inner.split(","):
            value = part.strip()
            if not (value.startswith('"') and value.endswith('"')):
                raise ValueError(f"Unsupported TOML array item: {value}")
            values.append(value[1:-1])
        return values
    try:
        return int(raw_value)
    except ValueError:
        raise ValueError(f"Unsupported TOML value: {raw_value}") from None


def add_if_missing(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def check_known_fail_quarantine(check_id: str, raw_expires: Any, errors: list[str]) -> None:
    if not raw_expires:
        return
    if not isinstance(raw_expires, str):
        errors.append(f"{check_id} known-fail quarantine_expires must be YYYY-MM-DD")
        return
    try:
        expires = date.fromisoformat(raw_expires)
    except ValueError:
        errors.append(f"{check_id} known-fail quarantine_expires must be YYYY-MM-DD")
        return
    if expires < date.today():
        errors.append(f"{check_id} known-fail quarantine_expires is expired: {raw_expires}")


def check_harness_manifest(root: Path, errors: list[str]) -> None:
    manifest_path = root / "harness.toml"
    add_if_missing(errors, manifest_path.exists(), "harness.toml is missing")
    if not manifest_path.exists():
        return

    manifest = load_toml(manifest_path)
    checks = manifest.get("checks", [])
    add_if_missing(errors, isinstance(checks, list), "harness.toml checks must be a list")
    for check in checks:
        check_id = check.get("id", "<missing-id>")
        command = check.get("command", "")
        requirements = check.get("requires", [])
        safe_for_agents = check.get("safe_for_agents")
        tier = check.get("tier")
        expectation = check.get("expectation")

        add_if_missing(errors, isinstance(check_id, str), "harness check id must be a string")
        add_if_missing(
            errors,
            isinstance(requirements, list),
            f"{check_id} requires must be a list",
        )
        add_if_missing(
            errors,
            isinstance(safe_for_agents, bool),
            f"{check_id} safe_for_agents must be boolean",
        )
        if tier not in ALLOWED_TIERS:
            errors.append(f"{check_id} tier must be one of: required, guarded")
        if expectation not in ALLOWED_EXPECTATIONS:
            errors.append(
                f"{check_id} expectation must be one of: pass, blocked, known-fail"
            )
        if safe_for_agents is True and requirements:
            errors.append(f"{check_id} is safe_for_agents but still has requirements")
        if safe_for_agents is False and not requirements:
            errors.append(f"{check_id} is guarded but declares no requirements")
        if tier == "required" and safe_for_agents is not True:
            errors.append(f"{check_id} required gate must be safe_for_agents")
        if tier == "required" and requirements:
            errors.append(f"{check_id} required gate must not declare requirements")
        if tier == "guarded" and safe_for_agents is not False:
            errors.append(f"{check_id} guarded gate must not be safe_for_agents")
        if tier == "guarded" and not requirements:
            errors.append(f"{check_id} guarded gate must declare requirements")
        if tier == "required" and expectation == "blocked":
            errors.append(f"{check_id} required gate cannot have blocked expectation")
        if tier == "guarded" and expectation == "pass":
            errors.append(f"{check_id} guarded gate cannot have pass expectation")
        if expectation == "known-fail":
            for field in KNOWN_FAIL_FIELDS:
                if not check.get(field):
                    errors.append(f"{check_id} known-fail is missing {field}")
            check_known_fail_quarantine(check_id, check.get("quarantine_expires"), errors)

        guarded_tokens = [token for token in GUARDED_COMMAND_TOKENS if token in command]
        if guarded_tokens and safe_for_agents is True and not requirements:
            errors.append(f"{check_id} is marked safe_for_agents but declares no requirements")
        for token in guarded_tokens:
            if safe_for_agents is True:
                errors.append(
                    f"{check_id} safe_for_agents command contains guarded token: {token.strip()}"
                )


def check_executable_policy(root: Path, errors: list[str]) -> None:
    executable_paths = []
    for pattern in (
        "scripts/*.sh",
        "scripts/*.py",
        "data/bin/*",
        "data/NetworkManager/dispatcher.d/*",
    ):
        executable_paths.extend(sorted(root.glob(pattern)))

    for path in executable_paths:
        if not path.is_file():
            continue
        text = read_text(path)
        path_rel = rel(root, path)
        add_if_missing(errors, text.startswith("#!"), f"{path_rel} is missing a shebang")
        mode = path.stat().st_mode
        add_if_missing(
            errors,
            bool(mode & stat.S_IXUSR),
            f"{path_rel} is not executable by the owner",
        )
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if "bash" in first_line:
            add_if_missing(
                errors,
                "set -euo pipefail" in text,
                f"{path_rel} is missing set -euo pipefail",
            )


def check_local_harness(root: Path, errors: list[str]) -> None:
    script_path = root / "scripts/check-local.sh"
    add_if_missing(errors, script_path.exists(), "scripts/check-local.sh is missing")
    if not script_path.exists():
        return

    script = read_text(script_path)
    required_fragments = (
        'run_step "ruff"',
        'run_step "engineering policy"',
        "scripts/check-engineering-policy.py",
        'run_step "shell syntax"',
        'run_step "pytest"',
        'run_step "compileall"',
    )
    for fragment in required_fragments:
        add_if_missing(
            errors,
            fragment in script,
            f"scripts/check-local.sh does not run {fragment}",
        )


def check_agent_facing_docs(root: Path, errors: list[str]) -> None:
    for relative in AGENT_FACING_DOCS:
        path = root / relative
        if not path.exists():
            continue
        text = read_text(path)
        if STALE_DEVICE_TARGET in text:
            errors.append(f"{relative} contains stale device target {STALE_DEVICE_TARGET}")

    agents = root / "AGENTS.md"
    if agents.exists():
        text = read_text(agents)
        add_if_missing(
            errors,
            "harness.toml" in text,
            "AGENTS.md does not point to harness.toml",
        )
        add_if_missing(
            errors,
            CURRENT_DEVICE_TARGET in text,
            f"AGENTS.md does not name current device target {CURRENT_DEVICE_TARGET}",
        )


def check_legacy_prefixes(root: Path, errors: list[str]) -> None:
    for prefix_root in CANONICAL_PAYLOAD_ROOTS:
        for path in (root / prefix_root).rglob("*"):
            if not path.is_file():
                continue
            text = read_text(path)
            path_rel = rel(root, path)
            for prefix in LEGACY_INSTALL_PREFIXES:
                if prefix in text:
                    errors.append(f"{path_rel} references legacy install prefix {prefix}")


def check_release_workflow_shape(root: Path, errors: list[str]) -> None:
    ci = root / ".github/workflows/ci.yml"
    if ci.exists():
        text = read_text(ci)
        add_if_missing(errors, "scripts/check-local.sh" in text, "ci.yml must run local harness")

    release = root / ".github/workflows/arch-release.yml"
    if release.exists():
        text = read_text(release)
        add_if_missing(
            errors,
            "verify-repo-artifact:" in text,
            "arch-release.yml must define verify-repo-artifact",
        )
        add_if_missing(
            errors,
            "needs: [validate, build-repo, verify-repo-artifact]" in text,
            "deploy-pages must depend on verify-repo-artifact",
        )


def collect_errors(root: Path) -> list[str]:
    errors: list[str] = []
    check_harness_manifest(root, errors)
    check_executable_policy(root, errors)
    check_local_harness(root, errors)
    check_agent_facing_docs(root, errors)
    check_legacy_prefixes(root, errors)
    check_release_workflow_shape(root, errors)
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enforce repository engineering policy gates.")
    parser.add_argument(
        "--root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Repository root to inspect.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:  # noqa: UP045 - direct Python 3.9 use.
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    errors = collect_errors(root)
    if errors:
        for error in errors:
            print(f"engineering policy: {error}", file=sys.stderr)
        return 1

    print("engineering policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
