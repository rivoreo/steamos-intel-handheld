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
