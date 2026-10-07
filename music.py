"""Deteccion de ritmo de lo que suena en el PC (WASAPI loopback via ctypes, sin dependencias).

Captura el audio que sale por tus altavoces/auriculares (Spotify, YouTube, juegos...), extrae un
"onset" de graves a 200 Hz, estima el tempo por autocorrelacion y alinea una rejilla de beats
buscando el peso conjunto de tempo+fase. Todo en Python puro, en un hilo aparte.
"""
import ctypes
import math
import subprocess
import threading
import time
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_uint32, c_void_p, c_ubyte, c_float, c_short

ole32 = ctypes.windll.ole32

HOP_S = 0.005  # 200 onsets por segundo
LAG_MIN, LAG_MAX = 66, 200  # 180 .. 60 BPM
HISTORY_S = 10.0


class GUID(ctypes.Structure):
    _fields_ = [("bytes", c_ubyte * 16)]


def guid(s):
    g = GUID()
    ctypes.memmove(byref(g), uuid.UUID(s).bytes_le, 16)
    return g


CLSID_ENUM = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_ENUM = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_CLIENT = guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_CAPTURE = guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


def method(obj, idx, *argtypes):
    """Metodo idx de la vtable de un objeto COM (obj = direccion entera)."""
    vt = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn = ctypes.cast(vt, POINTER(c_void_p))[idx]
    proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)
    f = proto(fn)
    return lambda *a: f(obj, *a)


def release(obj):
    if obj:
        method(obj, 2)()


# --------------------------------------------------------------- analisis (puro)
class BeatTracker:
    """Recibe onsets (200 Hz) con su marca de tiempo y mantiene tempo + rejilla de beats."""

    def __init__(self):
        self.t0 = None  # tiempo (perf_counter) del onset 0
        self.on = []  # fuerza de onset por hop
        self.bpm = None
        self.iv = None  # intervalo de beat en segundos
        self.ref = None  # un beat de la rejilla (perf_counter)
        self.score = 0.0
        self.locked = False
        self._last_bpm, self._stable_since = None, None

    def push(self, onsets, t_first):
        if self.t0 is None:
            self.t0 = t_first
        self.on.extend(onsets)
        keep = int(HISTORY_S / HOP_S)
        if len(self.on) > keep:
            drop = len(self.on) - keep
            del self.on[:drop]
            self.t0 += drop * HOP_S

    # -- tempo (costoso: llamar solo fuera de las partidas)
    def update_tempo(self):
        n = int(6.0 / HOP_S)
        o = self.on[-n:]
        if len(o) < n // 2:
            return
        m = sum(o) / len(o)
        o = [x - m for x in o]
        N = len(o)
        r = {}
        for lag in range(LAG_MIN, LAG_MAX + 1):
            r[lag] = sum(a * b for a, b in zip(o, o[lag:])) / (N - lag)
        best, best_s = None, -1e18
        for lag in range(LAG_MIN, LAG_MAX + 1):
            s = r[lag] + (0.5 * r[2 * lag] if 2 * lag <= LAG_MAX else 0.0)
            bpm = 60.0 / (lag * HOP_S)
            s *= math.exp(-0.5 * (math.log2(bpm / 115.0) / 0.9) ** 2)  # preferencia suave por tempos tipicos
            if s > best_s:
                best, best_s = lag, s
        if best is None or r[best] <= 0:
            self._unlock()
            return
        # interpolacion parabolica del pico
        a, b, c = r.get(best - 1, r[best]), r[best], r.get(best + 1, r[best])
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if den < 0 else 0.0
        lag = best + max(-1.0, min(1.0, off))
        bpm = 60.0 / (lag * HOP_S)
        if self._last_bpm and abs(bpm - self._last_bpm) / self._last_bpm < 0.03:
            self._stable_since = self._stable_since or time.perf_counter()
        else:
            self._stable_since = None
        self._last_bpm = bpm
        self.bpm, self.iv = bpm, 60.0 / bpm
        self.locked = bool(self._stable_since and time.perf_counter() - self._stable_since >= 2.0)

    def _unlock(self):
        self.locked, self._stable_since = False, None

    # -- fase (barata: se puede llamar siempre)
    def _comb(self, iv, phase, span_s=8.0):
        n = len(self.on)
        if n < 50:
            return 0.0
        beats = int(min(span_s, n * HOP_S) / iv)
        tot = 0.0
        end = n - 1
        for k in range(beats):
            idx = end - int((phase + k * iv) / HOP_S)
            if 1 <= idx < n - 1:
                tot += max(self.on[idx - 1], self.on[idx], self.on[idx + 1])
        return tot / max(1, beats)

    def update_phase(self):
        if not self.iv or len(self.on) < 400:
            return
        best = (-1.0, self.iv, 0.0)
        base = self.iv
        for d in (-0.015, -0.01, -0.005, 0.0, 0.005, 0.01, 0.015):
            iv = base * (1 + d)
            for p in range(int(iv / HOP_S)):
                s = self._comb(iv, p * HOP_S)
                if s > best[0]:
                    best = (s, iv, p * HOP_S)
        s, iv, ph = best
        mean_on = sum(self.on) / len(self.on) or 1e-9
        new_ref = self.t0 + (len(self.on) - 1) * HOP_S - ph  # un beat reciente en tiempo perf_counter
        if self.ref is None:
            self.ref, self.iv, self.score = new_ref, iv, s / mean_on
        else:
            cur = self._comb(self.iv, (self.t0 + (len(self.on) - 1) * HOP_S - self.ref) % self.iv)
            if s > cur * 1.12:
                self.ref, self.iv = new_ref, iv
            self.score = max(cur, s) / mean_on
        self.bpm = 60.0 / self.iv

    def next_beat(self, after):
        if self.ref is None or not self.iv:
            return None
        k = math.ceil((after - self.ref) / self.iv)
        return self.ref + k * self.iv


# --------------------------------------------------------------- captura WASAPI
class MusicListener(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.tracker = BeatTracker()
        self.level = 0.0
        self.error = None
        self.sr = 48000
        self.running = True
        self.heavy_ok = True  # el tempo (costoso) solo se recalcula cuando esto es True
        self.active = True  # False: solo vacia el buffer, sin analizar (durante partidas sin ritmo)
        self.hot = 0.0  # ultimo instante con sonido
        self.title = ""

    # -- API de consulta
    @property
    def bpm(self):
        return self.tracker.bpm

    @property
    def listening(self):
        return time.perf_counter() - self.hot < 2.0

    @property
    def ready(self):
        t = self.tracker
        return bool(self.listening and t.locked and t.ref is not None and t.score > 1.3)

    def next_beat(self, after):
        return self.tracker.next_beat(after)

    # -- hilo
    def run(self):
        try:
            self._capture()
        except Exception as e:  # sin audio / sin dispositivo
            self.error = str(e)

    def _capture(self):
        ole32.CoInitializeEx(None, 0)
        dev = c_void_p()
        enum = c_void_p()
        if ole32.CoCreateInstance(byref(CLSID_ENUM), None, 23, byref(IID_ENUM), byref(enum)) != 0:
            raise RuntimeError("no se pudo abrir WASAPI")
        method(enum.value, 4, c_int, c_int, POINTER(c_void_p))(0, 0, byref(dev))  # eRender, eConsole
        client = c_void_p()
        method(dev.value, 3, POINTER(GUID), c_uint32, c_void_p, POINTER(c_void_p))(byref(IID_CLIENT), 23, None, byref(client))
        fmt = c_void_p()
        method(client.value, 8, POINTER(c_void_p))(byref(fmt))
        raw = ctypes.string_at(fmt.value, 40)
        tag = int.from_bytes(raw[0:2], "little")
        ch = int.from_bytes(raw[2:4], "little")
        self.sr = int.from_bytes(raw[4:8], "little")
        bits = int.from_bytes(raw[14:16], "little")
        is_float = tag == 3 or (tag == 0xFFFE and int.from_bytes(raw[24:28], "little") == 3)
        if not (is_float and bits == 32) and bits != 16:
            raise RuntimeError("formato de audio no soportado (%d bits)" % bits)
        # compartido + loopback
        hr = method(client.value, 3, c_int, c_uint32, ctypes.c_longlong, ctypes.c_longlong, c_void_p, c_void_p)(
            0, 0x00020000, 10000000, 0, fmt.value, None)
        if hr != 0:
            raise RuntimeError("Initialize fallo (0x%08X)" % (hr & 0xFFFFFFFF))
        cap = c_void_p()
        method(client.value, 14, POINTER(GUID), POINTER(c_void_p))(byref(IID_CAPTURE), byref(cap))
        get_size = method(cap.value, 5, POINTER(c_uint32))
        get_buf = method(cap.value, 3, POINTER(c_void_p), POINTER(c_uint32), POINTER(c_uint32), c_void_p, c_void_p)
        rel_buf = method(cap.value, 4, c_uint32)
        method(client.value, 10)()  # Start
        hop = int(self.sr * HOP_S)
        grp = 48
        pending = []
        prev_env = 0.0
        total_hops = 0
        offset = None
        while self.running:
            size = c_uint32(0)
            get_size(byref(size))
            if size.value == 0:
                time.sleep(0.004)
                continue
            while size.value:
                data, frames, flags = c_void_p(), c_uint32(), c_uint32()
                get_buf(byref(data), byref(frames), byref(flags), None, None)
                n = frames.value
                if flags.value & 2 or not data.value:  # silencio
                    pending.extend([0.0] * n)
                else:
                    if is_float:
                        arr = (c_float * (n * ch)).from_address(data.value)
                    else:
                        arr = (c_short * (n * ch)).from_address(data.value)
                    scale = 1.0 if is_float else 1 / 32768
                    pending.extend([x * scale for x in arr[0::ch]])
                rel_buf(n)
                get_size(byref(size))
            if not self.active:
                pending.clear()
                continue
            now = time.perf_counter()
            onsets = []
            while len(pending) >= hop:
                seg = pending[:hop]
                del pending[:hop]
                low = sum(sum(seg[j:j + grp]) ** 2 for j in range(0, hop - grp + 1, grp)) / (grp * grp)
                full = sum(x * x for x in seg[::6]) / (hop / 6)
                self.level = max(self.level * 0.97, min(1.0, math.sqrt(full) * 3))
                if full > 1e-7:
                    self.hot = now
                env = math.log1p(300.0 * low)
                onsets.append(max(0.0, env - prev_env))
                prev_env = env
                total_hops += 1
            if onsets:
                # instante (perf_counter) del primer onset de este lote, segun el reloj de audio
                t_last = now
                t_first = t_last - (len(onsets) - 1) * HOP_S - len(pending) / self.sr
                if offset is None:
                    offset = 0.0
                self.tracker.push(onsets, t_first)
                if time.perf_counter() - getattr(self, "_t_ph", 0) > 0.5:
                    self._t_ph = time.perf_counter()
                    if self.heavy_ok and time.perf_counter() - getattr(self, "_t_tp", 0) > 1.0:
                        self._t_tp = time.perf_counter()
                        if self.listening:
                            self.tracker.update_tempo()
                    if self.listening:
                        self.tracker.update_phase()
        method(client.value, 11)()


# ------------------------------------------------------------------- now playing
PS_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | ? { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $m = $asTask.MakeGenericMethod($t); $n = $m.Invoke($null, @($op)); $n.Wait(-1) | Out-Null; $n.Result }
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$s = $mgr.GetCurrentSession()
if ($s) {
  $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
  $st = $s.GetPlaybackInfo().PlaybackStatus
  "$($p.Artist) - $($p.Title) [$st]"
}
"""


def now_playing():
    """Titulo de lo que suena (Spotify, navegador...), o '' si no se puede saber."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT],
                             capture_output=True, text=True, timeout=15,
                             creationflags=0x08000000)  # sin ventana
        return out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    except Exception:
        return ""
