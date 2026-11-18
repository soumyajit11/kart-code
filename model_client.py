"""Send Chat Completions requests and validate assistant messages."""

import json
from http.client import HTTPException
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

Message = dict[str, Any]
API_URL = "https://api.openai.com/v1/chat/completions"


class ModelError(Exception):
    """A model request failed or returned an unusable reply."""


class Model(Protocol):
    def complete(self, messages: list[Message], tools: list[dict]) -> Message:
        ...


class OpenAIClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, messages: list[Message], tools: list[dict]) -> Message:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "max_completion_tokens": 8192,
            "store": False,
        }
        request = Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        request.add_unredirected_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urlopen(request, timeout=30) as response:
                return parse_reply(json.loads(response.read(1024 * 1024)))
        except HTTPError as error:
            status = error.code
            error.close()
            hints = {
                401: "Check OPENAI_API_KEY.",
                403: "Check project permissions and model access.",
                404: "Check OPENAI_MODEL and your project's model access.",
                429: "Check API rate limits and project quota.",
            }
            hint = hints.get(status, "Check your model configuration or try again.")
            raise ModelError(f"OpenAI returned HTTP {status}. {hint}") from None
        except (URLError, OSError, HTTPException):
            raise ModelError("Could not reach OpenAI. Check your network or retry.") from None
        except (ValueError, RecursionError):
            raise ModelError("OpenAI returned an invalid JSON response.") from None


def parse_reply(payload: Any) -> Message:
    """Keep only the fields needed to send this assistant message back."""
    try:
        choice = payload["choices"][0]
        message = choice["message"]
        reason = choice["finish_reason"]
        content = message.get("content")
        calls = message.get("tool_calls", [])
        if reason == "length":
            raise ModelError("The model reached its response token limit.")
        if reason == "content_filter":
            raise ModelError("OpenAI filtered the model response.")
        if not isinstance(calls, list) or (content is not None and not isinstance(content, str)):
            raise ValueError
        if reason == "stop" and not calls:
            answer = content or message.get("refusal")
            if isinstance(answer, str) and answer.strip():
                return {"role": "assistant", "content": answer}
        if reason == "tool_calls" and calls:
            validated = []
            seen_ids = set()
            for call in calls:
                call_id = call["id"]
                function = call["function"]
                name, arguments = function["name"], function["arguments"]
                if (
                    call["type"] != "function"
                    or not all(isinstance(value, str) for value in (call_id, name, arguments))
                    or not call_id
                    or call_id in seen_ids
                ):
                    raise ValueError
                seen_ids.add(call_id)
                validated.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                })
            return {"role": "assistant", "content": content, "tool_calls": validated}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        pass
    raise ModelError("OpenAI returned an unexpected assistant message.")
