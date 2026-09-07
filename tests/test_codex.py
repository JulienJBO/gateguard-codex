"""Codex adapter: native mutations fail closed and MCP records success."""
from __future__ import annotations

import json
from pathlib import Path

from gateguard import codex


def test_codex_hook_blocks_native_write(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({
        "toolName": "apply_patch", "toolArguments": {"path": "x.py"},
    })))
    codex.hook_main()
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "block"


def test_codex_hook_allows_guarded_mcp(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({
        "toolName": "mcp__gateguard__write", "toolArguments": {},
    })))
    codex.hook_main()
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_mcp_read_records_only_after_success(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "sample.py"
    target.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = codex._call("read", {"path": str(target)})
    assert result["content"] == "x = 1\n"
    try:
        codex._call("read", {"path": str(tmp_path / "missing.py")})
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing read must fail")


def test_codex_init_merges_hook_and_registers_mcp(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from gateguard.cli import build_parser, cmd_init

    home = tmp_path / "codex"
    hooks = home / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "keep"}]}]}}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    calls = []
    monkeypatch.setattr("gateguard.cli.subprocess.run", lambda args, check: calls.append(args))
    assert cmd_init(build_parser().parse_args(["init", str(tmp_path / "repo"), "--runtime", "codex"])) == 0
    written = json.loads(hooks.read_text(encoding="utf-8"))
    assert written["hooks"]["Stop"][0]["hooks"][0]["command"] == "keep"
    assert any(h["command"] == "gateguard-codex-hook" for g in written["hooks"]["PreToolUse"] for h in g["hooks"])
    assert calls == [["codex", "mcp", "add", "gateguard", "--", "gateguard", "mcp", "serve"]]
