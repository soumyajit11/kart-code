import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent import Agent
from model_client import ModelError
from tools import WorkspaceTools


def answer(text):
    return {"role": "assistant", "content": text}


def call(name, **arguments):
    return {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }


def make_agent(tmp_path, replies, approval=False, max_iterations=8):
    snapshots = []
    replies = iter(replies)

    def complete(messages, tools):
        snapshots.append(deepcopy(messages))
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    approve = Mock(return_value=approval)
    agent = Agent(Mock(complete=complete), WorkspaceTools(tmp_path, approve), max_iterations)
    return agent, snapshots, approve


def tool_result(messages):
    message = messages[-1]
    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_1"
    return json.loads(message["content"])


def test_normal_question_and_follow_up_keep_history(tmp_path):
    agent, snapshots, _ = make_agent(tmp_path, [answer("A list is mutable."), answer("Use append.")])
    assert agent.run_turn("What is a list?") == "A list is mutable."
    assert agent.run_turn("How do I add an element?") == "Use append."
    assert snapshots[1][1:] == [
        {"role": "user", "content": "What is a list?"},
        answer("A list is mutable."),
        {"role": "user", "content": "How do I add an element?"},
    ]


@pytest.mark.parametrize("path", ["notes.txt", "missing.txt", "../outside.txt"])
def test_read_result_is_sent_back_to_the_model(tmp_path, path):
    (tmp_path / "notes.txt").write_text("The real content.", encoding="utf-8")
    request = call("read_file", path=path)
    agent, snapshots, approval = make_agent(tmp_path, [request, answer("Done.")])
    assert agent.run_turn("Read the file.") == "Done."
    assert snapshots[1][-2] == request
    result = tool_result(snapshots[1])
    if path == "notes.txt":
        assert result == {"ok": True, "content": "The real content."}
    else:
        assert result["ok"] is False
        assert ("was not found" if path == "missing.txt" else "workspace-relative") in result["error"]
    approval.assert_not_called()


@pytest.mark.parametrize("approved", [False, True])
def test_write_requires_approval(tmp_path, approved):
    target = tmp_path / "notes.txt"
    target.write_text("before", encoding="utf-8")
    request = call("write_file", path="notes.txt", content="after\n")
    agent, snapshots, approval = make_agent(tmp_path, [request, answer("Done.")], approved)
    agent.run_turn("Update my notes.")
    approval.assert_called_once()
    assert "after\\n" in approval.call_args.args[0]
    result = tool_result(snapshots[1])
    assert result["ok"] is approved
    assert target.read_bytes() == (b"after\n" if approved else b"before")
    if not approved:
        assert "denied" in result["error"]


def test_failed_pytest_is_returned_to_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    (tmp_path / "test_example.py").write_text(
        "def test_example():\n    assert 2 + 2 == 5\n", encoding="utf-8"
    )
    request = call("run_command", command="pytest")
    agent, snapshots, approval = make_agent(tmp_path, [request, answer("A test failed.")], True)
    assert agent.run_turn("Run the tests.") == "A test failed."
    approval.assert_called_once()
    result = tool_result(snapshots[1])
    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert "1 failed" in result["output"]


def test_iteration_limit_leaves_tool_history_complete(tmp_path):
    read = call("read_file", path="missing.txt")
    write = call("write_file", path="notes.txt", content="new")
    agent, snapshots, approval = make_agent(
        tmp_path, [read, write, answer("Back again.")], True, max_iterations=2
    )
    assert "maximum of 2" in agent.run_turn("Keep trying.")
    assert len(snapshots) == 2
    assert agent.history[-1]["role"] == "tool"
    assert not (tmp_path / "notes.txt").exists()
    approval.assert_not_called()
    assert agent.run_turn("Continue.") == "Back again."


def test_api_error_after_tool_execution_preserves_the_result(tmp_path):
    request = call("write_file", path="notes.txt", content="saved")
    agent, snapshots, _ = make_agent(
        tmp_path, [request, ModelError("Connection lost."), answer("Recovered.")], True
    )
    assert agent.run_turn("Write the file.") == "Model error: Connection lost."
    assert agent.run_turn("Try again.") == "Recovered."
    assert tool_result(snapshots[2][:-1])["ok"]
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "saved"
