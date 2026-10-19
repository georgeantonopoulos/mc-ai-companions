"""The Pixel Pops' jingles: backing track + robot-pop vocals, written as Minecraft sounds into Claude_RP.

  python3 songs/make.py   (needs numpy, scipy, soundfile, and macOS `say` for the voices)

Each word is spoken by a macOS voice (one voice per singer), placed on its note, then a channel vocoder puts it
exactly on the melody's pitch: the words stay clear and the singing is in tune (the auto-tuned pop sound).
Chorus lines are sung by all three in harmony.
"""
import json
import os
import subprocess

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt, resample_poly

HERE = os.path.dirname(os.path.abspath(__file__))
RP = os.path.join(os.path.dirname(HERE), "Claude_RP")
TMP = os.path.join(HERE, "tmp")
SR = 44100
BPM = 124
BEAT = 60 / BPM

VOICES = {"Rosie": "Samantha", "Zara": "Karen", "Ivy": "Moira"}
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name):
    """'E4' -> Hz."""
    n, octave = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((NOTE[n] + 12 * (octave + 1) - 69) / 12)


# --- songs: (who, [(word, note, beats), ...]); who "all" = the three in harmony; chords one per bar ---------------
SONGS = {
    "block_by_block": {
        "title": "Block by Block",
        "chords": ["C", "G", "A-", "F", "C", "G", "F", "G"],
        "lines": [
            ("Rosie", [("Block", "E4", 1), ("by", "E4", 0.5), ("block", "G4", 1.5), ("we", "A4", 0.5), ("build", "G4", 0.5)]),
            ("Rosie", [("it", "E4", 1), ("high", "D4", 3)]),
            ("Zara", [("Glass", "E4", 1), ("and", "E4", 0.5), ("wood", "G4", 1.5), ("up", "A4", 0.5), ("to", "A4", 0.5)]),
            ("Zara", [("the", "B4", 1), ("sky", "C5", 3)]),
            ("Ivy", [("Gas", "G4", 1), ("ton", "A4", 1), ("says", "G4", 1), ("vite", "E4", 1)]),
            ("Ivy", [("we", "D4", 1), ("go", "E4", 1), ("go", "G4", 2)]),
            ("all", [("Pix", "C5", 1), ("el", "C5", 0.5), ("Pops", "A4", 1.5), ("hey", "G4", 1)]),
            ("all", [("hey", "C5", 4)]),
        ],
    },
    "dragon_dash": {
        "title": "Dragon Dash",
        "chords": ["A-", "F", "C", "G", "A-", "F", "G", "G"],
        "lines": [
            ("Zara", [("Run", "A4", 1), ("run", "A4", 1), ("run", "C5", 1), ("to", "B4", 0.5), ("the", "A4", 0.5)]),
            ("Zara", [("dra", "G4", 1), ("gon's", "A4", 1), ("lair", "E4", 2)]),
            ("Ivy", [("En", "A4", 1), ("der", "A4", 1), ("eyes", "C5", 1), ("are", "B4", 0.5), ("in", "A4", 0.5)]),
            ("Ivy", [("the", "G4", 1), ("air", "A4", 3)]),
            ("Rosie", [("Dash", "E5", 1), ("says", "D5", 1), ("go", "C5", 1), ("go", "B4", 1)]),
            ("Rosie", [("we", "A4", 1), ("are", "B4", 1), ("there", "C5", 2)]),
            ("all", [("Pix", "E5", 1), ("el", "E5", 0.5), ("Pops", "D5", 1.5), ("go", "B4", 1)]),
            ("all", [("go", "A4", 4)]),
        ],
    },
    "baby_boo": {
        "title": "Baby Boo",
        "chords": ["F", "C", "G", "C", "F", "C", "G", "C"],
        "lines": [
            ("Ivy", [("Goo", "C5", 1), ("goo", "C5", 1), ("ga", "A4", 1), ("ga", "A4", 1)]),
            ("Ivy", [("Ba", "G4", 1), ("by", "G4", 1), ("Boo", "C5", 2)]),
            ("Rosie", [("Took", "C5", 1), ("the", "C5", 0.5), ("pick", "A4", 1.5), ("axe", "A4", 1)]),
            ("Rosie", [("what", "G4", 1), ("to", "A4", 1), ("do", "G4", 2)]),
            ("Zara", [("Catch", "E4", 1), ("him", "F4", 1), ("quick", "G4", 1), ("and", "A4", 1)]),
            ("Zara", [("peek", "C5", 1), ("a", "D5", 1), ("boo", "C5", 2)]),
            ("all", [("Pix", "C5", 1), ("el", "C5", 0.5), ("Pops", "A4", 1.5), ("hey", "G4", 1)]),
            ("all", [("hey", "C5", 4)]),
        ],
    },
}
HARMONY = {"Rosie": 0, "Zara": -3, "Ivy": -5}  # chorus: melody, a third below, a fifth below (in semitones, near enough)


# --- vocals ------------------------------------------------------------------------------------------------------

def speak(word, voice):
    """One word, spoken, as float audio at SR, trimmed of silence."""
    path = os.path.join(TMP, "%s_%s.aiff" % (voice, "".join(c for c in word if c.isalnum())))
    if not os.path.exists(path):
        subprocess.run(["say", "-v", voice, "-r", "175", "-o", path, word], check=True)
    d, sr = sf.read(path)
    if d.ndim > 1:
        d = d.mean(axis=1)
    d = resample_poly(d, SR, sr)
    loud = np.where(np.abs(d) > 0.02)[0]
    return d[loud[0]:loud[-1] + 1] if len(loud) else d


def fit(x, n):
    """Squeeze or stretch a word into n samples (by resampling: it shifts the timbre a little, the vocoder hides it)."""
    if len(x) == 0:
        return np.zeros(n)
    y = resample_poly(x, n, len(x)) if abs(len(x) - n) > 8 else x
    y = y[:n] if len(y) >= n else np.pad(y, (0, n - len(y)))
    fade = min(len(y) // 6, int(0.02 * SR))
    if fade:
        y[:fade] *= np.linspace(0, 1, fade)
        y[-fade:] *= np.linspace(1, 0, fade)
    return y


BANDS = np.geomspace(120, 7500, 28)
SOS = [butter(2, [lo, hi], btype="band", fs=SR, output="sos") for lo, hi in zip(BANDS[:-1], BANDS[1:])]
ENV = butter(2, 40, fs=SR, output="sos")


def vocode(speech, f0):
    """A channel vocoder: the speech's spectral shape on a bright buzz at the melody's pitch (f0, per sample)."""
    phase = np.cumsum(f0 / SR)
    carrier = 2 * (phase % 1) - 1  # sawtooth
    carrier += 0.5 * (2 * ((phase * 1.005) % 1) - 1)  # a slightly detuned second saw: thicker, more "pop"
    noise = np.random.default_rng(1).standard_normal(len(speech)) * 0.3  # for s, t, k sounds
    out = np.zeros(len(speech))
    for i, sos in enumerate(SOS):
        env = sosfilt(ENV, np.abs(sosfilt(sos, speech)))
        src = carrier if BANDS[i] < 4000 else 0.4 * carrier + noise
        out += sosfilt(sos, src) * env
    out += 0.05 * speech  # a touch of the real voice keeps the words clear (more, and it sings off-key)
    return out / (np.abs(out).max() + 1e-9)


def vocal_line(notes, singer, start, total, shift=0):
    """A sung line placed at `start` samples into a buffer of `total`."""
    buf = np.zeros(total)
    f0 = np.zeros(total)
    voice = VOICES[singer]
    t = start
    for word, note, beats in notes:
        n = int(beats * BEAT * SR)
        sung = int(n * 0.9)
        w = fit(speak(word, voice), sung)
        if t + n > total:
            break
        buf[t:t + sung] = w
        f = hz(note) * 2 ** (shift / 12)
        glide = min(int(0.03 * SR), sung)
        f0[t:t + sung] = f
        if t > 0 and f0[t - 1] > 0:  # a little slide into each note, like a singer
            f0[t:t + glide] = np.linspace(f0[t - 1], f, glide)
        # vibrato on long notes
        if beats >= 2:
            k = np.arange(sung)
            f0[t:t + sung] *= 1 + 0.012 * np.sin(2 * np.pi * 5.5 * k / SR) * np.clip(k / SR - 0.25, 0, 1)
        t += n
    f0[f0 == 0] = 220
    v = vocode(buf, f0)
    return v * envelope_of(buf)


def envelope_of(x):
    e = sosfilt(butter(2, 20, fs=SR, output="sos"), np.abs(x))
    return np.clip(e / (e.max() + 1e-9) * 3, 0, 1)


# --- backing -----------------------------------------------------------------------------------------------------

CHORD = {"C": ["C3", "E3", "G3"], "G": ["G2", "B2", "D3"], "A-": ["A2", "C3", "E3"], "F": ["F2", "A2", "C3"]}


def tone(f, n, kind="saw"):
    t = np.arange(n) / SR
    if kind == "saw":
        return sum((2 * ((t * f * d) % 1) - 1) for d in (1, 1.004, 0.996)) / 3
    if kind == "sine":
        return np.sin(2 * np.pi * f * t)
    if kind == "square":
        return np.sign(np.sin(2 * np.pi * f * t))


def adsr(n, a=0.01, d=0.1, s=0.7, r=0.1):
    e = np.ones(n) * s
    A, D, R = int(a * SR), int(d * SR), int(r * SR)
    e[:A] = np.linspace(0, 1, A)
    e[A:A + D] = np.linspace(1, s, len(e[A:A + D]))
    if R and R < n:
        e[-R:] *= np.linspace(1, 0, R)
    return e


def backing(chords, total):
    out = np.zeros(total)
    bar = int(4 * BEAT * SR)
    rng = np.random.default_rng(7)
    lp = butter(2, 2200, fs=SR, output="sos")
    for b, ch in enumerate(chords):
        s = b * bar
        if s >= total:
            break
        n = min(bar, total - s)
        # pad: soft supersaw chord
        pad = sum(tone(hz(x) * 2, n) for x in CHORD[ch]) / 3
        out[s:s + n] += 0.16 * sosfilt(lp, pad) * adsr(n, 0.05, 0.3, 0.8, 0.1)
        # bass: root, eighth notes
        root = hz(CHORD[ch][0]) / 2
        e8 = int(BEAT * SR / 2)
        for k in range(8):
            o = s + k * e8
            m = min(e8, total - o)
            if m > 0:
                out[o:o + m] += 0.22 * np.tanh(2 * tone(root, m, "saw")) * adsr(m, 0.005, 0.08, 0.5, 0.03)
        # drums: four-on-the-floor kick, snare on 2 and 4, hats on the offbeats
        for k in range(4):
            o = s + int(k * BEAT * SR)
            m = min(int(0.25 * SR), total - o)
            if m <= 0:
                continue
            tt = np.arange(m) / SR
            out[o:o + m] += 0.6 * np.sin(2 * np.pi * (50 + 120 * np.exp(-tt * 40)) * tt) * np.exp(-tt * 9)
            if k in (1, 3):
                out[o:o + m] += 0.25 * rng.standard_normal(m) * np.exp(-tt * 18)
            h = o + int(BEAT * SR / 2)
            hm = min(int(0.05 * SR), total - h)
            if hm > 0:
                hh = rng.standard_normal(hm) * np.exp(-np.arange(hm) / SR * 80)
                out[h:h + hm] += 0.08 * sosfilt(butter(2, 7000, "high", fs=SR, output="sos"), hh)
    return out


def song(key):
    sp = SONGS[key]
    bar = int(4 * BEAT * SR)
    total = bar * (len(sp["chords"]) + 1)
    mix = backing(sp["chords"], total)
    vox = np.zeros(total)
    for i, (who, notes) in enumerate(sp["lines"]):
        start = i * bar
        singers = list(VOICES) if who == "all" else [who]
        for s in singers:
            vox += vocal_line(notes, s, start, total, HARMONY[s] if who == "all" else 0) * (0.55 if who == "all" else 0.8)
    mix += 0.9 * vox
    # end on a final kick and a little fade
    mix[-int(0.5 * SR):] *= np.linspace(1, 0, int(0.5 * SR))
    mix = np.tanh(1.3 * mix / (np.abs(mix).max() + 1e-9)) * 0.9
    return mix


if __name__ == "__main__":
    os.makedirs(TMP, exist_ok=True)
    out_dir = os.path.join(RP, "sounds", "pixelpops")
    os.makedirs(out_dir, exist_ok=True)
    defs = {}
    info = {}
    for key in SONGS:
        audio = song(key)
        sf.write(os.path.join(TMP, key + ".wav"), audio, SR)  # to listen to on the Mac
        # mono Ogg Vorbis: what Minecraft plays, and mono so it comes from where the band stands
        sf.write(os.path.join(out_dir, key + ".ogg"), audio, SR, format="OGG", subtype="VORBIS")
        defs["pixelpops." + key] = {"category": "record", "min_distance": 4.0, "max_distance": 48.0,
                                    "sounds": [{"name": "sounds/pixelpops/" + key, "stream": True, "volume": 1.0}]}
        info[key] = {"title": SONGS[key]["title"], "secs": round(len(audio) / SR, 1)}
        print(key, info[key])
    json.dump({"format_version": "1.20.20", "sound_definitions": defs},
              open(os.path.join(RP, "sounds", "sound_definitions.json"), "w"), indent=1)
    json.dump(info, open(os.path.join(HERE, "songs.json"), "w"), indent=1)
