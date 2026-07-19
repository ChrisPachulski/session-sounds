#!/usr/bin/env python3
"""
Generate the default theme: 5+ copyright-safe synthesized notification sounds.

These are original compositions using pure math synthesis -- no sampled audio,
no references to copyrighted properties. They ship with the repo as the
out-of-box experience for anyone who clones session-sounds.

Output: sounds/themes/default/*.wav (tracked bundled CC0 assets)
All output: mono, 44100 Hz, 16-bit PCM WAV, under 5 seconds.

Usage:
    python tools/generate_default_theme.py
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SR = 44100
THEME_DIR = Path(__file__).parent.parent / "sounds" / "themes" / "default"


def _write_wav(name: str, samples: list[int]) -> None:
    path = THEME_DIR / f"{name}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    clamped = [max(-32767, min(32767, s)) for s in samples]
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack(f"<{len(clamped)}h", *clamped))
    peak = max(abs(s) for s in clamped) if clamped else 0
    dur = len(clamped) / SR
    print(f"  {name}.wav: {dur:.2f}s, peak={peak}")
    assert peak > 0, f"SILENT OUTPUT: {path}"


def _sine(freq: float, t: float) -> float:
    return math.sin(2 * math.pi * freq * t)


def _square(freq: float, t: float, duty: float = 0.5) -> float:
    phase = (freq * t) % 1.0
    return 1.0 if phase < duty else -1.0


def _fade(samples: list[int], fade_in_ms: int = 10, fade_out_ms: int = 30) -> list[int]:
    fi = int(fade_in_ms / 1000 * SR)
    fo = int(fade_out_ms / 1000 * SR)
    out = list(samples)
    for i in range(min(fi, len(out))):
        out[i] = int(out[i] * i / fi)
    for i in range(min(fo, len(out))):
        idx = len(out) - fo + i
        if 0 <= idx < len(out):
            out[idx] = int(out[idx] * (1.0 - i / fo))
    return out


def _silence(dur_s: float) -> list[int]:
    return [0] * int(SR * dur_s)


###############################################################################
# 1. Bright Cascade -- rising major arpeggio with bell-like decay
###############################################################################
def generate_bright_cascade() -> list[int]:
    """Ascending major triad arpeggio with shimmer harmonics."""
    samples: list[int] = []
    # C5 -> E5 -> G5 -> C6 (major triad + octave)
    freqs = [523.25, 659.25, 783.99, 1046.50]
    for i, freq in enumerate(freqs):
        dur_s = 0.18 - i * 0.02  # accelerating
        n = int(dur_s * SR)
        note: list[int] = []
        for j in range(n):
            t = j / SR
            env = math.exp(-t * 6.0) * 0.45
            val = _sine(freq, t) + 0.3 * _sine(freq * 2, t) + 0.1 * _sine(freq * 3, t)
            note.append(int(val * env * 32767))
        samples.extend(_fade(note, fade_in_ms=3, fade_out_ms=15))
        samples.extend(_silence(0.02))
    # Sustain final note
    n = int(0.3 * SR)
    for j in range(n):
        t = j / SR
        env = math.exp(-t * 4.0) * 0.35
        val = _sine(1046.50, t) + 0.2 * _sine(2093.0, t)
        samples.append(int(val * env * 32767))
    return _fade(samples, fade_in_ms=3, fade_out_ms=40)


###############################################################################
# 2. Warm Bell -- single rich bell strike with long decay
###############################################################################
def generate_warm_bell() -> list[int]:
    """Deep bell tone with inharmonic partials for metallic warmth."""
    dur_s = 2.5
    n = int(dur_s * SR)
    samples: list[int] = []
    base = 440.0
    # Bell partials (inharmonic ratios give the metallic character)
    partials = [
        (base, 0.5),
        (base * 2.0, 0.35),
        (base * 2.76, 0.25),  # inharmonic
        (base * 3.58, 0.15),  # inharmonic
        (base * 4.13, 0.08),  # inharmonic
    ]
    for i in range(n):
        t = i / SR
        val = 0.0
        for freq, amp in partials:
            decay = math.exp(-t * (2.0 + freq / 300))
            val += amp * decay * _sine(freq, t)
        samples.append(int(val * 0.4 * 32767))
    return _fade(samples, fade_in_ms=2, fade_out_ms=80)


###############################################################################
# 3. Pulse Bounce -- bouncing square wave with pitch descent
###############################################################################
def generate_pulse_bounce() -> list[int]:
    """Descending square wave bounces -- playful retro feel."""
    samples: list[int] = []
    bounces = [(880, 0.08), (660, 0.10), (550, 0.12), (440, 0.14), (330, 0.18)]
    for freq, dur_s in bounces:
        n = int(dur_s * SR)
        note: list[int] = []
        for i in range(n):
            t = i / SR
            env = math.exp(-t * 8.0) * 0.35
            val = _square(freq, t, 0.35) + 0.2 * _sine(freq, t)
            note.append(int(val * env * 32767))
        samples.extend(_fade(note, fade_in_ms=2, fade_out_ms=10))
        samples.extend(_silence(0.04))
    return samples


###############################################################################
# 4. Glass Chime -- high crystalline tones with slow shimmer
###############################################################################
def generate_glass_chime() -> list[int]:
    """Two high pure tones with beating interference -- glass wind chime."""
    dur_s = 2.0
    n = int(dur_s * SR)
    samples: list[int] = []
    # Two close frequencies create audible beating
    f1, f2 = 1318.5, 1336.0  # ~17 Hz beat frequency
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 1.8) * 0.4
        val = _sine(f1, t) + _sine(f2, t)
        # Add a lower octave for body
        val += 0.3 * math.exp(-t * 3.0) * _sine(659.25, t)
        samples.append(int(val * env * 32767))
    return _fade(samples, fade_in_ms=5, fade_out_ms=60)


###############################################################################
# 5. Synth Stab -- punchy electronic chord hit
###############################################################################
def generate_synth_stab() -> list[int]:
    """Short aggressive synth chord -- immediate and punchy."""
    samples: list[int] = []
    # Minor chord for edge: C4, Eb4, G4
    chord = [261.63, 311.13, 392.00]
    dur_s = 0.6
    n = int(dur_s * SR)
    for i in range(n):
        t = i / SR
        # Fast attack, medium decay
        env = min(1.0, t / 0.005) * math.exp(-t * 5.0) * 0.3
        val = 0.0
        for freq in chord:
            val += _square(freq, t, 0.4) + 0.5 * _sine(freq, t)
        val /= len(chord)
        samples.append(int(val * env * 32767))
    return _fade(samples, fade_in_ms=2, fade_out_ms=20)


###############################################################################
# 6. Kalimba -- thumb piano pluck with gentle overtones
###############################################################################
def generate_kalimba() -> list[int]:
    """Two-note kalimba pattern -- woody, warm, organic."""
    samples: list[int] = []
    notes = [(523.25, 0.5), (659.25, 0.7)]  # C5, E5
    for freq, dur_s in notes:
        n = int(dur_s * SR)
        note: list[int] = []
        for i in range(n):
            t = i / SR
            # Kalimba has a bright attack that quickly mellows
            attack_env = min(1.0, t / 0.002)
            body_env = math.exp(-t * 4.0)
            bright_env = math.exp(-t * 12.0)
            val = _sine(freq, t) * body_env
            val += 0.6 * _sine(freq * 3, t) * bright_env  # bright harmonic fades fast
            val += 0.2 * _sine(freq * 5, t) * bright_env
            note.append(int(val * attack_env * 0.45 * 32767))
        samples.extend(_fade(note, fade_in_ms=1, fade_out_ms=30))
        samples.extend(_silence(0.06))
    return samples


###############################################################################
# 7. Orbit -- rising frequency sweep with wobble
###############################################################################
def generate_orbit() -> list[int]:
    """Smooth rising sweep with LFO wobble -- sci-fi ping."""
    dur_s = 1.8
    n = int(dur_s * SR)
    samples: list[int] = []
    for i in range(n):
        t = i / SR
        frac = t / dur_s
        freq = 300 + 900 * frac * frac  # accelerating rise
        wobble = 1.0 + 0.15 * _sine(6.0, t)  # 6 Hz LFO
        env = math.sin(math.pi * frac) * 0.4  # fade in and out
        val = _sine(freq * wobble, t)
        val += 0.3 * _sine(freq * 2 * wobble, t)
        samples.append(int(val * env * 32767))
    return _fade(samples, fade_in_ms=10, fade_out_ms=40)


###############################################################################
# Main
###############################################################################

GENERATORS = {
    "bright_cascade": generate_bright_cascade,
    "warm_bell": generate_warm_bell,
    "pulse_bounce": generate_pulse_bounce,
    "glass_chime": generate_glass_chime,
    "synth_stab": generate_synth_stab,
    "kalimba": generate_kalimba,
    "orbit": generate_orbit,
}

DISPLAY_NAMES = {
    "bright_cascade": "Bright Cascade",
    "warm_bell": "Warm Bell",
    "pulse_bounce": "Pulse Bounce",
    "glass_chime": "Glass Chime",
    "synth_stab": "Synth Stab",
    "kalimba": "Kalimba",
    "orbit": "Orbit",
}


def main() -> None:
    import json

    print("Generating default theme sounds:")
    THEME_DIR.mkdir(parents=True, exist_ok=True)

    for name, gen_fn in GENERATORS.items():
        samples = gen_fn()
        _write_wav(name, samples)

    # Update theme.json
    theme_json = THEME_DIR / "theme.json"
    config = {
        "schema_version": 1,
        "name": "Default",
        "description": "Synthesized notification sounds -- copyright-free, ships with session-sounds",
        "author": "session-sounds",
        "sounds": DISPLAY_NAMES,
    }
    theme_json.write_text(json.dumps(config, indent=4))
    print(f"\n  Updated {theme_json}")
    print(f"  {len(GENERATORS)} sounds generated.")


if __name__ == "__main__":
    main()
