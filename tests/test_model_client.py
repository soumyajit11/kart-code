import io
import json
import sys
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

import pytest

import main
import model_client
from model_client import ModelError, OpenAIClient
from tools import TOOL_DEFINITIONS


def response(reason="stop", **message):
    return io.BytesIO(json.dumps({
        "choices": [{"message": message, "finish_reason": reason}]
    }).encode("utf-8"))


def test_model_client_sends_chat_completions_schema(monkeypatch):
    transport = Mock(return_value=response(content="Hello!"))
    monkeypatch.setattr(model_client, "urlopen", transport)
    messages = [{"role": "user", "content": "Hi"}]
    reply = OpenAIClient("fake-key", "test-model").complete(messages, TOOL_DEFINITIONS)
    request = transport.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == model_client.API_URL
    assert request.get_header("Authorization") == "Bearer fake-key"
    assert "fake-key" not in request.data.decode()
    assert payload["messages"] == messages
    assert payload["model"] == "test-model"
    assert payload["tools"] == TOOL_DEFINITIONS
    assert payload["tool_choice"] == "auto"
    assert reply == {"role": "assistant", "content": "Hello!"}


@pytest.mark.parametrize(("error", "message"), [
    (HTTPError(model_client.API_URL, 401, "fake-key", None, io.BytesIO()), "HTTP 401"),
    (URLError("fake-key"), "Could not reach OpenAI"),
])
def test_api_error_does_not_expose_raw_details(monkeypatch, error, message):
    monkeypatch.setattr(model_client, "urlopen", Mock(side_effect=error))
    with pytest.raises(ModelError, match=message) as caught:
        OpenAIClient("fake-key", "test-model").complete([], TOOL_DEFINITIONS)
    assert "fake-key" not in str(caught.value)


def test_malformed_tool_reply_is_a_model_error(monkeypatch):
    malformed = response("tool_calls", tool_calls=[{"id": "call_1", "type": "function"}])
    monkeypatch.setattr(model_client, "urlopen", Mock(return_value=malformed))
    with pytest.raises(ModelError, match="unexpected assistant message"):
        OpenAIClient("fake-key", "test-model").complete([], TOOL_DEFINITIONS)


def test_cli_reads_real_file_with_mocked_api(tmp_path, monkeypatch, capsys):
    (tmp_path / "example.py").write_text("answer = 42\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(sys, "argv", ["main.py", "--workspace", str(tmp_path)])
    monkeypatch.setattr("builtins.input", Mock(side_effect=["What is in example.py?", "exit"]))
    tool_call = {
        "id": "call_read", "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "example.py"}'},
    }

    def transport(request, timeout):
        messages = json.loads(request.data)["messages"]
        if messages[-1]["role"] == "user":
            return response("tool_calls", content=None, tool_calls=[tool_call])
        assert messages[-2]["tool_calls"] == [tool_call]
        assert messages[-1]["tool_call_id"] == "call_read"
        content = json.loads(messages[-1]["content"])["content"].strip()
        return response(content=f"example.py contains {content}.")

    monkeypatch.setattr(model_client, "urlopen", transport)
    main.main()
    assert capsys.readouterr().out == "example.py contains answer = 42.\ngoodbye!\n"
