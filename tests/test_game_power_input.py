import contextlib
import os
import time

from steamos_intel_handheld.game_power_input import (
    InputActivityMonitor,
    discover_input_event_devices,
)

# Shaped like the reference device: a virtual pad that Steam Input re-emits on
# (the only thing that actually moves during play), a physical pad, plus devices
# that advertise keys but fire from lid/power/thermal events.
PROC_DEVICES = """I: Bus=0019 Vendor=0000 Product=0005 Version=0000
N: Name="Lid Switch"
H: Handlers=event0
B: EV=21

I: Bus=0019 Vendor=0000 Product=0003 Version=0000
N: Name="Sleep Button"
H: Handlers=event1
B: EV=3

I: Bus=0011 Vendor=0001 Product=0001 Version=ab83
N: Name="AT Translated Set 2 keyboard"
H: Handlers=sysrq kbd event4 leds
B: EV=120013

I: Bus=0003 Vendor=045e Product=028e Version=0110
N: Name="Micro Star International Xbox360 Controller for Windows"
H: Handlers=js0 event9
B: EV=20000b

I: Bus=0000 Vendor=0000 Product=0000 Version=0000
N: Name="PC Speaker"
H: Handlers=kbd event10
B: EV=40001

I: Bus=0000 Vendor=0000 Product=0000 Version=0000
N: Name="sof-hda-dsp Headphone"
H: Handlers=event15
B: EV=21

I: Bus=0003 Vendor=045e Product=028e Version=0110
N: Name="Microsoft X-Box 360 pad 0"
H: Handlers=js1 event21
B: EV=20000b
"""


def _write_proc(tmp_path, text=PROC_DEVICES):
    path = tmp_path / "devices"
    path.write_text(text)
    return path


def test_discovery_keeps_input_capable_devices_and_drops_the_rest(tmp_path):
    found = discover_input_event_devices(_write_proc(tmp_path))

    assert "/dev/input/event21" in found, "the virtual pad is the signal that moves"
    assert "/dev/input/event9" in found
    assert "/dev/input/event4" in found
    # A lid or power button firing must never read as "the player is here".
    assert "/dev/input/event0" not in found
    assert "/dev/input/event1" not in found
    assert "/dev/input/event10" not in found
    assert "/dev/input/event15" not in found


def test_discovery_survives_a_missing_proc_file(tmp_path):
    assert discover_input_event_devices(tmp_path / "absent") == []


def test_monitor_reports_no_devices_rather_than_claiming_idle(tmp_path):
    monitor = InputActivityMonitor(paths=[])

    assert monitor.start() is False
    assert monitor.watched == []


def test_monitor_idle_grows_with_the_clock_and_resets_on_activity():
    now = [500.0]
    monitor = InputActivityMonitor(paths=[], clock=lambda: now[0])

    assert monitor.idle_s() == 0.0
    now[0] += 45.0
    assert monitor.idle_s() == 45.0

    monitor.mark_active()
    assert monitor.idle_s() == 0.0


def test_monitor_observes_a_real_write_through_a_pipe():
    """The reader must actually notice bytes arriving, not just tick a clock."""
    read_fd, write_fd = os.pipe()
    monitor = InputActivityMonitor(paths=[])
    monitor._fds = {read_fd: "pipe"}
    monitor._thread = None
    monitor._last_event = time.monotonic() - 30.0
    assert monitor.idle_s() >= 30.0

    import threading

    thread = threading.Thread(target=monitor._run, daemon=True)
    thread.start()
    try:
        os.write(write_fd, b"x" * 64)
        deadline = time.monotonic() + 2.0
        while monitor.idle_s() > 1.0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert monitor.idle_s() < 1.0, "an event must reset the idle timer"
    finally:
        monitor.stop()
        thread.join(timeout=2.0)
        with contextlib.suppress(OSError):
            os.close(write_fd)


# --- GPU PMU parsing -------------------------------------------------------
# The counters replace the fdinfo render-busy signal, which does not exist on
# xe. Parsing is tested against the device's real perf output shape.

_E = "xe_0000_00_02.0"
_ENG = "engine_class=0,engine_instance=0,gt=0"
PERF_OUTPUT = "\n".join(
    [
        f"1.001839263,32730852,,{_E}/engine-active-ticks,{_ENG}/,1001886153,100.00,,",
        f"1.001839263,38436310,,{_E}/engine-total-ticks,{_ENG}/,1001886567,100.00,,",
        f"1.001839263,0,ms,{_E}/gt-c6-residency,gt=0/,1001890295,100.00,,",
        f"1.001839263,1550,MHz,{_E}/gt-actual-frequency,gt=0/,1001887677,100.00,,",
    ]
) + "\n"


def test_pmu_parser_reads_real_device_output():
    from steamos_intel_handheld.game_power_gpu_pmu import parse_perf_csv

    sample = parse_perf_csv(PERF_OUTPUT, window_s=1.0)

    assert sample is not None
    assert sample.render_busy == round(32730852 / 38436310, 4)
    assert sample.c6_ms == 0.0
    assert sample.actual_mhz == 1550.0
    # 85% busy: inside the healthy band, still has slack.
    assert sample.saturated is False


def test_pmu_parser_flags_a_saturated_engine():
    from steamos_intel_handheld.game_power_gpu_pmu import GpuUtilisationSample

    # No slack left: a deeper cap from here costs frames.
    pinned = GpuUtilisationSample(
        render_busy=0.99, c6_ms=0.0, actual_mhz=1350.0, window_s=1.0
    )
    assert pinned.saturated is True

    unknown = GpuUtilisationSample(
        render_busy=None, c6_ms=None, actual_mhz=1950.0, window_s=1.0
    )
    assert unknown.saturated is None


def test_pmu_parser_returns_none_on_junk():
    from steamos_intel_handheld.game_power_gpu_pmu import parse_perf_csv

    assert parse_perf_csv("", window_s=1.0) is None
    assert parse_perf_csv("not,csv\n", window_s=1.0) is None


def test_pmu_monitor_reports_absent_pmu_rather_than_guessing(monkeypatch):
    from steamos_intel_handheld import game_power_gpu_pmu as pmu

    monkeypatch.setattr(pmu, "discover_xe_pmu", lambda *a, **k: None)
    monitor = pmu.GpuUtilisationMonitor()

    assert monitor.start() is False
    assert monitor.latest() is None
