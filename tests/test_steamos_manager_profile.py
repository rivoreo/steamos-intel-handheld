from pathlib import Path

import tomllib

from steamos_intel_handheld import restore_etc, steamos_manager_profile

VALVE_PROFILE = """[[device]]
dmi.sys_vendor = "Micro-Star International Co., Ltd."
dmi.board_name = "MS-1T52"
device = "claw"
variant = "Claw 8 AI+ A2VM"
friendly_name = "MSI Claw"

[gpu_performance]
driver = "intel"

[inputplumber]
target_devices = ["deck-uhid", "keyboard"]
"""


def write_upstream(system_root: Path, text: str | None) -> None:
    path = system_root / steamos_manager_profile.UPSTREAM_PROFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text)


def test_composed_profile_carries_every_section_upstream_declares():
    """The bug this design exists to prevent: our profile replaces Valve's, so
    anything of theirs missing from ours is silently switched off. Losing
    [inputplumber] stopped the deck-uhid and keyboard targets being created and
    killed the handheld's volume keys."""
    decision = steamos_manager_profile.decide(VALVE_PROFILE)
    assert decision.should_exist
    composed = tomllib.loads(str(decision.content))

    upstream = tomllib.loads(VALVE_PROFILE)
    for section, value in upstream.items():
        assert composed[section] == value, section
    assert composed["tdp_limit"]["method"] == "remote"


def test_a_section_valve_adds_later_is_inherited_without_any_change_here():
    """The whole point: no hand-maintained copy to fall behind."""
    future = VALVE_PROFILE + '\n[something_new]\nkey = "value"\n'
    composed = tomllib.loads(str(steamos_manager_profile.decide(future).content))
    assert composed["something_new"] == {"key": "value"}
    assert composed["tdp_limit"]["method"] == "remote"


def test_we_step_aside_once_upstream_declares_its_own_tdp_method():
    """If Valve fixes this themselves, overriding their profile stops being a
    fix and becomes a liability."""
    upstream = VALVE_PROFILE + '\n[tdp_limit]\nmethod = "firmware_attribute"\n'
    decision = steamos_manager_profile.decide(upstream)
    assert not decision.should_exist
    assert "upstream" in decision.reason


def test_no_profile_is_written_when_there_is_nothing_to_extend():
    decision = steamos_manager_profile.decide(None)
    assert not decision.should_exist


def test_restore_writes_the_composed_profile_and_relocks_the_partition(tmp_path):
    system_root = tmp_path / "system"
    write_upstream(system_root, VALVE_PROFILE)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "manifest.toml").write_text("")
    runner = restore_etc.RecordingRunner()

    result = restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=artifact_root,
        apply=True,
        runner=runner,
        run_actions=False,
        system_root=system_root,
    )

    composed = steamos_manager_profile.composed_path(system_root)
    assert composed.is_file()
    assert "inputplumber" in composed.read_text()
    assert result.changed
    commands = [restore_etc.command_to_string(c) for c in runner.commands]
    assert commands.index("steamos-readonly disable") < commands.index("steamos-readonly enable")


def test_restore_is_a_no_op_once_the_profile_already_matches(tmp_path):
    system_root = tmp_path / "system"
    write_upstream(system_root, VALVE_PROFILE)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "manifest.toml").write_text("")

    for expected_changed in (True, False):
        runner = restore_etc.RecordingRunner()
        result = restore_etc.restore(
            etc_root=tmp_path / "etc",
            artifact_root=artifact_root,
            apply=True,
            runner=runner,
            run_actions=False,
            system_root=system_root,
        )
        assert result.changed is expected_changed
        if not expected_changed:
            assert not any("steamos-readonly" in c for c in runner.commands)


def test_restore_removes_our_profile_once_upstream_declares_tdp_itself(tmp_path):
    system_root = tmp_path / "system"
    write_upstream(system_root, VALVE_PROFILE)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "manifest.toml").write_text("")
    restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=artifact_root,
        apply=True,
        runner=restore_etc.RecordingRunner(),
        run_actions=False,
        system_root=system_root,
    )
    assert steamos_manager_profile.composed_path(system_root).is_file()

    write_upstream(system_root, VALVE_PROFILE + '\n[tdp_limit]\nmethod = "amdgpu_hwmon"\n')
    restore_etc.restore(
        etc_root=tmp_path / "etc",
        artifact_root=artifact_root,
        apply=True,
        runner=restore_etc.RecordingRunner(),
        run_actions=False,
        system_root=system_root,
    )
    assert not steamos_manager_profile.composed_path(system_root).exists()


def test_the_offered_range_matches_what_the_daemon_will_honour():
    """A slider position the daemon clamps away is a control that lies."""
    unit = (
        Path(__file__).resolve().parents[1]
        / "data/systemd/steamos-intel-handheld-power-control.service"
    ).read_text()
    assert f"--min-w {steamos_manager_profile.TDP_MIN_W}" in unit
    assert f"--max-w {steamos_manager_profile.TDP_MAX_W}" in unit
