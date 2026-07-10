"""
Sound pack discovery, validation, and resolution.

Loads pack manifests from the packs/ directory tree and resolves them
into the same pool format that sound_manager._load_pool() produces.
Backward-compatible: if no pack is active, the legacy loose-WAV
auto-discovery still works unchanged.

Pack selection priority (first match wins):
    1. CLAUDE_SOUND_PACK env var         (e.g. "windows-native")
    2. ~/.claude/sounds/active_pack.txt  (persisted config)
    3. No pack -- fall through to legacy auto-discovery

Stdlib only. No external dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import TypedDict

log = logging.getLogger("sound_manager")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class SoundEntry(TypedDict):
    file: str
    name: str


class PackManifest(TypedDict, total=False):
    schema_version: int
    name: str
    author: str
    description: str
    category: str
    platform: list[str]
    mode: str
    sounds: list[SoundEntry]


class PackInfo(TypedDict):
    """A loaded, validated pack ready for pool building."""
    manifest: PackManifest
    pack_dir: Path
    pack_id: str


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PLATFORM_MAP: dict[str, str] = {
    "win32": "windows",
    "darwin": "darwin",
    "linux": "linux",
}

VALID_MODES = {"bundled", "system", "recipe", "platform-native"}
VALID_CATEGORIES = {"game", "movie", "ambient", "system", "music", "meme", "mixed", "other"}
VALID_PLATFORMS = {"windows", "darwin", "linux"}

# Where packs live at runtime (after install)
_RUNTIME_PACKS_DIR = Path.home() / ".claude" / "sounds" / "packs"

# Where packs live in the repo (for development / installer source)
_REPO_PACKS_DIR = Path(__file__).parent / "packs"

# Persisted pack selection
_ACTIVE_PACK_FILE = Path.home() / ".claude" / "sounds" / "active_pack.txt"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_manifest(data: dict, pack_dir: Path) -> list[str]:
    """Validate a parsed pack.json dict. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    if data.get("schema_version") != 1:
        sv = data.get("schema_version")
        errors.append(f"schema_version must be 1, got {sv!r}")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name is required and must be a non-empty string")
    elif len(name) > 64:
        errors.append(f"name exceeds 64 chars: {len(name)}")

    sounds = data.get("sounds") or data.get("pool")
    if not isinstance(sounds, list) or len(sounds) == 0:
        errors.append("sounds (or pool) must be a non-empty array")
    else:
        for i, s in enumerate(sounds):
            if not isinstance(s, dict):
                errors.append(f"sounds[{i}] is not an object")
                continue
            if not isinstance(s.get("file"), str) or not s["file"].strip():
                errors.append(f"sounds[{i}].file is required")
            if not isinstance(s.get("name"), str) or not s["name"].strip():
                errors.append(f"sounds[{i}].name is required")

    mode = data.get("mode", "bundled")
    if mode not in VALID_MODES:
        errors.append(f"mode must be one of {VALID_MODES}, got {mode!r}")

    category = data.get("category")
    if category is not None and category not in VALID_CATEGORIES:
        errors.append(f"category must be one of {VALID_CATEGORIES}, got {category!r}")

    platform = data.get("platform")
    if platform is not None:
        if not isinstance(platform, list) or len(platform) == 0:
            errors.append("platform must be a non-empty array if provided")
        else:
            for p in platform:
                if p not in VALID_PLATFORMS:
                    errors.append(f"unknown platform {p!r}")

    return errors


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _discover_packs_in(search_dir: Path) -> dict[str, PackInfo]:
    """Scan a directory for subdirectories containing pack.json. Returns {pack_id: PackInfo}."""
    packs: dict[str, PackInfo] = {}
    if not search_dir.is_dir():
        return packs

    for child in sorted(search_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "pack.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("pack_loader: skipping %s -- %s", child.name, exc)
            continue

        errors = validate_manifest(data, child)
        if errors:
            log.warning("pack_loader: skipping %s -- validation errors: %s", child.name, errors)
            continue

        pack_id = child.name
        packs[pack_id] = PackInfo(
            manifest=data,
            pack_dir=child,
            pack_id=pack_id,
        )
    return packs


def discover_all_packs() -> dict[str, PackInfo]:
    """Return all valid packs from both repo and runtime directories.

    Runtime packs override repo packs with the same pack_id.
    """
    packs = _discover_packs_in(_REPO_PACKS_DIR)
    packs.update(_discover_packs_in(_RUNTIME_PACKS_DIR))
    return packs


# ---------------------------------------------------------------------------
# Resolution: manifest -> pool entries
# ---------------------------------------------------------------------------

def _current_platform() -> str:
    return _PLATFORM_MAP.get(sys.platform, "linux")


def _is_absolute_path(file_str: str) -> bool:
    """Check if a file string is an absolute path (cross-platform)."""
    if file_str.startswith("/"):
        return True
    if len(file_str) >= 3 and file_str[1] == ":" and file_str[2] in ("/", chr(92)):
        return True
    return False


def resolve_pack(pack: PackInfo) -> list[dict[str, str]]:
    """Resolve a pack into pool entries: [{"file": <abs_path>, "name": <display_name>}, ...].

    Only includes sounds whose WAV files actually exist on disk.
    For recipe packs, missing files are silently skipped (that is the point --
    the user fills them in over time).
    """
    manifest = pack["manifest"]
    pack_dir = pack["pack_dir"]
    mode = manifest.get("mode", "bundled")
    platform = manifest.get("platform")
    current = _current_platform()

    # Platform check
    if platform and current not in platform:
        log.debug("pack_loader: skipping pack %r -- platform %s not in %s",
                  manifest["name"], current, platform)
        return []

    pool: list[dict[str, str]] = []
    for sound in manifest.get("sounds") or manifest.get("pool", []):
        file_str = sound["file"]
        display_name = sound["name"]

        # Resolve to absolute path
        if _is_absolute_path(file_str):
            wav_path = Path(file_str)
        else:
            wav_path = pack_dir / file_str

        if not wav_path.is_file():
            if mode == "recipe":
                log.debug("pack_loader: recipe sound missing (expected): %s", wav_path)
            elif mode == "system":
                log.debug("pack_loader: system sound missing: %s", wav_path)
            else:
                log.warning("pack_loader: bundled sound missing: %s", wav_path)
            continue

        pool.append({"file": str(wav_path), "name": display_name})

    return pool


# ---------------------------------------------------------------------------
# Active pack selection
# ---------------------------------------------------------------------------

def get_active_pack_id() -> str | None:
    """Determine which pack is active. Returns pack_id or None for legacy mode.

    Priority:
        1. CLAUDE_SOUND_PACK env var
        2. ~/.claude/sounds/active_pack.txt
        3. None (legacy auto-discovery)
    """
    env_pack = os.environ.get("CLAUDE_SOUND_PACK", "").strip()
    if env_pack:
        return env_pack

    if _ACTIVE_PACK_FILE.is_file():
        try:
            text = _ACTIVE_PACK_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass

    return None


def set_active_pack(pack_id: str | None) -> None:
    """Persist the active pack selection. Pass None to clear (return to legacy mode)."""
    if pack_id is None:
        _ACTIVE_PACK_FILE.unlink(missing_ok=True)
        log.debug("pack_loader: cleared active pack")
    else:
        _ACTIVE_PACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_PACK_FILE.write_text(pack_id, encoding="utf-8")
        log.debug("pack_loader: set active pack to %r", pack_id)


# ---------------------------------------------------------------------------
# Main entry point for sound_manager integration
# ---------------------------------------------------------------------------

def load_pack_pool() -> list[dict[str, str]] | None:
    """Load the active pack sound pool.

    Returns:
        list of pool entries if a pack is active and has sounds.
        None if no pack is active (caller should fall through to legacy).
    """
    pack_id = get_active_pack_id()
    if pack_id is None:
        return None

    all_packs = discover_all_packs()
    pack = all_packs.get(pack_id)
    if pack is None:
        log.warning("pack_loader: active pack %r not found in: %s",
                    pack_id, list(all_packs.keys()))
        return None

    pool = resolve_pack(pack)
    if not pool:
        log.warning("pack_loader: active pack %r resolved to zero playable sounds", pack_id)
        return None

    log.debug("pack_loader: loaded pack %r with %d sounds", manifest_name(pack), len(pool))
    return pool


def manifest_name(pack: PackInfo) -> str:
    """Extract display name from a pack."""
    return pack["manifest"].get("name", pack["pack_id"])


# ---------------------------------------------------------------------------
# CLI: pack management commands
# ---------------------------------------------------------------------------

def list_packs() -> None:
    """Print all discovered packs with status."""
    active_id = get_active_pack_id()
    all_packs = discover_all_packs()

    if not all_packs:
        print("No packs found.")
        print(f"  Install packs to: {_RUNTIME_PACKS_DIR}")
        return

    fmt = "  {:<20} {:<24} {:<10} {:<16} {}"
    print(fmt.format("Pack ID", "Name", "Mode", "Sounds", "Status"))
    print(fmt.format("-" * 19, "-" * 23, "-" * 9, "-" * 15, "-" * 12))

    for pack_id, pack in sorted(all_packs.items()):
        m = pack["manifest"]
        resolved = resolve_pack(pack)
        total = len(m.get("sounds", []))
        available = len(resolved)
        mode = m.get("mode", "bundled")
        is_active = "ACTIVE" if pack_id == active_id else ""
        status_detail = f"{available}/{total} ready"
        if mode == "recipe" and available < total:
            status_detail += f" ({total - available} needed)"

        print(fmt.format(pack_id, m.get("name", "?"), mode, status_detail, is_active))

    if active_id is None:
        print()
        print("  No active pack. Using legacy auto-discovery.")
    print()
    print("  Activate: python pack_loader.py activate <pack-id>")
    print("  Deactivate: python pack_loader.py deactivate")


def activate_pack(pack_id: str) -> None:
    """Activate a pack by ID."""
    all_packs = discover_all_packs()
    if pack_id not in all_packs:
        available = ", ".join(sorted(all_packs.keys()))
        print(f"Pack not found: {pack_id}. Available: {available}")
        return

    pack = all_packs[pack_id]
    pool = resolve_pack(pack)
    set_active_pack(pack_id)
    print(f"Activated pack: {manifest_name(pack)}")
    print(f"  {len(pool)} sounds ready for session assignment")

    if not pool:
        mode = pack["manifest"].get("mode", "bundled")
        if mode == "recipe":
            pd = pack["pack_dir"]
            print(f"  Recipe pack -- add WAV files to: {pd}")
        else:
            print("  Warning: no playable sounds found")


def deactivate_pack() -> None:
    """Return to legacy auto-discovery mode."""
    set_active_pack(None)
    print("Pack deactivated. Returning to legacy auto-discovery mode.")


def validate_pack_cli(pack_dir_str: str) -> None:
    """Validate a pack.json file from a given directory."""
    pack_dir = Path(pack_dir_str)
    manifest_path = pack_dir / "pack.json"
    if not manifest_path.is_file():
        print(f"No pack.json found in: {pack_dir}")
        return

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}")
        return

    errors = validate_manifest(data, pack_dir)
    if errors:
        print(f"Validation FAILED ({len(errors)} errors):")
        for e in errors:
            print(f"  - {e}")
        return

    pack_id = pack_dir.name
    pack = PackInfo(manifest=data, pack_dir=pack_dir, pack_id=pack_id)
    pool = resolve_pack(pack)
    entries = data.get("sounds") or data.get("pool") or []
    total = len(entries)
    mode = data.get("mode", "bundled")

    pack_name = data.get("name", "?")
    print(f"Valid pack: {pack_name}")
    print(f"  Mode: {mode}")
    print(f"  Sounds: {len(pool)}/{total} playable")

    if mode == "recipe":
        missing = [
            s["name"] for s in entries
            if not (pack_dir / s["file"]).is_file()
            and not _is_absolute_path(s["file"])
        ]
        if missing:
            print(f"  Missing files ({len(missing)}):")
            for m in missing:
                print(f"    - {m}")


def _info_pack(pack_id: str) -> None:
    """Print detailed information about a pack."""
    all_packs = discover_all_packs()
    pack = all_packs.get(pack_id)
    if pack is None:
        print(f"Pack not found: {pack_id}")
        return

    m = pack["manifest"]
    pool = resolve_pack(pack)
    active_id = get_active_pack_id()

    pack_name = m.get("name", "?")
    author = m.get("author", "(not set)")
    desc = m.get("description", "(not set)")
    cat = m.get("category", "(not set)")
    platform_str = ", ".join(m.get("platform", ["all"]))
    mode = m.get("mode", "bundled")
    loc = pack["pack_dir"]
    active_str = "yes" if pack_id == active_id else "no"
    total = len(m.get("sounds", []))

    print(f"Pack: {pack_name}")
    print(f"  ID:          {pack_id}")
    print(f"  Author:      {author}")
    print(f"  Description: {desc}")
    print(f"  Category:    {cat}")
    print(f"  Platform:    {platform_str}")
    print(f"  Mode:        {mode}")
    print(f"  Location:    {loc}")
    print(f"  Active:      {active_str}")
    print(f"  Sounds:      {len(pool)}/{total} playable")
    print()

    for s in m.get("sounds", []):
        file_str = s["file"]
        if _is_absolute_path(file_str):
            wav_path = Path(file_str)
        else:
            wav_path = pack["pack_dir"] / file_str
        exists = wav_path.is_file()
        status = "ok" if exists else "MISSING"
        sname = s["name"]
        print(f"    [{status:<7}] {sname:<24} {file_str}")


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

_USAGE = """Usage: python pack_loader.py <command> [args]

Commands:
  list                  Show all discovered packs
  activate <pack-id>    Set the active pack
  deactivate            Return to legacy auto-discovery
  validate <pack-dir>   Validate a pack.json in the given directory
  info <pack-id>        Show detailed info for a pack
"""


if __name__ == "__main__":
    import sys as _sys
    args = _sys.argv[1:]
    if not args:
        print(_USAGE)
    elif args[0] == "list":
        list_packs()
    elif args[0] == "activate" and len(args) >= 2:
        activate_pack(args[1])
    elif args[0] == "deactivate":
        deactivate_pack()
    elif args[0] == "validate" and len(args) >= 2:
        validate_pack_cli(args[1])
    elif args[0] == "info" and len(args) >= 2:
        _info_pack(args[1])
    else:
        print(_USAGE)
