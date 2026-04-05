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

# Skill pairs: each entry is (claude_path, codex_path, shared_sections).
# shared_sections=None means the files must be fully identical (no runtime-specific parts).
SKILL_PAIRS: list[tuple[Path, Path, list[str] | None]] = [
    (
        REPO_ROOT / ".claude" / "skills" / "session-sounds" / "SKILL.md",
        REPO_ROOT / ".codex" / "skills" / "session-sounds" / "SKILL.md",
        # Shared functional sections -- runtime-specific sections are expected to differ
        [
            "Sound Pool & Assignment",
            "Packs & Themes",
            "Personal Sound Customization",
            "Gitignore Rules",
            "File Inventory (After Install)",
            "Terminal Title Dispatch",
        ],
    ),
    (
        REPO_ROOT / ".claude" / "skills" / "sound-authoring" / "SKILL.md",
        REPO_ROOT / ".codex" / "skills" / "sound-authoring" / "SKILL.md",
        None,  # Fully identical -- no runtime-specific content
    ),
]

# Legacy aliases for backward compat
CLAUDE_SKILL = SKILL_PAIRS[0][0]
CODEX_SKILL = SKILL_PAIRS[0][1]
SHARED_SECTIONS = SKILL_PAIRS[0][2]


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


def _check_full_file(claude_path: Path, codex_path: Path, label: str) -> list[str]:
    """Check that two files are fully identical (for skills with no runtime-specific parts)."""
    if not claude_path.is_file():
        print(f"  ERROR: Claude skill not found: {claude_path}")
        return [f"{label}: (claude missing)"]
    if not codex_path.is_file():
        print(f"  ERROR: Codex skill not found: {codex_path}")
        return [f"{label}: (codex missing)"]

    claude_text = claude_path.read_text(encoding="utf-8").strip()
    codex_text = codex_path.read_text(encoding="utf-8").strip()

    if claude_text != codex_text:
        # Find first differing line
        cl = claude_text.splitlines()
        cx = codex_text.splitlines()
        for j, (a, b) in enumerate(zip(cl, cx)):
            if a != b:
                print(f"  DRIFT in {label} (line {j + 1}):")
                print(f"    Claude: {a[:80]}")
                print(f"    Codex:  {b[:80]}")
                break
        else:
            longer = "Claude" if len(cl) > len(cx) else "Codex"
            print(f"  DRIFT in {label}: {longer} has extra lines")
        return [f"{label}: files differ"]
    return []


def _check_sections(
    claude_path: Path, codex_path: Path, sections: list[str],
) -> list[str]:
    """Check that specific H2 sections match between two files."""
    if not claude_path.is_file():
        print(f"  ERROR: Claude skill not found: {claude_path}")
        return ["(claude skill missing)"]
    if not codex_path.is_file():
        print(f"  ERROR: Codex skill not found: {codex_path}")
        return ["(codex skill missing)"]

    claude_sections = _extract_sections(claude_path.read_text(encoding="utf-8"))
    codex_sections = _extract_sections(codex_path.read_text(encoding="utf-8"))

    drifted: list[str] = []
    for section in sections:
        claude_content = claude_sections.get(section, "")
        codex_content = codex_sections.get(section, "")

        if claude_content != codex_content:
            drifted.append(section)
            if not claude_content:
                print(f"  MISSING in Claude: ## {section}")
            elif not codex_content:
                print(f"  MISSING in Codex:  ## {section}")
            else:
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


def check_drift() -> list[str]:
    """Check all skill pairs. Returns list of drift descriptions."""
    all_drifted: list[str] = []
    for claude_path, codex_path, sections in SKILL_PAIRS:
        label = claude_path.parent.name
        if sections is None:
            all_drifted.extend(_check_full_file(claude_path, codex_path, label))
        else:
            all_drifted.extend(_check_sections(claude_path, codex_path, sections))
    return all_drifted


def _fix_full_file(claude_path: Path, codex_path: Path, label: str) -> int:
    """Fix by copying Claude file to Codex (for fully-identical skills)."""
    if not claude_path.is_file():
        print(f"  ERROR: Claude skill not found: {claude_path}")
        return 0
    claude_text = claude_path.read_text(encoding="utf-8")
    codex_text = codex_path.read_text(encoding="utf-8") if codex_path.is_file() else ""
    if claude_text.strip() != codex_text.strip():
        codex_path.parent.mkdir(parents=True, exist_ok=True)
        codex_path.write_text(claude_text, encoding="utf-8")
        print(f"  FIXED: {label} (full file copy)")
        return 1
    return 0


def _fix_sections(
    claude_path: Path, codex_path: Path, sections: list[str],
) -> int:
    """Fix by copying shared sections from Claude to Codex."""
    if not claude_path.is_file() or not codex_path.is_file():
        print("  ERROR: Both skill files must exist before --fix can run.")
        return 0

    claude_sections = _extract_sections(claude_path.read_text(encoding="utf-8"))
    codex_text = codex_path.read_text(encoding="utf-8")

    fixed = 0
    for section in sections:
        claude_content = claude_sections.get(section, "")
        if not claude_content:
            continue
        pattern = re.compile(
            rf"(^## {re.escape(section)}\s*$)(.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(codex_text)
        if match:
            old_content = match.group(2).strip()
            if old_content != claude_content:
                replacement = f"\n\n{claude_content}\n\n"
                codex_text = codex_text[:match.start(2)] + replacement + codex_text[match.end(2):]
                fixed += 1
                print(f"  FIXED: ## {section}")
        else:
            print(f"  WARNING: ## {section} not found in Codex skill -- add manually")

    if fixed:
        codex_path.write_text(codex_text, encoding="utf-8")
    return fixed


def fix_drift() -> int:
    """Fix all skill pairs. Returns 0 on success."""
    total_fixed = 0
    for claude_path, codex_path, sections in SKILL_PAIRS:
        label = claude_path.parent.name
        if sections is None:
            total_fixed += _fix_full_file(claude_path, codex_path, label)
        else:
            total_fixed += _fix_sections(claude_path, codex_path, sections)

    if total_fixed:
        print(f"\nFixed {total_fixed} item(s).")
    else:
        print("\nNo fixes needed.")
    return 0


if __name__ == "__main__":
    do_fix = "--fix" in sys.argv

    if do_fix:
        sys.exit(fix_drift())
    else:
        print("Checking Claude/Codex skill alignment...")
        drifted = check_drift()
        if drifted:
            print(f"\n{len(drifted)} item(s) have drifted:")
            for s in drifted:
                print(f"  - {s}")
            print("\nRun 'python tools/sync_skills.py --fix' to sync Codex from Claude.")
            sys.exit(1)
        else:
            print("All skills are aligned.")
            sys.exit(0)
