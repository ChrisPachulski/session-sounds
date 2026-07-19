---
name: sound-authoring
description: Create, convert, normalize, register, activate, and troubleshoot personal WAV themes for the Herdr Session Sounds plugin. Use when adding custom notification tones, turning lawful audio into short WAVs with ffmpeg, editing theme.json or config.toml, diagnosing fallback to the default theme, or regenerating the bundled synthesized CC0 defaults as a repository maintainer.
---

# Herdr personal-theme sound authoring

Create personal themes only in the user-owned Herdr plugin config directory. Do not edit the bundled default for ordinary customization.

## Create and register a theme

1. Resolve the config directory and create a safe theme ID:

       CONFIG_DIR="$(herdr plugin config-dir chrispachulski.session-sounds)"
       THEME_ID="personal"
       mkdir -p "$CONFIG_DIR/themes/$THEME_ID"

2. Choose a sound ID containing one safe filename component. Prefer lowercase ASCII letters, digits, underscores, or hyphens. Do not use a slash, backslash, colon, empty ID, or name ending in .wav.

3. Put each WAV at $CONFIG_DIR/themes/$THEME_ID/<sound-id>.wav.

4. Register every WAV in theme.json. The sounds object is the theme inventory; an unregistered file is not loaded.

       {
         "schema_version": 1,
         "name": "Personal",
         "description": "My short notification tones",
         "author": "you",
         "sounds": {
           "calm_chime": "Calm Chime",
           "wood_tick": "Wood Tick"
         }
       }

   Keep schema_version at 1, make name and every display name nonempty, and ensure each key has a matching WAV in the same directory.

5. Edit the existing $CONFIG_DIR/config.toml without discarding unrelated keys:

       enabled = true
       theme = "personal"

## Convert and normalize audio

Use audio you have permission to use. Convert the full source to a predictable intermediate WAV before cutting:

    ffmpeg -y -i source.ext -ar 44100 -ac 1 -c:a pcm_s16le source-full.wav

Cut a short section from that WAV and normalize it conservatively:

    ffmpeg -y -ss 00:00:12.0 -t 2.5 -i source-full.wav \
      -af "loudnorm=I=-20:LRA=7:TP=-2" \
      -ar 44100 -ac 1 -c:a pcm_s16le calm_chime.wav

Keep notifications brief, audition them at the user's normal system volume, avoid clipping, and add short fades when a cut clicks. Treat 44.1 kHz mono 16-bit PCM as the compatibility target; the platform player ultimately decides whether an encoding is playable.

Copy only the selected final WAV into the theme and add its ID/display name to theme.json.

## Activate, test, and troubleshoot

Select a pane, then test the active theme through Herdr:

    herdr plugin action invoke test-sound --plugin chrispachulski.session-sounds
    herdr plugin log list --plugin chrispachulski.session-sounds --limit 50

If the plugin warns and uses Default, verify all of these:

- config.toml names the intended theme ID.
- theme.json is valid JSON with schema version 1 and at least one registered sound.
- Every registered ID is safe and has a regular <id>.wav inside the theme directory.
- Every display name is a nonempty string.

A missing, malformed, empty, or unsafe personal theme deliberately falls back to the bundled default. Fix the warning and run test-sound again.

## Maintain bundled defaults

Only repository maintainers should run:

    python tools/generate_default_theme.py

That tool regenerates the seven bundled synthesized WAVs and their manifest. Keep those originals CC0, inspect the resulting diff, and run the full Rust tests before committing them.
