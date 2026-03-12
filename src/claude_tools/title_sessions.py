#!/usr/bin/env python3
"""Generate AI titles for Claude Code sessions that don't have one.

Reads the first messages of each session and calls the Claude API to generate
a short, descriptive title. Writes the title as a custom-title entry to the
session JSONL file (supported by both CLI and VS Code extension).
Never overwrites existing custom or AI titles.

Interactive: claude-title-sessions
Direct:      claude-title-sessions <project-path>
All:         claude-title-sessions --all
Dry run:     claude-title-sessions [--all | <path>] --dry-run
"""

import json
import os
import sys

from claude_tools.utils import (
    DEFAULT_MODEL,
    PROJECTS_DIR,
    call_claude,
    dirname_to_path,
    list_project_dirs,
    list_sessions,
    path_to_dirname,
    preserve_mtime,
    require_api_key,
    require_projects_dir,
    resolve_path,
    session_description,
)

TITLE_PROMPT = """\
Generate a succinct title for a coding session based on the provided description.

Rules:
- Maximum 6 words.
- Always use imperative mood (e.g. "Add", "Fix", "Refactor", "Implement", "Update", \
"Debug", "Migrate", "Set up", "Remove", "Improve").
- Use sentence case (capitalize only the first word and proper nouns).
- Be specific about what was done, not vague.
- No articles ("a", "the") unless necessary for clarity.

Good examples:
- "Fix login button on mobile"
- "Add Whisper subtitle generation"
- "Migrate SQLAlchemy models to Mapped"
- "Set up pre-commit hooks"
- "Debug Metabase network connectivity"

Bad examples (do NOT generate these styles):
- "Building a new feature" (gerund, not imperative)
- "Login button fix" (noun phrase, not imperative)
- "Working on the API" (vague, gerund)

Return ONLY the title text. No quotes, no JSON, no explanation.

<description>{description}</description>"""


def generate_title(api_key, description, model):
    """Call the Claude API to generate a session title."""
    text = call_claude(
        api_key, model,
        [{"role": "user", "content": TITLE_PROMPT.format(description=description)}],
        max_tokens=50,
    )
    if not text:
        return None
    title = text.strip('"').strip("'")
    # Reject titles that are clearly confused / meta-responses
    if "I need more" in title or "provide" in title.lower():
        return None
    # Truncate to fit the VS Code sidebar / CLI resume list
    if len(title) > 60:
        title = title[:57] + "..."
    return title


def build_description(session):
    """Build a description string from session data to feed to the title generator."""
    parts = []
    if session["summary"]:
        parts.append(session["summary"])
    if session["first_prompt"]:
        # Truncate very long first prompts
        fp = session["first_prompt"][:500]
        parts.append(fp)
    return "\n\n".join(parts) if parts else ""


def write_title(project_dir, session_id, title):
    """Append a custom-title entry to the session JSONL file.

    Uses custom-title (not ai-title) because the CLI only supports custom-title,
    while the VS Code extension supports both. Preserves the file's mtime so
    Claude Code doesn't think the session was just used.
    """
    filepath = os.path.join(project_dir, f"{session_id}.jsonl")
    entry = json.dumps({"type": "custom-title", "sessionId": session_id, "customTitle": title})
    with preserve_mtime(filepath):
        with open(filepath, "a") as fh:
            fh.write(entry + "\n")


def title_project(project_dir, project_name, api_key, model, dry_run):
    """Generate titles for untitled sessions in a project. Returns count of titles generated."""
    sessions = list_sessions(project_dir)
    untitled = [
        s for s in sessions
        if not s["custom_title"] and not s["ai_title"] and s["msg_count"] > 0
    ]

    if not untitled:
        print(f"  No untitled sessions.")
        return 0

    print(f"  {len(untitled)} untitled session(s) out of {len(sessions)} total.")
    count = 0

    for s in untitled:
        desc_text = build_description(s)
        if not desc_text:
            print(f"    {s['session_id']}  (skipped — no content to describe)")
            continue

        title = generate_title(api_key, desc_text, model)
        if not title:
            print(f"    {s['session_id']}  (skipped — API error)")
            continue

        if dry_run:
            print(f"    {s['session_id']}  -> {title}")
        else:
            write_title(project_dir, s["session_id"], title)
            print(f"    {s['session_id']}  -> {title}")
        count += 1

    return count


def main():
    require_projects_dir()

    dry_run = False
    scan_all = False
    target_path = ""
    model = DEFAULT_MODEL

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            print("Usage:")
            print("  claude-title-sessions                  # interactive (pick a project)")
            print("  claude-title-sessions <project-path>   # title specific project")
            print("  claude-title-sessions --all            # title all projects")
            print("  claude-title-sessions --dry-run        # preview only, don't write")
            print(f"  claude-title-sessions --model <id>     # model (default: {DEFAULT_MODEL})")
            sys.exit(0)
        elif arg == "--dry-run":
            dry_run = True
        elif arg == "--all":
            scan_all = True
        elif arg == "--model":
            i += 1
            if i >= len(args):
                print("--model requires a value")
                sys.exit(1)
            model = args[i]
        else:
            target_path = arg
        i += 1

    api_key = require_api_key()

    if dry_run:
        print("(dry-run mode — titles will be shown but not written)\n")

    # --all mode
    if scan_all:
        projects = list_project_dirs()
        if not projects:
            print("No project histories found.")
            sys.exit(0)

        print(f"Titling sessions across {len(projects)} project(s)...\n")
        total = 0
        for dir_name, decoded, full_path in projects:
            print(f"Project: {decoded}")
            total += title_project(full_path, decoded, api_key, model, dry_run)
            print()

        print(f"{'Would generate' if dry_run else 'Generated'} {total} title(s).")
        sys.exit(0)

    # Direct mode
    if target_path:
        target_path = resolve_path(target_path).rstrip("/")
        dir_name = path_to_dirname(target_path)
        project_dir = os.path.join(PROJECTS_DIR, dir_name)

        if not os.path.isdir(project_dir):
            print(f"No Claude history found for: {target_path}")
            sys.exit(1)

        print(f"Project: {target_path}\n")
        count = title_project(project_dir, target_path, api_key, model, dry_run)
        print(f"\n{'Would generate' if dry_run else 'Generated'} {count} title(s).")
        sys.exit(0)

    # Interactive mode
    print("claude-title-sessions - Generate AI titles for untitled sessions\n")

    projects = list_project_dirs()
    if not projects:
        print("No project histories found.")
        sys.exit(0)

    # Check current directory first
    cwd = os.getcwd()
    cwd_dirname = path_to_dirname(cwd)
    cwd_project_dir = os.path.join(PROJECTS_DIR, cwd_dirname)
    has_current = os.path.isdir(cwd_project_dir)

    if has_current:
        print(f"  c) Current project: {cwd}  (default)")
    print("  a) All projects")
    print("  s) Select a specific project")
    print()

    try:
        if has_current:
            choice = input("Scope (c/a/s): ").strip().lower() or "c"
        else:
            choice = input("Scope (a/s): ").strip().lower() or "a"
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(0)

    if choice == "c":
        if not has_current:
            print(f"No Claude history found for current directory: {cwd}")
            sys.exit(1)
        print(f"\nProject: {cwd}\n")
        count = title_project(cwd_project_dir, cwd, api_key, model, dry_run)
        print(f"\n{'Would generate' if dry_run else 'Generated'} {count} title(s).")

    elif choice == "a":
        print(f"\nTitling sessions across {len(projects)} project(s)...\n")
        total = 0
        for dir_name, decoded, full_path in projects:
            print(f"Project: {decoded}")
            total += title_project(full_path, decoded, api_key, model, dry_run)
            print()
        print(f"{'Would generate' if dry_run else 'Generated'} {total} title(s).")

    elif choice == "s":
        print()
        for idx, (dir_name, decoded, full_path) in enumerate(projects, 1):
            marker = "  *" if not os.path.isdir(decoded) else "   "
            print(f"  {marker} {idx}) {decoded}")
        print()

        try:
            pick = input("Project #: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(0)

        if not pick:
            print("Cancelled.")
            sys.exit(0)

        try:
            idx = int(pick) - 1
        except ValueError:
            print("Invalid choice.")
            sys.exit(1)

        if idx < 0 or idx >= len(projects):
            print("Invalid choice.")
            sys.exit(1)

        dir_name, decoded, full_path = projects[idx]
        print(f"\nProject: {decoded}\n")
        count = title_project(full_path, decoded, api_key, model, dry_run)
        print(f"\n{'Would generate' if dry_run else 'Generated'} {count} title(s).")

    elif choice == "q":
        print("Cancelled.")
    else:
        print("Invalid choice.")
        sys.exit(1)


if __name__ == "__main__":
    main()