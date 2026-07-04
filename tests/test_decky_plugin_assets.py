import json
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


def test_game_power_decky_frontend_exposes_intent_not_raw_policy_knobs():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    assert "definePlugin" in frontend
    assert "callable" in frontend
    assert '"get_status"' in frontend
    assert '"sample_once"' in frontend
    assert '"set_mode"' in frontend
    assert '"restore_defaults"' in frontend
    assert "Balance CPU/GPU" in frontend
    assert "View data only" in frontend
    assert "Turn scheduler off" in frontend
    assert "遊戲電力" in frontend
    assert "平衡 CPU/GPU" in frontend
    assert "只看數據" in frontend
    assert "完全停用" in frontend
    assert "平衡 CPU/GPU" in bundled
    assert "只看數據" in bundled
    assert "完全停用" in bundled
    assert "模式: automatic" not in frontend
    assert "動作: observe-only" not in frontend
    assert "模式: automatic" not in bundled
    assert "動作: observe-only" not in bundled
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


def test_game_power_decky_mode_copy_explains_control_state_differences():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    required_copy = (
        "Balances CPU and GPU power while a game is running.",
        "Keeps sampling and decisions visible without changing power settings.",
        "Stops game-power sampling and leaves power behavior to the system.",
        "遊戲執行時自動平衡 CPU 與 GPU 功耗。",
        "保留採樣與判斷，只顯示數據，不改變功耗設定。",
        "停止遊戲電力採樣與調度，交回系統處理。",
    )
    ambiguous_copy = (
        "Monitor only",
        "Power scheduler off",
        "只監測",
        "停用調度",
        "只讀取遊戲電力資料，不改變功耗行為。",
        "不接管 CPU/GPU 功耗，交回系統處理。",
    )

    for text in required_copy:
        assert text in frontend
        assert text in bundled
    for text in ambiguous_copy:
        assert text not in frontend
        assert text not in bundled


def test_game_power_decky_backend_exposes_safe_mode_api():
    backend = (GAME_POWER_PLUGIN / "main.py").read_text()

    assert "class Plugin" in backend
    assert "async def get_status" in backend
    assert "async def sample_once" in backend
    assert "async def set_mode" in backend
    assert "async def restore_defaults" in backend
    assert "steamos-intel-handheld-game-power-control" in backend
    assert "steamos-intel-handheld-power-control.service" in backend
    assert "70-game-power-decky.conf" not in backend
    assert "ExecStart=" not in backend
    assert "systemctl\", \"restart" not in backend
    assert "--game-power-pcore-max-mhz" not in backend
    assert "--game-power-ecore-max-mhz" not in backend
    assert "/usr/bin/python3" not in backend
    assert "LD_LIBRARY_PATH" not in backend
