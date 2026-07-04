from __future__ import annotations

import re
from typing import Any

UNSAFE_REPORT_PATH_CHARS = (
    ";",
    "&",
    "|",
    "$",
    "`",
    "(",
    ")",
    "<",
    ">",
    "\n",
    "\r",
    "\t",
    " ",
)

_SHELL_COMMAND_BOUNDARY = r"(?<![\w-])"
_OPTIONAL_PATH_PREFIX = r"(?:[~./\w-]+/)?"
_SHELL_COMMAND_SUFFIX = r"(?=$|[\s;&|()<>`])"
_QEMU_SYSTEM_SUFFIX = r"(?=$|[\s;&|()<>`_-])"


def _command_pattern(name: str, suffix: str = _SHELL_COMMAND_SUFFIX) -> re.Pattern[str]:
    return re.compile(rf"{_SHELL_COMMAND_BOUNDARY}{_OPTIONAL_PATH_PREFIX}{name}{suffix}")


GUARDED_COMMAND_PATTERNS = (
    ("ssh ", _command_pattern("ssh")),
    ("scp ", _command_pattern("scp")),
    ("curl ", _command_pattern("curl")),
    ("gh ", _command_pattern("gh")),
    ("git push", _command_pattern(r"git\s+push")),
    ("sudo ", _command_pattern("sudo")),
    ("pacman", _command_pattern("pacman")),
    ("mount", _command_pattern("mount")),
    ("chroot", _command_pattern("chroot")),
    ("losetup", _command_pattern("losetup")),
    ("qemu-system", _command_pattern("qemu-system", _QEMU_SYSTEM_SUFFIX)),
)


def guarded_command_tokens(command: Any) -> list[str]:
    if not isinstance(command, str):
        return []
    return [token for token, pattern in GUARDED_COMMAND_PATTERNS if pattern.search(command)]


def check_is_guarded(check: dict[str, Any]) -> bool:
    return (
        bool(check.get("requires", []))
        or check.get("tier") == "guarded"
        or check.get("safe_for_agents") is False
        or bool(guarded_command_tokens(check.get("command", "")))
    )


def validate_report_path_token(raw_path: str) -> None:
    if any(char in raw_path for char in UNSAFE_REPORT_PATH_CHARS):
        raise ValueError(f"report path contains unsafe shell characters: {raw_path}")
