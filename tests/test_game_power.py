from steamos_intel_handheld.game_power import (
    EnergyReading,
    GamePowerMode,
    RaplPowerWindow,
    compute_rapl_power_window,
)


def test_compute_rapl_power_window_converts_energy_delta_to_watts():
    start = EnergyReading(
        timestamp_s=10.0,
        energy_uj={
            "package": 100_000_000,
            "core": 40_000_000,
            "uncore": 30_000_000,
            "dram": 2_000_000,
            "psys": 140_000_000,
        },
    )
    end = EnergyReading(
        timestamp_s=20.0,
        energy_uj={
            "package": 319_111_133,
            "core": 125_587_122,
            "uncore": 104_545_525,
            "dram": 6_523_548,
            "psys": 450_515_380,
        },
    )

    window = compute_rapl_power_window(start, end)

    assert isinstance(window, RaplPowerWindow)
    assert window.duration_s == 10.0
    assert round(window.package_w, 2) == 21.91
    assert round(window.core_w, 2) == 8.56
    assert round(window.uncore_w, 2) == 7.45
    assert round(window.dram_w, 2) == 0.45
    assert round(window.psys_w, 2) == 31.05
    assert round(window.core_share, 2) == 0.39
    assert round(window.uncore_share, 2) == 0.34


def test_compute_rapl_power_window_rejects_non_positive_duration():
    start = EnergyReading(timestamp_s=10.0, energy_uj={"package": 1})
    end = EnergyReading(timestamp_s=10.0, energy_uj={"package": 2})

    try:
        compute_rapl_power_window(start, end)
    except ValueError as exc:
        assert "positive duration" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_game_power_mode_values_are_stable_for_cli_and_service_config():
    assert [mode.value for mode in GamePowerMode] == ["off", "observe", "gpu-priority"]
