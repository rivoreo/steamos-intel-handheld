from pathlib import Path

import pytest

from steamos_intel_handheld import restore_etc


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def write_manifest(root: Path, text: str) -> None:
    write_file(root / "manifest.toml", text)


def artifact_root(tmp_path: Path) -> Path:
    root = tmp_path / "opt" / "steamos-intel-handheld" / "share" / "etc-artifacts"
    root.mkdir(parents=True)
    return root


def test_load_manifest_merges_fragments_and_rejects_duplicate_destinations(tmp_path):
    root = artifact_root(tmp_path)
    write_file(root / "payload.conf", "canonical\n")
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/example.conf"
source = "payload.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["systemd-system"]
""",
    )
    write_file(
        root / "manifest.d" / "10-dup.toml",
        """
[[artifact]]
destination = "/etc/example.conf"
source = "other.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = []
""",
    )

    with pytest.raises(restore_etc.ManifestError, match="duplicate destination"):
        restore_etc.load_manifest(root)


def test_manifest_rejects_sources_outside_artifact_root(tmp_path):
    root = artifact_root(tmp_path)
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/example.conf"
source = "../outside.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = []
""",
    )

    with pytest.raises(restore_etc.ManifestError, match="source escapes artifact root"):
        restore_etc.load_manifest(root)


def test_manifest_rejects_unsafe_symlink_target(tmp_path):
    root = artifact_root(tmp_path)
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/systemd/user/example.service.wants/example.service"
target = "../../example.service"
type = "symlink"
policy = "managed"
owner = "root"
group = "root"
actions = ["systemd-user"]
""",
    )

    with pytest.raises(restore_etc.ManifestError, match="unsafe symlink target"):
        restore_etc.load_manifest(root)


def test_check_reports_missing_managed_file_and_planned_actions(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    write_file(root / "dbus-1/system.d/example.conf", "canonical\n")
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/dbus-1/system.d/example.conf"
source = "dbus-1/system.d/example.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["dbus-system"]
""",
    )

    result = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=False,
        runner=restore_etc.RecordingRunner(user_bus_exists=True),
    )

    assert result.changed is False
    assert result.failures == []
    assert result.actions == ["dbus-system"]
    assert result.artifacts[0].destination == "/etc/dbus-1/system.d/example.conf"
    assert result.artifacts[0].state == "missing"
    assert not (etc_root / "dbus-1/system.d/example.conf").exists()


def test_apply_restores_missing_file_with_mode(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    write_file(root / "NetworkManager/dispatcher.d/90-rncn-steamdeck-wg", "#!/bin/sh\n", 0o755)
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg"
source = "NetworkManager/dispatcher.d/90-rncn-steamdeck-wg"
type = "file"
policy = "managed"
mode = "0755"
owner = "root"
group = "root"
actions = ["networkmanager-dispatcher"]
""",
    )

    result = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=True,
        runner=restore_etc.RecordingRunner(user_bus_exists=True),
    )

    restored = etc_root / "NetworkManager/dispatcher.d/90-rncn-steamdeck-wg"
    assert result.changed is True
    assert result.failures == []
    assert restored.read_text() == "#!/bin/sh\n"
    assert restored.stat().st_mode & 0o777 == 0o755


def test_apply_restores_steamos_manager_remote_without_restarting_user_manager(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    write_file(root / "steamos-manager/remotes.d/99-rivoreo-power-control.toml", "canonical\n")
    write_file(etc_root / "steamos-manager/remotes.d/99-rivoreo-power-control.toml", "drifted\n")
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml"
source = "steamos-manager/remotes.d/99-rivoreo-power-control.toml"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = []
""",
    )

    runner = restore_etc.RecordingRunner(user_bus_exists=True)
    result = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=True,
        runner=runner,
    )

    assert result.changed is True
    assert (etc_root / "steamos-manager/remotes.d/99-rivoreo-power-control.toml").read_text() == (
        "canonical\n"
    )
    assert result.actions == []
    assert not runner.commands


def test_apply_repairs_symlink_artifact(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    display_wants = (
        "/etc/systemd/user/gamescope-session.service.wants/"
        "steamos-intel-handheld-gamescope-display.service"
    )
    write_manifest(
        root,
        f"""
[[artifact]]
destination = "{display_wants}"
target = "../steamos-intel-handheld-gamescope-display.service"
type = "symlink"
policy = "managed"
owner = "root"
group = "root"
actions = ["systemd-user"]
""",
    )
    wrong = (
        etc_root
        / "systemd/user/gamescope-session.service.wants"
        / "steamos-intel-handheld-gamescope-display.service"
    )
    write_file(wrong, "not a symlink\n")

    result = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=True,
        runner=restore_etc.RecordingRunner(user_bus_exists=True),
    )

    assert result.changed is True
    assert wrong.is_symlink()
    assert wrong.readlink() == Path("../steamos-intel-handheld-gamescope-display.service")


def test_health_check_reports_wireguard_config_without_restoring(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/wireguard/rncn-steamdeck.conf"
type = "file"
policy = "health-check"
owner = "root"
group = "root"
actions = []
health_services = ["wg-quick@rncn-steamdeck.service"]
""",
    )

    result = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=True,
        runner=restore_etc.RecordingRunner(active_services=set()),
    )

    assert result.changed is False
    assert result.failures == []
    assert result.artifacts[0].state == "missing-health-check"
    assert any("/etc/wireguard/rncn-steamdeck.conf" in warning for warning in result.warnings)
    assert not (etc_root / "wireguard/rncn-steamdeck.conf").exists()


def test_user_bus_failures_are_warnings_but_system_reload_failure_is_fatal(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    write_file(root / "systemd/system/example.service", "[Service]\nExecStart=/bin/true\n")
    write_file(root / "systemd/user/example.service", "[Service]\nExecStart=/bin/true\n")
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/systemd/system/example.service"
source = "systemd/system/example.service"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["systemd-system"]

[[artifact]]
destination = "/etc/systemd/user/example.service"
source = "systemd/user/example.service"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["systemd-user"]
service_restarts = ["example.service"]
""",
    )
    fatal_runner = restore_etc.RecordingRunner(
        user_bus_exists=True,
        fail_commands={"systemctl daemon-reload"},
    )

    fatal = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=True,
        runner=fatal_runner,
    )

    assert fatal.changed is True
    assert any("systemctl daemon-reload" in failure for failure in fatal.failures)

    warning_root = tmp_path / "warning"
    warning_runner = restore_etc.RecordingRunner(
        user_bus_exists=True,
        fail_commands={"systemctl --user daemon-reload", "systemctl --user --no-block try-restart"},
    )

    warning = restore_etc.restore(
        etc_root=warning_root / "etc",
        artifact_root=root,
        apply=True,
        runner=warning_runner,
    )

    assert warning.failures == []
    assert any("systemctl --user daemon-reload" in item for item in warning.warnings)
    assert any(
        "systemctl --user --no-block try-restart example.service" in item
        for item in warning.warnings
    )


def test_user_systemd_actions_are_bounded_and_restart_without_waiting(tmp_path):
    root = artifact_root(tmp_path)
    etc_root = tmp_path / "etc"
    write_file(root / "systemd/user/example.service", "[Service]\nExecStart=/bin/true\n")
    write_manifest(
        root,
        """
[[artifact]]
destination = "/etc/systemd/user/example.service"
source = "systemd/user/example.service"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["systemd-user"]
service_restarts = ["example.service"]
""",
    )
    runner = restore_etc.RecordingRunner(user_bus_exists=True)

    result = restore_etc.restore(
        etc_root=etc_root,
        artifact_root=root,
        apply=True,
        runner=runner,
    )

    assert result.failures == []
    assert [
        "runuser",
        "-u",
        "deck",
        "--",
        "env",
        "XDG_RUNTIME_DIR=/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
        "systemctl",
        "--user",
        "--no-block",
        "try-restart",
        "example.service",
    ] in runner.commands
    assert all(timeout is not None for timeout in runner.command_timeouts[-2:])


SYSTEM_ARTIFACT = """
[[artifact]]
destination = "/usr/share/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml"
source = "steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = []
"""


def _system_manifest(tmp_path: Path) -> tuple[Path, Path]:
    root = artifact_root(tmp_path)
    write_file(root / "steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml", "profile\n")
    write_manifest(root, SYSTEM_ARTIFACT)
    system_root = tmp_path / "system"
    (system_root / "usr/share/steamos-manager/devices").mkdir(parents=True)
    return root, system_root


def test_destination_allowlist_is_exact_directories_not_a_usr_prefix():
    """A manifest typo must fail to parse rather than hand the restore service
    write access to the system partition."""
    restore_etc._validate_destination("/etc/systemd/system/x.service")
    restore_etc._validate_destination("/usr/share/steamos-manager/devices/x.toml")

    for rejected in (
        "/usr/bin/gamescope",
        "/usr/share/steamos-manager/config.toml",
        "/usr/share/steamos-manager/devices/nested/x.toml",
        "/usr/share/steamos-manager/devices/../../bin/x",
        "/usr",
        "relative/path.toml",
    ):
        with pytest.raises(restore_etc.ManifestError):
            restore_etc._validate_destination(rejected)


def test_missing_system_partition_file_is_restored_with_the_partition_unlocked(tmp_path):
    root, system_root = _system_manifest(tmp_path)
    runner = restore_etc.RecordingRunner()

    result = restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=root,
        apply=True,
        runner=runner,
        run_actions=False,
        system_root=system_root,
    )

    live = system_root / "usr/share/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml"
    assert live.read_text() == "profile\n"
    assert result.changed
    # Unlocked before the write and locked again afterwards, in that order.
    commands = [restore_etc.command_to_string(c) for c in runner.commands]
    assert "steamos-readonly disable" in commands
    assert "steamos-readonly enable" in commands
    assert commands.index("steamos-readonly disable") < commands.index("steamos-readonly enable")


def test_unchanged_system_partition_file_never_touches_steamos_readonly(tmp_path):
    """Unlocking the system partition on every boot would be a real cost for a
    file that is almost always already correct."""
    root, system_root = _system_manifest(tmp_path)
    live = system_root / "usr/share/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml"
    write_file(live, "profile\n")
    runner = restore_etc.RecordingRunner()

    result = restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=root,
        apply=True,
        runner=runner,
        run_actions=False,
        system_root=system_root,
    )

    assert not result.changed
    assert not any("steamos-readonly" in restore_etc.command_to_string(c) for c in runner.commands)


def test_the_partition_is_locked_again_even_when_the_restore_fails(tmp_path):
    root, system_root = _system_manifest(tmp_path)
    (root / "steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml").unlink()
    runner = restore_etc.RecordingRunner()

    result = restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=root,
        apply=True,
        runner=runner,
        run_actions=False,
        system_root=system_root,
    )

    assert result.failures
    commands = [restore_etc.command_to_string(c) for c in runner.commands]
    if "steamos-readonly disable" in commands:
        assert "steamos-readonly enable" in commands


def test_a_locked_partition_warns_instead_of_failing_the_whole_restore(tmp_path):
    """Everything under /etc still has to be repaired even if the system
    partition will not unlock."""
    root, system_root = _system_manifest(tmp_path)
    write_file(root / "payload.conf", "canonical\n")
    write_manifest(
        root,
        SYSTEM_ARTIFACT
        + """
[[artifact]]
destination = "/etc/example.conf"
source = "payload.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = []
""",
    )
    runner = restore_etc.RecordingRunner(fail_commands={"steamos-readonly disable"})

    result = restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=root,
        apply=True,
        runner=runner,
        run_actions=False,
        system_root=system_root,
    )

    assert (tmp_path / "etc/example.conf").read_text() == "canonical\n"
    assert any("could not unlock" in warning for warning in result.warnings)
