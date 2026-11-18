import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import tools
from tools import WorkspaceTools


@pytest.mark.parametrize(("name", "arguments"), [
    ("unknown", "{}"),
    ("read_file", "{"),
    ("read_file", "[]"),
    ("read_file", '{"path": 1}'),
    ("read_file", '{"path": "a.txt", "extra": true}'),
    ("run_command", '{"command": "pytest && echo unexpected"}'),
])
def test_invalid_operations_do_not_request_approval(tmp_path, name, arguments):
    approve = Mock()
    result = WorkspaceTools(tmp_path, approve).execute(name, arguments)
    assert result["ok"] is False
    assert result["error"]
    approve.assert_not_called()


def test_absolute_paths_are_rejected(tmp_path):
    workspace = WorkspaceTools(tmp_path, Mock())
    result = workspace.execute("read_file", json.dumps({"path": str(tmp_path / "inside.txt")}))
    assert result["ok"] is False


def test_symlink_escape_is_rejected_for_reads_and_writes(tmp_path):
    directory = tmp_path / "workspace"
    directory.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        (directory / "link.txt").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"This environment cannot create symlinks: {error}")
    approve = Mock()
    workspace = WorkspaceTools(directory, approve)
    assert not workspace.execute("read_file", '{"path": "link.txt"}')["ok"]
    assert not workspace.execute("write_file", '{"path": "link.txt", "content": "new"}')["ok"]
    assert outside.read_text(encoding="utf-8") == "private"
    approve.assert_not_called()


def test_file_permission_error_is_structured(tmp_path, monkeypatch):
    (tmp_path / "notes.txt").touch()
    monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError))
    result = WorkspaceTools(tmp_path, Mock()).execute("read_file", '{"path":"notes.txt"}')
    assert result == {"ok": False, "error": "The operating system denied permission."}


@pytest.mark.parametrize(("data", "error"), [
    (b"\xff", "UTF-8"),
    (b"x" * (tools.MAX_FILE_BYTES + 1), "read limit"),
], ids=["invalid-utf8", "oversize"])
def test_unreadable_file_contents_are_reported(tmp_path, data, error):
    (tmp_path / "notes.txt").write_bytes(data)
    result = WorkspaceTools(tmp_path, Mock()).execute("read_file", '{"path":"notes.txt"}')
    assert not result["ok"]
    assert error in result["error"]


def test_environment_files_and_configured_key_are_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-value")
    workspace = WorkspaceTools(tmp_path, Mock())
    (tmp_path / ".env.local").write_text("test-key-value", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("key: test-key-value", encoding="utf-8")
    assert not workspace.execute("read_file", '{"path":".env.local"}')["ok"]
    assert workspace.execute("read_file", '{"path":"notes.txt"}')["content"] == "key: [REDACTED]"


def test_write_rechecks_destination_after_approval(tmp_path):
    target = tmp_path / "notes.txt"

    def approve(operation):
        target.write_text("someone else created this", encoding="utf-8")
        return True

    result = WorkspaceTools(tmp_path, approve).execute(
        "write_file", '{"path":"notes.txt","content":"replacement"}'
    )
    assert not result["ok"]
    assert "changed during approval" in result["error"]
    assert target.read_text(encoding="utf-8") == "someone else created this"


def test_write_detects_same_size_edit_during_approval(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"old")
    base = target.stat().st_mtime_ns // 1_000_000_000 * 1_000_000_000
    os.utime(target, ns=(base, base))

    def approve(operation):
        target.write_bytes(b"new")
        os.utime(target, ns=(base, base + 500_000_000))
        return True

    result = WorkspaceTools(tmp_path, approve).execute(
        "write_file", '{"path":"notes.txt","content":"replacement"}'
    )
    assert not result["ok"]
    assert "changed during approval" in result["error"]
    assert target.read_bytes() == b"new"


def test_denied_command_does_not_spawn_process(tmp_path, monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(tools.subprocess, "Popen", spawn)
    result = WorkspaceTools(tmp_path, lambda _: False).execute("run_command", '{"command":"pytest"}')
    assert "denied" in result["error"]
    spawn.assert_not_called()


def test_command_uses_fixed_argv_workspace_and_excludes_key(tmp_path, monkeypatch):
    runner = Mock(return_value={"ok": True})
    monkeypatch.setattr(tools, "run_pytest", runner)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-value")
    workspace = WorkspaceTools(tmp_path, lambda _: True)
    assert workspace.execute("run_command", '{"command":"pytest"}')["ok"]
    argv, cwd, environment = runner.call_args.args
    assert argv == [sys.executable, "-m", "pytest", "-q"]
    assert cwd == tmp_path.resolve()
    assert "OPENAI_API_KEY" not in environment


def test_subprocess_output_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", 100)
    result = tools.run_pytest(
        [sys.executable, "-c", "print('x' * 300000)"], tmp_path, tools.os.environ.copy()
    )
    assert result["ok"]
    assert result["truncated"]
    assert result["output"] == "x" * 100


def test_subprocess_timeout_returns_a_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "COMMAND_TIMEOUT", 0.2)
    result = tools.run_pytest(
        [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, tools.os.environ.copy()
    )
    assert not result["ok"]
    assert "timed out" in result["error"]
    assert result["exit_code"] is not None
