import asyncio
import json
import os


SERVICE = "steamos-intel-handheld-power-control.service"
GAME_POWER = "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power"
GAME_POWER_CONTROL = "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-control"
POLICY_LABEL = "Balanced automatic policy"
VALID_MODES = {"automatic", "observe", "off"}


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


def validate_mode(mode: str) -> str:
    if mode in VALID_MODES:
        return mode
    raise ValueError(f"unsupported game-power mode: {mode}")


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
    runtime = await _runtime_status()
    values = _parse_systemctl_show(output)
    execstart = values.get("ExecStart", "")
    runtime_mode = runtime.get("mode")
    mode = runtime_mode if runtime_mode != "default" else _mode_from_execstart(execstart)
    return {
        "active_state": values.get("ActiveState", "unknown"),
        "sub_state": values.get("SubState", "unknown"),
        "mode": mode,
        "override_active": bool(runtime.get("override_active")),
        "policy_label": runtime.get("policy_label", POLICY_LABEL),
    }


async def _runtime_status() -> dict:
    output = await _run_command(GAME_POWER_CONTROL, "status", "--json")
    return json.loads(output)


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
        mode = validate_mode(mode)
        output = await _run_command(
            GAME_POWER_CONTROL,
            "set-mode",
            mode,
            "--source",
            "decky",
            "--json",
        )
        return json.loads(output)

    async def restore_defaults(self) -> dict:
        output = await _run_command(GAME_POWER_CONTROL, "restore-defaults", "--json")
        result = json.loads(output)
        result["restored"] = True
        return result
