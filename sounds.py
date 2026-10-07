"""Sonidos del juego: genera WAVs sencillos y los reproduce sin bloquear (winsound)."""
import math
import os
import struct
import wave

try:
    import winsound
except Exception:  # sin soporte de audio
    winsound = None

import sys

if getattr(sys, "frozen", False):  # exe: carpeta persistente del usuario
    DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.dirname(sys.executable)), "SensFinder", "sounds")
else:
    DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
SR = 44100
enabled = True

# nombre -> lista de (frecuencia Hz, ms, volumen)
SPECS = {
    "hit": [(1180, 55, 0.40), (1770, 45, 0.30)],
    "miss": [(170, 90, 0.35)],
    "beat": [(2600, 14, 0.18)],
    "done": [(660, 80, 0.35), (880, 80, 0.35), (1100, 140, 0.35)],
}


def _write(path, parts):
    frames = bytearray()
    for freq, ms, vol in parts:
        n = int(SR * ms / 1000)
        for i in range(n):
            env = math.exp(-5.0 * i / n) * min(1.0, i / 60)  # ataque corto, caida suave
            frames += struct.pack("<h", int(32767 * vol * env * math.sin(2 * math.pi * freq * i / SR)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))


def init():
    os.makedirs(DIR, exist_ok=True)
    for name, parts in SPECS.items():
        path = os.path.join(DIR, name + ".wav")
        if not os.path.exists(path):
            _write(path, parts)


def play(name):
    if enabled and winsound:
        try:
            winsound.PlaySound(os.path.join(DIR, name + ".wav"),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass
