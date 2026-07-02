#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for direct script use.
    tomllib = None


DEFAULT_ROOT = Path(__file__).resolve().parents[1]


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

    if expectation == "known-fail":
        signature = check.get("failure_signature", "")
        if completed.returncode == 0:
            status = "unexpected-pass"
        elif signature and signature in output:
            status = "known-fail"
        else:
            status = "new-failure"

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

    requirements = check.get("requires", [])
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
    completed = run_command(check, root, capture=False)
    payload = result_for_completed(check, completed, started, time.monotonic() - start)
    if args.report:
        write_report(Path(args.report), payload)
    return completed.returncode


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

    for check in checks_for_sweep(manifest, args.selector):
        requirements = check.get("requires", [])
        if requirements and args.selector != "all":
            result = {
                "id": check["id"],
                "command": check["command"],
                "returncode": 2,
                "status": "blocked",
                "expectation": check.get("expectation", "blocked"),
                "tier": check.get("tier", "guarded"),
                "requirements": requirements,
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
        "selector": args.selector,
        "results": results,
        "status": "passed" if success else "failed",
    }
    if args.report:
        write_report(Path(args.report), payload)
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

    run_parser = subparsers.add_parser("run", help="Run one harness check.")
    run_parser.add_argument("check_id", help="Harness check id from harness.toml.")
    run_parser.add_argument(
        "--allow-requirement",
        action="append",
        default=[],
        help="Acknowledge a non-local requirement before running a guarded check.",
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
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
