import json
from pathlib import Path

from steamos_intel_handheld.game_power_coloring import (
    Color,
    ColorThresholds,
    RoleObservation,
    aggregate_role_observations,
    build_color_ledger,
    build_color_ledger_from_artifacts,
    cap_thread_samples,
    classify_role_color,
    resolve_ledger_actuators,
    role_key_for,
)

FG = "0::/user.slice/.../app-steam-app1903340-1.scope"
GAMESCOPE = "0::/user.slice/.../gamescope-session.service"
HELPER = "/user.slice/user@1000.service/steamwebhelper.service"


def obs(**over) -> RoleObservation:
    base = dict(
        role_key="foreground-game:worker",
        cgroup=FG,
        comm="worker",
        tid_count=1,
        cpu_time_ms_delta=1000.0,
        runqueue_wait_ms_delta=0.0,
        timeslices_delta=100,
        cpus_seen=(0, 1),
        window_s=10.0,
        restore_covered=True,
        stable=True,
    )
    base.update(over)
    return RoleObservation(**base)


def test_role_key_uses_cgroup_role_and_normalized_comm():
    assert role_key_for(FG, "Worker Thread") == "foreground-game:worker-thread"
    assert role_key_for(GAMESCOPE, "gamescope") == "gamescope-helper:gamescope"


def test_classify_color_a_latency_hot_foreground_role():
    color = classify_role_color(
        obs(runqueue_wait_ms_delta=30.0, cpu_time_ms_delta=500.0),
        appid="1903340",
    )
    assert color == Color.A


def test_q1_classify_normalizes_run_length_windows_to_per_window_rates():
    # 300 s profiler aggregate: a cool role accrues raw deltas that exceed the
    # per-10s-window thresholds (3000 ms cpu, 300 ms wait) but is only
    # 10 ms/s cpu and 1 ms/s wait -- it must NOT classify as Color A.
    cool = obs(
        cpu_time_ms_delta=3000.0,
        runqueue_wait_ms_delta=300.0,
        timeslices_delta=3000,  # 0.1 ms wait per slice, below per-slice guard
        window_s=300.0,
    )
    assert classify_role_color(cool, appid="1903340") != Color.A

    # A genuinely hot role at the same run length still classifies A:
    # 50 ms/s cpu (500/window) and 3 ms/s wait (30/window).
    hot = obs(
        cpu_time_ms_delta=15_000.0,
        runqueue_wait_ms_delta=900.0,
        timeslices_delta=9000,
        window_s=300.0,
    )
    assert classify_role_color(hot, appid="1903340") == Color.A


def test_q1_daemon_window_classification_unchanged():
    # At the reference 10 s window the normalized rates equal the raw deltas,
    # so the daemon-side classification is bit-identical to before.
    color = classify_role_color(
        obs(runqueue_wait_ms_delta=30.0, cpu_time_ms_delta=500.0, window_s=10.0),
        appid="1903340",
    )
    assert color == Color.A


def test_classify_color_b_throughput_wide_foreground_role():
    color = classify_role_color(
        obs(
            tid_count=6,
            cpu_time_ms_delta=4000.0,
            runqueue_wait_ms_delta=1.0,
            timeslices_delta=4000,
            cpus_seen=(0, 1, 2, 3),
        ),
        appid="1903340",
    )
    assert color == Color.B


def test_classify_color_c_compositor_never_shaped():
    color = classify_role_color(
        obs(role_key="gamescope-helper:gamescope", cgroup=GAMESCOPE, comm="gamescope"),
        appid="1903340",
    )
    assert color == Color.C


def test_classify_color_d_background_helper_shapable():
    color = classify_role_color(
        obs(
            role_key="steam-helper:steamwebhelper",
            cgroup=HELPER,
            comm="steamwebhelper",
            cpu_time_ms_delta=2000.0,
        ),
        appid="1903340",
    )
    assert color == Color.D


def test_classify_color_e_for_role_without_restore_coverage():
    color = classify_role_color(
        obs(runqueue_wait_ms_delta=30.0, restore_covered=False),
        appid="1903340",
    )
    assert color == Color.E


def test_color_c_entry_is_observe_only_even_with_active_actuators():
    ledger = build_color_ledger(
        [obs(role_key="gamescope-helper:gamescope", cgroup=GAMESCOPE, comm="gamescope")],
        appid="1903340",
        active_actuators={"uclamp-min", "bg-weight"},
    )
    entry = ledger.entries[0]
    assert entry.color == Color.C
    assert entry.actuator == "observe-only"
    assert entry.actuator_state == "active"
    assert entry.blocking_reason_codes == ()


def test_gated_color_a_is_blocked_without_verdict_then_active_when_unlocked():
    hot = obs(runqueue_wait_ms_delta=30.0)
    blocked = build_color_ledger([hot], appid="1903340")
    assert blocked.entries[0].actuator == "uclamp-min"
    assert blocked.entries[0].actuator_state == "blocked"
    assert blocked.entries[0].blocking_reason_codes == ("no-verdict-for-context",)

    unlocked = resolve_ledger_actuators(
        blocked.entries, active_actuators={"uclamp-min"}
    )
    assert unlocked[0].actuator_state == "active"
    assert unlocked[0].blocking_reason_codes == ()


def test_profiler_ledger_reports_gated_actuators_as_advisory():
    hot = obs(runqueue_wait_ms_delta=30.0)
    ledger = build_color_ledger([hot], appid="1903340", advisory=True)
    assert ledger.entries[0].actuator_state == "advisory"


def test_cap_thread_samples_keeps_top_budget_and_marks_truncated():
    samples = [
        {"tid": i, "cpu_time_ms_delta": float(i), "cgroup": FG, "comm": "w"}
        for i in range(5)
    ]
    kept, truncated = cap_thread_samples(samples, budget=3)
    assert truncated is True
    assert sorted(s["tid"] for s in kept) == [2, 3, 4]


def test_aggregate_role_observations_sums_threads_and_unions_cpus():
    samples = [
        {
            "tid": 1,
            "comm": "worker",
            "cgroup": FG,
            "cpu_time_ms_delta": 500.0,
            "runqueue_wait_ms_delta": 10.0,
            "timeslices_delta": 50,
            "cpus_seen": [0, 1],
        },
        {
            "tid": 2,
            "comm": "worker",
            "cgroup": FG,
            "cpu_time_ms_delta": 300.0,
            "runqueue_wait_ms_delta": 5.0,
            "timeslices_delta": 30,
            "cpus_seen": [2],
        },
    ]
    observations = aggregate_role_observations(samples, window_s=10.0)
    assert len(observations) == 1
    role = observations[0]
    assert role.tid_count == 2
    assert role.cpu_time_ms_delta == 800.0
    assert role.cpus_seen == (0, 1, 2)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_build_color_ledger_from_artifacts_is_deterministic(tmp_path):
    schedstat = tmp_path / "thread-schedstat.jsonl"
    _write_jsonl(
        schedstat,
        [
            {
                "threads": [
                    {
                        "tid": 10,
                        "comm": "RenderThread",
                        "cgroup": FG,
                        "run_time_ns": 0,
                        "runqueue_wait_ns": 0,
                        "timeslices": 0,
                        "current_cpu": 0,
                    }
                ]
            },
            {
                "threads": [
                    {
                        "tid": 10,
                        "comm": "RenderThread",
                        "cgroup": FG,
                        "run_time_ns": 800_000_000,
                        "runqueue_wait_ns": 40_000_000,
                        "timeslices": 500,
                        "current_cpu": 1,
                    }
                ]
            },
        ],
    )
    cgroups = tmp_path / "process-cgroups.jsonl"

    def _helper(cpu):
        proc = {"pid": 99, "comm": "steamwebhelper", "cgroup": HELPER, "cpu_time_s": cpu}
        return {"processes": [proc]}

    _write_jsonl(cgroups, [_helper(0.0), _helper(3.0)])

    ledger = build_color_ledger_from_artifacts(
        appid="1903340",
        window_s=10.0,
        thread_schedstat_jsonl=schedstat,
        process_cgroups_jsonl=cgroups,
    )
    colors = {entry.role_key: entry.color for entry in ledger.entries}
    assert colors["foreground-game:renderthread"] == Color.A
    assert colors["steam-helper:steamwebhelper"] == Color.D
    # Deterministic ordering: run twice, identical JSON.
    again = build_color_ledger_from_artifacts(
        appid="1903340",
        window_s=10.0,
        thread_schedstat_jsonl=schedstat,
        process_cgroups_jsonl=cgroups,
    )
    assert ledger.to_json() == again.to_json()


def test_q3_iter_jsonl_rows_tolerates_truncated_final_line(tmp_path):
    from steamos_intel_handheld.game_power_coloring import iter_jsonl_rows

    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n{"b": 2}\n{"c": 3, "trunc')
    assert list(iter_jsonl_rows(path)) == [{"a": 1}, {"b": 2}]


def test_q3_iter_jsonl_rows_rejects_malformed_middle_line(tmp_path):
    import pytest

    from steamos_intel_handheld.game_power_coloring import iter_jsonl_rows

    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\nnot-json\n{"b": 2}\n')
    with pytest.raises(json.JSONDecodeError):
        list(iter_jsonl_rows(path))


def test_thresholds_are_configurable():
    strict = ColorThresholds(latency_cpu_ms_per_window=100_000.0)
    color = classify_role_color(
        obs(runqueue_wait_ms_delta=30.0), appid="1903340", thresholds=strict
    )
    # CPU threshold too high to be latency-hot -> falls through to E.
    assert color == Color.E
