import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "decky" / "steamos-intel-handheld-ec"


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
