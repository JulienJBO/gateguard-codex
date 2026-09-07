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


def test_codex_init_defaults_to_local_without_subprocess_and_preserves_files(
    tmp_path: Path, monkeypatch
) -> None:
    from gateguard.cli import build_parser, cmd_init

    repo = tmp_path / "repo"
    config = repo / ".codex" / "config.toml"
    hooks = repo / ".codex" / "hooks.json"
    config.parent.mkdir(parents=True)
    config.write_text('[mcp_servers.other]\ncommand = "keep"\n', encoding="utf-8")
    hooks.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"command": "keep"}]}]},
    }), encoding="utf-8")
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))

    def no_subprocess(*args, **kwargs):
        raise AssertionError("default local init must not invoke codex mcp")

    monkeypatch.setattr("gateguard.cli.subprocess.run", no_subprocess)
    parser = build_parser()
    args = parser.parse_args(["init", str(repo), "--runtime", "codex"])
    assert cmd_init(args) == 0
    assert (repo / ".gateguard.yml").exists()
    assert not home.exists()

    written_hooks = json.loads(hooks.read_text(encoding="utf-8"))
    assert written_hooks["hooks"]["Stop"][0]["hooks"][0]["command"] == "keep"
    assert any(
        hook["command"] == "gateguard-codex-hook"
        for group in written_hooks["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    )
    written_config = config.read_text(encoding="utf-8")
    assert '[mcp_servers.other]\ncommand = "keep"' in written_config
    assert written_config.count("[mcp_servers.gateguard]") == 1
    assert 'command = "gateguard"' in written_config
    assert 'args = ["mcp", "serve"]' in written_config
    assert (repo / ".agents" / "skills" / "gateguard-codex" / "SKILL.md").exists()

    assert cmd_init(args) == 0
    assert config.read_text(encoding="utf-8").count("[mcp_servers.gateguard]") == 1


def test_codex_init_global_uses_codex_home_and_registers_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    from gateguard.cli import build_parser, cmd_init

    repo = tmp_path / "repo"
    home = tmp_path / "codex-home"
    hooks = home / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"command": "keep"}]}]},
    }), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    calls = []

    class Result:
        returncode = 1

    def run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr("gateguard.cli.subprocess.run", run)
    parser = build_parser()
    assert cmd_init(parser.parse_args([
        "init", str(repo), "--runtime", "codex", "--global",
    ])) == 0

    assert (repo / ".gateguard.yml").exists()
    assert not (repo / ".codex").exists()
    assert not (repo / ".agents").exists()
    written = json.loads(hooks.read_text(encoding="utf-8"))
    assert written["hooks"]["Stop"][0]["hooks"][0]["command"] == "keep"
    assert any(
        hook["command"] == "gateguard-codex-hook"
        for group in written["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    )
    assert (home / "skills" / "gateguard-codex" / "SKILL.md").exists()
    assert calls == [
        ["codex", "mcp", "get", "gateguard"],
        ["codex", "mcp", "add", "gateguard", "--", "gateguard", "mcp", "serve"],
    ]


def test_codex_doctor_defaults_to_project_and_global_is_explicit(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from gateguard.cli import build_parser, cmd_doctor, cmd_init

    repo = tmp_path / "repo"
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    parser = build_parser()
    assert cmd_init(parser.parse_args(["init", str(repo), "--runtime", "codex"])) == 0
    capsys.readouterr()

    (home / "skills" / "gateguard-codex").mkdir(parents=True)
    (home / "skills" / "gateguard-codex" / "SKILL.md").write_text(
        "---\nname: gateguard-codex\n---\n", encoding="utf-8"
    )
    (home / "hooks.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{"hooks": [{"command": "gateguard-codex-hook"}]}]},
    }), encoding="utf-8")

    assert cmd_doctor(parser.parse_args(["doctor", "--runtime", "codex", str(repo)])) == 0
    assert "OK mcp" in capsys.readouterr().out

    (repo / ".codex" / "hooks.json").unlink()
    assert cmd_doctor(parser.parse_args(["doctor", "--runtime", "codex", str(repo)])) == 1
    assert "MISSING hook" in capsys.readouterr().out

    assert cmd_doctor(parser.parse_args(["doctor", "--runtime", "codex", "--global"])) == 0
    assert "OK hook" in capsys.readouterr().out
