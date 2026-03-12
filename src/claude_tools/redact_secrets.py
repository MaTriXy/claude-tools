#!/usr/bin/env python3
"""Scan Claude Code conversation history for secrets and optionally redact them.

Interactive: claude-redact-secrets
Direct:      claude-redact-secrets <project-path>
Scan all:    claude-redact-secrets --all
Dry run:     claude-redact-secrets [--all | <path>] --dry-run
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime

from claude_tools.utils import (
    PROJECTS_DIR,
    dirname_to_path,
    extract_strings,
    list_project_dirs,
    path_to_dirname,
    preserve_mtime,
    print_sessions,
    require_projects_dir,
    resolve_path,
)

# ---------------------------------------------------------------------------
# detect-secrets imports (must be available)
# ---------------------------------------------------------------------------
from detect_secrets.core.scan import scan_file
from detect_secrets.settings import transient_settings

# ---------------------------------------------------------------------------
# detect-secrets configuration
# ---------------------------------------------------------------------------

# All detect-secrets plugins including entropy detectors.
# Pre-filtering by keyword hints keeps this fast even on large histories.
DETECT_SECRETS_SETTINGS = {
    "plugins_used": [
        {"name": "ArtifactoryDetector"},
        {"name": "AWSKeyDetector"},
        {"name": "AzureStorageKeyDetector"},
        {"name": "Base64HighEntropyString", "limit": 4.5},
        {"name": "BasicAuthDetector"},
        {"name": "CloudantDetector"},
        {"name": "DiscordBotTokenDetector"},
        {"name": "GitHubTokenDetector"},
        {"name": "GitLabTokenDetector"},
        {"name": "HexHighEntropyString", "limit": 3.0},
        {"name": "IbmCloudIamDetector"},
        {"name": "IbmCosHmacDetector"},
        {"name": "JwtTokenDetector"},
        {"name": "KeywordDetector"},
        {"name": "MailchimpDetector"},
        {"name": "NpmDetector"},
        {"name": "OpenAIDetector"},
        {"name": "PrivateKeyDetector"},
        {"name": "PypiTokenDetector"},
        {"name": "SendGridDetector"},
        {"name": "SlackDetector"},
        {"name": "SoftlayerDetector"},
        {"name": "SquareOAuthDetector"},
        {"name": "StripeDetector"},
        {"name": "TelegramBotTokenDetector"},
        {"name": "TwilioKeyDetector"},
    ],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REDACT_MARKER = "***REDACTED***"


def redact(value):
    """Replace middle portion with ***REDACTED***, keeping prefix/suffix for identification."""
    if len(value) <= 12:
        return value[:3] + "***REDACTED***"
    prefix_len = min(8, len(value) // 4)
    suffix_len = min(4, len(value) // 6)
    return value[:prefix_len] + "***REDACTED***" + value[-suffix_len:]


def is_false_positive(secret_val):
    if not secret_val or len(secret_val) < 8:
        return True

    # Skip values already redacted by a previous run
    if REDACT_MARKER in secret_val:
        return True

    sv_lower = secret_val.lower()

    # Exact known false positives
    FALSE_POSITIVES = {
        "password", "changeme", "xxxxxxxx", "xxxxxxxxxxxxxxxx",
        "sk-ant-xxx", "sk-ant-xxxxxxxxxxxxxxxxxxxx",
        "your-api-key-here", "your_api_key_here",
        "placeholder", "example", "12345678",
    }
    if sv_lower in FALSE_POSITIVES:
        return True

    # All same char or very low entropy placeholder
    stripped = sv_lower.replace("-", "").replace("_", "")
    if len(stripped) > 0 and len(set(stripped)) <= 2:
        return True

    # Test/mock/fake values (e.g., test-jwt-secret, mock_api_key, fake-token-123)
    if re.match(
        r'^(?:test|mock|fake|dummy|example|sample|demo|foo|bar|baz|my|temp|tmp)'
        r'[-_.]',
        sv_lower,
    ):
        return True

    # Tokens that are obviously placeholders (short, repetitive, descriptive)
    if re.match(
        r'^(?:test|mock|fake|dummy|example|sample|demo|foo|bar|baz)'
        r'[-_.]?'
        r'(?:secret|password|token|key|api.?key|jwt|value|data|string|credential)?'
        r'[-_.]?'
        r'(?:\d+|long|short|here|xxx)*$',
        sv_lower,
    ):
        return True

    # Common placeholder passwords (admin123, user123, passw0rd, etc.)
    if re.match(r'^(?:admin|user|root|pass|super)\w{0,10}\d{0,6}$', sv_lower):
        return True

    # Prefixed test keys (mb_admin_key, mb_key_alice, sk-ant-test, etc.)
    if re.match(r'^(?:mb|sk|jwt|api)[_-](?:admin|test|dev|local|fake|mock|user|special|key[_-]|ant-test)', sv_lower):
        return True

    # Short descriptive names that aren't real secrets (admin-key, user-key-123, etc.)
    if re.match(
        r'^(?:admin|user|test|dev|local|special|default|internal)'
        r'[-_]?'
        r'(?:key|secret|token|password|pwd|credential|session)?'
        r'[-_]?\d*$',
        sv_lower,
    ):
        return True

    # Google OAuth placeholder/example IDs (not the real secret values)
    if re.match(r'^google[-_]?(?:client[-_]?(?:id|secret)|oauth)', sv_lower):
        return True

    # Claude SDK tool use IDs and message IDs (not secrets)
    if re.match(r'^(?:toolu_|msg_|req_|chatcmpl-|run_)', sv_lower):
        return True

    # Base64-encoded protobuf/binary data from SDK messages (e.g., EpYCCkYI...)
    # These often end with = padding and contain non-word base64 chars (+/=)
    if re.match(r'^[A-Za-z][A-Za-z0-9+/]{8,}={0,2}$', secret_val):
        # Contains non-alphanumeric base64 chars or ends with padding -> likely binary
        if '/' in secret_val or '+' in secret_val or secret_val.endswith('='):
            return True

    return False


# Pre-filter regex: only scan lines that might contain secrets.
# This massively reduces the text fed to detect-secrets (code lines are skipped).
HINT_RE = re.compile(
    r'(?i)'
    r'(?:password|passwd|pwd|secret|token|api.?key|apikey|auth.?token'
    r'|bearer|basic\s|private.key|BEGIN\s.*KEY'
    r'|credential|access.key|session.id|connection.string'
    r'|sk-ant-|sk-proj-|sk-[a-z]|ghp_|gho_|github_pat_|glpat-'
    r'|xox[bporas]-|hooks\.slack\.com|AKIA[A-Z0-9]'
    r'|AIza[A-Za-z0-9]|[rs]k_(?:live|test)_|SG\.[A-Za-z]'
    r'|npm_[A-Za-z0-9]|pypi-[A-Za-z0-9]|dckr_pat_|lin_api_'
    r'|eyJ[A-Za-z0-9].*\.eyJ[A-Za-z0-9]'
    r'|_KEY\s*=|_SECRET\s*=|_TOKEN\s*=|_PASSWORD\s*=)'
)

# ---------------------------------------------------------------------------
# Core scanning function
# ---------------------------------------------------------------------------


def scan_project(project_dir, project_name, dry_run):
    """Scan a single project directory for secrets.

    Returns:
        0 - no findings
        42 - findings exist (dry-run mode, nothing redacted)
        0 - findings redacted successfully
    """
    _start = time.monotonic()

    def _elapsed():
        return f"{time.monotonic() - _start:.1f}s"

    # Strategy: for each JSONL file, extract text content that passes the hint
    # filter into a single temp file with a line-number mapping, then scan once
    # per file.

    findings = []  # (filepath, line_num, secret_type, secret_value, redacted_value)

    with transient_settings(DETECT_SECRETS_SETTINGS):
        for fname in sorted(os.listdir(project_dir)):
            if not fname.endswith('.jsonl'):
                continue
            filepath = os.path.join(project_dir, fname)

            with open(filepath, 'r', errors='replace') as fh:
                raw_lines = fh.readlines()

            # Build combined text file + mapping from temp line -> jsonl_line_num
            tmp_lines = []
            line_map = {}  # temp_line_num (1-based) -> jsonl_line_num (1-based)

            for jsonl_line_num, raw_line in enumerate(raw_lines, 1):
                raw_stripped = raw_line.strip()
                if not raw_stripped:
                    continue
                try:
                    entry = json.loads(raw_stripped)
                except (json.JSONDecodeError, ValueError):
                    continue

                for text in extract_strings(entry):
                    for subline in text.split("\n"):
                        subline = subline.strip()
                        if not subline or not HINT_RE.search(subline):
                            continue
                        tmp_line_num = len(tmp_lines) + 1
                        tmp_lines.append(subline)
                        line_map[tmp_line_num] = jsonl_line_num

            if not tmp_lines:
                continue

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.py', delete=False
            ) as tmp:
                tmp.write("\n".join(tmp_lines) + "\n")
                tmp_path = tmp.name

            try:
                secrets = list(scan_file(tmp_path))
            finally:
                os.unlink(tmp_path)

            for secret in secrets:
                if is_false_positive(secret.secret_value):
                    continue

                tmp_ln = secret.line_number
                jsonl_ln = line_map.get(tmp_ln)
                if jsonl_ln is None:
                    continue

                raw_line = raw_lines[jsonl_ln - 1]
                secret_val = secret.secret_value

                # Verify the secret actually appears in the raw JSONL line
                escaped_val = secret_val.replace('\\', '\\\\').replace('"', '\\"')
                if secret_val not in raw_line and escaped_val not in raw_line:
                    continue

                findings.append((
                    filepath, jsonl_ln,
                    secret.type, secret_val, redact(secret_val),
                ))

    if not findings:
        print(f"  No secrets found in {project_name} ({_elapsed()})")
        return 0

    # Deduplicate by (file, line, value)
    seen = set()
    unique = []
    for f in findings:
        key = (f[0], f[1], f[3])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    findings = unique

    print(f"\n  Found {len(findings)} secret(s) in {project_name}:\n")

    # Group by file
    by_file = {}
    for filepath, line_num, stype, value, redacted_val in findings:
        by_file.setdefault(filepath, []).append((line_num, stype, value, redacted_val))

    for filepath, items in sorted(by_file.items()):
        fname = os.path.basename(filepath)
        print(f"  {fname}:")
        for line_num, stype, value, redacted_val in items:
            print(f"    Line {line_num}: {stype}")
            print(f"      {redacted_val}")
        print()

    if dry_run:
        print(f"  [dry-run] No changes made. ({_elapsed()})")
        return 42  # signal: findings exist but not redacted

    # Group findings by file so we read/write each file only once
    by_file_for_redact = {}
    for filepath, line_num, stype, value, redacted_val in findings:
        by_file_for_redact.setdefault(filepath, []).append((line_num, value, redacted_val))

    # Back up files before redacting (stored outside ~/.claude to avoid interference)
    backup_root = os.path.join(os.path.expanduser("~"), ".claude-history-backups")
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir = os.path.join(backup_root, timestamp)
    os.makedirs(backup_dir, exist_ok=True)

    for filepath in by_file_for_redact:
        # Preserve directory structure relative to ~/.claude/projects/
        projects_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects")
        rel = os.path.relpath(filepath, projects_dir)
        dest = os.path.join(backup_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(filepath, dest)

    print(f"  Backed up {len(by_file_for_redact)} file(s) to {backup_dir}")

    # Perform redaction
    redacted_count = 0
    files_modified = set()

    for filepath, items in by_file_for_redact.items():
        with open(filepath, 'r', errors='replace') as fh:
            lines = fh.readlines()

        modified = False
        for line_num, value, redacted_val in items:
            idx = line_num - 1
            if idx >= len(lines):
                continue
            # Try raw form first, then JSON-escaped form
            if value in lines[idx]:
                lines[idx] = lines[idx].replace(value, redacted_val)
                redacted_count += 1
                modified = True
            else:
                escaped_val = value.replace('\\', '\\\\').replace('"', '\\"')
                escaped_redacted = redacted_val.replace('\\', '\\\\').replace('"', '\\"')
                if escaped_val in lines[idx]:
                    lines[idx] = lines[idx].replace(escaped_val, escaped_redacted)
                    redacted_count += 1
                    modified = True

        if modified:
            files_modified.add(filepath)
            with preserve_mtime(filepath):
                with open(filepath, 'w') as fh:
                    fh.writelines(lines)

    print(f"  Redacted {redacted_count} secret(s) across {len(files_modified)} file(s). ({_elapsed()})")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    require_projects_dir()

    dry_run = False
    scan_all = False
    target_path = ""

    # --- Parse arguments ---
    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--all":
            scan_all = True
        elif arg in ("--help", "-h"):
            print("Usage:")
            print("  claude-redact-secrets                  # interactive (pick a project)")
            print("  claude-redact-secrets <project-path>   # scan specific project")
            print("  claude-redact-secrets --all            # scan all projects")
            print("  claude-redact-secrets --dry-run        # report only, don't redact")
            sys.exit(0)
        else:
            target_path = arg

    # --- Resolve project directories to scan ---

    if scan_all:
        project_dirs = []  # (project_dir, display_name)
        for _dir_name, decoded, full_path in list_project_dirs():
            project_dirs.append((full_path, decoded))

        if not project_dirs:
            print("No project histories found.")
            sys.exit(0)

        print(f"Scanning {len(project_dirs)} project(s) for secrets...")
        print("")

        total_found = 0
        for pdir, pname in project_dirs:
            print(f"Project: {pname}")
            status = scan_project(pdir, pname, dry_run)
            if status == 42:
                total_found += 1
        sys.exit(0)

    elif target_path:
        # Direct mode - resolve path
        target_path = resolve_path(target_path)
        target_path = target_path.rstrip("/")
        target_dir_name = path_to_dirname(target_path)
        target_project_dir = os.path.join(PROJECTS_DIR, target_dir_name)

        if not os.path.isdir(target_project_dir):
            print(f"No Claude history found for: {target_path}")
            print(f"  (looked in {target_project_dir})")
            sys.exit(1)

        print(f"Scanning project: {target_path}")
        print("")
        scan_project(target_project_dir, target_path, dry_run)
        sys.exit(0)

    else:
        # Interactive mode
        print("claude-redact-secrets - Find and redact secrets in Claude Code history")
        print("")
        if dry_run:
            print("  (dry-run mode - will report but not redact)")
            print("")

        # List all project directories
        print("Select a project to scan (or use --all to scan everything):")
        print("")

        candidates = []  # (full_path, decoded_path)
        idx = 0
        for _dir_name, decoded, full_path in list_project_dirs():
            idx += 1
            candidates.append((full_path, decoded))

            if os.path.isdir(decoded):
                print(f"     {idx}) {decoded}")
            else:
                print(f"  *  {idx}) {decoded}  (directory no longer exists)")
            print_sessions(full_path)
            print("")

        if idx == 0:
            print("  (no project histories found)")
            sys.exit(0)

        print("---")
        try:
            choice = input("Scan which project? (#, 'a' for all, or 'q' to quit): ")
        except (EOFError, KeyboardInterrupt):
            print()
            print("Cancelled.")
            sys.exit(0)

        choice = choice.strip()

        if choice.lower() in ("q", ""):
            print("Cancelled.")
            sys.exit(0)

        if choice.lower() == "a":
            print("")
            print(f"Scanning all {len(candidates)} project(s)...")
            print("")
            for pdir, pname in candidates:
                print(f"Project: {pname}")
                scan_project(pdir, pname, dry_run)
            sys.exit(0)

        try:
            choice_num = int(choice)
        except ValueError:
            print("Invalid choice.")
            sys.exit(1)

        if choice_num < 1 or choice_num > idx:
            print("Invalid choice.")
            sys.exit(1)

        selected_dir, selected_name = candidates[choice_num - 1]

        print("")
        print(f"Scanning: {selected_name}")
        print("")

        if not dry_run:
            # Preview first (dry run), then confirm
            status = scan_project(selected_dir, selected_name, True)
            if status == 42:
                print("")
                try:
                    confirm = input("Redact these secrets? (y/N): ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    print("Cancelled.")
                    sys.exit(0)
                if confirm.strip().lower() == "y":
                    print("")
                    scan_project(selected_dir, selected_name, False)
                else:
                    print("Cancelled.")
        else:
            scan_project(selected_dir, selected_name, True)


if __name__ == "__main__":
    main()
