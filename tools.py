"""Validate and execute MiniCodex's three local tools."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Thread
from typing import Callable

MAX_FILE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
COMMAND_TIMEOUT = 30
Approval = Callable[[str], bool]

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }
    for name, description, properties in (
        ("read_file", "Read a UTF-8 file using a workspace-relative path.", {
            "path": {"type": "string"},
        }),
        ("write_file", "Create or replace a UTF-8 file. Provide the full new content. Requires approval.", {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }),
        ("run_command", "Run the workspace pytest suite. Requires approval.", {
            "command": {"type": "string", "enum": ["pytest"]},
        }),
    )
]
ARGUMENTS = {
    definition["function"]["name"]: set(definition["function"]["parameters"]["required"])
    for definition in TOOL_DEFINITIONS
}


class ToolError(Exception):
    """Invalid or unsupported tool operation."""


def redact(text: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return text.replace(key, "[REDACTED]") if key else text


class WorkspaceTools:
    def __init__(self, workspace: Path, approve: Approval) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory.")
        self.approve = approve

    def execute(self, name: str, arguments: str) -> dict:
        """Turn serialized arguments and expected failures into a tool result."""
        if name not in ARGUMENTS:
            return {"ok": False, "error": "Unknown tool."}
        try:
            values = json.loads(arguments)
        except (ValueError, RecursionError):
            return {"ok": False, "error": "Tool arguments must be valid JSON."}
        if (
            not isinstance(values, dict)
            or values.keys() != ARGUMENTS[name]
            or any(not isinstance(value, str) for value in values.values())
        ):
            return {"ok": False, "error": "Tool arguments do not match the required fields."}

        try:
            if name == "read_file":
                return self.read_file(values["path"])
            if name == "write_file":
                return self.write_file(values["path"], values["content"])
            return self.run_command(values["command"])
        except ToolError as error:
            return {"ok": False, "error": str(error)}
        except FileNotFoundError:
            return {"ok": False, "error": "File or parent directory was not found."}
        except PermissionError:
            return {"ok": False, "error": "The operating system denied permission."}
        except UnicodeError:
            return {"ok": False, "error": "The file or content is not valid UTF-8."}
        except (OSError, ValueError):
            return {"ok": False, "error": "The operating system could not complete the operation."}

    def resolve_path(self, path: str) -> Path:
        relative = Path(path)
        if not relative.parts or relative.anchor or ".." in relative.parts or "\x00" in path:
            raise ToolError("Use a workspace-relative file path without '..'.")
        if os.name == "nt" and (
            relative.is_reserved()
            or any(":" in part or part.endswith((" ", ".")) for part in relative.parts)
        ):
            raise ToolError("Invalid Windows file path.")
        try:
            target = (self.workspace / relative).resolve()
        except RuntimeError:
            raise ToolError("The file path contains a symlink loop.") from None
        if not target.is_relative_to(self.workspace):
            raise ToolError("The file path resolves outside the workspace.")
        for candidate in (relative, target.relative_to(self.workspace)):
            for part in candidate.parts:
                name = part.casefold()
                if name in {".git", ".env"} or name.startswith(".env."):
                    raise ToolError("Access to Git metadata and environment files is disabled.")
        return target

    def read_file(self, path: str) -> dict:
        target = self.resolve_path(path)
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise ToolError("The path must be a regular file without hard links.")
        with target.open("rb") as file:
            data = file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ToolError(f"File exceeds the {MAX_FILE_BYTES}-byte read limit.")
        return {"ok": True, "content": redact(data.decode("utf-8"))}

    def write_file(self, path: str, content: str) -> dict:
        target = self.resolve_path(path)
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ToolError(f"Content exceeds the {MAX_FILE_BYTES}-byte write limit.")
        if not target.parent.is_dir():
            raise ToolError("The parent directory must already exist.")
        existing = target.stat() if target.exists() else None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ToolError("The path must be a regular file.")
        if redact(content) != content:
            raise ToolError("The proposed content includes the configured OpenAI API key.")
        action = "Replace" if existing else "Create"
        preview = json.dumps(content, ensure_ascii=True)
        if not self.approve(f"{action} {str(target)!a} with this UTF-8 content:\n{preview}"):
            raise ToolError("User denied approval.")
        if self.resolve_path(path) != target:
            raise ToolError("The file path changed during approval.")
        current = target.stat() if target.exists() else None
        if current != existing or (
            current is not None and existing is not None
            and current.st_mtime_ns != existing.st_mtime_ns
        ):
            raise ToolError("The destination changed during approval.")

        temporary = None
        try:
            with NamedTemporaryFile(dir=target.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
            if existing and os.name != "nt":
                temporary.chmod(stat.S_IMODE(existing.st_mode))
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"ok": True, "bytes_written": len(data)}

    def run_command(self, command: str) -> dict:
        if command != "pytest":
            raise ToolError("Only the pytest command is permitted.")
        argv = [sys.executable, "-m", "pytest", "-q"]
        if not self.approve(f"Run {argv!a} in {str(self.workspace)!a}"):
            raise ToolError("User denied approval.")
        environment = os.environ.copy()
        environment.pop("OPENAI_API_KEY", None)
        return run_pytest(argv, self.workspace, environment)


def run_pytest(argv: list[str], workspace: Path, environment: dict[str, str]) -> dict:
    """Drain the pipe while retaining only a small prefix of its bytes."""
    output = bytearray()
    process = subprocess.Popen(
        argv, cwd=workspace, env=environment, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    pipe = process.stdout
    assert pipe is not None

    def drain() -> None:
        with pipe:
            while chunk := pipe.read1(4096):
                available = MAX_OUTPUT_BYTES + 1 - len(output)
                output.extend(chunk[:available])

    reader = Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=COMMAND_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        # A descendant may still hold the pipe open after the direct child exits.
        reader.join(timeout=1)

    captured = bytes(output)
    result = {
        "ok": not timed_out and process.returncode == 0,
        "output": redact(captured[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")),
        "truncated": len(captured) > MAX_OUTPUT_BYTES or reader.is_alive(),
        "exit_code": process.returncode,
    }
    if timed_out:
        result["error"] = f"Command timed out after {COMMAND_TIMEOUT} seconds."
    elif process.returncode:
        result["error"] = f"pytest exited with code {process.returncode}."
    return result
