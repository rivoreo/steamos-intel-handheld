import asyncio
import json
import os
import shlex


SERVICE = "steamos-intel-handheld-power-control.service"
DROPIN_DIR = f"/run/systemd/system/{SERVICE}.d"
DROPIN_PATH = f"{DROPIN_DIR}/70-game-power-decky.conf"
POWER_CONTROL = "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control"
GAME_POWER = "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power"
POLICY_LABEL = "Balanced automatic policy"


def _clean_env() -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin"}
    if "LANG" in os.environ:
        env["LANG"] = os.environ["LANG"]
    return env


async def _run_command(*cmd: str, input_text: str | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        env=_clean_env(),
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(
        input_text.encode() if input_text is not None else None
    )
    if process.returncode != 0:
        message = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(message or f"{cmd[0]} failed with {process.returncode}")
    return stdout.decode()


def mode_to_service_args(mode: str) -> list[str]:
    if mode == "off":
        return ["--game-power-mode", "off"]
    if mode == "observe":
        return ["--game-power-mode", "observe"]
    if mode == "automatic":
        return [
            "--game-power-mode",
            "gpu-priority",
            "--game-power-cpu-cap",
            "on",
            "--game-power-pcore-max-mhz",
            "3000",
            "--game-power-ecore-max-mhz",
            "2400",
            "--game-power-cpu-cap-core-share-threshold",
            "0.30",
        ]
    raise ValueError(f"unsupported game-power mode: {mode}")


def _service_args(mode: str) -> list[str]:
    return [
        POWER_CONTROL,
        "wait-and-serve",
        "--user",
        "deck",
        "--bus",
        "system",
        "--apply-rapl",
        "--apply-msi-claw-ec",
        "--ec-write-debounce-ms",
        "750",
        "--tdp-policy",
        "auto",
        "--msi-claw-ec-shift-policy",
        "tdp-threshold",
        "--prepare-mangohud-sensors",
        *mode_to_service_args(mode),
        "--min-w",
        "8",
        "--max-w",
        "30",
        "--short-limit-max-w",
        "37",
        "--state-file",
        "/var/lib/steamos-intel-handheld/tdp_w",
    ]


def _dropin_text(mode: str) -> str:
    command = " ".join(shlex.quote(arg) for arg in _service_args(mode))
    return f"[Service]\nExecStart=\nExecStart={command}\n"


def _parse_systemctl_show(output: str) -> dict[str, str]:
    values = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _mode_from_execstart(execstart: str) -> str:
    parts = execstart.split()
    try:
        raw = parts[parts.index("--game-power-mode") + 1]
    except (ValueError, IndexError):
        return "unknown"
    if raw == "gpu-priority":
        return "automatic"
    if raw in {"off", "observe"}:
        return raw
    return "unknown"


async def _service_status() -> dict:
    output = await _run_command(
        "systemctl",
        "show",
        SERVICE,
        "-p",
        "ActiveState",
        "-p",
        "SubState",
        "-p",
        "ExecStart",
        "--no-pager",
    )
    values = _parse_systemctl_show(output)
    execstart = values.get("ExecStart", "")
    return {
        "active_state": values.get("ActiveState", "unknown"),
        "sub_state": values.get("SubState", "unknown"),
        "mode": _mode_from_execstart(execstart),
        "override_active": os.path.exists(DROPIN_PATH),
        "policy_label": POLICY_LABEL,
    }


async def _sample_once() -> dict:
    output = await _run_command(
        GAME_POWER,
        "--mode",
        "observe",
        "--duration-s",
        "2",
        "--poll-s",
        "1",
        "--output-format",
        "jsonl",
    )
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    return {
        "appid": None,
        "action": "observe-only",
        "reason": "no foreground game sample",
        "package_w": None,
        "core_w": None,
        "uncore_w": None,
        "pl1_w": None,
        "render_busy": None,
    }


async def _restart_service() -> None:
    await _run_command("systemctl", "daemon-reload")
    await _run_command("systemctl", "restart", SERVICE)


class Plugin:
    async def _main(self) -> None:
        pass

    async def _unload(self) -> None:
        pass

    async def get_status(self) -> dict:
        return {"service": await _service_status()}

    async def sample_once(self) -> dict:
        return await _sample_once()

    async def set_mode(self, mode: str) -> dict:
        mode_to_service_args(mode)
        await _run_command("install", "-d", "-m", "0755", DROPIN_DIR)
        await _run_command("tee", DROPIN_PATH, input_text=_dropin_text(mode))
        await _restart_service()
        return {
            "mode": mode,
            "policy_label": POLICY_LABEL,
            "override_active": True,
        }

    async def restore_defaults(self) -> dict:
        await _run_command("rm", "-f", DROPIN_PATH)
        await _restart_service()
        return {"restored": True, "policy_label": POLICY_LABEL}
