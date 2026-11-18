# MiniCodex

MiniCodex is a small terminal coding agent for learning how model requests, tool
calls, and local execution fit together. It can answer questions, read files in
your workspace, propose a complete file replacement, and run pytest with your approval.

## Setup

Use Python 3.10 or later. The CLI uses the Python standard library. Install
pytest in the interpreter you use to run MiniCodex if you want to run tests.

From the project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pytest
```

For live use, load your OpenAI API key into the terminal's `OPENAI_API_KEY`
environment variable. This PowerShell prompt masks the key as you enter it and
sets it for the current shell. The offline tests and `--help` work without a key.
Get a project key through the [OpenAI API dashboard](https://platform.openai.com/api-keys).

```powershell
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new(
    '', (Read-Host 'OpenAI API key' -AsSecureString)
).Password
.\.venv\Scripts\python.exe main.py --workspace .
```

On macOS or Linux, the virtual environment interpreter is `.venv/bin/python`.
The workspace defaults to the current directory. The default model is
`gpt-4.1-mini`. Set `OPENAI_MODEL` to select a different Chat Completions model
with function calling. [Default model capabilities](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

## Use it

```text
minicodex > Read main.py and tell me what happens when I type exit.
process_input returns None, run_cli breaks out of the input loop, and the CLI prints goodbye!
minicodex > Which function checks that command?
process_input in main.py.
minicodex > exit
goodbye!
```

Example requests include `Create notes.txt containing "Review the agent loop."`
and `Run the test suite and summarize any failures.` A write prompt shows the
resolved destination and complete proposed content as a quoted JSON string, so
`\n` represents a newline. A command prompt shows the exact argument list and
working directory. Type `yes` to approve the displayed operation. Enter any
other response to decline. EOF or Ctrl+C ends the session.

## Architecture

| Component | Input | Output and responsibility |
| --- | --- | --- |
| `main.py` | Terminal input, command-line options, environment | Configures the workspace and client, sends each user message to the agent, prints replies, and gathers approvals. |
| `agent.py` | A user message, model client, and local tools | Keeps the ordered conversation in memory and returns a final answer or error. Routes structured calls to the tools and adds their results to history. |
| `model_client.py` | Conversation messages and JSON tool definitions | Makes an HTTPS Chat Completions request using `urllib` and returns a validated assistant message or raises `ModelError`. |
| `tools.py` | A tool name and JSON-encoded arguments | Validates the operation, obtains approval when required, and returns a dictionary with `ok` and result data or an error. |
| `tests/` | Mocked model replies and temporary workspaces | Checks CLI, API message format, history, permission checks, and real local file and subprocess behavior. |

### One agent turn

**User request → Model → Tool call → Tool execution → Tool result → Model → Final answer**

1. You ask, “What does `example.py` contain?” The agent appends a user message
   and sends the history and tool definitions to OpenAI.
2. The model returns an assistant message with call ID `call_read`, function
   `read_file`, and arguments `{"path":"example.py"}`.
3. MiniCodex validates the relative path and reads the file. It appends the
   assistant call to history ahead of a tool message with the matching
   `tool_call_id` and JSON content such as `{"ok":true,"content":"answer = 42\n"}`.
4. MiniCodex sends the updated history. The model can reply “`example.py`
   assigns 42 to `answer`.” The CLI prints the reply and waits for the next input.

The same message sequence carries tool errors and declined operations. The
limit defaults to eight model requests per turn. If the last request returns a
tool call, MiniCodex stops before executing that call so completed actions
always have room for a follow-up request. The same session can accept another
message afterward. [Chat Completions function calling](https://developers.openai.com/api/docs/guides/function-calling?api-mode=chat)

## Tool limits

- `read_file` and `write_file` use workspace-relative paths and UTF-8 text.
  MiniCodex checks resolved paths against the workspace and rejects parent
  traversal, absolute paths, and symlink escapes. Git metadata and `.env`
  files are blocked. Each file operation is limited to 64 KiB. Writes require
  an existing parent directory and replace the destination with the full
  proposed content after approval.
- `run_command` accepts `pytest` and launches the current interpreter with
  `-m pytest -q`. It uses a fixed argument list, no shell, a 30-second timeout,
  and at most 16 KiB of captured combined output. The timeout kills the direct
  child process. Tests and plugins run with your OS permissions and can launch
  their own processes, so only approve test runs in workspaces you trust.
- File path checks are subject to races if another process changes the
  filesystem during an operation. MiniCodex does not provide OS-level
  isolation. Conversation history, file contents read by the tools, and
  command output are sent to OpenAI and held locally in memory for the session.
  Use a workspace whose contents you are comfortable sending to the API.

## Verify and troubleshoot

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

| Symptom | What to check |
| --- | --- |
| Missing key or HTTP 401 | Load `OPENAI_API_KEY` in the same shell. For 401, verify that the project key is active. |
| HTTP 403 or 404 | Check the project's access to the model and the value of `OPENAI_MODEL`. |
| HTTP 429 | Check project quota and API rate limits. |
| Could not reach OpenAI | Check network connectivity and any proxy settings. |
| Missing file or invalid path | Check `--workspace` and give a path relative to it. |
| Pytest missing or tests fail | Install pytest in the same interpreter and inspect the tool's exit code and captured output. |
| Turn reaches the request limit | Narrow the request or restart with a higher `--max-iterations N`. Long sessions resend more history. |

See [LEARNING.md](LEARNING.md) for a guided code walkthrough and exercises.
