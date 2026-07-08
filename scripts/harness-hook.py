#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
STATE_VERSION = 1

SKILL_CATALOG = [
    {
        "name": "model-tier-prompting",
        "path": ".codex/skills/model-tier-prompting/SKILL.md",
        "summary": (
            "Tier-aware prompt design/rewrite for subagent prompts, cross-model "
            "migration, and prompt behavior diagnosis."
        ),
        "patterns": [
            r"model-tier-prompting",
            r"prompt(?:ing)?|提示詞|提示词",
            r"子代理|sub-?agent|派工|委派",
            r"frontier|workhorse|haiku|mini|flash",
            r"reasoning[_ -]?effort|verbosity|effort",
            r"模型.*(分層|分层|層級|层级|遷移|迁移|改寫|改写)",
            r"不聽話|不听话|太囉嗦|太啰嗦|虛報|虚报|過度工程|过度工程|召回率|refusal",
        ],
    },
    {
        "name": "refine",
        "path": ".codex/skills/refine/SKILL.md",
        "summary": (
            "Expand rough ideas into a confirmable task brief with intent, "
            "acceptance criteria, boundaries, and execution advice."
        ),
        "patterns": [
            r"(?:^|\s)/refine(?:\s|$)",
            r"\brefine\b",
            r"展開成|展开成|寫清楚|写清楚|任務書|任务书|需求.*(展開|展开|整理|寫清楚|写清楚)",
            r"粗略想法|模糊需求|可開工|可施工|驗收標準|验收标准|本次不做|開放問題|开放问题",
        ],
    },
]


def read_stdin_json() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def event_name(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    return str(
        args.event
        or payload.get("hook_event_name")
        or payload.get("hookEventName")
        or ""
    )


def session_id(payload: dict[str, Any]) -> str:
    raw = str(payload.get("session_id") or payload.get("sessionId") or os.getppid())
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)[:120] or str(os.getppid())


def default_state_dir() -> Path:
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / (
        "steamos-intel-handheld-harness-hook"
    )


def load_state(path: Optional[Path]) -> dict[str, Any]:  # noqa: UP045
    if path is None or not path.exists():
        return {
            "version": STATE_VERSION,
            "session_notice_keys": {},
            "stop_block_keys": {},
            "skill_notice_keys": {},
        }
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError:
        state = {}
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return {
            "version": STATE_VERSION,
            "session_notice_keys": {},
            "stop_block_keys": {},
            "skill_notice_keys": {},
        }
    state.setdefault("session_notice_keys", {})
    state.setdefault("stop_block_keys", {})
    state.setdefault("skill_notice_keys", {})
    return state


def save_state(path: Optional[Path], state: dict[str, Any]) -> None:  # noqa: UP045
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def run_status(root: Path) -> tuple[dict[str, Any], Optional[str]]:  # noqa: UP045
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/harness.py"),
            "--root",
            str(root),
            "status",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {}, detail or f"harness status exited {completed.returncode}"
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        return {}, f"harness status returned invalid JSON: {error}"
    if not isinstance(payload, dict):
        return {}, "harness status returned non-object JSON"
    return payload, None


def pending_key(status: dict[str, Any], error: Optional[str] = None) -> str:  # noqa: UP045
    context = (status.get("last_report") or {}).get("context") or {}
    data = {
        "error": error,
        "freshness": status.get("freshness"),
        "pending_verification": status.get("pending_verification"),
        "report_path": status.get("report_path"),
        "manifest": context.get("manifest"),
        "workspace": context.get("workspace", {}).get("fingerprint"),
    }
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def pending_lines(status: dict[str, Any], error: Optional[str] = None) -> list[str]:  # noqa: UP045
    if error:
        return [
            "Harness pending verification: status could not be read.",
            f"- {error}",
            "Fix harness status before ending the session.",
        ]

    pending = status.get("pending_verification") or []
    lines = ["Harness pending verification:"]
    for item in pending:
        checks = ", ".join(item.get("checks", [])) or str(item.get("scope", "required"))
        lines.append(f"- {checks}: {item.get('reason', 'verification pending')}")
    command = status.get("trusted_suite")
    if command:
        lines.append(f"Run trusted suite: {command}")
    lines.append("This hook is read-only; it does not run checks for you.")
    return lines


def hook_tool_input(payload: dict[str, Any]) -> Any:
    for key in ("tool_input", "toolInput", "input", "parameters"):
        if key in payload:
            return payload[key]
    return {}


def command_from_tool_input(tool_input: Any) -> str:
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return ""
    for key in ("cmd", "command", "shell_command", "shellCommand"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def hook_text(payload: dict[str, Any]) -> str:
    tool_input = hook_tool_input(payload)
    parts: list[str] = []
    for key in ("prompt", "user_prompt", "userPrompt", "message", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
    if isinstance(tool_input, str):
        parts.append(tool_input)
    elif isinstance(tool_input, dict):
        for key in ("cmd", "command", "shell_command", "shellCommand", "file_path", "path"):
            value = tool_input.get(key)
            if isinstance(value, str):
                parts.append(value)
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    for key in ("file_path", "old_string", "new_string"):
                        value = edit.get(key)
                        if isinstance(value, str):
                            parts.append(value)
    return "\n".join(parts)


def skill_response(
    event: str,
    payload: dict[str, Any],
    state: dict[str, Any],
) -> Optional[dict[str, Any]]:  # noqa: UP045
    if not re.fullmatch(r"(UserPromptSubmit|PreToolUse)", event, flags=re.IGNORECASE):
        return None

    text = hook_text(payload)
    matched: list[dict[str, Any]] = []
    skill_notice_keys = state.setdefault("skill_notice_keys", {})
    for skill in SKILL_CATALOG:
        if skill_notice_keys.get(skill["name"]):
            continue
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in skill["patterns"]):
            matched.append(skill)

    if not matched:
        return None

    for skill in matched:
        skill_notice_keys[skill["name"]] = True

    lines = [
        f"SteamOS Skill Hook matched {event}.",
        (
            "Before responding, reading more files, or editing, load and follow "
            "these SKILL.md files if they are not already active:"
        ),
        *[
            f"- {skill['name']}: {skill['path']} ({skill['summary']})"
            for skill in matched
        ],
        (
            "Usage rule: If there is even a 1% chance a matched skill applies, "
            "read it first and explicitly follow it."
        ),
    ]
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": "\n".join(lines),
        }
    }


def is_git_commit_command(command: str) -> bool:
    if not command.strip():
        return False
    tokens = shell_command_tokens(command)
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token_is_shell_separator(token):
            if command_segment_is_git_commit(segment):
                return True
            segment = []
            continue
        segment.append(token)
    return False


def shell_command_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return re.findall(r"&&|\|\||[;&|]|[^\s;&|()<>`]+", command)


def token_is_shell_separator(token: str) -> bool:
    return token in {";", "&&", "||", "|", "&"}


def command_segment_is_git_commit(segment: list[str]) -> bool:
    while segment and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", segment[0]):
        segment = segment[1:]
    if segment and segment[0] == "command":
        segment = segment[1:]
    if segment and segment[0] == "env":
        segment = segment[1:]
        while segment and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", segment[0]):
            segment = segment[1:]
    if not segment:
        return False
    if Path(segment[0]).name != "git":
        return False

    git_args = segment[1:]
    index = 0
    options_with_values = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    while index < len(git_args):
        token = git_args[index]
        if token == "--":
            index += 1
            break
        if token in options_with_values:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in options_with_values):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return index < len(git_args) and git_args[index] == "commit"


def has_pending(status: dict[str, Any], error: Optional[str] = None) -> bool:  # noqa: UP045
    return bool(error or status.get("pending_verification"))


def build_response(
    event: str,
    payload: dict[str, Any],
    status: dict[str, Any],
    state: dict[str, Any],
    key: str,
    error: Optional[str] = None,  # noqa: UP045
) -> Optional[dict[str, Any]]:  # noqa: UP045
    if not has_pending(status, error):
        return None

    reason = "\n".join(pending_lines(status, error))
    if re.fullmatch(r"PreToolUse", event, flags=re.IGNORECASE):
        command = command_from_tool_input(hook_tool_input(payload))
        if is_git_commit_command(command):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "git commit is blocked because verification is pending.\n"
                    f"{reason}"
                ),
                "pending_verification": status.get("pending_verification", []),
                "trusted_suite": status.get("trusted_suite"),
            }
        return None

    if re.fullmatch(r"SessionStart", event, flags=re.IGNORECASE):
        if state["session_notice_keys"].get(event) == key:
            return None
        state["session_notice_keys"][event] = key
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": reason,
            },
            "pending_verification": status.get("pending_verification", []),
        }

    if re.fullmatch(r"(Stop|SubagentStop)", event, flags=re.IGNORECASE):
        if state["stop_block_keys"].get(event) == key:
            return None
        state["stop_block_keys"][event] = key
        return {
            "decision": "block",
            "reason": reason,
            "pending_verification": status.get("pending_verification", []),
            "trusted_suite": status.get("trusted_suite"),
        }

    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only harness hook reminder.")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root containing harness.toml.",
    )
    parser.add_argument("--platform", default="auto", help="Hook platform label.")
    parser.add_argument("--event", default="", help="Override hook event name.")
    parser.add_argument("--state-dir", type=Path, default=None, help="Hook state directory.")
    parser.add_argument("--no-state", action="store_true", help="Disable reminder dedupe.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:  # noqa: UP045
    args = build_parser().parse_args(argv)
    payload = read_stdin_json()
    event = event_name(args, payload)
    state_path = None
    if not args.no_state:
        state_root = args.state_dir or default_state_dir()
        state_path = state_root / f"{session_id(payload)}.json"
    state = load_state(state_path)
    status, error = run_status(args.root.resolve())
    key = pending_key(status, error)
    response = build_response(event, payload, status, state, key, error)
    if response is None:
        response = skill_response(event, payload, state)
    save_state(state_path, state)
    if response:
        print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
