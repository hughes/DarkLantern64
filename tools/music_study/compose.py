"""Generate an original, editable XM study. Requires numpy; no downloaded audio.

The score JSON is authoritative and can be edited without changing this script.
Sample design is deterministic for the recorded Python/numpy environment.
"""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "art/music/lanterns_below"
RATE = 16726  # XM C-4 rate with relative-note +12.


def note(text):
    names = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5,
             "F#": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}
    return 1 + 12 * int(text[-1]) + names[text[:-1]]


def initial_score():
    events = []
    def put(row, channel, pitch, instrument, volume):
        events.append(dict(row=row, channel=channel, note=pitch,
                           instrument=instrument, volume=volume))
    # Six-second harmonic cells. The last cell leaves an audible breath at wrap.
    roots = ["D2", "D2", "Bb1", "C2", "D2", "F2", "G1", "A1"]
    fifths = ["A3", "A3", "F3", "G3", "A3", "C4", "D4", "E3"]
    tops = ["F4", "F4", "D4", "E4", "F4", "A4", "Bb3", "C#4"]
    for cell, (root, fifth, top) in enumerate(zip(roots, fifths, tops)):
        row = cell * 32
        put(row, 0, root, 1, 50 if cell < 6 else 44)
        put(row, 1, fifth, 2, 33)
        put(row, 2, top, 2, 23)
        if cell < 7:
            put(row + 8, 7, "C4", 7, 16)
        put(row + 2, 6, "C3", 5, 40)
        if cell < 7:
            put(row + 18, 6, "G3", 5, 24)
            put(row + 24, 7, "C5", 6, 21)
    phrases = [
        [(4, "D4"), (10, "F4"), (18, "A4"), (23, "F4")],
        [(4, "D4"), (12, "A4"), (20, "F4"), (23, "D4")],
        [(4, "Bb3"), (10, "D4"), (18, "F4"), (23, "D4")],
        [(4, "C4"), (10, "E4"), (18, "G4"), (23, "E4")],
        [(4, "D4"), (10, "F4"), (18, "A4"), (23, "D5")],
        [(4, "F4"), (10, "A4"), (18, "C5"), (23, "A4")],
        [(4, "G4"), (10, "Bb4"), (18, "D5"), (23, "Bb4")],
        [(3, "A4"), (10, "E4"), (17, "C#4"), (23, "E4")],
    ]
    for cell, phrase in enumerate(phrases):
        for j, (offset, pitch) in enumerate(phrase):
            row = cell * 32 + offset
            put(row, 3, pitch, 3, [44, 33, 38, 28][j])
            if row + 3 < 247:
                put(row + 3, 4, pitch, 3, 12)  # An authored echo costs a voice.
    for row, pitch in [(0, "D5"), (64, "Bb4"), (128, "D5"), (168, "F5"), (224, "A4")]:
        put(row, 5, pitch, 4, 27)
    return dict(title="Lanterns Below", author="Original DarkLantern64 study", revision=2,
                harmony=["Dm", "Dm", "Bb", "C", "Dm", "F", "Gm", "A"],
                bpm=80, ticks_per_row=6, rows=256, channels=8,
                channel_pans=[112, 62, 190, 94, 176, 152, 120, 170],
                events=sorted(events, key=lambda e: (e["row"], e["channel"])))


def palette():
    rng = np.random.default_rng(640809)
    n = 16384
    phase = np.arange(n) * (2 * np.pi / 64)
    cycle = np.arange(n) * (2 * np.pi / n)
    viol = sum(np.sin(k * phase + .06 * np.sin(cycle)) / k**1.7 for k in range(1, 13))
    viol *= .86 + .10 * np.sin(3 * cycle) + .04 * np.cos(5 * cycle)
    upper = sum(np.sin(k * phase + .04 * np.sin(2 * cycle)) / k**1.35 for k in range(1, 10))
    upper *= .9 + .1 * np.sin(5 * cycle)
    def time(seconds):
        return np.arange(int(RATE * seconds)) / RATE
    t = time(1.5)
    pluck = sum(a * np.sin(2 * np.pi * 261.625565 * k * t) * np.exp(-t / decay)
                for k, a, decay in [(1, 1, .42), (2, .28, .22), (3, .13, .12), (5, .07, .07)])
    pluck *= 1 - np.exp(-t / .006)
    t = time(3.2)
    bell = sum(a * np.sin(2 * np.pi * 261.625565 * ratio * t) * np.exp(-t / decay)
               for ratio, a, decay in [(1, 1, .95), (2.01, .3, .75), (2.76, .19, .45), (4.07, .1, .3)])
    bell *= 1 - np.exp(-t / .004)
    t = time(.7)
    drum = np.sin(2 * np.pi * (130 * t + 11 * (1 - np.exp(-t / .025)))) * np.exp(-t / .10)
    drum += .16 * rng.normal(size=t.size) * np.exp(-t / .022)
    drum *= 1 - np.exp(-t / .002)
    t = time(.45)
    metal = rng.normal(size=t.size)
    metal = np.convolve(metal, np.ones(5) / 5, mode="same") * np.exp(-t / .085)
    metal *= 1 - np.exp(-t / .013)
    # Periodic band-limited noise avoids a discontinuous loop boundary.
    spectrum = np.zeros(n // 2 + 1, dtype=complex)
    bins = np.arange(90, 900)
    spectrum[bins] = np.exp(1j * rng.uniform(0, 2 * np.pi, bins.size)) / np.sqrt(bins)
    breath = np.fft.irfft(spectrum, n=n) * (.8 + .2 * np.sin(cycle))
    return [
        ("01_low_viol", viol, True, 2, [(0, 0), (12, 50), (96, 45), (152, 36), (191, 0)]),
        ("02_veiled_strings", upper, True, 2, [(0, 0), (20, 48), (88, 42), (142, 30), (191, 0)]),
        ("03_felt_wire", pluck, False, 0, [(0, 64), (12, 32), (24, 0)]),
        ("04_watch_bell", bell, False, 0, []),
        ("05_frame_drum", drum, False, 0, []),
        ("06_brushed_iron", metal, False, 0, []),
        ("07_cold_air", breath, True, 0, [(0, 0), (12, 44), (40, 26), (88, 0)]),
    ]


def pcm(values, loop):
    values = values.copy()
    if not loop:
        values[-256:] *= np.linspace(1, 0, 256)
    return np.rint(values / max(abs(values)) * .78 * 32767).astype("<i2")


def make_xm(score, samples):
    rows, channels = score["rows"], score["channels"]
    assert rows % 64 == 0 and 0 < rows <= 8192 and 0 < channels <= 8
    orders = rows // 64
    header = (b"Extended Module: " + score["title"].encode("ascii")[:20].ljust(20, b" ")
              + b"\x1a" + b"DL64 Music Study".ljust(20, b" ") + struct.pack("<H", 0x104))
    header += struct.pack("<I8H", 276, orders, 0, channels, orders, len(samples), 1,
                          score["ticks_per_row"], score["bpm"])
    header += bytes(range(orders)).ljust(256, b"\0")
    data = np.zeros((rows, channels, 5), dtype=np.uint8)
    for ch, pan in enumerate(score["channel_pans"]):
        data[0, ch, 3:] = [8, pan]
    occupied = set()
    for e in score["events"]:
        key = (e["row"], e["channel"])
        assert key not in occupied, f"Two notes occupy {key}"
        occupied.add(key)
        assert 0 <= key[0] < rows and 0 <= key[1] < channels
        assert 1 <= e["instrument"] <= len(samples) and 0 <= e["volume"] <= 64
        data[key[0], key[1], :3] = [note(e["note"]), e["instrument"], 0x10 + e["volume"]]
        # Instrument key-on restores sample panning; specify it per note.
        data[key[0], key[1], 3:] = [8, score["channel_pans"][key[1]]]
    result = bytearray(header)
    for index in range(orders):
        pattern = data[index * 64:(index + 1) * 64].tobytes()
        result += struct.pack("<IBHH", 9, 0, 64, len(pattern)) + pattern
    for name, values, loop, fine, envelope in samples:
        raw = pcm(values, loop)
        ins = bytearray(263)
        struct.pack_into("<I", ins, 0, 263)
        ins[4:26] = name.encode()[:22].ljust(22, b" ")
        struct.pack_into("<HI", ins, 27, 1, 40)
        for i, point in enumerate(envelope):
            struct.pack_into("<HH", ins, 129 + i * 4, *point)
        ins[225], ins[233] = len(envelope), int(bool(envelope))
        struct.pack_into("<H", ins, 239, 256)
        sample_header = struct.pack("<IIIBbBBbB22s", raw.nbytes, 0, raw.nbytes if loop else 0,
                                    64, fine, 16 | int(loop), 128, 12, 0,
                                    name.encode()[:22].ljust(22, b" "))
        delta = np.diff(raw.astype(np.int32), prepend=0).astype("<i2")
        result += ins + sample_header + delta.tobytes()
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-score", action="store_true", help="Create score only if missing")
    args = parser.parse_args()
    SOURCE.mkdir(parents=True, exist_ok=True)
    score_path = SOURCE / "score.json"
    if args.seed_score and not score_path.exists():
        score_path.write_text(json.dumps(initial_score(), indent=2) + "\n")
    score = json.loads(score_path.read_text())
    samples = palette()
    sample_dir = SOURCE / "samples"
    sample_dir.mkdir(exist_ok=True)
    inventory = []
    for name, values, loop, fine, envelope in samples:
        path = sample_dir / (name + ".wav")
        raw = pcm(values, loop)
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, RATE, len(raw), "NONE", "not compressed"))
            out.writeframes(raw.tobytes())
        inventory.append(dict(name=name, frames=len(raw), rate=RATE, pcm_bytes=raw.nbytes,
                              loop=loop, xm_relative_note=12, xm_finetune=fine, envelope=envelope))
    xm = make_xm(score, samples)
    (SOURCE / "lanterns_below.xm").write_bytes(xm)
    seconds = score["rows"] * score["ticks_per_row"] * 2.5 / score["bpm"]
    manifest = dict(title=score["title"], status="original exploration asset; not shipped in game",
                    duration_seconds=seconds, channels=score["channels"], instruments=len(samples),
                    numpy_version=np.__version__, source_sha256=hashlib.sha256(xm).hexdigest(),
                    samples=inventory)
    (SOURCE / "source_report.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(dict(xm_bytes=len(xm), seconds=seconds, instruments=len(samples))))


if __name__ == "__main__":
    main()
