# claude-tools

A collection of CLI tools and hooks for working with [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## Installation

```bash
# Clone and install (editable mode)
git clone https://github.com/seidnerj/claude-tools.git
cd claude-tools
uv pip install -e .

# Or with pip
pip install -e .
```

This installs the following commands:

- `claude-find-session` - Search conversation history
- `claude-redact-secrets` - Find and redact leaked secrets
- `claude-set-history` - Move history after renaming a project directory
- `claude-set-key` - Manage per-directory API keys in macOS Keychain
- `claude-title-sessions` - Generate AI titles for untitled sessions

### Hook (LLM Safety Check)

The safety hook requires `tsx` installed globally:

```bash
npm install -g tsx
```

Then symlink and configure:

```bash
ln -sf "$(pwd)/ts/src/bin/llm-safety-check.ts" ~/.claude/hooks/llm-safety-check.ts
```

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "tsx ~/.claude/hooks/llm-safety-check.ts",
            "timeout": 35
          }
        ]
      }
    ]
  }
}
```

## Tools

### claude-find-session

Search Claude Code conversation history by text or natural language (LLM mode).

```bash
# Interactive
claude-find-session

# Text search across all projects
claude-find-session "some text" --all

# LLM-powered semantic search
claude-find-session "which session fixed the login bug?" --llm --all
```

### claude-title-sessions

Generate AI titles for untitled Claude Code sessions using the Claude API.

```bash
# Interactive
claude-title-sessions

# Title all projects
claude-title-sessions --all

# Preview without writing
claude-title-sessions --all --dry-run
```

### claude-redact-secrets

Scan Claude Code conversation history for leaked secrets and optionally redact them. Uses [detect-secrets](https://github.com/Yelp/detect-secrets) for detection.

```bash
# Interactive
claude-redact-secrets

# Scan all projects (dry run)
claude-redact-secrets --all --dry-run

# Scan and redact a specific project
claude-redact-secrets /path/to/project
```

### claude-set-key

Manage per-directory Anthropic API keys in macOS Keychain, with `.envrc` integration for automatic key loading via [direnv](https://direnv.net/).

```bash
# Interactive - set, delete, or name keys
claude-set-key
```

### claude-set-history

Move Claude Code conversation history when you rename or move a project directory.

```bash
# Interactive (run from the new directory)
claude-set-history

# Direct
claude-set-history /old/path /new/path
```

## Hooks

### llm-safety-check.ts

A `PreToolUse` hook for Claude Code that sends Bash commands to Claude (with extended thinking) for safety evaluation before execution. Returns one of:

- **approve** - auto-allows the command (skips permission prompt)
- **deny** - blocks the command
- **prompt** - falls through to normal Claude Code permission dialog

Requires `ANTHROPIC_API_KEY` env var or a key stored in macOS Keychain under "Claude Code".

## Requirements

- Node.js >= 20
- `tsx` installed globally (`npm install -g tsx`) - required for the safety hook
- Python 3.10+ (for the Python CLI tools)
- macOS (for Keychain-based features in `claude-set-key` and the safety hook)
- An Anthropic API key (for LLM features)

## License

MIT
