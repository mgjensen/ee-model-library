"""
Retrofit CREATED/MODIFIED date stamps on all modules and registry entries.

Usage:
    python _tools/retrofit_dates.py

Part 1: Inserts CREATED: and MODIFIED: lines into module docstrings.
Part 2: Adds "created" and "modified" fields to module_registry.json.

Dates derived from git history:
  CREATED  = date of first commit that introduced the file
  MODIFIED = date of most recent commit that touched the file
"""

import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = subprocess.check_output(
    ["git", "log", "-1", "--format=%ai"], cwd=REPO_ROOT
).decode().strip()[:10]  # fallback: date of latest commit


def _git_date(filepath: str, mode: str) -> str:
    """Get created or modified date from git history.

    mode="created"  -> first commit that added the file
    mode="modified" -> most recent commit that touched the file
    """
    abs_path = os.path.join(REPO_ROOT, filepath)
    try:
        if mode == "created":
            out = subprocess.check_output(
                ["git", "log", "--diff-filter=A", "--format=%ai", "--", filepath],
                cwd=REPO_ROOT, stderr=subprocess.DEVNULL,
            ).decode().strip()
            # Take the last line (oldest = first commit)
            lines = [l for l in out.splitlines() if l.strip()]
            return lines[-1][:10] if lines else TODAY
        else:
            out = subprocess.check_output(
                ["git", "log", "-1", "--format=%ai", "--", filepath],
                cwd=REPO_ROOT, stderr=subprocess.DEVNULL,
            ).decode().strip()
            return out[:10] if out else TODAY
    except subprocess.CalledProcessError:
        return TODAY


def retrofit_docstrings():
    """Part 1: Add CREATED/MODIFIED to module docstrings."""
    search_dirs = ["modules", "assembly"]
    count = 0

    for search_dir in search_dirs:
        full_dir = os.path.join(REPO_ROOT, search_dir)
        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                filepath = os.path.join(root, fname)
                relpath = os.path.relpath(filepath, REPO_ROOT).replace("\\", "/")

                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()

                if "MODULE_ID:" not in content:
                    continue

                # Check if already has CREATED:
                if "CREATED:" in content and "MODIFIED:" in content:
                    # Update MODIFIED only
                    modified = _git_date(relpath, "modified")
                    content = re.sub(
                        r"(MODIFIED:\s+)\S+",
                        rf"\g<1>{modified}",
                        content,
                        count=1,
                    )
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"  UPDATED  {relpath} (MODIFIED={modified})")
                    count += 1
                    continue

                # Insert CREATED/MODIFIED after TECHNOLOGIES: line
                created = _git_date(relpath, "created")
                modified = _git_date(relpath, "modified")

                # Find the TECHNOLOGIES: line and insert after it
                lines = content.split("\n")
                new_lines = []
                inserted = False
                for line in lines:
                    new_lines.append(line)
                    if not inserted and "TECHNOLOGIES:" in line:
                        # Determine alignment from existing lines
                        # Find the column where values start (after the colon + spaces)
                        match = re.match(r"(\s*)(MODULE_ID:\s+)", content)
                        if match:
                            prefix = match.group(1)
                        else:
                            prefix = ""
                        new_lines.append(f"{prefix}CREATED:      {created}")
                        new_lines.append(f"{prefix}MODIFIED:     {modified}")
                        inserted = True

                if inserted:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write("\n".join(new_lines))
                    print(f"  INSERTED {relpath} (CREATED={created}, MODIFIED={modified})")
                    count += 1
                else:
                    print(f"  SKIPPED  {relpath} (no TECHNOLOGIES: line found)")

    print(f"\nPart 1: {count} files processed")
    return count


def retrofit_registry():
    """Part 2: Add created/modified to module_registry.json."""
    reg_path = os.path.join(REPO_ROOT, "registry", "module_registry.json")
    with open(reg_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    count = 0
    for mod_id, entry in registry.items():
        filepath = entry.get("path", "")
        if not filepath:
            continue

        created = _git_date(filepath, "created")
        modified = _git_date(filepath, "modified")
        entry["created"] = created
        entry["modified"] = modified
        print(f"  {mod_id}: created={created}, modified={modified}")
        count += 1

    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=4, ensure_ascii=False)
        f.write("\n")

    print(f"\nPart 2: {count} registry entries updated")
    return count


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    print("=== Part 1: Retrofit module docstrings ===\n")
    retrofit_docstrings()
    print("\n=== Part 2: Retrofit module_registry.json ===\n")
    retrofit_registry()
    print("\nDone. Run: python -m pytest tests/ -q")
