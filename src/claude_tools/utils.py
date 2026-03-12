"""Shared utilities for Claude Code history scripts."""

import json
import os
import subprocess
import sys


CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")
HISTORY_FILE = os.path.join(CLAUDE_DIR, "history.jsonl")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def path_to_dirname(path):
    """Convert an absolute path to a Claude project directory name."""
    return path.replace("/", "-").replace(".", "-")


def dirname_to_path(dirname):
    """Convert a Claude project directory name back to an absolute path.

    Since path_to_dirname replaces / with -, the reverse is ambiguous when
    path components contain dashes (e.g. 'claude-relay'). We resolve this
    by listing actual directory contents at each level.
    """
    if not dirname.startswith("-"):
        return dirname.replace("-", "/")

    parts = dirname[1:].split("-")
    if not parts:
        return "/"

    # Handle -- sequences: Claude Code replaces both / and . with -,
    # so -- typically means a dot-prefixed component (e.g. .claude → --claude).
    merged = []
    i = 0
    while i < len(parts):
        if parts[i] == "" and i + 1 < len(parts):
            merged.append("." + parts[i + 1])
            i += 2
        else:
            merged.append(parts[i])
            i += 1
    parts = merged

    def _resolve(parts, current):
        if not parts:
            return current

        # If parent exists, list its entries to find the right child
        if os.path.isdir(current):
            try:
                entries = set(os.listdir(current))
            except OSError:
                entries = set()
            # Try segments of increasing length (e.g. "foo", "foo-bar", "foo-bar-baz")
            for i in range(1, len(parts) + 1):
                segment = "-".join(parts[:i])
                if segment not in entries:
                    continue
                candidate = current + "/" + segment
                remaining = parts[i:]
                if not remaining:
                    return candidate
                if os.path.isdir(candidate):
                    result = _resolve(remaining, candidate)
                    if os.path.exists(result):
                        return result

        # No match in parent (or parent doesn't exist) — use / and continue
        return _resolve(parts[1:], current + "/" + parts[0])

    return _resolve(parts, "")


def require_projects_dir():
    """Exit if the Claude projects directory doesn't exist."""
    if not os.path.isdir(PROJECTS_DIR):
        print(f"No Claude projects directory found at {PROJECTS_DIR}")
        sys.exit(1)


def resolve_path(path):
    """Resolve a path to its absolute, real form."""
    return os.path.realpath(os.path.abspath(path))


class preserve_mtime:
    """Context manager that restores a file's mtime after modification.

    Claude Code uses file mtime to determine session recency (lastModified).
    Use this when modifying session files to avoid changing their sort order.
    """

    def __init__(self, filepath):
        self.filepath = filepath
        self.original_times = None

    def __enter__(self):
        stat = os.stat(self.filepath)
        self.original_times = (stat.st_atime, stat.st_mtime)
        return self

    def __exit__(self, *exc):
        if self.original_times:
            os.utime(self.filepath, self.original_times)
        return False


def get_api_key():
    """Get Anthropic API key from macOS Keychain, falling back to env var."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code", "-w"],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return key or None


def call_claude(api_key, model, messages, max_tokens=1024, system=None):
    """Call the Claude API and return the text response, or None on error."""
    import urllib.request
    import urllib.error

    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        body["system"] = system

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"  API error {e.code}: {error_body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  API error: {e}", file=sys.stderr)
        return None

    for block in data.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return None


def require_api_key():
    """Get API key or exit with an error message."""
    api_key = get_api_key()
    if not api_key:
        print("No API key found.")
        print("  Set ANTHROPIC_API_KEY or store a key via: claude-set-key")
        sys.exit(1)
    return api_key


def extract_strings(obj, depth=0):
    """Recursively extract all string values from a JSON object."""
    if depth > 10:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            yield from extract_strings(item, depth + 1)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from extract_strings(v, depth + 1)


def parse_session(filepath):
    """Parse a session .jsonl file and return its metadata.

    Returns a dict with keys:
        session_id, msg_count, custom_title, ai_title, summary,
        first_prompt, created, modified
    """
    msg_count = 0
    first_prompt = ""
    summary = ""
    custom_title = ""
    ai_title = ""
    created = ""
    modified = ""

    with open(filepath, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            t = e.get("type", "")

            if t == "custom-title":
                custom_title = e.get("customTitle", "")
            elif t == "ai-title":
                ai_title = e.get("aiTitle", "")
            elif t == "summary" and not summary:
                s = e.get("summary", "")
                if s and not s.startswith("I don") and not s.startswith("Unable to"):
                    summary = s

            if t in ("user", "assistant"):
                msg_count += 1
                ts = e.get("timestamp", "")
                if ts:
                    if not created:
                        created = ts
                    modified = ts
                if t == "user" and not first_prompt:
                    c = e.get("message", {}).get("content", "")
                    if isinstance(c, str) and c != "Warmup":
                        first_prompt = c

    fname = os.path.basename(filepath)
    session_id = fname.replace(".jsonl", "")

    return {
        "session_id": session_id,
        "msg_count": msg_count,
        "custom_title": custom_title,
        "ai_title": ai_title,
        "summary": summary,
        "first_prompt": first_prompt,
        "created": created,
        "modified": modified,
    }


def session_description(s):
    """Return the best description for a session, using the standard priority."""
    return s["custom_title"] or s["ai_title"] or s["summary"] or s["first_prompt"][:60]


def list_sessions(project_dir):
    """Parse all sessions in a project directory. Returns list of session dicts."""
    sessions = []
    for fname in sorted(os.listdir(project_dir)):
        if not fname.endswith(".jsonl"):
            continue
        filepath = os.path.join(project_dir, fname)
        s = parse_session(filepath)
        if s["msg_count"] > 0:
            sessions.append(s)
    return sessions


def print_sessions(project_dir):
    """Print a formatted list of sessions in a project directory."""
    try:
        sessions = list_sessions(project_dir)
    except Exception:
        print("    (error reading sessions)")
        return

    if not sessions:
        print("    (no conversation sessions)")
    else:
        for s in sessions:
            desc = session_description(s)
            sid = s["session_id"]
            cr = s["created"][:10]
            mod = s["modified"][:10]
            print(f"    {sid}  {s['msg_count']:>4} msgs  [{cr} -> {mod}]  {desc[:55]}")
    print(f"  Total: {len(sessions)} session(s)")


def list_project_dirs():
    """List all project directories with their decoded paths.

    Returns list of (dir_name, decoded_path, project_dir_full_path) tuples.
    """
    results = []
    if not os.path.isdir(PROJECTS_DIR):
        return results
    for entry in sorted(os.listdir(PROJECTS_DIR)):
        full_path = os.path.join(PROJECTS_DIR, entry)
        if not os.path.isdir(full_path):
            continue
        decoded = dirname_to_path(entry)
        results.append((entry, decoded, full_path))
    return results


def prompt_choice(prompt_text, max_val, allow_quit=True):
    """Prompt user for a numeric choice. Returns 0-based index or None if cancelled."""
    try:
        choice = input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if allow_quit and choice.lower() in ("q", ""):
        return None

    try:
        n = int(choice)
    except ValueError:
        print("Invalid choice.")
        sys.exit(1)

    if n < 1 or n > max_val:
        print("Invalid choice.")
        sys.exit(1)

    return n - 1
