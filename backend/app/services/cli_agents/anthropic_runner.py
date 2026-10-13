import argparse
import asyncio
import contextlib
import json
import sys
from typing import Any

import claude_agent_sdk


AGENT_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "Bash"]
READ_ONLY_TOOLS = ["Read", "Glob", "Grep"]
DISALLOWED_TOOLS = [
    "Task", "Agent", "WebFetch", "WebSearch",
    "Bash(git push:*)", "Bash(git commit:*)", "Bash(git reset:*)", "Bash(git checkout:*)",
    "Bash(git clean:*)", "Bash(git stash:*)", "Bash(rm:*)", "Bash(rmdir:*)", "Bash(del:*)",
    "Bash(Remove-Item:*)", "Bash(powershell:*)", "Bash(pwsh:*)", "Bash(cmd:*)",
    "Bash(curl:*)", "Bash(wget:*)",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--max-turns", required=True, type=int)
    parser.add_argument("--max-budget-usd", required=True, type=float)
    parser.add_argument("--read-only", action="store_true")
    return parser.parse_args(argv)


def build_options(args: argparse.Namespace) -> claude_agent_sdk.ClaudeAgentOptions:
    denied = list(DISALLOWED_TOOLS)
    if args.read_only:
        denied.extend(["Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"])
    tools = READ_ONLY_TOOLS if args.read_only else AGENT_TOOLS
    return claude_agent_sdk.ClaudeAgentOptions(
        model=args.model, cwd=args.cwd, permission_mode="plan" if args.read_only else "acceptEdits",
        allowed_tools=list(tools), tools=list(tools), disallowed_tools=denied,
        max_turns=args.max_turns, max_budget_usd=args.max_budget_usd,
        setting_sources=[], strict_mcp_config=True,
        system_prompt={"type": "preset", "preset": "claude_code"},
    )


def empty_result() -> dict[str, Any]:
    return {"text": "", "is_error": False, "total_cost_usd": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}, "session_id": "", "error": ""}


def result_error(message: claude_agent_sdk.ResultMessage) -> str:
    errors = getattr(message, "errors", None) or []
    parts = [str(error) for error in errors]
    subtype = getattr(message, "subtype", "")
    if subtype and subtype != "success":
        parts.append(subtype)
    return "\n".join(parts) or str(message.result or "Error del Agent SDK")


def update_result(result: dict[str, Any], message: claude_agent_sdk.ResultMessage) -> None:
    failed = message.is_error or message.subtype.startswith("error_")
    result.update(text=message.result or result["text"], is_error=failed,
                  total_cost_usd=message.total_cost_usd, usage=message.usage or result["usage"],
                  session_id=message.session_id)
    if failed:
        result["error"] = result_error(message)
    status = getattr(message, "api_error_status", None)
    if status is not None:
        result["api_error_status"] = status


def record_exception(result: dict[str, Any], error: Exception) -> None:
    data = getattr(error, "data", None)
    if isinstance(data, dict):
        for key in ("total_cost_usd", "usage", "session_id", "api_error_status"):
            if data.get(key) is not None:
                result[key] = data[key]
    result["is_error"] = True
    result["error"] = "\n".join(part for part in (result["error"], str(error)) if part)
    status = getattr(error, "api_error_status", None)
    if status is not None:
        result["api_error_status"] = status


async def run_query(task: str, args: argparse.Namespace) -> dict[str, Any]:
    result = empty_result()
    completed = False
    try:
        async for message in claude_agent_sdk.query(prompt=task, options=build_options(args)):
            if isinstance(message, claude_agent_sdk.ResultMessage):
                update_result(result, message)
                completed = True
            elif isinstance(message, claude_agent_sdk.AssistantMessage):
                chunks = [block.text for block in message.content if isinstance(block, claude_agent_sdk.TextBlock)]
                result["text"] += "\n".join(chunks)
        if not completed:
            raise RuntimeError("El Agent SDK terminó sin un resultado final")
    except Exception as error:
        record_exception(result, error)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(run_query(sys.stdin.read(), args))
    except Exception as error:
        result = empty_result()
        record_exception(result, error)
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result["is_error"] else 0


if __name__ == "__main__":
    sys.exit(main())
