"""Codex adapter and guarded stdio MCP server.

Codex has a pre-tool hook but no post-tool event.  The hook therefore blocks
native mutation paths, while this MCP server records evidence only after the
operation it performed has completed successfully.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import hook as gate
from .audit import evidence_entry, record_evidence
from .config import load_config
from .log import log_event
from .state import update_state

MCP_PREFIX = "mcp__gateguard__"
READ_NAMES = {"read", "read_file"}
WRITE_NAMES = {"write", "write_file", "apply_patch", "edit", "edit_file"}
COMMAND_NAMES = {"bash", "shell", "exec", "exec_command", "command", "functions.exec"}


def _event(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(data.get("tool_name") or data.get("toolName") or data.get("name") or "")
    args = data.get("tool_input") or data.get("toolArguments") or data.get("arguments") or {}
    return name, args if isinstance(args, dict) else {}


def _legacy_input(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    lowered = name.lower()
    if lowered in {"grep", "search"}:
        return "Grep", {"path": args.get("path") or "", "pattern": args.get("pattern") or args.get("query") or ""}
    if lowered == "glob":
        return "Glob", {"path": args.get("path") or "", "pattern": args.get("pattern") or ""}
    if lowered in READ_NAMES:
        return "Read", {"file_path": args.get("path") or args.get("file_path") or ""}
    if lowered in WRITE_NAMES:
        return ("Edit" if lowered in {"edit", "edit_file", "apply_patch"} else "Write"), {
            "file_path": args.get("path") or args.get("file_path") or "",
            "old_string": args.get("old_string") or "",
            "new_string": args.get("new_string") or args.get("content") or "",
            "content": args.get("content") or "",
        }
    if lowered in COMMAND_NAMES:
        return "Bash", {"command": args.get("command") or args.get("cmd") or ""}
    return "", {}


def _allowed(tool: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Run the existing engine, translating its deny into a Codex response."""
    denied: list[str] = []
    original = gate._deny

    def deny(reason: str, **kwargs: Any) -> None:
        denied.append(reason)
        log_event(kwargs["tool_name"], kwargs["tool_input"], kwargs["gate_type"], "deny", extra=kwargs.get("extra"))

    gate._deny = deny
    try:
        cfg = load_config()
        if tool in ("Edit", "Write"):
            allowed = gate._handle_edit_or_write(tool, args, cfg)
        elif tool == "Bash":
            allowed = gate._handle_bash(args, cfg)
        else:
            allowed = True
    finally:
        gate._deny = original
    return allowed, denied[-1] if denied else ""


def _record_success(tool: str, args: dict[str, Any]) -> None:
    now = time.time()
    entry = evidence_entry(tool, args, now)
    if tool == "Read" or entry is not None:
        def update(state: dict) -> dict:
            if tool == "Read" and args.get("file_path"):
                state["read_files"] = list(dict.fromkeys([*state.get("read_files", []), args["file_path"]]))
            if entry is not None:
                record_evidence(state, entry, now)
            return state
        update_state(update)
        log_event(tool, args, "evidence", "observe")


def hook_main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(json.dumps({"decision": "block", "reason": "GateGuard: invalid Codex hook input"}))
        return
    name, args = _event(data)
    if name.startswith(MCP_PREFIX):
        print(json.dumps({"continue": True}))
        return
    tool, legacy_args = _legacy_input(name, args)
    if tool in {"Edit", "Write", "Bash"}:
        print(json.dumps({"continue": True} if False else {"decision": "block", "reason": "GateGuard: use the guarded gateguard MCP tools; native mutations are blocked."}))
        return
    if not tool:
        print(json.dumps({"decision": "block", "reason": f"GateGuard: unknown native tool {name!r}; use gateguard MCP."}))
        return
    print(json.dumps({"continue": True}))


def _result(request_id: Any, result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)


def _error(request_id: Any, message: str) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message}}), flush=True)


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool, args = _legacy_input(name, arguments)
    if not tool:
        raise ValueError("unknown GateGuard MCP tool")
    allowed, reason = _allowed(tool, args)
    if not allowed:
        raise PermissionError(reason)
    if tool == "Read":
        content = Path(args["file_path"]).read_text(encoding="utf-8")
        _record_success(tool, args)
        return {"content": content}
    if tool == "Grep":
        root = Path(args["path"] or ".")
        matches = [f"{p}:{i}:{line}" for p in root.rglob("*") if p.is_file()
                   for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
                   if args["pattern"] in line]
        _record_success(tool, args)
        return {"matches": matches[:200]}
    if tool == "Glob":
        matches = glob.glob(str(Path(args["path"] or ".") / args["pattern"]), recursive=True)
        _record_success(tool, args)
        return {"matches": matches[:200]}
    if tool == "Write":
        Path(args["file_path"]).write_text(arguments.get("content", ""), encoding="utf-8")
        return {"written": args["file_path"]}
    if tool == "Bash":
        proc = subprocess.run(args["command"], shell=True, text=True, capture_output=True)
        if proc.returncode:
            raise RuntimeError(proc.stderr or f"command exited {proc.returncode}")
        _record_success(tool, args)
        return {"stdout": proc.stdout, "stderr": proc.stderr}
    raise ValueError("unsupported operation")


def mcp_main() -> None:
    tools = [{"name": n, "description": f"GateGuard guarded {n}", "inputSchema": {"type": "object"}}
             for n in ("read", "search", "glob", "write", "command")]
    for line in sys.stdin:
        try:
            req = json.loads(line)
            method, request_id = req.get("method"), req.get("id")
            if method == "initialize":
                _result(request_id, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "gateguard", "version": "0.7.0"}})
            elif method == "tools/list":
                _result(request_id, {"tools": tools})
            elif method == "tools/call":
                params = req.get("params") or {}
                value = _call(str(params.get("name", "")), params.get("arguments") or {})
                _result(request_id, {"content": [{"type": "text", "text": json.dumps(value)}]})
            elif request_id is not None:
                _error(request_id, f"unsupported method {method}")
        except Exception as exc:
            _error(req.get("id") if "req" in locals() else None, str(exc))
