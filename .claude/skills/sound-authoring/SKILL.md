---
name: sound-authoring
description: Create, extract, and register custom sounds for session-sounds personal themes. Covers YouTube clip extraction (with the webm silence bug workaround), phrase-boundary-aware cutting via tools/extract_clip.py, WAV normalization, procedural synthesis, and event sound generation. Use when the user wants to add their own sounds or customize their sound pool.
---

# Sound Authoring

Tools and workflows for creating custom session sounds. All authoring tools live in `tools/` (repo only, not installed to `~/.claude/sounds/`). Zero external dependencies -- stdlib `wave` + `struct` only.

## The WebM Silence Bug

The single most common source of broken clips. ffmpeg's `-ss` (seek) flag on WebM files produces SILENT output.

**WRONG -- produces silence:**
```bash
ffmpeg -ss 10 -i source.webm -t 5 clip.wav
```

**CORRECT -- convert full file first, then cut with Python:**
```bash
ffmpeg -y -i source.webm -ar 44100 -ac 1 source_full.wav
python tools/extract_clip.py source_full.wav 10 15 clip.wav
```

Never use `-ss` on `.webm` input. Always convert the entire file to WAV first, then cut.

## YouTube to Personal Theme Sound

### 1. Download

```bash
yt-dlp -x -o "source.%(ext)s" "https://youtube.com/watch?v=VIDEO_ID"
```

Requires `yt-dlp` on PATH. Output is usually `.webm` or `.opus`.

### 2. Convert to WAV (full file, NO seeking)

```bash
ffmpeg -y -i source.webm -ar 44100 -ac 1 source_full.wav
```

- `-ar 44100` -- session-sounds standard sample rate
- `-ac 1` -- mono (required)
- Do NOT add `-ss` or `-t` flags here

### 3. Extract phrase-aligned candidates

```bash
python tools/extract_clip.py source_full.wav 10 20 my_sound.wav --candidates 3 --boost 4
```

Produces `my_sound_a.wav`, `my_sound_b.wav`, `my_sound_c.wav` -- three clips auto-snapped to natural phrase boundaries (note onsets, decays, breath points) within the 10-20s search range.

| Arg | Purpose | Default |
|-----|---------|---------|
| `source` | Input WAV file | required |
| `start` | Approximate start time (seconds) | required |
| `end` | Approximate end time (seconds) | required |
| `output` | Output WAV path | required |
| `--boost N` | Volume multiplier | 4.0 |
| `--candidates N` | Generate N candidates instead of single best | 0 (single) |
| `--max-dur N` | Maximum clip duration (seconds) | 5.0 |
| `--show-envelope` | Print ASCII energy visualization | off |

### How extract_clip.py works

1. Reads the full WAV into memory (mono, 16-bit)
2. Computes short-time RMS energy envelope (100ms non-overlapping windows)
3. Finds phrase boundaries where energy dips below 25% of the local median
4. Snaps the requested start/end times to the nearest phrase boundaries
5. For `--candidates N`: generates N non-overlapping boundary pairs sorted by proximity to range center
6. Applies volume boost, hard-clips to 16-bit range, adds 80ms fade-in + 150ms fade-out
7. Asserts peak > 0 (catches silent failures)

### 4. Present candidates to the user

You cannot hear audio. Always generate 3+ candidates and present them with metadata so the user can pick:

```
Candidate A: 10.2-14.8s (4.6s) peak=28451
Candidate B: 12.1-15.9s (3.8s) peak=31204
Candidate C: 8.7-13.1s (4.4s) peak=25830
```

Never choose for them.

### 5. Install to personal theme

```bash
cp my_sound_a.wav ~/.claude/sounds/themes/personal/my_sound.wav
```

### 6. Register display name (optional)

Edit `~/.claude/sounds/themes/personal/theme.json`:

```json
{
    "schema_version": 1,
    "name": "Personal",
    "description": "My custom sounds",
    "author": "you",
    "sounds": {
        "my_sound": "My Custom Sound"
    }
}
```

If not registered, the filename auto-titles: `my_sound.wav` -> "My Sound".

### 7. Activate personal theme

Set in `~/.claude/sounds/config.json`:
```json
{"enabled": true, "theme": "personal"}
```

Or via environment variable: `export SESSION_SOUNDS_THEME=personal`

### 8. Clean up temp files

Remove source downloads, intermediate WAVs, and rejected candidates. Only the final clip in `themes/personal/` matters.

## Sound File Requirements

| Property | Value | Notes |
|----------|-------|-------|
| Format | WAV | 16-bit PCM |
| Sample rate | 44100 Hz | Non-negotiable |
| Channels | Mono (1) | Stereo works but wastes space |
| Duration | Under 5 seconds | Hard limit for notification sounds |

### Normalization

Check **RMS**, not just peak. A keyboard stab at peak 16383 is inaudible because its RMS is tiny. A sustained pad at peak 32767 is painfully loud because its RMS is high.

| Sound type | Target peak | Why |
|------------|-------------|-----|
| Continuous (pads, drones, themes) | ~16383 | High RMS -- existing pool baseline |
| Sparse (stabs, clicks, waka-waka) | 32767 | Low RMS -- needs full-scale peak for audibility |

Apply 80ms fade-in, 150ms fade-out on clips >= 4.4s to prevent click artifacts.

## Procedural Synthesis

### Reference implementations (tools/generate_all_sounds.py)

Three synthesized sounds demonstrate the pattern:

- **Mario Power-Up**: NES square wave arpeggios stepping up in major thirds (`_square()` helper, 25% duty for chiptune brightness)
- **Scorpion**: Gong hit + chain whoosh + metallic impact + rattle + bass thud (layered sine harmonics with per-stage envelopes)
- **Pokeball**: Ball lands + 3 wobbles + click + success sparkle (pitch-bent square waves for wobble, ascending arpeggio for sparkle)

### Event sound generation (tools/generate_event_sounds.py)

Regenerates the three default event-type sounds:

```bash
python tools/generate_event_sounds.py
python tools/generate_event_sounds.py --output-dir ~/.claude/sounds/events
```

| Event | Sound | Technique |
|-------|-------|-----------|
| Error | Two descending buzzy notes (520 Hz -> 380 Hz) | Square-ish wave: fundamental + 3rd + 5th harmonics |
| Approval | Rising two-note chime (C5 -> E5) | Sine + gentle 2nd harmonic, exponential decay |
| End | Soft descending tone (440 Hz -> 330 Hz) | Sine with linear frequency sweep, exponential decay |

All output: mono, 44100 Hz, 16-bit PCM, under 2 seconds, peaks at 30-45% of full scale.

### Synthesis pattern

1. Define frequency, duration, waveform per note
2. Apply envelope (attack/decay via `math.exp(-t * rate)`)
3. Add harmonics for timbre (square for chiptune, sine for clean)
4. Fade edges with `_fade()` helper (prevents click artifacts)
5. Write with `wave` + `struct` modules -- stdlib only, zero dependencies

## Gitignore

All personal WAV files are gitignored -- they never leave the user's machine:

- `sounds/*.wav` -- pool sounds
- `sounds/themes/**/*.wav` -- theme sounds including personal

Exception: `sounds/events/*/default.wav` -- synthesized event defaults ARE tracked (non-copyrighted).

The `tools/` directory is NOT installed by the installer. It stays in the repo for authoring use only.
