"""
Validate that Claude and Codex SKILL.md files stay aligned on shared content.

Shared sections must be byte-identical between the two files. Runtime-specific
sections (Architecture, Critical Constraints) are expected to diverge.

Usage:
    python tools/sync_skills.py           # Check for drift
    python tools/sync_skills.py --fix     # Copy shared sections from Claude -> Codex

Can be wired as a pre-commit hook:
    pre-commit:
      - repo: local
        hooks:
          - id: sync-skills
            name: Sync session-sounds skills
            entry: python tools/sync_skills.py
            language: python
            pass_filenames: false
            files: '\\.claude/skills/session-sounds/SKILL\\.md|\\.codex/skills/session-sounds/SKILL\\.md'
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLAUDE_SKILL = REPO_ROOT / ".claude" / "skills" / "session-sounds" / "SKILL.md"
CODEX_SKILL = REPO_ROOT / ".codex" / "skills" / "session-sounds" / "SKILL.md"

# Sections that MUST be identical between Claude and Codex SKILL.md files.
# These are the shared functional sections -- runtime-specific sections are
# expected to differ and are excluded from comparison.
SHARED_SECTIONS = [
    "Sound Pool & Assignment",
    "Packs & Themes",
    "Personal Sound Customization",
    "Gitignore Rules",
    "File Inventory (After Install)",
    "Terminal Title Dispatch",
]


def _extract_sections(text: str) -> dict[str, str]:
    """Extract markdown H2 sections from text. Returns {heading: stripped_content}.

    Content is stripped of leading/trailing blank lines so whitespace
    differences between files don't cause false drift reports.
    """
    sections: dict[str, str] = {}
    # Split on ## headings
    parts = re.split(r"^(## .+)$", text, flags=re.MULTILINE)
    # parts alternates: [preamble, heading, content, heading, content, ...]
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].lstrip("# ").strip()
        content = parts[i + 1].strip()
        sections[heading] = content
    return sections


def check_drift() -> list[str]:
    """Compare shared sections. Returns list of section names that differ."""
    if not CLAUDE_SKILL.is_file():
        print(f"ERROR: Claude skill not found: {CLAUDE_SKILL}")
        return ["(claude skill missing)"]
    if not CODEX_SKILL.is_file():
        print(f"ERROR: Codex skill not found: {CODEX_SKILL}")
        return ["(codex skill missing)"]

    claude_sections = _extract_sections(CLAUDE_SKILL.read_text(encoding="utf-8"))
    codex_sections = _extract_sections(CODEX_SKILL.read_text(encoding="utf-8"))

    drifted: list[str] = []
    for section in SHARED_SECTIONS:
        claude_content = claude_sections.get(section, "")
        codex_content = codex_sections.get(section, "")

        if claude_content != codex_content:
            drifted.append(section)
            if not claude_content:
                print(f"  MISSING in Claude: ## {section}")
            elif not codex_content:
                print(f"  MISSING in Codex:  ## {section}")
            else:
                # Show first differing line
                cl = claude_content.splitlines()
                cx = codex_content.splitlines()
                for j, (a, b) in enumerate(zip(cl, cx)):
                    if a != b:
                        print(f"  DRIFT in ## {section} (line {j + 1}):")
                        print(f"    Claude: {a[:80]}")
                        print(f"    Codex:  {b[:80]}")
                        break
                else:
                    longer = "Claude" if len(cl) > len(cx) else "Codex"
                    print(f"  DRIFT in ## {section}: {longer} has extra lines")

    return drifted


def fix_drift() -> int:
    """Copy shared sections from Claude SKILL.md to Codex SKILL.md."""
    if not CLAUDE_SKILL.is_file() or not CODEX_SKILL.is_file():
        print("ERROR: Both skill files must exist before --fix can run.")
        return 1

    claude_sections = _extract_sections(CLAUDE_SKILL.read_text(encoding="utf-8"))
    codex_text = CODEX_SKILL.read_text(encoding="utf-8")

    fixed = 0
    for section in SHARED_SECTIONS:
        claude_content = claude_sections.get(section, "")
        if not claude_content:
            continue

        # Replace the section in Codex text
        pattern = re.compile(
            rf"(^## {re.escape(section)}\s*$)(.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(codex_text)
        if match:
            old_content = match.group(2).strip()
            if old_content != claude_content:
                # Reconstruct with consistent spacing: \n\n before content, \n\n after
                replacement = f"\n\n{claude_content}\n\n"
                codex_text = codex_text[:match.start(2)] + replacement + codex_text[match.end(2):]
                fixed += 1
                print(f"  FIXED: ## {section}")
        else:
            print(f"  WARNING: ## {section} not found in Codex skill -- add manually")

    if fixed:
        CODEX_SKILL.write_text(codex_text, encoding="utf-8")
        print(f"\nFixed {fixed} section(s). Codex skill updated.")
    else:
        print("\nNo fixes needed.")
    return 0


if __name__ == "__main__":
    do_fix = "--fix" in sys.argv

    if do_fix:
        sys.exit(fix_drift())
    else:
        print("Checking Claude/Codex SKILL.md alignment...")
        drifted = check_drift()
        if drifted:
            print(f"\n{len(drifted)} shared section(s) have drifted:")
            for s in drifted:
                print(f"  - {s}")
            print("\nRun 'python tools/sync_skills.py --fix' to sync Codex from Claude.")
            sys.exit(1)
        else:
            print("All shared sections are aligned.")
            sys.exit(0)
