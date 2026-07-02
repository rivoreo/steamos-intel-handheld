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


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "harness.toml"


def load_manifest() -> dict[str, Any]:
    text = MANIFEST.read_text()
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
    manifest = load_manifest()
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


def run_check(args: argparse.Namespace) -> int:
    manifest = load_manifest()
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
    completed = subprocess.run(check["command"], cwd=ROOT, shell=True, env=os.environ.copy())
    finished = datetime.now(timezone.utc)
    payload = {
        "id": check["id"],
        "command": check["command"],
        "returncode": completed.returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(time.monotonic() - start, 3),
        "requirements": requirements,
    }
    if args.report:
        write_report(Path(args.report), payload)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List and run project harness checks.")
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

    return parser


def main(argv: Optional[list[str]] = None) -> int:  # noqa: UP045 - keep direct Python 3.9 use.
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
