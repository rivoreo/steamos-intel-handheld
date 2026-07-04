#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from harnesslib.rules import check_is_guarded, guarded_command_tokens, validate_report_path_token

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for direct script use.
    tomllib = None


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = ".cache/harness/required.json"


def load_manifest(root: Path) -> dict[str, Any]:
    text = (root / "harness.toml").read_text()
    if tomllib is not None:
        return tomllib.loads(text)
    return parse_simple_manifest(text)


def parse_simple_manifest(text: str) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    current_check: Optional[dict[str, Any]] = None  # noqa: UP045 - keep direct Python 3.9 use.
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
            raise ValueError(f"Unsupported manifest line: {raw_line}")
        key, raw_value = (part.strip() for part in line.split("=", 1))
        target = current_check if current_check is not None else manifest
        target[key] = parse_simple_value(raw_value)

    manifest["checks"] = checks
    return manifest


def parse_simple_value(raw_value: str) -> Any:
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
                raise ValueError(f"Unsupported array item: {value}")
            values.append(value[1:-1])
        return values
    try:
        return int(raw_value)
    except ValueError:
        raise ValueError(f"Unsupported manifest value: {raw_value}") from None


def checks_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["id"]: check for check in manifest["checks"]}


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_output(root: Path, args: list[str]) -> Optional[str]:  # noqa: UP045
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def path_is_ignored(path: str, ignored_paths: tuple[str, ...]) -> bool:
    parts = Path(path).parts
    if "__pycache__" in parts or path.endswith(".pyc"):
        return True
    return any(path == ignored or path.startswith(f"{ignored}/") for ignored in ignored_paths)


def git_status_path_candidates(line: str) -> list[str]:
    raw_path = line[3:].strip()
    if " -> " in raw_path:
        return [part.strip('"') for part in raw_path.split(" -> ", 1)]
    return [raw_path.strip('"')]


def filtered_git_status(status: str, ignored_paths: tuple[str, ...]) -> list[str]:
    lines = []
    for line in status.splitlines():
        paths = git_status_path_candidates(line)
        if all(path_is_ignored(path, ignored_paths) for path in paths):
            continue
        lines.append(line)
    return lines


def untracked_content_fingerprints(root: Path, status_lines: list[str]) -> list[str]:
    fingerprints = []
    for line in status_lines:
        if not line.startswith("?? "):
            continue
        for relative in git_status_path_candidates(line):
            path = root / relative
            if not path.is_file():
                continue
            fingerprints.append(f"{relative}:{file_sha256(path)}")
    return sorted(fingerprints)


def git_diff(root: Path, ignored_paths: tuple[str, ...], *, cached: bool = False) -> str:
    args = ["diff", "--binary"]
    if cached:
        args.append("--cached")
    if ignored_paths:
        args.extend(["--", "."])
        args.extend(f":(exclude){path}" for path in ignored_paths)
    return git_output(root, args) or ""


def workspace_state(root: Path, ignored_paths: tuple[str, ...] = ()) -> dict[str, Any]:
    head = git_output(root, ["rev-parse", "HEAD"])
    if head is None:
        return {
            "available": False,
            "fingerprint": "unavailable",
        }

    raw_status = git_output(root, ["status", "--porcelain=v1", "--untracked-files=all"]) or ""
    status_lines = filtered_git_status(raw_status, ignored_paths)
    unstaged = git_diff(root, ignored_paths)
    staged = git_diff(root, ignored_paths, cached=True)
    untracked_hashes = "\n".join(untracked_content_fingerprints(root, status_lines))
    fingerprint = sha256_text(
        "\0".join([head, "\n".join(status_lines), untracked_hashes, unstaged, staged])
    )
    return {
        "available": True,
        "head": head.strip(),
        "dirty": bool(status_lines),
        "status": status_lines,
        "untracked_content": untracked_hashes.splitlines(),
        "fingerprint": fingerprint,
    }


def report_context(root: Path, manifest: dict[str, Any], report_path: Path) -> dict[str, Any]:
    report_display_path = display_path(root, report_path)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trusted_suite": trusted_suite(manifest),
        "report_path": report_display_path,
        "manifest": {
            "path": "harness.toml",
            "sha256": file_sha256(root / "harness.toml"),
        },
        "workspace": workspace_state(root, (report_display_path,)),
    }


def canonical_trusted_suite(manifest: dict[str, Any]) -> str:
    return f"scripts/harness.py sweep required --report {report_path_value(manifest)}"


def trusted_suite(manifest: dict[str, Any]) -> str:
    expected = canonical_trusted_suite(manifest)
    actual = str(manifest.get("trusted_suite", expected))
    if actual != expected:
        raise ValueError("trusted_suite must be the required sweep only")
    return actual


def iteration_hint(manifest: dict[str, Any]) -> str:
    return str(manifest.get("iteration_hint", ".venv/bin/python -m pytest <path-or-node>"))


def report_path_value(manifest: dict[str, Any]) -> str:
    raw_path = str(manifest.get("report_path", DEFAULT_REPORT_PATH))
    validate_report_path_token(raw_path)
    return raw_path


def resolve_report_path(
    root: Path,
    manifest: dict[str, Any],
    override: Optional[str],  # noqa: UP045 - keep direct Python 3.9 use.
) -> Path:
    root = root.resolve()
    raw_value = override or report_path_value(manifest)
    validate_report_path_token(raw_value)
    raw_path = Path(raw_value)
    resolved = raw_path.resolve() if raw_path.is_absolute() else (root / raw_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"report path escapes repository root: {raw_path}") from None
    return resolved


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def read_report(path: Path, root: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": display_path(root, path),
        "exists": path.exists(),
    }
    if not path.exists():
        payload["status"] = "missing"
        payload["results"] = []
        return payload
    try:
        report = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        payload["status"] = "invalid"
        payload["error"] = str(error)
        payload["results"] = []
        return payload

    if not valid_report_shape(report):
        payload["status"] = "invalid"
        payload["error"] = "report must be a sweep report or single run report"
        payload["results"] = []
        payload["raw_report"] = report
        return payload

    payload.update(report)
    payload["exists"] = True
    payload["path"] = display_path(root, path)
    return payload


def valid_result_shape(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("id"), str)
        and isinstance(result.get("status"), str)
    )


def valid_report_shape(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    if report.get("report_type") == "run":
        return valid_result_shape(report)
    return valid_sweep_report_shape(report)


def valid_sweep_report_shape(report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    if not isinstance(report.get("selector"), str):
        return False
    if not isinstance(report.get("status"), str):
        return False
    results = report.get("results")
    if not isinstance(results, list):
        return False
    return all(valid_result_shape(result) for result in results)


def report_freshness(
    root: Path,
    manifest: dict[str, Any],
    report: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    if not report.get("exists"):
        return {
            "status": "missing",
            "reasons": ["report missing"],
        }
    if report.get("status") == "invalid":
        return {
            "status": "invalid",
            "reasons": ["report is invalid JSON"],
        }

    reasons = []
    context = report.get("context")
    if not isinstance(context, dict):
        reasons.append("report missing context")
    else:
        canonical_required_path = resolve_report_path(root, manifest, None)
        if report.get("report_type") == "run" and report_path.resolve() == canonical_required_path:
            reasons.append("required report is not a sweep report")
        elif report.get("report_type") != "run" and report.get("selector") != "required":
            reasons.append("report selector is not required")
        if context.get("trusted_suite") != trusted_suite(manifest):
            reasons.append("trusted suite changed since report")
        manifest_context = context.get("manifest", {})
        if manifest_context.get("sha256") != file_sha256(root / "harness.toml"):
            reasons.append("manifest changed since report")

        report_workspace = context.get("workspace", {})
        current_workspace = workspace_state(root, (display_path(root, report_path),))
        if current_workspace.get("available") is True:
            if not report_workspace.get("available") or not report_workspace.get("fingerprint"):
                reasons.append("report missing workspace fingerprint")
            elif report_workspace.get("fingerprint") != current_workspace.get("fingerprint"):
                reasons.append("workspace changed since report")

    return {
        "status": "fresh" if not reasons else "stale",
        "reasons": reasons,
    }


def report_results_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = report.get("results")
    if isinstance(results, list):
        return {
            result["id"]: result
            for result in results
            if isinstance(result, dict) and isinstance(result.get("id"), str)
        }
    if isinstance(report.get("id"), str):
        return {report["id"]: report}
    return {}


def guarded_blocked_reason(check: dict[str, Any]) -> Optional[str]:  # noqa: UP045
    requirements = check.get("requires", [])
    if requirements:
        return f"requires: {', '.join(requirements)}"
    tokens = [token.strip() for token in guarded_command_tokens(check.get("command", ""))]
    if tokens:
        return f"guarded command token: {', '.join(tokens)}"
    if check.get("tier") == "guarded":
        return "tier: guarded"
    if check.get("safe_for_agents") is False:
        return "safe_for_agents: false"
    return None


def artifact_result(artifact_id: str, status: str, detail: str) -> dict[str, str]:
    return {
        "id": artifact_id,
        "status": status,
        "detail": detail,
    }


ARTIFACT_PATTERNS: dict[str, tuple[str, str, str]] = {
    "verify-on-device-output": (
        r"OK: SteamOS Manager TDP remote works and restored \d+W",
        "device verifier success marker found",
        "device verifier success marker not found",
    ),
    "systemd-state": (
        r"OK: systemd failed-unit list is empty",
        "systemd state marker found",
        "systemd state marker not found",
    ),
    "steamos-manager-state": (
        r"OK: SteamOS Manager TDP remote works and restored \d+W",
        "SteamOS Manager state marker found",
        "SteamOS Manager state marker not found",
    ),
    "rapl-pl1-state": (
        r"OK: RAPL PL1/PL2 restored",
        "RAPL restore marker found",
        "RAPL restore marker not found",
    ),
    "failed-units-list": (
        r"OK: systemd failed-unit list is empty",
        "failed-unit list marker found",
        "failed-unit list marker not found",
    ),
    "observe-output": (
        r"== game-power observe ==",
        "game-power observe output marker found",
        "game-power observe output marker not found",
    ),
    "gpu-priority-output": (
        r"== game-power gpu-priority ==",
        "game-power gpu-priority output marker found",
        "game-power gpu-priority output marker not found",
    ),
    "cpu-policy-restore-diff": (
        r"game-power verifier: CPU policy restored",
        "CPU policy restore marker found",
        "CPU policy restore marker not found",
    ),
    "profile-manifest-json": (
        r"profile artifact manifest\.json: .+/manifest\.json",
        "profile manifest marker found",
        "profile manifest marker not found",
    ),
    "profile-summary-json": (
        r"profile artifact summary\.json: .+/summary\.json",
        "profile summary marker found",
        "profile summary marker not found",
    ),
    "mangohud-csv": (
        r"profile artifact mangohud\.csv: .+/mangohud\.csv",
        "MangoHud CSV marker found",
        "MangoHud CSV marker not found",
    ),
    "game-power-jsonl": (
        r"profile artifact game-power\.jsonl: .+/game-power\.jsonl",
        "game-power JSONL marker found",
        "game-power JSONL marker not found",
    ),
    "restore-snapshot": (
        r"profile artifact restore snapshot: .+/restore-affinity\.json",
        "restore snapshot marker found",
        "restore snapshot marker not found",
    ),
    "runtime-telemetry-contract-json": (
        r"profile artifact runtime telemetry contract: .+/runtime-telemetry-contract\.json",
        "runtime telemetry contract marker found",
        "runtime telemetry contract marker not found",
    ),
    "profile-runtime-telemetry-contract-json": (
        (
            r"profile artifact runtime telemetry aggregate: "
            r".+/profile-runtime-telemetry-contract\.json"
        ),
        "profile runtime telemetry aggregate marker found",
        "profile runtime telemetry aggregate marker not found",
    ),
    "action-equivalence-replay-summary": (
        r"profile artifact action equivalence: .+/action-equivalence\.json",
        "action equivalence replay marker found",
        "action equivalence replay marker not found",
    ),
    "pacman-repository-shape": (
        r"GitLab pacman artifact dry run passed: .+/rivoreo-steamos/os/x86_64",
        "pacman repository shape marker found",
        "pacman repository shape marker not found",
    ),
    "artifact-root": (
        r"GitLab pacman artifact dry run passed: \S+",
        "artifact root marker found",
        "artifact root marker not found",
    ),
    "verification-output": (
        r"GitLab pacman artifact dry run passed: \S+",
        "verification output marker found",
        "verification output marker not found",
    ),
    "mangoapp-artifact-path": (
        r"mangoapp artifact path: \S+",
        "mangoapp artifact path marker found",
        "mangoapp artifact path marker not found",
    ),
    "file-output": (
        r"mangoapp: .*ELF",
        "file output marker found",
        "file output marker not found",
    ),
    "rootfs-build-log": (
        r"rootfs build log: meson compile mangoapp completed",
        "rootfs build log marker found",
        "rootfs build log marker not found",
    ),
}


def validate_evidence_artifact(artifact_id: str, output: str) -> dict[str, str]:
    if artifact_id in {"command-output", "fixture-output"}:
        if output.strip():
            return artifact_result(artifact_id, "pass", "command output captured")
        return artifact_result(artifact_id, "missing", "command output was empty")
    if artifact_id == "ruff-summary":
        if "==> ruff" in output and "All checks passed!" in output:
            return artifact_result(artifact_id, "pass", "ruff summary found")
        return artifact_result(artifact_id, "missing", "ruff summary marker not found")
    if artifact_id == "engineering-policy-summary":
        if "==> engineering policy" in output and "engineering policy passed" in output:
            return artifact_result(artifact_id, "pass", "engineering policy summary found")
        return artifact_result(
            artifact_id,
            "missing",
            "engineering policy summary marker not found",
        )
    if artifact_id == "shell-syntax-summary":
        if "==> shell syntax" in output:
            return artifact_result(artifact_id, "pass", "shell syntax summary found")
        return artifact_result(artifact_id, "missing", "shell syntax summary marker not found")
    if artifact_id == "pytest-summary":
        if "==> pytest" in output and re.search(r"\d+\s+passed\b", output):
            return artifact_result(artifact_id, "pass", "pytest summary found")
        return artifact_result(artifact_id, "missing", "pytest summary marker not found")
    if artifact_id == "compileall-summary":
        if "==> compileall" in output:
            return artifact_result(artifact_id, "pass", "compileall summary found")
        return artifact_result(artifact_id, "missing", "compileall summary marker not found")
    if artifact_id in ARTIFACT_PATTERNS:
        pattern, pass_detail, missing_detail = ARTIFACT_PATTERNS[artifact_id]
        if re.search(pattern, output):
            return artifact_result(artifact_id, "pass", pass_detail)
        return artifact_result(artifact_id, "missing", missing_detail)
    return artifact_result(artifact_id, "missing", "no validator registered for artifact")


def validate_evidence_artifacts(check: dict[str, Any], output: str) -> list[dict[str, str]]:
    return [
        validate_evidence_artifact(artifact_id, output)
        for artifact_id in check.get("evidence_artifacts", [])
    ]


def missing_evidence_artifacts(
    result: Optional[dict[str, Any]],  # noqa: UP045
    artifact_ids: Optional[list[str]] = None,  # noqa: UP045
) -> list[str]:
    if not isinstance(result, dict):
        return []
    artifacts = artifact_ids if artifact_ids is not None else result.get("evidence_artifacts", [])
    artifact_results = result.get("evidence_artifact_results")
    if not artifacts:
        return []
    if not isinstance(artifact_results, list):
        return list(artifacts)
    statuses = {
        item.get("id"): item.get("status")
        for item in artifact_results
        if isinstance(item, dict)
    }
    return [
        artifact_id
        for artifact_id in artifacts
        if statuses.get(artifact_id) != "pass"
    ]


def evidence_state(result: Optional[dict[str, Any]], artifact_ids: list[str]) -> str:  # noqa: UP045
    if not artifact_ids:
        return "not-required"
    if result is None:
        return "pending"
    missing = missing_evidence_artifacts(result, artifact_ids)
    if not missing:
        return "verified"
    if isinstance(result.get("evidence_artifact_results"), list):
        return "failed"
    return "pending"


def effective_checks(
    manifest: dict[str, Any],
    report: dict[str, Any],
    freshness: dict[str, Any],
) -> list[dict[str, Any]]:
    results = report_results_by_id(report)
    rows = []
    for check in manifest["checks"]:
        check_id = check["id"]
        result = results.get(check_id)
        guarded = check_is_guarded(check)
        artifact_ids = check.get("evidence_artifacts", [])
        row_evidence_state = evidence_state(result, artifact_ids)
        if guarded and result is None:
            verification_state = "guarded"
        elif freshness["status"] != "fresh" or result is None or row_evidence_state == "pending":
            verification_state = "pending"
        elif row_evidence_state == "failed":
            verification_state = "failed"
        elif status_is_success(str(result.get("status", ""))):
            verification_state = "verified"
        else:
            verification_state = "failed"

        rows.append(
            {
                "id": check_id,
                "declared_tier": check.get("tier", "required"),
                "effective_tier": "guarded" if guarded else check.get("tier", "required"),
                "safe_for_agents": check.get("safe_for_agents"),
                "requirements": check.get("requires", []),
                "runnable_by_default": not guarded,
                "blocked_reason": guarded_blocked_reason(check) if guarded else None,
                "expectation": check.get("expectation", "pass"),
                "required_evidence": check.get("evidence", "command output"),
                "evidence_artifacts": artifact_ids,
                "evidence_state": row_evidence_state,
                "evidence_artifact_results": (
                    result.get("evidence_artifact_results")
                    if isinstance(result, dict)
                    else None
                ),
                "verification_state": verification_state,
                "last_result": result,
            }
        )
    return rows


def pending_verification(
    manifest: dict[str, Any],
    report: dict[str, Any],
    freshness: dict[str, Any],
    matrix: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    command = trusted_suite(manifest)
    required_ids = [
        check["id"]
        for check in manifest["checks"]
        if check.get("tier") == "required" and not check_is_guarded(check)
    ]
    if freshness["status"] == "missing":
        return [
            {
                "scope": "required",
                "checks": required_ids,
                "reason": "last report is missing",
                "command": command,
            }
        ]
    if freshness["status"] == "invalid":
        return [
            {
                "scope": "required",
                "checks": required_ids,
                "reason": "last report is invalid",
                "command": command,
            }
        ]
    if freshness["status"] == "stale":
        reasons = "; ".join(freshness["reasons"])
        return [
            {
                "scope": "required",
                "checks": required_ids,
                "reason": f"last report is stale: {reasons}",
                "command": command,
            }
        ]
    if report.get("report_type") == "run":
        pending = []
        for row in matrix:
            if row.get("last_result") is None or row["verification_state"] == "verified":
                continue
            pending.append(
                {
                    "scope": "check",
                    "checks": [row["id"]],
                    "reason": f"{row['id']} last result is {row['verification_state']}",
                    "command": row["last_result"].get("command", command),
                }
            )
        return pending
    pending = []
    for row in matrix:
        if row["declared_tier"] != "required" or row["effective_tier"] == "guarded":
            continue
        if row["verification_state"] == "verified":
            continue
        last_result = row.get("last_result") or {}
        result_status = last_result.get("status", "missing")
        missing_artifacts = missing_evidence_artifacts(
            row.get("last_result"),
            row.get("evidence_artifacts", []),
        )
        if result_status == "evidence-fail" and missing_artifacts:
            if isinstance((row.get("last_result") or {}).get("evidence_artifact_results"), list):
                reason = (
                    f"{row['id']} evidence artifacts are missing: "
                    f"{', '.join(missing_artifacts)}"
                )
            else:
                reason = (
                    f"{row['id']} evidence artifacts are unverified: "
                    f"{', '.join(missing_artifacts)}"
                )
            pending.append(
                {
                    "scope": "check",
                    "checks": [row["id"]],
                    "reason": reason,
                    "command": command,
                }
            )
            continue
        if result_status != "missing" and not status_is_success(str(result_status)):
            pending.append(
                {
                    "scope": "check",
                    "checks": [row["id"]],
                    "reason": f"{row['id']} last result is {result_status}",
                    "command": command,
                }
            )
            continue
        if report.get("status") != "passed" and status_is_success(str(result_status)):
            continue
        if missing_artifacts:
            pending.append(
                {
                    "scope": "check",
                    "checks": [row["id"]],
                    "reason": (
                        f"{row['id']} evidence artifacts are unverified: "
                        f"{', '.join(missing_artifacts)}"
                    ),
                    "command": command,
                }
            )
            continue
        pending.append(
            {
                "scope": "check",
                "checks": [row["id"]],
                "reason": f"{row['id']} last result is {result_status}",
                "command": command,
            }
        )
    if pending:
        return pending
    if report.get("status") != "passed":
        return [
            {
                "scope": "required",
                "checks": required_ids,
                "reason": f"last report status is {report.get('status', 'missing')}",
                "command": command,
            }
        ]
    return pending


def list_checks(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.root.resolve())
    if args.json:
        print_json(manifest)
        return 0

    for check in manifest["checks"]:
        requirements = ",".join(check.get("requires", [])) or "none"
        safe = "safe" if check.get("safe_for_agents") else "guarded"
        print(f"{check['id']}\t{safe}\trequires={requirements}\t{check['command']}")
    return 0


def status_payload(
    root: Path,
    manifest: dict[str, Any],
    report_override: Optional[str],  # noqa: UP045 - keep direct Python 3.9 use.
) -> dict[str, Any]:
    checks = manifest["checks"]
    report_path = resolve_report_path(root, manifest, report_override)
    last_report = read_report(report_path, root)
    freshness = report_freshness(root, manifest, last_report, report_path)
    last_report["freshness"] = freshness
    matrix = effective_checks(manifest, last_report, freshness)
    return {
        "root": root.as_posix(),
        "trusted_suite": trusted_suite(manifest),
        "iteration_hint": iteration_hint(manifest),
        "report_path": display_path(root, report_path),
        "required_checks": [
            check["id"] for check in checks if check.get("tier") == "required"
        ],
        "guarded_checks": [
            check["id"] for check in checks if check.get("tier") == "guarded"
        ],
        "freshness": freshness,
        "gate_matrix": matrix,
        "effective_checks": matrix,
        "pending_verification": pending_verification(
            manifest,
            last_report,
            freshness,
            matrix,
        ),
        "last_report": last_report,
        "next_actions": [
            f"Use focused iteration for narrow TDD: {iteration_hint(manifest)}",
            f"Close local changes with the trusted suite: {trusted_suite(manifest)}",
            "Run guarded device, release, QEMU, or network checks only when explicitly needed.",
        ],
    }


def print_status_text(payload: dict[str, Any]) -> None:
    print(f"trusted suite: {payload['trusted_suite']}")
    print(f"iteration hint: {payload['iteration_hint']}")
    print(f"report path: {payload['report_path']}")
    print(f"required checks: {', '.join(payload['required_checks']) or 'none'}")
    print(f"guarded checks: {', '.join(payload['guarded_checks']) or 'none'}")
    last_report = payload["last_report"]
    print(f"last report: {last_report.get('status', 'unknown')} ({last_report['path']})")
    if last_report.get("results"):
        for result in last_report["results"]:
            duration = result.get("duration_seconds")
            suffix = f", {duration}s" if duration is not None else ""
            print(f"  - {result['id']}: {result['status']}{suffix}")
    print("next actions:")
    for action in payload["next_actions"]:
        print(f"  - {action}")


def show_status(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = load_manifest(root)
    payload = status_payload(root, manifest, args.report)
    if args.json:
        print_json(payload)
    else:
        print_status_text(payload)
    return 0


def allow_requirement_flags(check: dict[str, Any]) -> str:
    requirements = check.get("requires", [])
    if not requirements:
        return ""
    return " ".join(f"--allow-requirement {requirement}" for requirement in requirements)


def guarded_acknowledgement_flags(check: dict[str, Any]) -> str:
    flags = ["--allow-guarded"]
    requirement_flags = allow_requirement_flags(check)
    if requirement_flags:
        flags.append(requirement_flags)
    return " ".join(flags)


def explain_check(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = load_manifest(root)
    checks = checks_by_id(manifest)
    check = checks.get(args.check_id)
    if check is None:
        print(f"Unknown harness check: {args.check_id}", file=sys.stderr)
        print("Available checks:", ", ".join(sorted(checks)), file=sys.stderr)
        return 2

    requirements = ", ".join(check.get("requires", [])) or "none"
    print(check["id"])
    print(f"  description: {check.get('description', '')}")
    print(f"  tier: {check.get('tier', 'required')}")
    print(f"  expectation: {check.get('expectation', 'pass')}")
    print(f"  safe_for_agents: {str(check.get('safe_for_agents')).lower()}")
    print(f"  requires: {requirements}")
    print(f"  command: {check['command']}")
    print(f"  expected duration: {check.get('expected_duration', 'unknown')}")
    print(f"  evidence: {check.get('evidence', 'command output')}")
    artifacts = check.get("evidence_artifacts", [])
    if artifacts:
        print(f"  evidence artifacts: {', '.join(artifacts)}")
    run_command_line = f"scripts/harness.py run {check['id']}"
    print(f"  run command: {run_command_line}")
    if check_is_guarded(check):
        print(f"  guarded acknowledgement required: {guarded_acknowledgement_flags(check)}")
    return 0


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_command(check: dict[str, Any], root: Path, capture: bool) -> subprocess.CompletedProcess:
    return subprocess.run(
        check["command"],
        cwd=root,
        shell=True,
        env=os.environ.copy(),
        text=True,
        capture_output=capture,
    )


def result_for_completed(
    check: dict[str, Any],
    completed: subprocess.CompletedProcess,
    started: datetime,
    duration_seconds: float,
) -> dict[str, Any]:
    expectation = check.get("expectation", "pass")
    output = (completed.stdout or "") + (completed.stderr or "")
    status = "pass" if completed.returncode == 0 else "fail"
    evidence_results = validate_evidence_artifacts(check, output)

    if expectation == "known-fail":
        signature = check.get("failure_signature", "")
        if completed.returncode == 0:
            status = "unexpected-pass"
        elif signature and signature in output:
            status = "known-fail"
        else:
            status = "new-failure"
    elif status == "pass" and any(
        result["status"] != "pass" for result in evidence_results
    ):
        status = "evidence-fail"

    return {
        "id": check["id"],
        "command": check["command"],
        "returncode": completed.returncode,
        "status": status,
        "expectation": expectation,
        "tier": check.get("tier", "required"),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "requirements": check.get("requires", []),
        "evidence_artifacts": check.get("evidence_artifacts", []),
        "evidence_artifact_results": evidence_results,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def status_is_success(status: str) -> bool:
    return status in {"pass", "known-fail"}


def run_check(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = load_manifest(root)
    checks = checks_by_id(manifest)
    check = checks.get(args.check_id)
    if check is None:
        print(f"Unknown harness check: {args.check_id}", file=sys.stderr)
        print("Available checks:", ", ".join(sorted(checks)), file=sys.stderr)
        return 2

    report_path = resolve_report_path(root, manifest, args.report) if args.report else None
    requirements = check.get("requires", [])
    if check_is_guarded(check) and not requirements:
        print(f"{check['id']} is guarded but declares no requirements", file=sys.stderr)
        return 2
    if check_is_guarded(check) and not args.allow_guarded:
        print(f"Refusing to run {check['id']} because it is guarded.", file=sys.stderr)
        print("Re-run with --allow-guarded only after explicit human approval.", file=sys.stderr)
        for requirement in requirements:
            print(
                f"Re-run with --allow-requirement {requirement} to acknowledge it.",
                file=sys.stderr,
            )
        print(f"Command: {check['command']}", file=sys.stderr)
        return 2
    allowed = set(args.allow_requirement)
    missing = [requirement for requirement in requirements if requirement not in allowed]
    if missing:
        print(
            f"Refusing to run {check['id']} because it requires: {', '.join(missing)}",
            file=sys.stderr,
        )
        for requirement in missing:
            print(
                f"Re-run with --allow-requirement {requirement} to acknowledge it.",
                file=sys.stderr,
            )
        print(f"Command: {check['command']}", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    start = time.monotonic()
    print(f"Running {check['id']}: {check['command']}", flush=True)
    completed = run_command(check, root, capture=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    payload = result_for_completed(check, completed, started, time.monotonic() - start)
    if report_path:
        payload["schema_version"] = 1
        payload["report_type"] = "run"
        payload["context"] = report_context(root, manifest, report_path)
        write_report(report_path, payload)
    if status_is_success(payload["status"]):
        return 0
    return completed.returncode if completed.returncode else 1


def checks_for_sweep(manifest: dict[str, Any], selector: str) -> list[dict[str, Any]]:
    checks = manifest["checks"]
    if selector == "required":
        return [check for check in checks if check.get("tier") == "required"]
    if selector == "safe":
        return [check for check in checks if check.get("safe_for_agents") is True]
    if selector == "all":
        return list(checks)
    raise ValueError(f"Unsupported sweep selector: {selector}")


def sweep_checks(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest = load_manifest(root)
    results = []
    success = True
    report_path = resolve_report_path(root, manifest, args.report) if args.report else None

    for check in checks_for_sweep(manifest, args.selector):
        requirements = check.get("requires", [])
        if check_is_guarded(check):
            result = {
                "id": check["id"],
                "command": check["command"],
                "returncode": 2,
                "status": "blocked",
                "expectation": check.get("expectation", "blocked"),
                "tier": check.get("tier", "guarded"),
                "requirements": requirements,
                "evidence_artifacts": check.get("evidence_artifacts", []),
            }
            results.append(result)
            success = False
            print(f"{check['id']}: blocked", file=sys.stderr)
            continue

        started = datetime.now(timezone.utc)
        start = time.monotonic()
        print(f"Sweeping {check['id']}: {check['command']}", flush=True)
        completed = run_command(check, root, capture=True)
        result = result_for_completed(check, completed, started, time.monotonic() - start)
        results.append(result)
        print(f"{check['id']}: {result['status']}", flush=True)
        if not status_is_success(result["status"]):
            success = False
            print(f"{check['id']}: {result['status']}", file=sys.stderr)

    payload = {
        "schema_version": 1,
        "report_type": "sweep",
        "selector": args.selector,
        "results": results,
        "status": "passed" if success else "failed",
    }
    if report_path:
        payload["context"] = report_context(root, manifest, report_path)
        write_report(report_path, payload)
    else:
        print_json(payload)

    return 0 if success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List and run project harness checks.")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Repository root containing harness.toml.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List harness checks.")
    list_parser.add_argument("--json", action="store_true", help="Emit the manifest as JSON.")
    list_parser.set_defaults(func=list_checks)

    status_parser = subparsers.add_parser("status", help="Show harness control-plane status.")
    status_parser.add_argument("--json", action="store_true", help="Emit status as JSON.")
    status_parser.add_argument(
        "--report",
        help="Optional sweep report path; defaults to harness.toml report_path.",
    )
    status_parser.set_defaults(func=show_status)

    explain_parser = subparsers.add_parser("explain", help="Explain one harness check.")
    explain_parser.add_argument("check_id", help="Harness check id from harness.toml.")
    explain_parser.set_defaults(func=explain_check)

    run_parser = subparsers.add_parser("run", help="Run one harness check.")
    run_parser.add_argument("check_id", help="Harness check id from harness.toml.")
    run_parser.add_argument(
        "--allow-requirement",
        action="append",
        default=[],
        help="Acknowledge a non-local requirement before running a guarded check.",
    )
    run_parser.add_argument(
        "--allow-guarded",
        action="store_true",
        help="Acknowledge that a guarded check may execute outside the safe local loop.",
    )
    run_parser.add_argument("--report", help="Optional path for a JSON run report.")
    run_parser.set_defaults(func=run_check)

    sweep_parser = subparsers.add_parser("sweep", help="Run a set of harness checks.")
    sweep_parser.add_argument("selector", choices=["required", "safe", "all"])
    sweep_parser.add_argument("--report", help="Optional path for a JSON sweep report.")
    sweep_parser.set_defaults(func=sweep_checks)

    return parser


def main(argv: Optional[list[str]] = None) -> int:  # noqa: UP045 - keep direct Python 3.9 use.
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
