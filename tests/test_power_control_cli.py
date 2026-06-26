from steamos_intel_handheld import power_control


def test_wait_and_serve_prepares_mangohud_sensors_before_wait(monkeypatch):
    events = []

    def fake_prepare(args):
        events.append("prepare")

    def fake_wait(user, timeout_s, interval_s):
        events.append("wait")

    async def fake_serve(args):
        events.append("serve")

    monkeypatch.setattr(power_control, "prepare_mangohud_sensors_from_args", fake_prepare)
    monkeypatch.setattr(power_control, "wait_for_user_steamos_manager", fake_wait)
    monkeypatch.setattr(power_control, "serve", fake_serve)

    power_control.main(["wait-and-serve", "--prepare-mangohud-sensors"])

    assert events == ["prepare", "wait", "serve"]


def test_parser_enables_guarded_msi_claw_ec_backend():
    args = power_control.build_parser().parse_args(["serve", "--apply-msi-claw-ec"])
    backend = power_control.build_backend(args)

    assert backend.apply_msi_claw_ec is True


def test_parser_configures_ec_write_debounce_ms():
    args = power_control.build_parser().parse_args(
        ["serve", "--apply-msi-claw-ec", "--ec-write-debounce-ms", "750"]
    )
    backend = power_control.build_backend(args)

    assert backend.ec_write_debounce_ms == 750


def test_parser_configures_tdp_policy_mode_and_power_source_override():
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--tdp-policy",
            "battery-maxq",
            "--power-source-override",
            "battery",
        ]
    )
    backend = power_control.build_backend(args)

    assert backend.tdp_policy_mode == power_control.TdpPolicyMode.BATTERY_MAXQ
    assert backend.power_source_override == power_control.PowerSource.BATTERY


def test_parser_configures_power_source_poll_interval():
    args = power_control.build_parser().parse_args(["serve", "--power-source-poll-s", "5"])
    backend = power_control.build_backend(args)

    assert backend.power_source_poll_s == 5.0


def test_parser_configures_msi_claw_ec_shift_policy():
    args = power_control.build_parser().parse_args(
        ["serve", "--msi-claw-ec-shift-policy", "profile"]
    )
    backend = power_control.build_backend(args)

    assert backend.msi_claw_ec_shift_policy == power_control.MsiClawEcShiftPolicy.PROFILE
