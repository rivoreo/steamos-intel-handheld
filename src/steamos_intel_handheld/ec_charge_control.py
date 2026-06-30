#!/usr/bin/env python3
"""MSI Claw EC charge-limit status and preset preview helpers."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MSI_EC_CHARGE_CONTROL_ADDRESS = 0xD7
MSI_EC_CHARGE_CONTROL_START_OFFSET = 0x8A
MSI_EC_CHARGE_CONTROL_END_OFFSET = 0x80
CHARGE_LIMIT_PRESETS = (60, 80, 100)
CHARGE_LIMIT_WRITE_ENABLED = True
CHARGE_LIMIT_SOURCE = "msi-claw-8-ai-plus-validated"
MSI_CLAW_8_AI_PLUS_DMI = {
    "sys_vendor": "Micro-Star International Co., Ltd.",
    "product_name": "Claw 8 AI+ A2VM",
    "board_name": "MS-1T52",
}


class EcChargeSafetyError(RuntimeError):
    """Raised when EC charge-limit access is unsafe or not yet supported."""


@dataclass(frozen=True)
class ChargeLimitStatus:
    raw_value: int
    address: int
    start_threshold: int
    end_threshold: int
    writes_enabled: bool
    source: str

    @property
    def raw_hex(self) -> str:
        return f"0x{self.raw_value:02x}"

    @property
    def address_hex(self) -> str:
        return f"0x{self.address:02x}"

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_hex"] = self.raw_hex
        payload["address_hex"] = self.address_hex
        payload["restart_explanation"] = (
            f"{self.end_threshold}% mode restarts charging below "
            f"{self.start_threshold}%"
        )
        return payload


def decode_charge_limit_raw(raw_value: int) -> ChargeLimitStatus:
    raw_value = int(raw_value)
    if raw_value < MSI_EC_CHARGE_CONTROL_START_OFFSET or raw_value > 0xE4:
        raise EcChargeSafetyError(
            f"EC charge-limit raw value 0x{raw_value:02x} is outside the known MSI range"
        )
    return ChargeLimitStatus(
        raw_value=raw_value,
        address=MSI_EC_CHARGE_CONTROL_ADDRESS,
        start_threshold=raw_value - MSI_EC_CHARGE_CONTROL_START_OFFSET,
        end_threshold=raw_value - MSI_EC_CHARGE_CONTROL_END_OFFSET,
        writes_enabled=CHARGE_LIMIT_WRITE_ENABLED,
        source=CHARGE_LIMIT_SOURCE,
    )


def encode_charge_limit_end_threshold(end_threshold: int) -> ChargeLimitStatus:
    end_threshold = int(end_threshold)
    if end_threshold not in CHARGE_LIMIT_PRESETS:
        presets = ", ".join(str(preset) for preset in CHARGE_LIMIT_PRESETS)
        raise EcChargeSafetyError(
            f"unsupported charge limit preset {end_threshold}; expected one of {presets}"
        )
    return decode_charge_limit_raw(MSI_EC_CHARGE_CONTROL_END_OFFSET + end_threshold)


class MsiEcChargeController:
    """Read and update the validated MSI EC battery threshold byte."""

    def __init__(
        self,
        debugfs_root: str | Path = "/sys/kernel/debug",
        dmi_root: str | Path = "/sys/class/dmi/id",
    ) -> None:
        self.debugfs_root = Path(debugfs_root)
        self.dmi_root = Path(dmi_root)

    def read_status(self) -> ChargeLimitStatus:
        self.preflight()
        ec = self._read_ec()
        return decode_charge_limit_raw(ec[MSI_EC_CHARGE_CONTROL_ADDRESS])

    def preview_preset(self, end_threshold: int) -> ChargeLimitStatus:
        return encode_charge_limit_end_threshold(end_threshold)

    def apply_preset(self, end_threshold: int) -> ChargeLimitStatus:
        target = self.preview_preset(end_threshold)
        self.preflight()
        self._write_ec_byte(MSI_EC_CHARGE_CONTROL_ADDRESS, target.raw_value)
        applied = self.read_status()
        if applied.raw_value != target.raw_value:
            raise EcChargeSafetyError(
                "EC charge-limit write did not stick: "
                f"expected {target.raw_hex}, read back {applied.raw_hex}"
            )
        return applied

    def preflight(self) -> None:
        self._assert_supported_dmi()

    def _assert_supported_dmi(self) -> None:
        mismatches = []
        for filename, expected in MSI_CLAW_8_AI_PLUS_DMI.items():
            actual = self._read_dmi_value(filename)
            if actual != expected:
                mismatches.append(f"{filename}={actual!r}")
        if mismatches:
            details = ", ".join(mismatches)
            raise EcChargeSafetyError(
                f"unsupported MSI Claw charge-limit target: {details}"
            )

    def _read_dmi_value(self, filename: str) -> str:
        try:
            return (self.dmi_root / filename).read_text().strip()
        except OSError:
            return ""

    def _ec_io(self) -> Path:
        return self.debugfs_root / "ec" / "ec0" / "io"

    def _read_ec(self) -> bytes:
        ec_io = self._ec_io()
        try:
            data = ec_io.read_bytes()
        except OSError as exc:
            raise EcChargeSafetyError(f"failed to read EC io file: {ec_io}") from exc
        if len(data) <= MSI_EC_CHARGE_CONTROL_ADDRESS:
            raise EcChargeSafetyError(f"short EC io dump from {ec_io}: {len(data)} bytes")
        return data

    def _write_ec_byte(self, offset: int, value: int) -> None:
        ec_io = self._ec_io()
        try:
            with ec_io.open("r+b") as ec_file:
                ec_file.seek(offset, os.SEEK_SET)
                ec_file.write(bytes([value]))
                ec_file.flush()
        except OSError as exc:
            raise EcChargeSafetyError(
                f"failed to write EC offset 0x{offset:02x} in {ec_io}"
            ) from exc


def status_payload(controller: MsiEcChargeController) -> dict[str, Any]:
    return controller.read_status().to_json_dict()


def preview_payload(controller: MsiEcChargeController, end_threshold: int) -> dict[str, Any]:
    current = controller.read_status()
    target = controller.preview_preset(end_threshold)
    return {
        "current": current.to_json_dict(),
        "target": target.to_json_dict(),
        "would_write": CHARGE_LIMIT_WRITE_ENABLED,
        "safety": "writes enabled after paired Windows 60/80/100 EC dump validation",
    }


def apply_payload(controller: MsiEcChargeController, end_threshold: int) -> dict[str, Any]:
    current = controller.read_status()
    target = controller.preview_preset(end_threshold)
    applied = controller.apply_preset(end_threshold)
    return {
        "current": current.to_json_dict(),
        "target": target.to_json_dict(),
        "applied": applied.to_json_dict(),
        "wrote": True,
        "safety": "wrote validated MSI Claw charge-limit byte 0xd7",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read EC charge-limit status")
    status.add_argument("--json", action="store_true")
    status.add_argument("--debugfs-root", default="/sys/kernel/debug")
    status.add_argument("--dmi-root", default="/sys/class/dmi/id")

    preview = subparsers.add_parser("preview", help="preview a safe preset without writing EC")
    preview.add_argument("end_threshold", type=int, choices=CHARGE_LIMIT_PRESETS)
    preview.add_argument("--json", action="store_true")
    preview.add_argument("--debugfs-root", default="/sys/kernel/debug")
    preview.add_argument("--dmi-root", default="/sys/class/dmi/id")

    apply = subparsers.add_parser("apply", help="write a validated EC charge-limit preset")
    apply.add_argument("end_threshold", type=int, choices=CHARGE_LIMIT_PRESETS)
    apply.add_argument("--json", action="store_true")
    apply.add_argument("--debugfs-root", default="/sys/kernel/debug")
    apply.add_argument("--dmi-root", default="/sys/class/dmi/id")

    return parser


def print_payload(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    controller = MsiEcChargeController(
        debugfs_root=args.debugfs_root,
        dmi_root=args.dmi_root,
    )

    if args.command == "status":
        print_payload(status_payload(controller), args.json)
    elif args.command == "preview":
        print_payload(preview_payload(controller, args.end_threshold), args.json)
    elif args.command == "apply":
        print_payload(apply_payload(controller, args.end_threshold), args.json)


if __name__ == "__main__":
    main()
