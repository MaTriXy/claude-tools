#!/usr/bin/env python3

import base64
import hashlib
import json
import os
import re
import subprocess
import sys


ENVRC_SNIPPET = '''ENCODED_DIR=$(echo -n "$PWD" | base64)
API_KEY=$(security find-generic-password -s "Claude Code $ENCODED_DIR" -w 2>/dev/null)

if [ -n "$API_KEY" ]; then
  export ANTHROPIC_API_KEY="$API_KEY"
fi'''

CONFIG_FILE = os.path.expanduser("~/.claude/key-config.json")


def _encoded_dir():
    """Encode current directory as base64 for reversible keychain lookup."""
    return base64.b64encode(os.getcwd().encode()).decode()


def _keychain_name():
    """Get the keychain service name for the current directory."""
    return f"Claude Code {_encoded_dir()}"


def _ensure_config():
    """Ensure the config file exists."""
    os.makedirs(os.path.expanduser("~/.claude"), exist_ok=True)
    if not os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            f.write("{}")


def config_get(path, default=""):
    """Read a value from config: config_get("key_names.a1b2c3d4", "")"""
    with open(CONFIG_FILE, "r") as f:
        d = json.load(f)
    keys = path.split(".")
    try:
        obj = d
        for k in keys:
            obj = obj[k]
        return str(obj)
    except (KeyError, TypeError):
        return default


def config_set(path, value):
    """Write a value to config: config_set("key_names.a1b2c3d4", "my-work-key")"""
    with open(CONFIG_FILE, "r") as f:
        d = json.load(f)
    keys = path.split(".")
    obj = d
    for k in keys[:-1]:
        obj = obj.setdefault(k, {})
    obj[keys[-1]] = value
    with open(CONFIG_FILE, "w") as f:
        json.dump(d, f, indent=2)


def key_hash(api_key):
    """Get the hash used to look up a key's name."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def get_key_name(api_key):
    """Look up a key's name from config."""
    return config_get(f"key_names.{key_hash(api_key)}")


def save_key_name(api_key, name):
    """Save a key's name to config."""
    config_set(f"key_names.{key_hash(api_key)}", name)


def security_find_password(service):
    """Run security find-generic-password and return the password, or empty string."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-w"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def security_add_password(service, password):
    """Run security add-generic-password."""
    return subprocess.run(
        ["security", "add-generic-password", "-a", os.environ.get("USER", ""), "-s", service, "-w", password],
        capture_output=True, text=True
    )


def security_delete_password(service):
    """Run security delete-generic-password."""
    return subprocess.run(
        ["security", "delete-generic-password", "-s", service],
        capture_output=True, text=True
    )


def key_label(service):
    """Get display label for a key: name if set, otherwise truncated key."""
    key = security_find_password(service)
    if not key:
        return "(empty)"
    name = get_key_name(key)
    if name:
        return name
    return f"{key[:12]}...{key[-4:]}"


def prompt_key_name(api_key):
    """Prompt for an optional key name after storing."""
    existing_name = get_key_name(api_key)
    if existing_name:
        print(f'Key is named "{existing_name}".')
        return
    key_name = input("Name this key (optional, Enter to skip): ")
    if key_name:
        save_key_name(api_key, key_name)
        print(f'Tagged as "{key_name}".')


def store_key(api_key, keychain_name):
    """Store a key in the keychain."""
    result = security_add_password(keychain_name, api_key)
    if result.returncode == 0:
        print("✅ API key stored successfully!")
    else:
        print("❌ Failed to store API key.")
        sys.exit(1)


def ensure_envrc():
    """Ensure .envrc contains the keychain lookup snippet."""
    envrc = os.path.join(os.getcwd(), ".envrc")
    if os.path.isfile(envrc):
        with open(envrc, "r") as f:
            content = f.read()
        if 'Claude Code $ENCODED_DIR' in content:
            print("📄 .envrc already contains keychain lookup.")
        else:
            with open(envrc, "a") as f:
                f.write("\n" + ENVRC_SNIPPET + "\n")
            print("📄 Appended keychain lookup to existing .envrc")
    else:
        with open(envrc, "w") as f:
            f.write(ENVRC_SNIPPET + "\n")
        print("📄 Created .envrc with keychain lookup")


def remove_envrc_snippet():
    """Remove the keychain lookup snippet from .envrc (or delete .envrc if nothing else remains)."""
    envrc = os.path.join(os.getcwd(), ".envrc")
    if not os.path.isfile(envrc):
        return

    with open(envrc, "r") as f:
        content = f.read()

    # Remove the snippet block: from ENCODED_DIR=... line through fi line
    # This mirrors: sed '/^ENCODED_DIR=\$(echo -n "\$PWD" | base64)$/,/^fi$/d'
    cleaned = re.sub(
        r'^ENCODED_DIR=\$\(echo -n "\$PWD" \| base64\)$.*?^fi$\n?',
        '',
        content,
        flags=re.MULTILINE | re.DOTALL
    )

    # Remove consecutive blank lines (mirrors: sed '/^$/N;/^\n$/d')
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    # Trim leading/trailing whitespace
    cleaned = cleaned.strip()

    if not cleaned:
        os.remove(envrc)
        print("📄 Removed .envrc (was only the keychain lookup).")
    else:
        with open(envrc, "w") as f:
            f.write(cleaned + "\n")
        print("📄 Removed keychain lookup from .envrc.")


def scan_keychain(current_encoded_dir):
    """Scan keychain for other 'Claude Code *' entries and decode directories."""
    result = subprocess.run(
        ["security", "dump-keychain"],
        capture_output=True, text=True
    )
    output = result.stdout if result.returncode == 0 else ""

    all_names = []
    all_dirs = []
    all_exists = []

    for line in output.splitlines():
        m = re.search(r'"svce".*="Claude Code (.+)"', line)
        if m:
            encoded = m.group(1)
            if encoded == current_encoded_dir:
                continue
            try:
                dir_path = base64.b64decode(encoded).decode()
            except Exception:
                continue
            if not dir_path.startswith("/"):
                continue
            # Verify the key actually exists in keychain
            if not security_find_password(f"Claude Code {encoded}"):
                continue
            all_names.append(encoded)
            all_dirs.append(dir_path)
            all_exists.append(os.path.isdir(dir_path))

    return all_names, all_dirs, all_exists


def show_dir_list(all_dirs, all_names, all_exists):
    """Display a numbered directory list (with * for non-existent paths)."""
    for i, dir_path in enumerate(all_dirs):
        marker = " *" if not all_exists[i] else ""
        label = key_label(f"Claude Code {all_names[i]}")
        print(f"  {i + 1}) {dir_path}{marker}  ({label})")
    # Show legend if any paths don't exist
    if any(not e for e in all_exists):
        print()
        print("  * directory no longer exists")


def pick_directory(prompt, all_dirs, all_names, all_exists):
    """Prompt user to pick a directory from the list, returns index."""
    show_dir_list(all_dirs, all_names, all_exists)
    print()
    choice = input(prompt)

    try:
        choice_int = int(choice)
    except ValueError:
        print("❌ Invalid choice. Cancelled.")
        sys.exit(1)

    if choice_int < 1 or choice_int > len(all_dirs):
        print("❌ Invalid choice. Cancelled.")
        sys.exit(1)

    return choice_int - 1


def main():
    _ensure_config()
    encoded_dir = _encoded_dir()
    keychain_name = _keychain_name()

    print(f"Directory: {os.getcwd()}")
    print()

    all_names, all_dirs, all_exists = scan_keychain(encoded_dir)
    has_others = len(all_dirs)

    # Check for the default "Claude Code" key (set by Claude Code itself)
    default_key = security_find_password("Claude Code")
    has_default = 1 if default_key else 0

    # --- Main flow ---
    existing_key = security_find_password(keychain_name)

    if existing_key:
        print(f"Current key: {key_label(keychain_name)}")
        print()

    # Build top-level menu
    options = []
    options.append("set")  # always available
    if has_others > 0 or existing_key:
        options.append("delete")
    # "Name" is available if any key exists anywhere
    if has_others > 0 or existing_key or has_default == 1:
        options.append("rename")

    if len(options) == 1 and not existing_key and has_others == 0 and has_default == 0:
        # Only option is "set" with no dirs to copy from — skip menu entirely
        action = "set"
    else:
        print("What would you like to do?")
        for n, opt in enumerate(options, 1):
            if opt == "set":
                print(f"  {n}) Set a key")
            elif opt == "delete":
                print(f"  {n}) Delete a key")
            elif opt == "rename":
                print(f"  {n}) Name a key")
        if existing_key:
            print("  q) Keep existing key")
        print()
        reply = input("Choice: ")
        print()

        if existing_key and reply.lower() == "q":
            print("Existing key unchanged.")
            ensure_envrc()
            sys.exit(0)

        try:
            reply_int = int(reply)
        except ValueError:
            print("Cancelled.")
            sys.exit(0)

        if reply_int < 1 or reply_int > len(options):
            print("Cancelled.")
            sys.exit(0)

        action = options[reply_int - 1]

    # --- Set a key ---
    if action == "set":
        has_copy_sources = has_default + has_others

        if has_copy_sources == 0:
            set_action = "new"
        else:
            n = 1
            print(f"  {n}) Enter a new API key")
            n += 1
            default_n = None
            copy_n = None
            if has_default == 1:
                print(f'  {n}) Copy from default ({key_label("Claude Code")})')
                default_n = n
                n += 1
            if has_others > 0:
                print(f"  {n}) Copy from another directory")
                copy_n = n
                n += 1
            print()
            reply = input("Choice: ")
            print()

            set_action = ""
            if reply == "1":
                set_action = "new"
            elif has_default == 1 and default_n is not None and reply == str(default_n):
                set_action = "default"
            elif has_others > 0 and copy_n is not None and reply == str(copy_n):
                set_action = "copy"
            else:
                print("Cancelled.")
                sys.exit(0)

        if set_action == "default":
            if existing_key:
                security_delete_password(keychain_name)
            print("Copying key from default...")
            print()
            store_key(default_key, keychain_name)
            ensure_envrc()
            sys.exit(0)

        if set_action == "copy":
            picked_idx = pick_directory("Copy key from (#): ", all_dirs, all_names, all_exists)
            api_key = security_find_password(f"Claude Code {all_names[picked_idx]}")
            if existing_key:
                security_delete_password(keychain_name)
            print()
            print(f'Copying key from "{all_dirs[picked_idx]}"...')
            print()
            store_key(api_key, keychain_name)
            ensure_envrc()
            sys.exit(0)

        # set_action = "new" — fall through to prompt below
        if existing_key:
            security_delete_password(keychain_name)

    # --- Delete a key ---
    if action == "delete":
        del_names = []
        del_dirs = []
        del_exists = []
        del_encoded = []
        n = 1

        # Include current directory if it has a key
        if existing_key:
            print(f"  {n}) . (current directory)")
            del_names.append("__current__")
            del_dirs.append(os.getcwd())
            del_exists.append(True)
            del_encoded.append(encoded_dir)
            n += 1

        # Include other directories
        for i in range(len(all_dirs)):
            marker = " *" if not all_exists[i] else ""
            label = key_label(f"Claude Code {all_names[i]}")
            print(f"  {n}) {all_dirs[i]}{marker}  ({label})")
            del_names.append(all_names[i])
            del_dirs.append(all_dirs[i])
            del_exists.append(all_exists[i])
            del_encoded.append(all_names[i])
            n += 1

        # Legend for non-existent paths
        if any(not e for e in del_exists):
            print()
            print("  * directory no longer exists")

        print()
        choice = input("Delete key for (#): ")

        try:
            choice_int = int(choice)
        except ValueError:
            print("❌ Invalid choice. Cancelled.")
            sys.exit(1)

        if choice_int < 1 or choice_int > len(del_dirs):
            print("❌ Invalid choice. Cancelled.")
            sys.exit(1)

        idx = choice_int - 1
        print()
        reply = input(f'Delete key for "{del_dirs[idx]}"? (y/N): ')

        if not reply or reply[0].lower() != "y":
            print("Cancelled.")
            sys.exit(0)

        security_delete_password(f"Claude Code {del_encoded[idx]}")
        print(f'🗑️  Deleted key for "{del_dirs[idx]}".')

        if del_names[idx] == "__current__":
            remove_envrc_snippet()
        sys.exit(0)

    # --- Name a key ---
    if action == "rename":
        # Collect ALL unique key values from every "Claude Code *" keychain entry
        name_keys = []
        name_labels = []
        name_is_default = []

        default_key_val = security_find_password("Claude Code")

        def add_unique_key(key):
            if not key:
                return
            if key in name_keys:
                return
            name_keys.append(key)
            name_labels.append(get_key_name(key))
            name_is_default.append(bool(default_key_val and key == default_key_val))

        if default_key_val:
            add_unique_key(default_key_val)

        # All per-directory keys from keychain
        result = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True, text=True
        )
        dump_output = result.stdout if result.returncode == 0 else ""

        for line in dump_output.splitlines():
            m = re.search(r'"svce".*="Claude Code (.+)"', line)
            if m:
                encoded = m.group(1)
                key = security_find_password(f"Claude Code {encoded}")
                add_unique_key(key)

        if not name_keys:
            print("No keys found.")
            sys.exit(0)

        # Sort: named keys first, then unnamed
        named_idx = [i for i in range(len(name_keys)) if name_labels[i]]
        unnamed_idx = [i for i in range(len(name_keys)) if not name_labels[i]]
        sorted_idx = named_idx + unnamed_idx

        for n, i in enumerate(sorted_idx, 1):
            k = name_keys[i]
            label = name_labels[i]
            tag = " [default]" if name_is_default[i] else ""
            if label:
                print(f"  {n}) {label}  ({k[:12]}...{k[-4:]}){tag}")
            else:
                print(f"  {n}) {k[:12]}...{k[-4:]}{tag}")
        print()
        choice = input("Name key (#): ")

        try:
            choice_int = int(choice)
        except ValueError:
            print("❌ Invalid choice. Cancelled.")
            sys.exit(1)

        if choice_int < 1 or choice_int > len(sorted_idx):
            print("❌ Invalid choice. Cancelled.")
            sys.exit(1)

        picked = sorted_idx[choice_int - 1]
        current_name = name_labels[picked]
        if current_name:
            print(f"Current name: {current_name}")
        new_name = input("New name: ")
        if not new_name:
            print("Cancelled.")
            sys.exit(0)
        save_key_name(name_keys[picked], new_name)
        print(f'Named "{new_name}".')
        sys.exit(0)

    # Prompt for new key (without -s so pasting works)
    print("Enter your Anthropic API key (paste works):")
    api_key = input()
    print()

    if not api_key:
        print("❌ No key provided. Cancelled.")
        sys.exit(1)

    # Add to keychain
    store_key(api_key, keychain_name)
    prompt_key_name(api_key)
    ensure_envrc()


if __name__ == "__main__":
    main()
