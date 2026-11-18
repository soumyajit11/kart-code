import subprocess
import sys
from pathlib import Path

from unittest.mock import Mock

import pytest

from main import approve, process_input, run_cli

PROJECT = Path(__file__).resolve().parents[1]


def test_process_input_forwards_a_normal_question():
    agent = Mock()
    agent.run_turn.return_value = "Hello!"
    assert process_input("  hello  ", agent) == "Hello!"
    agent.run_turn.assert_called_once_with("hello")


def test_exit_ends_the_cli_without_calling_the_model(monkeypatch, capsys):
    agent = Mock()
    monkeypatch.setattr("builtins.input", lambda prompt: " Exit ")
    run_cli(agent)
    assert capsys.readouterr().out == "goodbye!\n"
    agent.run_turn.assert_not_called()


def test_import_does_not_start_cli():
    result = subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=PROJECT, input="", text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_input_interruption_exits_cli(monkeypatch, capsys, interruption):
    monkeypatch.setattr("builtins.input", Mock(side_effect=interruption))
    run_cli(Mock())
    assert "goodbye!" in capsys.readouterr().out


@pytest.mark.parametrize(("answer", "approved"), [("yes", True), ("no", False), ("", False)])
def test_approval_requires_affirmative_answer(monkeypatch, capsys, answer, approved):
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    assert approve("Replace notes.txt") is approved
    assert "Replace notes.txt" in capsys.readouterr().out


def test_help_works_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        cwd=PROJECT, text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 0
    assert "--workspace" in result.stdout
    assert result.stderr == ""
