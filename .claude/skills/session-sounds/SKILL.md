---
name: session-sounds
description: Herdr Session Sounds plugin architecture, stable agent-tone assignment, metadata decoration, personal themes, installers, and diagnostics. Use whenever modifying this repository's Rust event/action code, Herdr manifest, release installers, bundled sounds, config/state contracts, background notification logic, or when troubleshooting missing, duplicate, foreground, or wrong-session sounds. Do not apply the archived pre-1.0 Claude/Codex launcher design.
---

# Session Sounds — Claude Code repository guide

This repository is a Herdr 0.7.4 plugin. Its runtime is the single Rust binary `session-sounds`; do not reintroduce any pre-1.0 host integration or mutate host-agent settings.

## Current architecture

- `herdr-plugin.toml` declares five pane events and four actions. Event hooks dispatch `session-sounds event`; action subcommands are `toggle-mute`, `reshuffle`, `test-sound`, and `doctor`.
- `src/app.rs` serializes config then state access, queries Herdr for live pane identity/visibility, updates state, decorates metadata, and requests playback.
- `src/state.rs` assigns unique tones for the first seven live identities, then reuses the least-recently assigned tone. Durable agent-session identity is preferred; terminal identity is the fallback.
- Only newly completed or newly blocked background runs alert. Visible panes are silent, initial detection is silent, and playback is debounced for 1.5 seconds per assignment.
- Metadata uses source `session-sounds`, token `sound=<display name>`, display agent `<agent> · <display name>`, a guarded agent source, monotonic sequences, and a 24-hour TTL.
- Playback is nonfatal: macOS uses `afplay`; Linux tries `pw-play`, `paplay`, then `aplay`; the future-ready Windows binary uses WinMM synchronously.

## Configuration and themes

Herdr supplies `HERDR_PLUGIN_ROOT`, `HERDR_PLUGIN_CONFIG_DIR`, and `HERDR_PLUGIN_STATE_DIR`; never hard-code user paths. `config.toml` supports only:

```toml
enabled = true
theme = "default"
```

Personal themes live at `<config-dir>/themes/<id>/theme.json`. Schema v1 requires nonempty `name` and `sounds`; optional fields are `description` and `author`. Each sound maps a safe ID to a nonempty display name and requires `<id>.wav` inside the theme directory. Invalid personal themes warn and fall back to `sounds/themes/default/`.

Keep exactly seven bundled synthesized WAVs and their IDs aligned with the default `theme.json`. They are CC0; code and documentation are MIT. Do not claim third-party audio.

## Constraints and verification

- Herdr's native sound must be disabled manually with `[ui.sound] enabled = false`. `doctor` detects but never edits it.
- Marketplace runtime support is Linux/macOS. Windows artifacts remain preview-only because Herdr 0.7.4 cannot reliably resolve plugin-relative executables on Windows; do not claim otherwise or widen manifest platforms without an upstream fix and end-to-end proof.
- Linked plugins skip build commands. Build release mode and populate `bin/session-sounds` before `herdr plugin link`.
- Installers derive version from `Cargo.toml`, verify the exact release asset in `SHA256SUMS`, and stage before replacement. Preserve that ordering.
- Before completion run format, strict clippy, all Rust tests, distribution contracts, target checks, and a guarded link/action-discovery/unlink smoke. Never leave a development link registered.

For troubleshooting, start with the `doctor` action, then `herdr plugin log list --plugin chrispachulski.session-sounds --limit 50`. Missing Linux players, enabled native Herdr sound, malformed theme manifests, and an unpopulated linked `bin/` are the common failure modes.
