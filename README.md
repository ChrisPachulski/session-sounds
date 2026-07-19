# Session Sounds for Herdr

Session Sounds gives every Herdr agent session a stable tone and uses that tone when the session needs your attention in the background. Herdr's native background notification is one shared sound; this plugin keeps parallel Claude, Codex, and other detected sessions audibly distinct.

It also decorates Herdr's agent label and metadata token with the assigned sound name, such as `codex · Warm Bell` and `sound=Warm Bell`.

## Platform status

| Platform | Status | Playback |
| --- | --- | --- |
| macOS arm64/x86_64 | Supported with Herdr 0.7.4+ | `afplay` |
| Linux arm64/x86_64 | Supported with Herdr 0.7.4+ | `pw-play`, then `paplay`, then `aplay` |
| Windows x86_64 | Preview build only | WinMM |

Windows binaries, checksums, installer logic, and CI coverage are included for future use. Herdr 0.7.4 cannot reliably resolve a plugin-relative executable before applying the plugin working directory on Windows, so the marketplace manifest intentionally enables the runtime only on Linux and macOS. Windows should not be treated as supported until Herdr fixes that launcher behavior and this manifest raises its minimum version.

## Install

Requirements:

- Herdr 0.7.4 or newer
- macOS or Linux for the supported marketplace runtime
- On Linux, one supported audio player on `PATH`: `pw-play`, `paplay`, or `aplay`

First disable Herdr's one shared background sound in your Herdr `config.toml`:

```toml
[ui.sound]
enabled = false
```

Then reload Herdr and install the plugin:

```sh
herdr server reload-config
herdr plugin install ChrisPachulski/session-sounds
```

Herdr's marketplace discovers public repositories; marketplace entries are not a promise that the source has received centralized review. Inspect this repository and its release workflow before installing. Installers download only the version declared in `Cargo.toml`, verify the matching entry in the release's `SHA256SUMS`, and replace the plugin binary only after verification succeeds.

Run the **Check Session Sounds setup** action after installation. Its `doctor` command reports the plugin environment, active theme, state, audio backend, and native Herdr sound setting. It never edits Herdr's configuration.

## Behavior

Session Sounds listens to Herdr's agent-detected, agent-status, pane-move, pane-exit, and pane-close events. An assignment follows the durable agent session when available and falls back to the terminal identity when necessary.

- The first seven concurrent assignments receive different bundled tones.
- When more than seven sessions are live, the least-recently assigned tone is reused.
- A sound plays when background work changes from working or blocked to done, or when an existing run newly becomes blocked.
- Initial detection is silent. A pane visible in the focused workspace's active tab is also silent, including a visible split that is not focused.
- A 1.5-second per-session debounce suppresses duplicate notifications without swallowing later work cycles.
- Audio and metadata errors are warnings. They never fail or stop the underlying agent.
- Muting clears Session Sounds metadata as well as suppressing playback; unmuting restores decoration on later events.

### Actions

| Action | Context | Result |
| --- | --- | --- |
| **Toggle Session Sounds** (`toggle-mute`) | Global | Atomically mute or unmute playback and decoration |
| **Reshuffle session sound** (`reshuffle`) | Pane | Give the selected session a different available tone |
| **Test session sound** (`test-sound`) | Pane | Play its assigned tone, or the theme's first tone |
| **Check Session Sounds setup** (`doctor`) | Global or workspace | Read-only environment and configuration diagnostics |

## Personal themes

Ask Herdr for this plugin's user-owned configuration directory:

```sh
herdr plugin config-dir chrispachulski.session-sounds
```

Create a theme under `<config-dir>/themes/<theme-id>/`. This is the exact v1 `theme.json` shape:

```json
{
  "schema_version": 1,
  "name": "Quiet Desk",
  "description": "Short tones for shared spaces",
  "author": "you",
  "sounds": {
    "soft_ping": "Soft Ping",
    "wood_tick": "Wood Tick"
  }
}
```

Place `soft_ping.wav` and `wood_tick.wav` beside that manifest. Every `sounds` key must be one safe filename component without `.wav`, `/`, `\`, or `:`; every display name must be nonempty; and every listed WAV must be a regular file contained in the theme directory. Use short, widely compatible PCM WAV files. The plugin verifies paths and existence, while the platform player decides whether the WAV encoding is playable.

Select the theme in `<config-dir>/config.toml`:

```toml
enabled = true
theme = "quiet-desk"
```

The default configuration is `enabled = true` and `theme = "default"`. A missing, malformed, empty, or unsafe personal theme produces a warning and falls back to the bundled seven-tone default. The `toggle-mute` action changes `enabled` while preserving the selected theme.

## Local development

Linked plugins skip manifest build commands, so populate `bin/` yourself before linking:

```sh
cargo build --release --locked
mkdir -p bin
install -m 0755 target/release/session-sounds bin/session-sounds
herdr plugin link "$(pwd -P)"
```

Useful checks:

```sh
cargo fmt --all --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-targets --all-features
herdr plugin list --plugin chrispachulski.session-sounds --json
herdr plugin action list --plugin chrispachulski.session-sounds
herdr plugin log list --plugin chrispachulski.session-sounds --limit 50
```

When finished:

```sh
herdr plugin unlink chrispachulski.session-sounds
```

Do not link over an existing installed registration unless you intend to replace it. Linking and unlinking change the local Herdr registry; unlinking does not delete user-owned config or state directories.

## Fresh start from the legacy project

Version `v0.9.0-legacy` freezes the former standalone Claude Code/Codex integration. Version 1.0.0 is a clean Herdr plugin and does not import, remove, rewrite, or otherwise manage any legacy installation or configuration. Remove or retain the old setup separately.

## Privacy and security

- Runtime event handling is offline and has no telemetry.
- Herdr owns the plugin checkout and supplies its root/config/state paths. Personal themes and `config.toml` live in the plugin config directory; assignments and metadata sequence counters live in the plugin state directory.
- Install is the network boundary: a release archive and `SHA256SUMS` are downloaded from `ChrisPachulski/session-sounds`, and the archive is not extracted or executed before its exact SHA-256 entry is verified.
- Playback uses `afplay` on macOS, the first available supported player on Linux, and WinMM in the future-ready Windows binary.
- Plugin metadata is guarded against the current Herdr agent source and expires after 24 hours if cleanup cannot run.

## Troubleshooting

**No sound:** Run **Check Session Sounds setup**. On Linux, install `pw-play` (PipeWire), `paplay` (PulseAudio), or `aplay` (ALSA), then run **Test session sound**.

**Two sounds for one completion:** Herdr's native sound is still enabled. Add `[ui.sound]` with `enabled = false` to Herdr's config and run `herdr server reload-config`. `doctor` detects this but deliberately does not edit it.

**A personal theme falls back to Default:** Inspect `herdr plugin log list --plugin chrispachulski.session-sounds --limit 50`. Confirm `theme.json` uses schema version 1, contains at least one sound, and every named WAV exists inside that theme directory.

**Plugin events or actions do not run:** Confirm it is enabled with `herdr plugin list --plugin chrispachulski.session-sounds --json`, inspect the plugin logs, and remember that a linked checkout needs a manually populated `bin/session-sounds`.

**Windows install/runtime fails:** Windows is not a supported Herdr 0.7.4 runtime. The checked release artifact is preview/future-ready, not a workaround for Herdr's plugin-relative launcher issue.

## License

The Rust code, scripts, and documentation are MIT licensed; see [LICENSE](LICENSE). The seven WAV files under `sounds/themes/default/` were synthesized for this project and are dedicated to the public domain under CC0 1.0; see [their notice](sounds/themes/default/README.md). No third-party audio is bundled.
