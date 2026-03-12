#!/usr/bin/env python3
"""Search Claude Code conversation history for text or via LLM.

Interactive: claude-find-session
CLI:         claude-find-session "text" <project-path>
CLI all:     claude-find-session "text" --all
CLI + flag:  claude-find-session "text" --all --case-sensitive
LLM mode:    claude-find-session "which session fixed the login bug?" --llm --all
"""

import json
import os
import re
import sys

try:
    from claude_tools.utils import (
        DEFAULT_MODEL, PROJECTS_DIR, call_claude, dirname_to_path,
        extract_strings, list_project_dirs, list_sessions, parse_session,
        path_to_dirname, require_api_key, require_projects_dir, session_description,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from utils import (  # type: ignore[no-redef]
        DEFAULT_MODEL, PROJECTS_DIR, call_claude, dirname_to_path,
        extract_strings, list_project_dirs, list_sessions, parse_session,
        path_to_dirname, require_api_key, require_projects_dir, session_description,
    )



# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def text_matches(text, search_text, search_lower, case_sensitive):
    if case_sensitive:
        return search_text in text
    return search_lower in text.lower()


def find_snippet(text, search_text, search_lower, case_sensitive, max_context=120):
    """Find the matching portion and return a snippet with surrounding context."""
    if case_sensitive:
        idx = text.find(search_text)
    else:
        idx = text.lower().find(search_lower)
    if idx == -1:
        return None

    # Find the line containing the match
    line_start = text.rfind("\n", 0, idx)
    line_start = 0 if line_start == -1 else line_start + 1
    line_end = text.find("\n", idx)
    line_end = len(text) if line_end == -1 else line_end
    line = text[line_start:line_end].strip()

    if len(line) <= max_context:
        return line

    # Trim around the match within the line
    match_pos = idx - line_start
    half = max_context // 2
    start = max(0, match_pos - half)
    end = min(len(line), start + max_context)
    if end == len(line):
        start = max(0, end - max_context)
    snippet = line[start:end]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(line) else ""
    return prefix + snippet + suffix


# ---------------------------------------------------------------------------
# LLM search: ask Claude which sessions match a query
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    "the a an in on at to for of with by from is was are were be been being have has "
    "had do does did will would could should may might can shall this that these those "
    "it its which who whom what where when how why and or but not no nor if then else "
    "about any all each every some most other into through during before after above "
    "below between up down out off over under again i me my we our you your he she "
    "they them his her their session sessions mention mentioned using used did "
    "find where discuss something like".split()
)


def _extract_query_terms(query):
    """Extract meaningful search terms from a natural-language query."""
    return [w for w in re.findall(r'\w+', query.lower()) if len(w) > 2 and w not in _STOP_WORDS]


def _text_search_session(filepath, terms):
    """Search a session's full content for terms. Returns {term: (count, [snippets])}."""
    results = {}
    with open(filepath, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            for text in extract_strings(entry):
                text_lower = text.lower()
                for term in terms:
                    if term not in text_lower:
                        continue
                    count, snippets = results.get(term, (0, []))
                    count += text_lower.count(term)
                    if len(snippets) < 3:
                        idx = text_lower.find(term)
                        start = max(0, idx - 60)
                        end = min(len(text), idx + len(term) + 60)
                        snip = text[start:end].replace("\n", " ").strip()
                        if snip and snip not in snippets:
                            snippets.append(snip)
                    results[term] = (count, snippets)
    return results


def llm_search_all(project_dirs, query, api_key, model):
    """Two-phase LLM search: local text scan, then one LLM call to rank results.

    Phase 1: Extract keywords from query, text-search all sessions across all
    projects exhaustively (fast, local, free).
    Phase 2: Send the search results to the LLM in a single API call for
    intelligent ranking and summarization.
    """
    terms = _extract_query_terms(query)
    if not terms:
        print(f"Could not extract search terms from query. Try rephrasing.")
        return

    print(f"  Searching for: {', '.join(terms)}")
    print("")

    # Phase 1: exhaustive local text search
    all_hits = []  # (project_name, session, term_results)

    for project_dir, project_name in project_dirs:
        try:
            sessions = list_sessions(project_dir)
        except Exception:
            continue
        for s in sessions:
            filepath = os.path.join(project_dir, f"{s['session_id']}.jsonl")
            term_results = _text_search_session(filepath, terms)
            if term_results:
                all_hits.append((project_name, s, term_results))

    if not all_hits:
        print("No matches found in any session.")
        return

    # Build a summary of hits for the LLM
    hit_lines = []
    for i, (pname, s, term_results) in enumerate(all_hits, 1):
        desc = session_description(s) or "(no description)"
        cr = s["created"][:10]
        mod = s["modified"][:10]
        hits_str = ", ".join(f'"{t}" ({c}x)' for t, (c, _) in term_results.items())
        all_snippets = []
        for _, (_, snips) in term_results.items():
            all_snippets.extend(snips)
        snip_str = ""
        if all_snippets:
            snip_str = "\n    Snippets: " + " | ".join(s[:100] for s in all_snippets[:3])
        hit_lines.append(
            f"{i}. Project: {pname}\n"
            f"   Session: {desc} ({s['msg_count']} msgs, {cr} -> {mod})\n"
            f"   Matches: {hits_str}{snip_str}"
        )

    prompt = (
        "Below are text search results from Claude Code session history. "
        "Each entry shows a session where keywords from the user's query were found, "
        "with match counts and text snippets.\n\n"
        "Search results:\n" + "\n\n".join(hit_lines) + "\n\n"
        f"User's question: {query}\n\n"
        "Based on these search results, provide a concise answer to the user's question.\n"
        "For each relevant session, write a specific one-line description of what happened "
        "in that session (e.g. 'the big RE session using Hopper/IDA/Ghidra to crack the "
        "authentication protocol'). Use the snippets to infer real context.\n"
        "Group results by project if multiple projects match. "
        "Ignore noise matches (e.g. a term only in a directory listing or file path)."
    )

    print(f"Found {len(all_hits)} session(s) with matches, asking LLM to analyze...\n")

    response = call_claude(api_key, model, [{"role": "user", "content": prompt}], max_tokens=2048)
    if not response:
        print("LLM analysis failed. Raw results:\n")
        for pname, s, term_results in all_hits:
            desc = session_description(s) or "(no description)"
            hits = ", ".join(f'"{t}" ({c}x)' for t, (c, _) in term_results.items())
            print(f"  [{pname}] {s['session_id']}  {desc[:60]}  — {hits}")
        return

    print(response)


# ---------------------------------------------------------------------------
# Core: search a project directory for text
# ---------------------------------------------------------------------------

def search_project(project_dir, project_name, search_text, case_sensitive):
    search_lower = search_text if case_sensitive else search_text.lower()

    # Scan each session file
    matching_sessions = []

    for fname in sorted(os.listdir(project_dir)):
        if not fname.endswith(".jsonl"):
            continue
        filepath = os.path.join(project_dir, fname)

        # Parse metadata via shared lib
        s = parse_session(filepath)
        desc = session_description(s)

        # Search all text content for matches
        match_count = 0
        snippets = []

        with open(filepath, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                for text in extract_strings(entry):
                    if text_matches(text, search_text, search_lower, case_sensitive):
                        match_count += 1
                        if len(snippets) < 3:
                            snippet = find_snippet(text, search_text, search_lower, case_sensitive)
                            if snippet and snippet not in snippets:
                                snippets.append(snippet)

        if match_count > 0:
            matching_sessions.append((
                s["session_id"], s["msg_count"], desc,
                s["created"][:10], s["modified"][:10],
                match_count, snippets,
            ))

    if not matching_sessions:
        print("  No matches found.")
        return

    print(f"  {len(matching_sessions)} session(s) matched:\n")

    for sid, count, desc, cr, mod, matches, snippets in matching_sessions:
        print(f"    {sid}  {count:>4} msgs  [{cr} -> {mod}]  ({matches} match{'es' if matches != 1 else ''})")
        if desc:
            print(f"      {desc[:80]}")
        for snip in snippets:
            print(f"      > {snip[:120]}")
        print()


# ---------------------------------------------------------------------------
# Argument parsing and main entry point
# ---------------------------------------------------------------------------

def _collect_project_dirs():
    """Gather all project dirs as (full_path, decoded_name) list."""
    return [(fp, dec) for _, dec, fp in list_project_dirs()]


def main():
    require_projects_dir()

    case_sensitive = None  # None means "not yet decided"
    scan_all = False
    use_llm = False
    target_path = ""
    search_text = ""
    model = DEFAULT_MODEL

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--case-sensitive", "-cs"):
            case_sensitive = True
        elif arg in ("--case-insensitive", "-ci"):
            case_sensitive = False
        elif arg == "--all":
            scan_all = True
        elif arg == "--llm":
            use_llm = True
        elif arg == "--model":
            i += 1
            if i >= len(args):
                print("--model requires a value")
                sys.exit(1)
            model = args[i]
        elif arg in ("--help", "-h"):
            print("Usage:")
            print("")
            print("  Text search (default):")
            print('    claude-find-session "text" --all                 # search all projects')
            print('    claude-find-session "text" <project-path>        # search specific project')
            print('    claude-find-session "text" --all -cs             # case-sensitive')
            print("")
            print("  LLM search (natural language queries):")
            print('    claude-find-session "which session fixed login?" --llm --all')
            print('    claude-find-session "where did we set up CI?" --llm')
            print("")
            print("  Interactive (prompts for everything):")
            print("    claude-find-session")
            print("    claude-find-session --llm")
            print("")
            print("Flags:")
            print("  --llm                     Use LLM to match sessions by meaning")
            print("  --model <id>              Model for LLM mode (default: haiku)")
            print("  --case-sensitive, -cs     Case-sensitive text matching")
            print("  --case-insensitive, -ci   Case-insensitive text matching (default)")
            print("  --all                     Search all projects")
            sys.exit(0)
        else:
            if not search_text:
                search_text = arg
            elif not target_path:
                target_path = arg
            else:
                print("Too many arguments. Use --help for usage.")
                sys.exit(1)
        i += 1

    # -----------------------------------------------------------------------
    # Interactive prompts for missing inputs
    # -----------------------------------------------------------------------

    if not search_text:
        mode_name = "LLM" if use_llm else "text"
        print(f"claude-find-session - Search Claude Code history ({mode_name} mode)")
        print("")
        try:
            prompt_label = "Query: " if use_llm else "Search text: "
            search_text = input(prompt_label)
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not search_text:
            print("Cancelled.")
            sys.exit(0)

    if case_sensitive is None:
        if use_llm:
            case_sensitive = False
        elif len(args) == 0:
            try:
                cs_choice = input("Case-sensitive? (y/N): ")
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(0)
            case_sensitive = cs_choice.strip().lower() == "y"
        else:
            case_sensitive = False

    api_key = None
    if use_llm:
        api_key = require_api_key()

    # -----------------------------------------------------------------------
    # Resolve project directories to search
    # -----------------------------------------------------------------------

    if use_llm:
        if scan_all:
            targets = _collect_project_dirs()
            if not targets:
                print("No project histories found.")
                sys.exit(0)
            print(f'Searching {len(targets)} project(s) for "{search_text}" (LLM)...\n')
        elif target_path:
            try:
                resolved = os.path.realpath(os.path.abspath(target_path))
            except Exception:
                resolved = target_path
            resolved = resolved.rstrip("/")
            target_dir_name = path_to_dirname(resolved)
            target_project_dir = os.path.join(PROJECTS_DIR, target_dir_name)
            if not os.path.isdir(target_project_dir):
                print(f"No Claude history found for: {resolved}")
                sys.exit(1)
            targets = [(target_project_dir, resolved)]
            print(f'Searching project: {resolved} (LLM)\n')
        else:
            targets = None

        if targets is not None:
            llm_search_all(targets, search_text, api_key, model)
            sys.exit(0)

    elif scan_all:
        project_dirs = _collect_project_dirs()
        if not project_dirs:
            print("No project histories found.")
            sys.exit(0)

        mode_label = "case-sensitive" if case_sensitive else "case-insensitive"
        print(f'Searching {len(project_dirs)} project(s) for "{search_text}" ({mode_label})...')
        print("")

        for pdir, pname in project_dirs:
            print(f"Project: {pname}")
            search_project(pdir, pname, search_text, case_sensitive)
            print("")
        sys.exit(0)

    elif target_path:
        try:
            resolved = os.path.realpath(os.path.abspath(target_path))
        except Exception:
            resolved = target_path
        resolved = resolved.rstrip("/")
        target_dir_name = path_to_dirname(resolved)
        target_project_dir = os.path.join(PROJECTS_DIR, target_dir_name)

        if not os.path.isdir(target_project_dir):
            print(f"No Claude history found for: {resolved}")
            print(f"  (looked in {target_project_dir})")
            sys.exit(1)

        print(f"Searching project: {resolved} (case-insensitive)")
        print("")
        search_project(target_project_dir, resolved, search_text, case_sensitive)
        sys.exit(0)

    # Interactive project selection
    mode_label = "LLM" if use_llm else ("case-sensitive" if case_sensitive else "case-insensitive")

    print("")
    print(f'  {"Query" if use_llm else "Search text"}: "{search_text}" ({mode_label})')
    print("")

    current_pwd = os.getcwd()
    current_dir_name = path_to_dirname(current_pwd)
    current_project_dir = os.path.join(PROJECTS_DIR, current_dir_name)
    has_current = os.path.isdir(current_project_dir)

    print("Search scope:")
    if has_current:
        print(f"  c) Current project: {current_pwd}  (default)")
    print("  a) All projects")
    print("  s) Select a specific project")
    print("")

    try:
        if has_current:
            scope_choice = input("Scope (c/a/s): ").strip() or "c"
        else:
            scope_choice = input("Scope (a/s): ").strip() or "a"
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    targets = None
    if scope_choice.lower() == "c":
        if not has_current:
            print(f"No Claude history found for current directory: {current_pwd}")
            sys.exit(1)
        targets = [(current_project_dir, current_pwd)]

    elif scope_choice.lower() == "a":
        targets = _collect_project_dirs()
        if not targets:
            print("No project histories found.")
            sys.exit(0)

    elif scope_choice.lower() == "s":
        print("")
        candidates = []
        idx = 0
        for dir_name, decoded, full_path in list_project_dirs():
            idx += 1
            candidates.append((full_path, decoded))
            if os.path.isdir(decoded):
                print(f"     {idx}) {decoded}")
            else:
                print(f"  *  {idx}) {decoded}  (directory no longer exists)")

        if idx == 0:
            print("  (no project histories found)")
            sys.exit(0)

        print("")
        try:
            choice = input("Project #: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if not choice:
            print("Cancelled.")
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
        targets = [(selected_dir, selected_name)]

    elif scope_choice.lower() == "q":
        print("Cancelled.")
        sys.exit(0)
    else:
        print("Invalid choice.")
        sys.exit(1)

    if targets:
        print("")
        if use_llm:
            llm_search_all(targets, search_text, api_key, model)
        else:
            for pdir, pname in targets:
                if len(targets) > 1:
                    print(f"Project: {pname}")
                else:
                    print(f"Searching: {pname}\n")
                search_project(pdir, pname, search_text, case_sensitive)
                if len(targets) > 1:
                    print("")


if __name__ == "__main__":
    main()
