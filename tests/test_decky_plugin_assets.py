import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "decky" / "steamos-intel-handheld-ec"
GAME_POWER_PLUGIN = ROOT / "decky" / "steamos-intel-handheld-game-power"


def test_decky_plugin_has_required_manifest_files():
    assert (PLUGIN / "plugin.json").is_file()
    assert (PLUGIN / "package.json").is_file()
    assert (PLUGIN / "rollup.config.js").is_file()
    assert not (PLUGIN / "webpack.config.js").exists()
    assert (PLUGIN / "main.py").is_file()
    assert (PLUGIN / "src" / "index.tsx").is_file()


def test_decky_frontend_uses_published_decky_ui_package():
    package = json.loads((PLUGIN / "package.json").read_text())

    assert package["dependencies"]["@decky/api"] == "^1.1.3"
    assert package["devDependencies"]["@decky/ui"] == "4.11.6"
    assert package["devDependencies"]["@decky/rollup"] == "^1.0.2"
    assert package["scripts"]["build"] == "rollup -c --forceExit"


def test_decky_plugin_manifest_names_charge_limit():
    manifest = (PLUGIN / "plugin.json").read_text()

    assert '"name": "Charge Limit"' in manifest
    assert '"api_version": 1' in manifest
    assert '"root"' in manifest
    assert '"_root"' not in manifest
    assert '"main": "dist/index.js"' in manifest
    assert "Intel Handheld EC" not in manifest


def test_decky_backend_exposes_status_and_preview_functions():
    backend = (PLUGIN / "main.py").read_text()

    assert "class Plugin" in backend
    assert "async def get_status" in backend
    assert "async def preview_limit" in backend
    assert "async def apply_limit" in backend
    assert "steamos_intel_handheld.ec_charge_control" in backend
    assert "/usr/bin/python3" in backend
    assert "LD_LIBRARY_PATH" not in backend


def test_decky_frontend_contains_safe_presets_and_apply_copy():
    frontend = (PLUGIN / "src" / "index.tsx").read_text()

    assert "definePlugin" in frontend
    assert "callable" in frontend
    assert '"get_status"' in frontend
    assert '"preview_limit"' in frontend
    assert '"apply_limit"' in frontend
    assert "GetCurrentLanguage" in frontend
    assert "tchinese" in frontend
    assert "充電上限" in frontend
    assert "設為" in frontend
    assert "Battery Charge Limit" in frontend
    assert "Set" in frontend
    assert "60%" in frontend
    assert "80%" in frontend
    assert "100%" in frontend
    assert "Unknown" not in frontend
    assert "EC status unavailable" not in frontend
    assert "Intel Handheld EC" not in frontend


def test_game_power_decky_plugin_has_separate_required_files():
    assert GAME_POWER_PLUGIN != PLUGIN
    assert (GAME_POWER_PLUGIN / "plugin.json").is_file()
    assert (GAME_POWER_PLUGIN / "package.json").is_file()
    assert (GAME_POWER_PLUGIN / "rollup.config.js").is_file()
    assert not (GAME_POWER_PLUGIN / "webpack.config.js").exists()
    assert (GAME_POWER_PLUGIN / "main.py").is_file()
    assert (GAME_POWER_PLUGIN / "src" / "index.tsx").is_file()
    assert (GAME_POWER_PLUGIN / "dist" / "index.js").is_file()


def test_game_power_decky_manifest_names_game_power_panel():
    manifest = json.loads((GAME_POWER_PLUGIN / "plugin.json").read_text())

    assert manifest["name"] == "Game Power"
    assert manifest["api_version"] == 1
    assert "root" in manifest["flags"]
    assert manifest["main"] == "dist/index.js"
    assert "performance" in manifest["publish"]["tags"]
    assert "charge-limit" not in manifest["publish"]["tags"]


def _game_power_frontend() -> tuple[str, str]:
    return (
        (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text(),
        (GAME_POWER_PLUGIN / "dist" / "index.js").read_text(),
    )


def test_game_power_decky_frontend_wires_the_backend_api():
    frontend, _ = _game_power_frontend()

    for name in (
        '"get_status"',
        '"sample_once"',
        '"set_mode"',
        '"set_fps_target"',
        '"restore_defaults"',
        '"set_persona"',
        '"clear_persona"',
        '"limiter_status"',
        '"set_limiter"',
        '"clear_limiter"',
    ):
        assert name in frontend
    assert "definePlugin" in frontend
    assert "callable" in frontend


def test_game_power_decky_frontend_uses_native_decky_controls():
    """A QAM panel is driven with a gamepad, so it must use Decky's own
    focusable controls rather than raw HTML inputs."""
    frontend, bundled = _game_power_frontend()

    for component in ("DropdownItem", "ToggleField"):
        assert component in frontend
        assert component in bundled
    # A raw range input cannot be focused or nudged with the D-pad.
    assert 'type="range"' not in frontend


def test_game_power_decky_merges_mode_and_persona_into_one_profile_control():
    """mode and persona are orthogonal in the daemon but persona silently does
    nothing unless mode is automatic; the panel must not expose that trap."""
    frontend, bundled = _game_power_frontend()

    assert "type Profile =" in frontend
    assert "PROFILE_TO_PERSONA" in frontend
    english = ("Automatic", "Save battery", "Quiet", "Performance", "Watch only", "Off")
    chinese = ("自動", "省電", "安靜", "效能", "只觀察", "關閉")
    for text in english + chinese:
        assert text in frontend
        assert text in bundled


def test_game_power_decky_status_headline_is_plain_language_in_both_locales():
    frontend, bundled = _game_power_frontend()

    english = (
        "Holding steady",
        "Full power",
        "Waiting for a game",
        "Turned off",
        "Watching only",
    )
    chinese = ("穩定維持中", "全力輸出", "等待遊戲中", "已關閉", "只觀察")
    for text in english + chinese:
        assert text in frontend
        assert text in bundled


def test_game_power_decky_headline_respects_off_and_observe_before_telemetry_state():
    """Off/observe are user intent and must win over whatever the last runtime
    snapshot happened to say."""
    frontend, bundled = _game_power_frontend()
    compact = "".join(frontend.split())

    assert "functionheadlineText(" in compact
    off = compact.index('control.mode==="off"')
    observe = compact.index('control.mode==="observe"')
    telemetry = compact.index("if(!runtime||runtime.error)")
    assert off < telemetry
    assert observe < telemetry
    assert 'mode==="off"' in "".join(bundled.split())


def test_game_power_decky_technical_readouts_are_opt_in():
    """Ladder steps, gated lanes and verdict ledgers are debugging output. They
    may exist, but only behind an explicitly disabled-by-default toggle."""
    frontend, bundled = _game_power_frontend()

    assert "const [showDiagnostics, setShowDiagnostics] = useState(false)" in frontend
    assert "{showDiagnostics ? (" in frontend
    assert "Show technical details" in frontend
    assert "顯示技術細節" in frontend
    assert "顯示技術細節" in bundled
    # Everything technical must render behind the gate, not merely be defined
    # behind it: check the always-visible JSX of the panel itself.
    panel_jsx = frontend.split("const GamePowerPanel")[1]
    always_on = panel_jsx.split("{showDiagnostics ? (")[0]
    assert "<Diagnostics" not in always_on
    assert "t.diag." not in always_on


def test_game_power_decky_offers_a_reachable_target_when_the_scene_cannot_hold_one():
    """Device evidence 2026-07-31: a scene sat below a 60 FPS target at full
    power for minutes. Burning full power forever is the wrong answer; the panel
    should say so and offer a target the scene can actually hold."""
    frontend, bundled = _game_power_frontend()

    assert "unreachableSuggestion" in frontend
    assert "starvedPolls" in frontend
    # Only counts when nothing of ours is applied -- otherwise we blame the game
    # for our own trims.
    assert "trim_rungs_active?.length ?? 0) === 0" in frontend
    assert "Target looks out of reach" in frontend
    assert "目標似乎達不到" in frontend
    assert "目標似乎達不到" in bundled


def test_game_power_decky_fps_target_uses_backend_options_and_never_seeds_a_literal():
    frontend, bundled = _game_power_frontend()

    # The offered targets come from the daemon (exact divisors of the live
    # refresh rate), filtered by the backend's supported_* contract -- never from
    # literals in the frontend. There is no working VRR on the reference panel,
    # so an off-divisor target judders however well it is scheduled.
    assert "runtime?.auto_target?.candidates" in frontend
    assert "fps >= supportedMin && fps <= supportedMax" in frontend
    # "Automatic" is an explicit option, not the absence of a choice.
    assert '{ data: "auto"' in frontend
    # The slider must never seed itself with a literal FPS value: a hardcoded
    # default renders as a real target the user never chose, and one tap on
    # "use this target" commits it (device report 2026-07-31: panel showed 40).
    seed = re.search(r"const \[manualFps, setManualFps\] = (useState[^;]*);", frontend)
    assert seed is not None
    assert seed.group(1) == "useState<number | null>(null)"
    assert "set_fps_target" in bundled

def test_game_power_decky_backend_exposes_safe_mode_api():
    backend = (GAME_POWER_PLUGIN / "main.py").read_text()

    assert "class Plugin" in backend
    assert "async def get_status" in backend
    assert "async def sample_once" in backend
    assert "async def set_mode" in backend
    assert "async def set_fps_target" in backend
    assert "async def restore_defaults" in backend
    assert "RUNTIME_SNAPSHOT" in backend
    assert "steamos-intel-handheld-game-power-control" in backend
    assert "steamos-intel-handheld-power-control.service" in backend
    assert "70-game-power-decky.conf" not in backend
    assert "ExecStart=" not in backend
    assert "systemctl\", \"restart" not in backend
    assert "--game-power-pcore-max-mhz" not in backend
    assert "--game-power-ecore-max-mhz" not in backend
    assert "/usr/bin/python3" not in backend
    assert "LD_LIBRARY_PATH" not in backend


def test_game_power_decky_frontend_v10_copy_avoids_raw_knob_vocabulary():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    for forbidden in (
        "P-core",
        "E-core",
        "pcore",
        "ecore",
        "frequency",
        "freq",
        "threshold",
        "uclamp",
        "CPUWeight",
        "PL2",
        "Tau",
        "affinity",
    ):
        assert forbidden not in frontend
        assert forbidden not in bundled


def test_game_power_decky_backend_exposes_persona_and_limiter_api():
    backend = (GAME_POWER_PLUGIN / "main.py").read_text()

    assert "async def set_persona" in backend
    assert "async def clear_persona" in backend
    assert "async def limiter_status" in backend
    assert "async def set_limiter" in backend
    assert "async def clear_limiter" in backend
    # The limiter helper hops to the gamescope session user; the daemon never
    # calls it. Root Decky backend uses the same runuser + env shape as scripts.
    assert "runuser" in backend
    assert "XDG_RUNTIME_DIR=" in backend
    assert "DBUS_SESSION_BUS_ADDRESS=" in backend
    assert '"set-persona"' in backend
    assert '"clear-persona"' in backend


