"""
Terminal-aware title setter with per-terminal dispatch.

Detects the running terminal emulator and uses the best available mechanism:
- Windows:  SetConsoleTitleW (primary) + CONOUT$ OSC (fallback)
- Kitty:    kitten @ set-tab-title (IPC -- only way to set kitty tab titles)
- WezTerm:  wezterm cli set-tab-title (IPC -- bypasses pane-vs-tab ambiguity)
- iTerm2:   OSC 1 via /dev/tty (tab-specific, not window+tab)
- tmux:     tmux rename-window + DCS passthrough to outer terminal
- Default:  /dev/tty OSC 0 (Unix) or stderr OSC 0 (fallback)

Agent-agnostic: works with Claude Code, Codex, or any terminal agent.
"""
import os
import shutil
import subprocess
import sys


###############################################################################
# Terminal detection
###############################################################################

def _detect_terminal() -> str:
    """Detect the current terminal emulator from environment variables."""
    if os.environ.get("TMUX"):
        return "tmux"
    # Kitty uses TERM=xterm-kitty and KITTY_WINDOW_ID, not TERM_PROGRAM
    if os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    term = os.environ.get("TERM_PROGRAM", "").lower()
    if "iterm" in term:
        return "iterm2"
    if "wezterm" in term:
        return "wezterm"
    if "apple_terminal" in term:
        return "apple-terminal"
    if os.environ.get("WT_SESSION"):
        return "windows-terminal"
    if sys.platform == "win32":
        return "windows"
    return "generic"


###############################################################################
# Per-terminal title setters
###############################################################################

def _title_windows(title: str) -> None:
    """Windows: SetConsoleTitleW (primary) + CONOUT$ OSC (fallback)."""
    import ctypes
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass
    # CONOUT$ fallback for terminals that ignore SetConsoleTitleW
    _write_osc_conout(title)


def _title_windows_terminal(title: str) -> None:
    """Windows Terminal: CONOUT$ OSC (primary, WT processes VT natively)
    + SetConsoleTitleW (fallback)."""
    _write_osc_conout(title)
    import ctypes
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def _title_kitty(title: str) -> None:
    """Kitty: IPC via kitten remote control (only way to set tab titles)."""
    kitten = shutil.which("kitten")
    if kitten:
        try:
            subprocess.run(
                [kitten, "@", "set-tab-title", title],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
            )
            return
        except Exception:
            pass
    # Fallback to generic (OSC 0 sets pane title, not tab -- partial)
    _write_osc_devtty(title)


def _title_wezterm(title: str) -> None:
    """WezTerm: CLI set-tab-title (bypasses pane-vs-tab ambiguity)."""
    wezterm = shutil.which("wezterm")
    if wezterm:
        try:
            subprocess.run(
                [wezterm, "cli", "set-tab-title", title],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
            )
            return
        except Exception:
            pass
    _write_osc_devtty(title)


def _title_iterm2(title: str) -> None:
    """iTerm2: OSC 1 via /dev/tty (tab-specific, more precise than OSC 0)."""
    # OSC 1 = set icon name (tab title in iTerm2), not window title
    seq = f"\033]1;{title}\007"
    _write_to_devtty(seq)


def _title_tmux(title: str) -> None:
    """tmux: rename-window + DCS passthrough OSC 0 to outer terminal."""
    # Set the tmux window name directly
    try:
        subprocess.run(
            ["tmux", "rename-window", title],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
    except Exception:
        pass
    # DCS passthrough to reach the outer terminal through tmux
    # Requires tmux 3.3+ with allow-passthrough on
    inner_osc = f"\033]0;{title}\007"
    dcs = f"\033Ptmux;\033{inner_osc}\033\\"
    _write_to_devtty(dcs)


def _title_apple_terminal(title: str) -> None:
    """macOS Terminal.app: OSC 1 (icon/tab name) + OSC 2 (window title).

    Terminal.app overwrites tab titles with the active process name by default.
    Setting OSC 1 (icon name) separately forces Terminal.app to show our title
    as the tab label. OSC 2 sets the window title without affecting the tab.
    """
    seq = f"\033]1;{title}\007\033]2;{title}\007"
    _write_to_devtty(seq)


def _title_generic(title: str) -> None:
    """Generic Unix: /dev/tty OSC 0 (primary) + stderr OSC 0 (fallback)."""
    _write_osc_devtty(title)


###############################################################################
# Low-level write helpers
###############################################################################

def _write_osc_devtty(title: str) -> None:
    """Write OSC 0 (set title + icon) to /dev/tty."""
    seq = f"\033]0;{title}\007"
    _write_to_devtty(seq)


def _write_to_devtty(seq: str) -> None:
    """Write raw escape sequence to /dev/tty (Unix) or stderr (fallback)."""
    if sys.platform != "win32":
        try:
            with open("/dev/tty", "w") as tty:
                tty.write(seq)
                tty.flush()
                return
        except Exception:
            pass
    # Fallback: stderr (works when TUI captures stdout but not stderr)
    try:
        sys.stderr.write(seq)
        sys.stderr.flush()
    except Exception:
        pass


def _write_osc_conout(title: str) -> None:
    """Write OSC 0 to CONOUT$ (Windows -- always reaches console)."""
    seq = f"\033]0;{title}\007"
    try:
        with open("CONOUT$", "w") as con:
            con.write(seq)
            con.flush()
    except Exception:
        pass


###############################################################################
# Public API
###############################################################################

_DISPATCH = {
    "windows": _title_windows,
    "windows-terminal": _title_windows_terminal,
    "kitty": _title_kitty,
    "wezterm": _title_wezterm,
    "iterm2": _title_iterm2,
    "tmux": _title_tmux,
    "apple-terminal": _title_apple_terminal,
    "generic": _title_generic,
}


def emit_title(title: str) -> None:
    """Set the terminal tab title using the best available mechanism.

    Agent-agnostic -- works from any context (launcher, hook, watcher).
    Detects the terminal emulator and dispatches to the optimal approach.
    """
    terminal = _detect_terminal()
    handler = _DISPATCH.get(terminal, _title_generic)
    handler(title)
