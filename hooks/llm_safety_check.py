#!/usr/bin/env python3
# ~/.claude/hooks/llm-safety-check.py
#
# PreToolUse hook that sends Bash commands to an LLM for safety evaluation.
# - "approve" -> auto-allows the command (skips permission prompt)
# - "deny"    -> blocks the command (exit 2, stderr shown to Claude)
# - "prompt"  -> falls through to normal Claude Code permission dialog

import json
import os
import subprocess
import sys
import urllib.request


def _get_api_key() -> str:
    """Resolve the Anthropic API key: env var first, then macOS Keychain."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    return ""


def check_command_safety(tool_name: str, tool_input: dict) -> dict | None:
    command = tool_input.get("command", "")
    description = tool_input.get("description", "")

    api_key = _get_api_key()
    if not api_key:
        print("LLM safety check: no API key found (env or Keychain)", file=sys.stderr)
        return None

    system_prompt = """You are a security judge for an AI coding assistant. You will be shown a shell command that the assistant wants to execute, and you must decide if it is safe to run.

Respond ONLY with a JSON object in this exact format:
{
  "decision": "approve" | "deny" | "prompt",
  "reason": "brief explanation"
}

Guidelines:
- "approve": Clearly safe, read-only, or routine dev operations. E.g. ls, grep, cat, git status, running tests, installing packages, building code, Python/Ruby/Perl heredocs, file inspection, docker commands for local dev.
- "deny": Clearly dangerous or destructive. E.g. rm -rf on important paths, curl | bash from unknown sources, overwriting system files, exfiltrating data to external services, anything that looks like an actual exploit or obfuscation technique, hidden arguments via newline+comment tricks.
- "prompt": Ambiguous - could be legitimate or risky depending on context. E.g. deleting a specific file, modifying config files, writing to paths outside the project, git force push, network requests to unfamiliar hosts.

Important context for accurate analysis:
- Quoted heredocs (<< 'EOF', << 'PYEOF', << 'SCRIPT', etc.) suppress ALL shell expansion. Variable references ($var), brace expansion ({...}), command substitution ($(...)), and any other shell metacharacters inside a quoted heredoc are passed as literal text to the target program. Do NOT flag patterns inside quoted heredocs as shell obfuscation - they are just source code in the target language (Python, Ruby, etc.).
- Unquoted heredocs (<< EOF without quotes) DO allow shell expansion, so those warrant normal scrutiny.
- Python f-strings like f'{var["key"]}' contain braces and quotes as part of Python syntax, not shell obfuscation.
- Build tools, package managers (npm, pip, uv, cargo), and test runners are routine dev operations.

When in doubt, prefer "prompt" over "deny". Only "deny" things that are unambiguously dangerous.
Do not output anything other than the JSON object."""

    user_message = (
        f"Tool: {tool_name}\n"
        f"Command: {command}\n"
        f"Description: {description if description else '(none provided)'}"
    )

    payload = json.dumps({
        "model": "claude-opus-4-6",
        "max_tokens": 1000,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "max"},
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read())
            # Extract text from content blocks (thinking blocks may also be present)
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block["text"].strip()
                    break
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
    except Exception as e:
        # On any failure, fall through to normal Claude Code handling
        print(f"LLM safety check failed: {e}", file=sys.stderr)
        return None


def main():
    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    result = check_command_safety(tool_name, tool_input)

    if result is None:
        # Couldn't reach API - let Claude Code decide normally
        sys.exit(0)

    decision = result.get("decision", "prompt")
    reason = result.get("reason", "")

    if decision == "approve":
        # Exit 0 with hookSpecificOutput to auto-allow
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": reason,
            }
        }
        print(json.dumps(output))
        sys.exit(0)
    elif decision == "deny":
        # Exit 2 + stderr = Claude Code blocks the tool call
        print(f"Blocked by safety check: {reason}", file=sys.stderr)
        sys.exit(2)
    else:
        # "prompt" - exit 0 without output = normal permission dialog
        sys.exit(0)


if __name__ == "__main__":
    main()
