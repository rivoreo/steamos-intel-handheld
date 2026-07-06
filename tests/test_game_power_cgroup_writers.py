from pathlib import Path

import pytest

from steamos_intel_handheld.game_power_cgroup_writers import (
    ForegroundUclampMinWriter,
    apply_background_shaping_to_cgroups,
    is_background_shaping_write_target,
    restore_background_shaping_from_report,
)


def _make_cgroup(root: Path, name: str, uclamp_min: str = "0.00") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "cpu.uclamp.min").write_text(f"{uclamp_min}\n")
    return path


def test_foreground_uclamp_min_writer_records_and_restores_original(tmp_path):
    cgroup = _make_cgroup(tmp_path, "fg", uclamp_min="0.00")
    writer = ForegroundUclampMinWriter(floor_value="25.00")

    applied = writer.apply(cgroup)

    assert applied["status"] == "written"
    assert applied["original_value"] == "0.00"
    assert (cgroup / "cpu.uclamp.min").read_text().strip() == "25.00"
    assert writer.active is True

    restored = writer.restore()

    assert restored["status"] == "restored"
    assert (cgroup / "cpu.uclamp.min").read_text().strip() == "0.00"
    assert writer.active is False


def test_foreground_uclamp_min_writer_apply_is_idempotent_and_holds(tmp_path):
    cgroup = _make_cgroup(tmp_path, "fg", uclamp_min="0.00")
    writer = ForegroundUclampMinWriter()

    writer.apply(cgroup)
    second = writer.apply(cgroup)

    assert second["status"] == "held"
    assert second["original_value"] == "0.00"


def test_foreground_uclamp_min_writer_latches_failed_when_file_missing(tmp_path):
    cgroup = tmp_path / "fg"
    cgroup.mkdir()

    writer = ForegroundUclampMinWriter()
    result = writer.apply(cgroup)

    assert result["status"] == "write-unavailable"
    assert writer.failed is True
    assert writer.apply(cgroup)["status"] == "disabled"


def test_background_shaping_only_lowers_allowlisted_helper_cgroups(tmp_path):
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "cpu.uclamp.max").write_text("max\n")
    game = tmp_path / "game"
    game.mkdir()
    (game / "cpu.uclamp.max").write_text("max\n")

    cgroups = [
        {
            "cgroup": "/user.slice/steamwebhelper.service",
            "path": str(helper),
        },
        {
            "cgroup": "/user.slice/app-steam-app1903340-1.scope",
            "path": str(game),
        },
    ]

    report = apply_background_shaping_to_cgroups(
        cgroups,
        appid="1903340",
        variant="uclamp-max-85",
    )

    written = {item["cgroup"]: item for item in report["writes"]}
    assert list(written) == ["/user.slice/steamwebhelper.service"]
    assert (helper / "cpu.uclamp.max").read_text().strip() == "85.00"
    assert (game / "cpu.uclamp.max").read_text().strip() == "max"

    restore = restore_background_shaping_from_report(report)

    assert restore["restored"] is True
    assert (helper / "cpu.uclamp.max").read_text().strip() == "max"


def test_is_background_shaping_write_target_excludes_foreground_and_bare_slices():
    assert is_background_shaping_write_target(
        "/user.slice/steamwebhelper.service", appid="1903340"
    )
    assert not is_background_shaping_write_target(
        "/user.slice/app-steam-app1903340-1.scope", appid="1903340"
    )
    assert not is_background_shaping_write_target("0::/user.slice", appid="1903340")


def test_apply_background_shaping_rejects_unknown_variant():
    with pytest.raises(ValueError):
        apply_background_shaping_to_cgroups([], appid="1", variant="nope")


def test_c16_apply_and_restore_foreground_uclamp_min_writes(tmp_path):
    import json

    from steamos_intel_handheld.game_power_cgroup_writers import (
        apply_foreground_uclamp_min_writes,
        restore_foreground_uclamp_min_writes,
    )

    fg = _make_cgroup(tmp_path, "fg", uclamp_min="0.00")
    snapshot = tmp_path / "restore-affinity.json"
    snapshot.write_text(
        json.dumps(
            {
                "cgroups": [
                    {
                        "cgroup": "/user.slice/app-steam-app1903340-1.scope",
                        "path": str(fg),
                    },
                    {
                        "cgroup": "/user.slice/steamwebhelper.service",
                        "path": str(tmp_path / "helper"),
                    },
                ]
            }
        )
    )
    writes_json = tmp_path / "foreground-uclamp-writes.json"
    report = apply_foreground_uclamp_min_writes(
        snapshot, writes_json, appid="1903340"
    )
    assert report["valid"] is True
    assert (fg / "cpu.uclamp.min").read_text().strip() == "25.00"
    persisted = json.loads(writes_json.read_text())
    assert persisted["write"]["original_value"] == "0.00"

    restore_json = tmp_path / "foreground-uclamp-restore.json"
    restore = restore_foreground_uclamp_min_writes(writes_json, restore_json)
    assert restore["restored"] is True
    assert restore["valid"] is True
    assert (fg / "cpu.uclamp.min").read_text().strip() == "0.00"
    assert json.loads(restore_json.read_text())["restored"] is True


def test_c16_foreground_uclamp_min_writes_invalid_without_foreground_cgroup(tmp_path):
    import json

    from steamos_intel_handheld.game_power_cgroup_writers import (
        apply_foreground_uclamp_min_writes,
    )

    snapshot = tmp_path / "restore-affinity.json"
    snapshot.write_text(json.dumps({"cgroups": []}))
    writes_json = tmp_path / "foreground-uclamp-writes.json"
    report = apply_foreground_uclamp_min_writes(
        snapshot, writes_json, appid="1903340"
    )
    assert report["valid"] is False
    assert report["skip_reason"] == "foreground-cgroup-not-found"


def test_c8_apply_on_new_path_restores_previous_path(tmp_path):
    a = _make_cgroup(tmp_path, "a", uclamp_min="0.00")
    b = _make_cgroup(tmp_path, "b", uclamp_min="1.00")
    writer = ForegroundUclampMinWriter(floor_value="25.00")

    writer.apply(a)
    assert (a / "cpu.uclamp.min").read_text().strip() == "25.00"

    result = writer.apply(b)
    assert result["status"] == "written"
    # The previous path must be restored to its original before switching.
    assert (a / "cpu.uclamp.min").read_text().strip() == "0.00"
    assert (b / "cpu.uclamp.min").read_text().strip() == "25.00"
    assert writer.active is True

    writer.restore()
    assert (b / "cpu.uclamp.min").read_text().strip() == "1.00"
    assert writer.failed is False


def test_c9_restore_failure_latches_failed_and_keeps_record(tmp_path):
    cgroup = _make_cgroup(tmp_path, "fg", uclamp_min="0.00")
    writer = ForegroundUclampMinWriter(floor_value="25.00")
    writer.apply(cgroup)

    # Force the restore write to raise by turning the control file into a dir.
    ctrl = cgroup / "cpu.uclamp.min"
    ctrl.unlink()
    ctrl.mkdir()

    result = writer.restore()
    assert result["status"] == "restore-failed"
    assert writer.failed is True
    # The record is kept so an unrestored floor stays visible in telemetry.
    assert writer.active is True
