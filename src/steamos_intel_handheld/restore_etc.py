#!/usr/bin/env python3
"""Restore SteamOS /etc integration files from package-owned canonical payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

DEFAULT_ARTIFACT_ROOT = Path("/opt/steamos-intel-handheld/share/etc-artifacts")
DEFAULT_ETC_ROOT = Path("/etc")

SUPPORTED_TYPES = {"file", "symlink"}
SUPPORTED_POLICIES = {"managed", "health-check"}
ACTION_ORDER = ("systemd-system", "dbus-system", "systemd-user", "networkmanager-dispatcher")
USER_BUS_ENV = [
    "XDG_RUNTIME_DIR=/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
]


class ManifestError(ValueError):
    """Raised when a restore manifest is invalid."""


class RestoreFailure(RuntimeError):
    """Raised when restore execution cannot proceed."""


@dataclass(frozen=True)
class Artifact:
    destination: str
    artifact_type: str
    policy: str
    source: str | None
    symlink_target: str | None
    mode: int | None
    owner: str
    group: str
    actions: tuple[str, ...] = ()
    service_restarts: tuple[str, ...] = ()
    health_services: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactStatus:
    destination: str
    artifact_type: str
    policy: str
    state: str
    changed: bool = False
    message: str = ""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner:
    """Small system command boundary for production and tests."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, args: list[str]) -> CommandResult:
        raise NotImplementedError

    def user_bus_exists(self) -> bool:
        return Path("/run/user/1000/bus").exists()

    def is_active(self, service: str) -> bool:
        result = self.run(["systemctl", "is-active", "--quiet", service])
        return result.ok


class SubprocessRunner(CommandRunner):
    def run(self, args: list[str]) -> CommandResult:
        self.commands.append(list(args))
        completed = subprocess.run(args, check=False, capture_output=True, text=True)
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class RecordingRunner(CommandRunner):
    def __init__(
        self,
        *,
        user_bus_exists: bool = False,
        fail_commands: set[str] | None = None,
        active_services: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._user_bus_exists = user_bus_exists
        self.fail_commands = fail_commands or set()
        self.active_services = active_services

    def run(self, args: list[str]) -> CommandResult:
        self.commands.append(list(args))
        command = command_to_string(args)
        for needle in self.fail_commands:
            if needle in command:
                return CommandResult(returncode=1, stderr=f"forced failure: {command}")
        return CommandResult(returncode=0)

    def user_bus_exists(self) -> bool:
        return self._user_bus_exists

    def is_active(self, service: str) -> bool:
        if self.active_services is None:
            return True
        return service in self.active_services


@dataclass
class RestoreResult:
    changed: bool = False
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    artifacts: list[ArtifactStatus] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    @property
    def has_unresolved_managed_artifacts(self) -> bool:
        return any(
            artifact.policy == "managed"
            and artifact.state in {"missing", "drifted", "source-missing", "restore-failed"}
            for artifact in self.artifacts
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "failures": self.failures,
            "warnings": self.warnings,
            "actions": self.actions,
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
            "commands": self.commands,
        }


def command_to_string(args: list[str]) -> str:
    return " ".join(args)


def load_manifest(artifact_root: Path = DEFAULT_ARTIFACT_ROOT) -> list[Artifact]:
    artifact_root = Path(artifact_root)
    manifest_paths = [artifact_root / "manifest.toml"]
    fragment_dir = artifact_root / "manifest.d"
    if fragment_dir.is_dir():
        manifest_paths.extend(sorted(fragment_dir.glob("*.toml")))

    artifacts: list[Artifact] = []
    seen_destinations: set[str] = set()
    for manifest_path in manifest_paths:
        if not manifest_path.exists():
            if manifest_path.name == "manifest.toml":
                raise ManifestError(f"manifest not found: {manifest_path}")
            continue
        payload = tomllib.loads(manifest_path.read_text())
        raw_artifacts = payload.get("artifact", [])
        if not isinstance(raw_artifacts, list):
            raise ManifestError(f"artifact table must be a list in {manifest_path}")
        for raw in raw_artifacts:
            artifact = _parse_artifact(raw, artifact_root, manifest_path)
            if artifact.destination in seen_destinations:
                raise ManifestError(f"duplicate destination: {artifact.destination}")
            seen_destinations.add(artifact.destination)
            artifacts.append(artifact)
    return artifacts


def _parse_artifact(raw: object, artifact_root: Path, manifest_path: Path) -> Artifact:
    if not isinstance(raw, dict):
        raise ManifestError(f"artifact entry must be a table in {manifest_path}")

    destination = _required_str(raw, "destination", manifest_path)
    _validate_destination(destination)

    artifact_type = raw.get("type", "file")
    if artifact_type not in SUPPORTED_TYPES:
        raise ManifestError(f"unsupported artifact type for {destination}: {artifact_type}")

    policy = raw.get("policy", "managed")
    if policy not in SUPPORTED_POLICIES:
        raise ManifestError(f"unsupported policy for {destination}: {policy}")

    source = raw.get("source")
    symlink_target = raw.get("target")
    mode = _parse_mode(raw.get("mode"), destination)

    if artifact_type == "file" and policy == "managed":
        if not isinstance(source, str) or not source:
            raise ManifestError(f"managed file requires source: {destination}")
        _validate_source(source, artifact_root, destination)
    elif source is not None and not isinstance(source, str):
        raise ManifestError(f"source must be a string for {destination}")

    if artifact_type == "symlink":
        if not isinstance(symlink_target, str) or not symlink_target:
            raise ManifestError(f"symlink artifact requires target: {destination}")
        _validate_symlink_target(destination, symlink_target)
    elif symlink_target is not None:
        raise ManifestError(f"target is only supported for symlink artifacts: {destination}")

    owner = raw.get("owner", "root")
    group = raw.get("group", "root")
    if not isinstance(owner, str) or not isinstance(group, str):
        raise ManifestError(f"owner and group must be strings for {destination}")

    return Artifact(
        destination=destination,
        artifact_type=str(artifact_type),
        policy=str(policy),
        source=source if isinstance(source, str) else None,
        symlink_target=symlink_target if isinstance(symlink_target, str) else None,
        mode=mode,
        owner=owner,
        group=group,
        actions=_string_tuple(raw.get("actions", []), "actions", destination),
        service_restarts=_string_tuple(
            raw.get("service_restarts", []), "service_restarts", destination
        ),
        health_services=_string_tuple(
            raw.get("health_services", []), "health_services", destination
        ),
    )


def _required_str(raw: dict[str, object], key: str, manifest_path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{key} must be a non-empty string in {manifest_path}")
    return value


def _validate_destination(destination: str) -> None:
    path = PurePosixPath(destination)
    if not path.is_absolute() or len(path.parts) < 3 or path.parts[1] != "etc":
        raise ManifestError(f"destination must be under /etc: {destination}")
    if ".." in path.parts:
        raise ManifestError(f"destination must not contain parent references: {destination}")


def _validate_source(source: str, artifact_root: Path, destination: str) -> None:
    source_path = PurePosixPath(source)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise ManifestError(f"source escapes artifact root for {destination}: {source}")
    root = artifact_root.resolve()
    candidate = (artifact_root / source).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"source escapes artifact root for {destination}: {source}") from exc


def _validate_symlink_target(destination: str, target: str) -> None:
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        raise ManifestError(f"unsafe symlink target for {destination}: {target}")

    dest_parent = PurePosixPath(destination).parent
    allowed_root = dest_parent.parent
    normalized = PurePosixPath(posixpath.normpath(str(dest_parent / target)))
    if normalized == allowed_root:
        return
    if str(normalized).startswith(f"{allowed_root}/"):
        return
    raise ManifestError(f"unsafe symlink target for {destination}: {target}")


def _parse_mode(value: object, destination: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 8)
        except ValueError as exc:
            raise ManifestError(f"invalid mode for {destination}: {value}") from exc
    raise ManifestError(f"invalid mode for {destination}: {value}")


def _string_tuple(value: object, key: str, destination: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{key} must be a list for {destination}")
    if not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{key} entries must be strings for {destination}")
    return tuple(value)


def restore(
    *,
    etc_root: Path = DEFAULT_ETC_ROOT,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    apply: bool,
    runner: CommandRunner | None = None,
    run_actions: bool = True,
) -> RestoreResult:
    runner = runner or SubprocessRunner()
    artifacts = load_manifest(artifact_root)
    result = RestoreResult()
    planned_actions: list[str] = []
    planned_restarts: list[str] = []

    for artifact in artifacts:
        status = _process_artifact(artifact, Path(etc_root), Path(artifact_root), apply)
        result.artifacts.append(status)
        if status.changed:
            result.changed = True
        if status.state in {"source-missing", "restore-failed"}:
            result.failures.append(status.message)
        if artifact.policy == "health-check":
            _record_health_status(artifact, status, runner, result)
        if _artifact_needs_actions(status):
            planned_actions.extend(artifact.actions)
            planned_restarts.extend(artifact.service_restarts)

    result.actions = _ordered_unique_actions(planned_actions)
    if apply and run_actions and not result.failures:
        _run_actions(result.actions, _ordered_unique(planned_restarts), runner, result)
    result.commands = [command_to_string(command) for command in runner.commands]
    return result


def _process_artifact(
    artifact: Artifact,
    etc_root: Path,
    artifact_root: Path,
    apply: bool,
) -> ArtifactStatus:
    if artifact.policy == "health-check":
        destination = _destination_path(etc_root, artifact.destination)
        if destination.exists() or destination.is_symlink():
            return ArtifactStatus(
                destination=artifact.destination,
                artifact_type=artifact.artifact_type,
                policy=artifact.policy,
                state="present-health-check",
            )
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state="missing-health-check",
            message=f"health-check file is missing: {artifact.destination}",
        )
    if artifact.artifact_type == "file":
        return _process_file_artifact(artifact, etc_root, artifact_root, apply)
    if artifact.artifact_type == "symlink":
        return _process_symlink_artifact(artifact, etc_root, apply)
    raise RestoreFailure(f"unsupported artifact type: {artifact.artifact_type}")


def _process_file_artifact(
    artifact: Artifact,
    etc_root: Path,
    artifact_root: Path,
    apply: bool,
) -> ArtifactStatus:
    destination = _destination_path(etc_root, artifact.destination)
    source = artifact_root / str(artifact.source)
    if not source.exists():
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state="source-missing",
            message=f"canonical source missing for {artifact.destination}: {source}",
        )

    if not destination.exists() and not destination.is_symlink():
        state = "missing"
    elif destination.is_symlink() or _sha256(source) != _sha256(destination):
        state = "drifted"
    else:
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state="present",
        )

    if not apply:
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state=state,
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        shutil.copyfile(source, destination)
        if artifact.mode is not None:
            destination.chmod(artifact.mode)
        _chown_if_root(destination, artifact.owner, artifact.group)
    except OSError as exc:
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state="restore-failed",
            message=f"failed to restore {artifact.destination}: {exc}",
        )

    return ArtifactStatus(
        destination=artifact.destination,
        artifact_type=artifact.artifact_type,
        policy=artifact.policy,
        state="restored",
        changed=True,
    )


def _process_symlink_artifact(
    artifact: Artifact,
    etc_root: Path,
    apply: bool,
) -> ArtifactStatus:
    destination = _destination_path(etc_root, artifact.destination)
    expected_target = artifact.symlink_target or ""
    if destination.is_symlink() and os.readlink(destination) == expected_target:
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state="present",
        )

    state = "drifted" if destination.exists() or destination.is_symlink() else "missing"
    if not apply:
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state=state,
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        os.symlink(expected_target, destination)
    except OSError as exc:
        return ArtifactStatus(
            destination=artifact.destination,
            artifact_type=artifact.artifact_type,
            policy=artifact.policy,
            state="restore-failed",
            message=f"failed to restore {artifact.destination}: {exc}",
        )

    return ArtifactStatus(
        destination=artifact.destination,
        artifact_type=artifact.artifact_type,
        policy=artifact.policy,
        state="restored",
        changed=True,
    )


def _destination_path(etc_root: Path, destination: str) -> Path:
    relative = PurePosixPath(destination).relative_to("/etc")
    return etc_root.joinpath(*relative.parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chown_if_root(path: Path, owner: str, group: str) -> None:
    if os.geteuid() != 0:
        return
    import grp
    import pwd

    os.chown(path, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid)


def _record_health_status(
    artifact: Artifact,
    status: ArtifactStatus,
    runner: CommandRunner,
    result: RestoreResult,
) -> None:
    if status.state == "missing-health-check":
        result.warnings.append(status.message)
    for service in artifact.health_services:
        if not runner.is_active(service):
            result.warnings.append(f"health-check service is inactive: {service}")


def _artifact_needs_actions(status: ArtifactStatus) -> bool:
    return status.policy == "managed" and status.state in {"missing", "drifted", "restored"}


def _ordered_unique_actions(actions: list[str]) -> list[str]:
    output: list[str] = []
    for action in ACTION_ORDER:
        if action in actions and action not in output:
            output.append(action)
    for action in actions:
        if action not in output:
            output.append(action)
    return output


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _run_actions(
    actions: list[str],
    service_restarts: list[str],
    runner: CommandRunner,
    result: RestoreResult,
) -> None:
    if "systemd-system" in actions:
        _run_fatal(["systemctl", "daemon-reload"], runner, result)
    if "dbus-system" in actions:
        _run_fatal(
            [
                "busctl",
                "call",
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ReloadConfig",
            ],
            runner,
            result,
        )
    if "systemd-user" in actions or service_restarts:
        _run_user_actions(service_restarts, runner, result)


def _run_fatal(args: list[str], runner: CommandRunner, result: RestoreResult) -> None:
    command = command_to_string(args)
    completed = runner.run(args)
    if not completed.ok:
        result.failures.append(f"{command} failed: {completed.stderr or completed.stdout}".strip())


def _run_user_actions(
    service_restarts: list[str],
    runner: CommandRunner,
    result: RestoreResult,
) -> None:
    if not runner.user_bus_exists():
        result.warnings.append("deck user bus is not active; skipped user systemd reload")
        return
    _run_warning(
        [
            "runuser",
            "-u",
            "deck",
            "--",
            "env",
            *USER_BUS_ENV,
            "systemctl",
            "--user",
            "daemon-reload",
        ],
        runner,
        result,
    )
    for service in service_restarts:
        _run_warning(
            [
                "runuser",
                "-u",
                "deck",
                "--",
                "env",
                *USER_BUS_ENV,
                "systemctl",
                "--user",
                "try-restart",
                service,
            ],
            runner,
            result,
        )


def _run_warning(args: list[str], runner: CommandRunner, result: RestoreResult) -> None:
    command = command_to_string(args)
    completed = runner.run(args)
    if not completed.ok:
        result.warnings.append(f"{command} failed: {completed.stderr or completed.stdout}".strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report drift without writing")
    mode.add_argument("--apply", action="store_true", help="restore managed artifacts")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--etc-root", type=Path, default=DEFAULT_ETC_ROOT)
    parser.add_argument("--skip-actions", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = restore(
            etc_root=args.etc_root,
            artifact_root=args.artifact_root,
            apply=args.apply,
            run_actions=not args.skip_actions,
        )
    except ManifestError as exc:
        result = RestoreResult(failures=[str(exc)])
        _print_result(result, args.json, error=True)
        return 2

    _print_result(result, args.json, error=bool(result.failures))
    if result.failures:
        return 1
    if result.has_unresolved_managed_artifacts:
        return 1
    return 0


def _print_result(result: RestoreResult, as_json: bool, *, error: bool) -> None:
    if as_json:
        print(json.dumps(result.to_json_dict(), sort_keys=True))
        return
    output = sys.stderr if error else sys.stdout
    print(f"changed={str(result.changed).lower()}", file=output)
    for artifact in result.artifacts:
        print(f"{artifact.destination}={artifact.state}", file=output)
    for warning in result.warnings:
        print(f"warning={warning}", file=output)
    for failure in result.failures:
        print(f"failure={failure}", file=output)


if __name__ == "__main__":
    raise SystemExit(main())
