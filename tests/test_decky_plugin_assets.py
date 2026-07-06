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
    assert '"set_fps_target"' in frontend
    assert '"restore_defaults"' in frontend
    assert "Balance to FPS target" in frontend
    assert "Watch data only" in frontend
    assert "Stop Game Power" in frontend
    assert "Manual FPS target" in frontend
    assert "Use SteamOS limit" in frontend
    assert "Learning status" in frontend
    assert "遊戲電力" in frontend
    assert "依 FPS 目標自動平衡" in frontend
    assert "只看數據，不調整功耗" in frontend
    assert "停止遊戲電力" in frontend
    assert "手動 FPS 目標" in frontend
    assert "使用 SteamOS 限制" in frontend
    assert "學習狀態" in frontend
    assert "依 FPS 目標自動平衡" in bundled
    assert "只看數據，不調整功耗" in bundled
    assert "停止遊戲電力" in bundled
    assert "手動 FPS 目標" in bundled
    assert "使用 SteamOS 限制" in bundled
    assert "模式: automatic" not in frontend
    assert "動作: observe-only" not in frontend
    assert "模式: automatic" not in bundled
    assert "動作: observe-only" not in bundled
    assert "自動觀察" not in frontend
    assert "自動觀察" not in bundled
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
        "Balance to FPS target",
        "Target-aware balancing",
        "Learning before reuse",
        "Watch data only",
        "Sampling is stopped",
        "Frame data missing",
        "Frame data live",
        "FPS target unknown",
        "Manual FPS target",
        "Use SteamOS limit",
        "Learning status",
        "Needs stable FPS target",
        "依 FPS 目標自動平衡",
        "依 FPS 目標平衡",
        "學習中，暫不復用",
        "只看數據，不調整功耗",
        "已停止採樣",
        "缺少影格資料",
        "影格資料即時可用",
        "FPS 目標未知",
        "手動 FPS 目標",
        "使用 SteamOS 限制",
        "學習狀態",
        "需要穩定 FPS 目標",
    )
    ambiguous_copy = (
        "Monitor only",
        "Power scheduler off",
        "只監測",
        "停用調度",
        "只讀取遊戲電力資料，不改變功耗行為。",
        "不接管 CPU/GPU 功耗，交回系統處理。",
        "自動觀察",
    )

    for text in required_copy:
        assert text in frontend
        assert text in bundled
    for text in ambiguous_copy:
        assert text not in frontend
        assert text not in bundled


def test_game_power_decky_frontend_exposes_evidence_readiness_copy_and_types():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    source_only = (
        "type EvidenceReadiness",
        "evidence_readiness: EvidenceReadiness",
        "evidenceLabel: string",
        'evidenceLabel: "Local evidence"',
        'evidenceLabel: "本機證據"',
    )
    required_copy = (
        "runtime?.evidence_readiness",
        "evidenceText(t, runtime?.evidence_readiness)",
        "isTargetAwareReady(runtime?.evidence_readiness)",
        "Local evidence",
        "Local target/frame evidence ready",
        "Local evidence: power signals only",
        "Local evidence unavailable",
        "View data only",
        "Game Power stopped",
        "本機證據",
        "本機 FPS 目標與影格資料可用",
        "本機證據：僅有功耗訊號",
        "本機證據不可用",
        "只看數據",
        "遊戲電力已停止",
    )

    for text in source_only:
        assert text in frontend
    for text in required_copy:
        assert text in frontend
        assert text in bundled


def test_game_power_decky_automatic_copy_requires_evidence_readiness_claim():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()
    compact_frontend = "".join(frontend.split())
    compact_bundled = "".join(bundled.split())

    assert (
        "functionisTargetAwareReady(readiness:EvidenceReadiness|null|undefined):boolean{"
        'returnreadiness?.status==="target-aware-live"&&readiness?.claim_ready===true;'
        "}"
    ) in compact_frontend
    assert 'isTargetAwareReady(runtime?.evidence_readiness)' in frontend
    assert (
        '!runtime?.stale&&!runtime?.error&&runtime?.fps_target?.status==="known"&&'
        'runtime?.frame_source?.status==="live"'
    ) not in compact_frontend
    assert (
        '!runtime?.stale&&!runtime?.error&&runtime?.fps_target?.status==="known"&&'
        'runtime?.frame_source?.status==="live"'
    ) not in compact_bundled


def test_game_power_decky_headline_respects_observe_and_off_before_telemetry_state():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    assert 'if (mode === "off")' in frontend
    assert 'if (mode === "observe")' in frontend
    assert 'runtimeHeadline(t, status.mode, runtime)' in frontend
    compact_bundled = "".join(bundled.split())
    assert 'mode==="off"' in compact_bundled
    assert 'mode==="observe"' in compact_bundled


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


def test_game_power_decky_frontend_exposes_v10_persona_and_limiter_intent_copy():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    assert '"set_persona"' in frontend
    assert '"clear_persona"' in frontend
    assert '"limiter_status"' in frontend
    assert '"set_limiter"' in frontend
    assert '"clear_limiter"' in frontend

    required_copy = (
        "Power intent",
        "Battery saver",
        "Quiet (plugged in)",
        "Performance (plugged in)",
        "Auto (match power source)",
        "Framework shipped; tuning constants are provisional.",
        "Frame limit helper",
        "Opt-in: caps in-game frames through gamescope. Device-unverified.",
        "Apply frame limit",
        "Clear frame limit",
        "Soft power budget",
        "Frame feed",
        "電力取向",
        "電池省電",
        "安靜（外接電源）",
        "效能（外接電源）",
        "自動（依電源）",
        "影格上限輔助",
        "選用：透過 gamescope 設定遊戲影格上限。尚未在裝置驗證。",
        "套用影格上限",
        "動態功耗預算",
        "影格資料流",
    )
    for text in required_copy:
        assert text in frontend
        assert text in bundled


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


def test_game_power_decky_frontend_exposes_safe_fps_slider_not_raw_policy_knobs():
    frontend = (GAME_POWER_PLUGIN / "src" / "index.tsx").read_text()
    bundled = (GAME_POWER_PLUGIN / "dist" / "index.js").read_text()

    required = (
        'type="range"',
        "min={30}",
        "max={120}",
        "step={5}",
        "set_fps_target",
        "Manual FPS target",
        "手動 FPS 目標",
    )
    for text in required:
        assert text in frontend
    assert "手動 FPS 目標" in bundled
    assert "set_fps_target" in bundled
