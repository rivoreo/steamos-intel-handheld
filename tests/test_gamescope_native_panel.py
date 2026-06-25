import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gamescope_wrapper_rewrites_deck_defaults_to_native_panel_mode(tmp_path):
    sysfs_drm = tmp_path / "drm"
    connector = sysfs_drm / "card0-eDP-1"
    connector.mkdir(parents=True)
    (connector / "status").write_text("connected\n")
    (connector / "modes").write_text("1920x1200\n1280x800\n")

    captured_args = tmp_path / "args.txt"
    real_gamescope = tmp_path / "real-gamescope"
    real_gamescope.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$STEAMOS_TEST_CAPTURED_ARGS"\n'
    )
    real_gamescope.chmod(0o755)

    env = os.environ.copy()
    env["STEAMOS_INTEL_HANDHELD_SYSFS_DRM_ROOT"] = str(sysfs_drm)
    env["STEAMOS_INTEL_HANDHELD_REAL_GAMESCOPE"] = str(real_gamescope)
    env["STEAMOS_TEST_CAPTURED_ARGS"] = str(captured_args)

    subprocess.run(
        [
            str(ROOT / "data/bin/gamescope"),
            "--generate-drm-mode",
            "fixed",
            "--xwayland-count",
            "2",
            "-w",
            "1280",
            "-h",
            "800",
            "-O",
            "*,eDP-1",
        ],
        env=env,
        check=True,
    )

    assert captured_args.read_text().splitlines() == [
        "--generate-drm-mode",
        "fixed",
        "--xwayland-count",
        "2",
        "-w",
        "1920",
        "-h",
        "1200",
        "-O",
        "*,eDP-1",
    ]
