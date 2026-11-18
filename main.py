"""Interactive entry point for MiniCodex."""

import argparse
import os
from pathlib import Path

from agent import Agent
from model_client import OpenAIClient
from tools import WorkspaceTools, redact


def process_input(message: str, agent: Agent) -> str | None:
    message = message.strip()
    if message.casefold() == "exit":
        return None
    return agent.run_turn(redact(message)) if message else ""


def run_cli(agent: Agent) -> None:
    while True:
        try:
            user_input = input("minicodex > ")
            agent_response = process_input(user_input, agent)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if agent_response is None:
            break
        if agent_response:
            # Keep newlines and tabs while escaping terminal control characters.
            text = redact(agent_response)
            print("".join(
                char if char.isprintable() or char in "\n\t" else ascii(char)[1:-1]
                for char in text
            ))
    print("goodbye!")


def approve(operation: str) -> bool:
    print(redact(operation))
    return input("Approve this operation? [yes/no] ").strip().casefold() == "yes"


def main() -> None:
    parser = argparse.ArgumentParser(description="MiniCodex coding assistant")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--max-iterations", type=int, default=8, metavar="N")
    args = parser.parse_args()
    if args.max_iterations < 1:
        parser.error("--max-iterations must be positive")
    try:
        tools = WorkspaceTools(args.workspace, approve)
    except (OSError, ValueError, RuntimeError):
        parser.error("--workspace must be an accessible directory")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        parser.error("set OPENAI_API_KEY in your environment before starting MiniCodex")
    model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4.1-mini"
    run_cli(Agent(OpenAIClient(key, model), tools, args.max_iterations))


if __name__ == "__main__":
    main()
