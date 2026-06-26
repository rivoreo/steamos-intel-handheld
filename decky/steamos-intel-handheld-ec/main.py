import asyncio
import json
import os


PYTHON = "/usr/bin/python3"
PYTHONPATH = "/opt/steamos-intel-handheld/src"
EC_CONTROL_MODULE = "steamos_intel_handheld.ec_charge_control"


def _clean_env() -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": PYTHONPATH,
    }
    if "LANG" in os.environ:
        env["LANG"] = os.environ["LANG"]
    return env


async def _run_ec_control(*args: str) -> dict:
    process = await asyncio.create_subprocess_exec(
        PYTHON,
        "-m",
        EC_CONTROL_MODULE,
        *args,
        "--json",
        env=_clean_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(message or f"{EC_CONTROL_MODULE} failed with {process.returncode}")
    return json.loads(stdout.decode())


class Plugin:
    async def _main(self) -> None:
        pass

    async def _unload(self) -> None:
        pass

    async def get_status(self) -> dict:
        return await _run_ec_control("status")

    async def preview_limit(self, limit: int) -> dict:
        return await _run_ec_control("preview", str(limit))

    async def apply_limit(self, limit: int) -> dict:
        return await _run_ec_control("apply", str(limit))
