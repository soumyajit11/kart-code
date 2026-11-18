# Learning MiniCodex

## 1. The CLI and Python entry points

`main.py` owns input and output. `main()` reads options and environment
variables, then constructs the agent and runs `run_cli()`. The loop passes each
line to `process_input()`, which returns a reply, an empty string for blank
input, or `None` to end the session. The explicit `is None` check distinguishes
exit from blank input. The `if __name__ == "__main__"` guard makes the functions
importable without starting the interactive loop. Catching `EOFError` and
`KeyboardInterrupt` gives the terminal a clean exit path.

Run `python -m pytest -q tests/test_cli.py` to check input handling and imports.

**Exercise.** Add a test that enters a blank line followed by `exit`. Check
that the model was never called.

## 2. HTTPS, JSON, and tool schemas

`OpenAIClient.complete()` in `model_client.py` receives Python lists and
dictionaries. `json.dumps()` serializes the payload and UTF-8 encoding turns
it into request bytes. The API key goes in an authorization header.
`parse_reply()` checks the shape of the decoded response and returns a usable
assistant message. The Chat Completions endpoint uses a JSON string inside
`function.arguments`, so the local tool dispatcher decodes that string
separately.

Each definition in `tools.py` describes the function's name, purpose, and
allowed arguments. The API can use that schema to construct a structured
request. The application still checks arguments before using them.
[OpenAI Chat Completions API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

Run `python -m pytest -q tests/test_model_client.py` to inspect serialized
requests and simulate an HTTP failure locally.

**Exercise.** Create a response with `finish_reason="length"` in a client test
and assert that it raises `ModelError` with the token-limit message.

## 3. The loop, history, and call IDs

`Agent.run_turn()` in `agent.py` appends a user message and iterates over a
fixed request budget. Each answer without a tool request ends the turn. For a
tool request, the agent adds the assistant message, invokes the tool, and
appends its result with the matching call ID. The next request includes those
messages in order, which lets the model explain what happened. The `Model`
protocol describes the one method the agent needs. Tests can provide a fake
object with that same method.

At the iteration limit, the agent skips any tool calls from the last reply
and leaves them out of history. An API error leaves completed tool results in
history, including any write that succeeded before the failure. That matters
if the user follows up after an error.

Run `python -m pytest -q tests/test_agent.py` to check the message order and
the state after a limit or an API error.

**Exercise.** In a fake model reply, request two reads with different call
IDs. Assert that the next model request contains both results in order.

## 4. Files, paths, and approval callbacks

`WorkspaceTools.execute()` in `tools.py` validates the tool name and argument
types. `resolve_path()` uses `pathlib` to resolve symlinks before checking
whether the destination is within the workspace. File reads decode bytes as
UTF-8. Common exceptions such as `FileNotFoundError` and `PermissionError`
become result dictionaries that the model can explain.

`write_file()` shows the proposal through an approval callback. Passing the
callback in lets the CLI use terminal input and tests use a simple function
that returns true or false. After approval the tool rechecks the destination,
writes to a temporary file in the same directory, and replaces the destination
after the write completes. This keeps a partially written replacement from
appearing at the destination if the temporary write fails.

Run `python -m pytest -q tests/test_tools.py -k "path or file or write or symlink"`
to check these boundaries.

**Exercise.** Add a test in which the user denies creation of a new file.
Assert that the file still does not exist.

## 5. Subprocesses and offline tests

`run_command()` maps the tool's `pytest` value to a fixed list of process
arguments. `run_pytest()` starts that process and uses a background thread to
drain its output pipe. The thread keeps only a bounded prefix so the child can
write more output without filling the pipe or growing memory indefinitely.
The main thread waits with a timeout. The tool returns an exit code and output
for normal completion, failure, or a timeout. The approved command runs with
the current user's OS permissions.

The suite uses pytest's `tmp_path` for disposable files and `monkeypatch` to
replace terminal input and HTTP transport. Some tests start local subprocesses
to verify behavior across a real process boundary. Run
`python -m pytest -q tests/test_tools.py -k "command or subprocess"` to check
process handling and `python -m pytest -q` for the full suite.

**Exercise.** Change the local test fixture that asserts `2 + 2 == 5` to
assert `2 + 2 == 4`. Update the expected result and check how the success
flag, exit code, and captured output change.
