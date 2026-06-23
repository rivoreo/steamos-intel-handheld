#!/usr/bin/env python3
"""SteamOS Manager remote TDP provider for Intel RAPL devices."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

BUS_NAME = "org.rivoreo.SteamOSManager.PowerControl"
OBJ_PATH = "/org/rivoreo/SteamOSManager/PowerControl"
IFACE_REMOTE = "com.steampowered.SteamOSManager1.RemoteInterface1"
IFACE_TDP = "com.steampowered.SteamOSManager1.TdpLimit1"

DEFAULT_MIN_W = 5
DEFAULT_MAX_W = 37
DEFAULT_STATE_FILE = "/var/lib/steamos-intel-handheld/tdp_w"
RAPL_DOMAIN_NAMES = ("intel-rapl:0", "intel-rapl-mmio:0")


class TdpRangeError(ValueError):
    """Raised when a requested TDP is outside the configured range."""


@dataclass(frozen=True)
class TdpLimits:
    pl1_uw: int
    pl2_uw: int


def compute_tdp_limits(watts: int, max_w: int, pl2_w: int | None = None) -> TdpLimits:
    """Return PL1/PL2 limits in microwatts for a requested TDP."""

    watts = int(watts)
    short_term_w = int(max_w if pl2_w is None else pl2_w)
    short_term_w = min(int(max_w), max(watts, short_term_w))
    return TdpLimits(pl1_uw=watts * 1_000_000, pl2_uw=short_term_w * 1_000_000)


class TdpBackend:
    """Hardware-facing TDP backend with an injectable sysfs root for tests."""

    def __init__(
        self,
        min_w: int = DEFAULT_MIN_W,
        max_w: int = DEFAULT_MAX_W,
        state_file: str | Path = DEFAULT_STATE_FILE,
        apply_rapl: bool = True,
        sysfs_root: str | Path = "/sys",
        pl2_w: int | None = None,
    ) -> None:
        self.min_w = int(min_w)
        self.max_w = int(max_w)
        self.state_file = Path(state_file)
        self.apply_rapl = bool(apply_rapl)
        self.sysfs_root = Path(sysfs_root)
        self.pl2_w = int(pl2_w) if pl2_w is not None else None
        if self.min_w <= 0 or self.max_w < self.min_w:
            raise ValueError(f"invalid TDP range {self.min_w}-{self.max_w}W")
        if self.pl2_w is not None and self.pl2_w <= 0:
            raise ValueError(f"invalid PL2 wattage {self.pl2_w}; expected > 0")

    def read_limit_w(self) -> int:
        state_limit = self._read_state_file()
        if state_limit is not None:
            return state_limit

        for domain in self.rapl_domains():
            pl1_file = domain / "constraint_0_power_limit_uw"
            try:
                return self._clamp_watts(int(pl1_file.read_text().strip()) // 1_000_000)
            except (OSError, ValueError):
                continue

        return self.max_w

    def write_limit_w(self, watts: int) -> None:
        watts = int(watts)
        self._validate_watts(watts)
        self._write_state_file(watts)
        if self.apply_rapl:
            self.apply_limit_to_rapl(watts)

    def restore_state_to_rapl(self) -> int | None:
        watts = self._read_state_file()
        if watts is None:
            return None
        self.apply_limit_to_rapl(watts)
        return watts

    def apply_limit_to_rapl(self, watts: int) -> None:
        watts = int(watts)
        self._validate_watts(watts)
        limits = compute_tdp_limits(watts, self.max_w, self.pl2_w)

        for domain in self.rapl_domains():
            pl1_file = domain / "constraint_0_power_limit_uw"
            pl2_file = domain / "constraint_1_power_limit_uw"
            if not pl1_file.exists():
                continue
            pl1_file.write_text(str(limits.pl1_uw))
            if pl2_file.exists():
                pl2_file.write_text(str(limits.pl2_uw))

    def rapl_domains(self) -> Iterable[Path]:
        powercap = self.sysfs_root / "class" / "powercap"
        for domain_name in RAPL_DOMAIN_NAMES:
            domain = powercap / domain_name
            if domain.exists():
                yield domain

    def _read_state_file(self) -> int | None:
        try:
            watts = int(self.state_file.read_text().strip())
        except (OSError, ValueError):
            return None
        if self.min_w <= watts <= self.max_w:
            return watts
        return None

    def _write_state_file(self, watts: int) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(str(watts))

    def _validate_watts(self, watts: int) -> None:
        if not self.min_w <= watts <= self.max_w:
            raise TdpRangeError(f"TDP {watts}W outside {self.min_w}-{self.max_w}W")

    def _clamp_watts(self, watts: int) -> int:
        return max(self.min_w, min(self.max_w, watts))


def wait_for_user_steamos_manager(user: str, timeout_s: int, interval_s: float) -> None:
    uid = _uid_for_user(user)
    runtime_dir = Path("/run/user") / str(uid)
    bus_address = f"unix:path={runtime_dir}/bus"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        if (runtime_dir / "bus").is_socket() and _user_service_active(
            user=user,
            runtime_dir=runtime_dir,
            bus_address=bus_address,
            service="steamos-manager",
        ):
            return
        time.sleep(interval_s)

    raise TimeoutError(f"timed out waiting for {user} user steamos-manager")


def _uid_for_user(user: str) -> int:
    result = subprocess.run(
        ["id", "-u", user],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def _user_service_active(user: str, runtime_dir: Path, bus_address: str, service: str) -> bool:
    result = subprocess.run(
        [
            "runuser",
            "-u",
            user,
            "--",
            "env",
            f"XDG_RUNTIME_DIR={runtime_dir}",
            f"DBUS_SESSION_BUS_ADDRESS={bus_address}",
            "systemctl",
            "--user",
            "is-active",
            "--quiet",
            service,
        ],
        check=False,
    )
    return result.returncode == 0


async def serve(args: argparse.Namespace) -> None:
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType, PropertyAccess
    from dbus_next.service import ServiceInterface, dbus_property

    backend = TdpBackend(
        min_w=args.min_w,
        max_w=args.max_w,
        state_file=args.state_file,
        apply_rapl=args.apply_rapl,
        sysfs_root=args.sysfs_root,
        pl2_w=args.pl2_w,
    )
    if args.restore_on_start and args.apply_rapl:
        restored = backend.restore_state_to_rapl()
        if restored is not None:
            print(f"restored TDP limit to {restored}W", flush=True, file=sys.stderr)

    class RemoteInterface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(IFACE_REMOTE)

        @dbus_property(access=PropertyAccess.READ)
        def RemoteInterfaces(self) -> "as":
            return [IFACE_TDP]

    class TdpLimitInterface(ServiceInterface):
        def __init__(self, tdp_backend: TdpBackend) -> None:
            super().__init__(IFACE_TDP)
            self.backend = tdp_backend

        @dbus_property(access=PropertyAccess.READWRITE)
        def TdpLimit(self) -> "u":
            watts = self.backend.read_limit_w()
            print(f"get TdpLimit -> {watts}", flush=True, file=sys.stderr)
            return watts

        @TdpLimit.setter
        def TdpLimit(self, value: "u") -> None:
            self.backend.write_limit_w(int(value))
            print(f"set TdpLimit <- {int(value)}", flush=True, file=sys.stderr)
            self.emit_properties_changed({"TdpLimit": int(value)})

        @dbus_property(access=PropertyAccess.READ)
        def TdpLimitMin(self) -> "u":
            return self.backend.min_w

        @dbus_property(access=PropertyAccess.READ)
        def TdpLimitMax(self) -> "u":
            return self.backend.max_w

    bus_type = BusType.SYSTEM if args.bus == "system" else BusType.SESSION
    bus = await MessageBus(bus_type=bus_type).connect()
    bus.export(OBJ_PATH, RemoteInterface())
    bus.export(OBJ_PATH, TdpLimitInterface(backend))
    await bus.request_name(BUS_NAME)
    await asyncio.Future()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["serve", "wait-and-serve"],
        nargs="?",
        default="serve",
        help="serve immediately or wait for the deck user's steamos-manager first",
    )
    parser.add_argument("--bus", choices=["system", "session"], default="system")
    parser.add_argument("--min-w", type=int, default=DEFAULT_MIN_W)
    parser.add_argument("--max-w", type=int, default=DEFAULT_MAX_W)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--sysfs-root", default="/sys")
    parser.add_argument("--pl2-w", type=int)
    parser.add_argument("--apply-rapl", action="store_true")
    parser.add_argument("--restore-on-start", action="store_true")
    parser.add_argument("--user", default="deck")
    parser.add_argument("--wait-timeout-s", type=int, default=600)
    parser.add_argument("--wait-interval-s", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "wait-and-serve":
        wait_for_user_steamos_manager(args.user, args.wait_timeout_s, args.wait_interval_s)
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
