#!/usr/bin/env python3
"""Move Claude Code project history when you rename/move a project directory.

Run from the NEW directory, or pass paths as arguments.

Interactive: claude_set_history.py
Direct:     claude_set_history.py <old-path> <new-path>
"""

import glob
import json
import os
import shutil
import sys

try:
    from claude_tools.utils import (
        PROJECTS_DIR, HISTORY_FILE, path_to_dirname, dirname_to_path,
        preserve_mtime, require_projects_dir, print_sessions, prompt_choice,
        list_project_dirs,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from utils import (  # type: ignore[no-redef]
        PROJECTS_DIR, HISTORY_FILE, path_to_dirname, dirname_to_path,
        preserve_mtime, require_projects_dir, print_sessions, prompt_choice,
        list_project_dirs,
    )


def resolve_path_arg(path):
    """Resolve a user-supplied path argument to an absolute path."""
    return os.path.realpath(os.path.abspath(path))


def clean_broken_resume_artifacts(project_dir):
    """Remove broken resume artifacts from session .jsonl files.

    Looks for sessions where a good summary is followed by junk summaries
    (starting with "I don" or "Unable to generate") and truncates the file
    after the last good summary.

    Returns the number of files cleaned.
    """
    cleaned = 0
    for f in sorted(glob.glob(os.path.join(project_dir, "*.jsonl"))):
        if not os.path.isfile(f):
            continue

        with open(f, "r", errors="replace") as fh:
            lines = fh.readlines()

        last_good_summary_idx = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                e = json.loads(stripped)
                if e.get("type") == "summary":
                    s = e.get("summary", "")
                    if s and not s.startswith("I don") and not s.startswith("Unable to generate"):
                        last_good_summary_idx = i
            except (json.JSONDecodeError, ValueError):
                pass

        if last_good_summary_idx is None:
            continue

        has_junk = False
        for i in range(last_good_summary_idx + 1, len(lines)):
            stripped = lines[i].strip()
            if not stripped:
                continue
            try:
                e = json.loads(stripped)
                if e.get("type") == "summary":
                    s = e.get("summary", "")
                    if s.startswith("I don") or s.startswith("Unable to generate"):
                        has_junk = True
                        break
            except (json.JSONDecodeError, ValueError):
                pass

        if has_junk:
            good_lines = lines[: last_good_summary_idx + 1]
            removed = len(lines) - len(good_lines)
            with preserve_mtime(f):
                with open(f, "w") as fh:
                    fh.writelines(good_lines)
            print(f"  Cleaned {removed} broken line(s) from {os.path.basename(f)}")
            cleaned += 1

    return cleaned


def main():
    require_projects_dir()

    # --- Interactive mode ---
    if len(sys.argv) == 1:
        target_path = os.getcwd()
        target_dir_name = path_to_dirname(target_path)
        target_project_dir = os.path.join(PROJECTS_DIR, target_dir_name)

        print("move-claude-history - Move Claude conversation history to a renamed directory")
        print()
        print(f"Current directory: {target_path}")
        print()

        # Show current directory's existing history
        if os.path.isdir(target_project_dir):
            print("This directory already has Claude history:")
            print_sessions(target_project_dir)
            print()
        else:
            print("This directory has no Claude history yet.")
            print()

        # List all project directories, marking orphaned ones
        print("All project histories (* = orphaned, directory no longer exists):")
        print()

        candidates = []
        idx = 0
        orphan_count = 0

        for dir_name, decoded, full_path in list_project_dirs():
            # Skip current directory
            if dir_name == target_dir_name:
                continue

            idx += 1
            candidates.append(dir_name)

            # Check if original directory still exists
            if os.path.isdir(decoded):
                print(f"     {idx}) {decoded}")
            else:
                print(f"  *  {idx}) {decoded}")
                orphan_count += 1

            print_sessions(full_path)
            print()

        if idx == 0:
            print("  (no other project histories found)")
            sys.exit(0)

        if orphan_count == 0:
            print("No orphaned histories found. All projects still point to valid directories.")
            print("You can still pick one to move, or run: move-claude-history <old-path> <new-path>")
            print()

        print("---")
        choice_idx = prompt_choice(
            "Move which project's history to this directory? (#, or 'q' to quit): ",
            idx,
            allow_quit=True,
        )

        if choice_idx is None:
            print("Cancelled.")
            sys.exit(0)

        selected_dir_name = candidates[choice_idx]
        old_path = dirname_to_path(selected_dir_name)
        new_path = target_path

    elif len(sys.argv) == 3:
        # --- Direct mode ---
        old_path = resolve_path_arg(sys.argv[1])
        new_path = resolve_path_arg(sys.argv[2])

    else:
        print("Usage:")
        print("  move-claude-history                    # interactive (run from new directory)")
        print("  move-claude-history <old-path> <new-path>  # direct")
        sys.exit(1)

    # Strip trailing slashes
    old_path = old_path.rstrip("/")
    new_path = new_path.rstrip("/")

    if old_path == new_path:
        print("Old and new paths are identical. Nothing to do.")
        sys.exit(0)

    old_dir_name = path_to_dirname(old_path)
    new_dir_name = path_to_dirname(new_path)
    old_project_dir = os.path.join(PROJECTS_DIR, old_dir_name)
    new_project_dir = os.path.join(PROJECTS_DIR, new_dir_name)

    print()
    print("Moving history:")
    print(f"  From: {old_path}")
    print(f"  To:   {new_path}")
    print()

    # --- Check state ---

    if os.path.isdir(new_project_dir) and os.path.isdir(old_project_dir):
        print("Both old and new project directories exist. Cannot merge automatically.")
        print(f"  Old: {old_project_dir}")
        print(f"  New: {new_project_dir}")
        sys.exit(1)

    if not os.path.isdir(old_project_dir) and not os.path.isdir(new_project_dir):
        print("No Claude history found for either path.")
        sys.exit(1)

    # --- Rename project directory ---

    if os.path.isdir(old_project_dir):
        print("Renaming project directory...")
        shutil.move(old_project_dir, new_project_dir)
        print("  Done.")
        print()
    elif os.path.isdir(new_project_dir):
        print("Project directory already renamed (skipping).")
        print()

    # --- Fix session .jsonl files (cwd fields) ---

    print("Fixing session files...")
    session_count = 0
    for f in sorted(glob.glob(os.path.join(new_project_dir, "*.jsonl"))):
        if not os.path.isfile(f):
            continue
        with open(f, "r", errors="replace") as fh:
            content = fh.read()
        old_ref = f'"{old_path}"'
        new_ref = f'"{new_path}"'
        if old_ref in content:
            content = content.replace(old_ref, new_ref)
            with preserve_mtime(f):
                with open(f, "w") as fh:
                    fh.write(content)
            session_count += 1
    print(f"  Updated {session_count} session file(s).")
    print()

    # --- Fix sessions-index.json ---

    sessions_index = os.path.join(new_project_dir, "sessions-index.json")
    if os.path.isfile(sessions_index):
        print("Fixing sessions-index.json...")
        with open(sessions_index, "r", errors="replace") as fh:
            content = fh.read()
        content = content.replace(old_dir_name, new_dir_name)
        content = content.replace(f'"{old_path}"', f'"{new_path}"')
        with open(sessions_index, "w") as fh:
            fh.write(content)
        print("  Done.")
    else:
        print("No sessions-index.json found (will be rebuilt by Claude on next launch).")
    print()

    # --- Fix global history.jsonl ---

    if os.path.isfile(HISTORY_FILE):
        print("Fixing global history.jsonl...")
        with open(HISTORY_FILE, "r", errors="replace") as fh:
            content = fh.read()
        old_ref = f'"{old_path}"'
        before_count = content.count(old_ref)
        if before_count > 0:
            content = content.replace(old_ref, f'"{new_path}"')
            with open(HISTORY_FILE, "w") as fh:
                fh.write(content)
            print(f"  Updated {before_count} reference(s).")
        else:
            print("  No references found (already clean).")
    else:
        print("No global history.jsonl found.")
    print()

    # --- Remove broken resume artifacts ---

    print("Cleaning up broken resume artifacts...")
    cleaned = clean_broken_resume_artifacts(new_project_dir)
    if cleaned == 0:
        print("  No broken artifacts found.")
    print()

    print(f"Done! Run 'claude --resume' from {new_path}")


if __name__ == "__main__":
    main()
