# Changelog

## 1.0.0 - 2026-07-18

- Rebuilt Session Sounds as a Herdr 0.7.4 plugin with one Rust executable.
- Added stable per-agent tone assignment, background done/blocked notifications, Herdr metadata decoration, pane lifecycle reconciliation, mute/reshuffle/test/doctor actions, personal themes, and seven bundled CC0 tones.
- Added verified release installers, five target artifacts, SHA-256 checksums, and Linux/macOS marketplace runtime support.
- Added a future-ready Windows binary and installer; Windows runtime remains preview-only until Herdr reliably resolves plugin-relative executables there.
- Started fresh: the plugin neither imports nor removes any legacy Claude Code or Codex installation.

## 0.9.0-legacy - 2026-07-18

- Frozen tag for the former standalone Python launcher, Claude Code hooks, Codex watcher, terminal-title behavior, themes, and sound packs.
- Retained only as an archive and migration escape hatch; it is not the architecture documented on the default branch.
