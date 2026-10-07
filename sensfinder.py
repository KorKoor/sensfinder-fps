"""SensFinder - encuentra tu sensibilidad de mouse ideal para FPS.

Aim-trainer 3D (flicks + tracking) con entrada raw de Windows. Un motor bayesiano
(engine.py) elige a ciegas que sensibilidades probar, corrige el aprendizaje/fatiga,
y se detiene cuando esta seguro. Puede sincronizar los blancos con la musica que suena
en el PC (music.py) y ajusta tambien la sensibilidad vertical.
"""
import ctypes
import ctypes.wintypes
import json
import math
import os
import random
import sys
import threading
import time
from datetime import datetime
import tkinter as tk

import engine as en
import gdi
import music as mus
import sounds
from rawmouse import RawMouse
from ui import C, Ui

if getattr(sys, "frozen", False):  # ejecutable: el historial va junto al .exe, no en la carpeta temporal
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "resultados.json")

FLICK_TARGETS = 15
VFLICK_TARGETS = 12
FLICK_CAP = 40.0
TRACK_TIME = 10.0
TRACK_WARMUP = 1.0
TARGET_RADIUS_DEG = 2.0
TRACK_RADIUS_DEG = 3.0
COUNTDOWN = 1.8
MIN_TTH = 0.12
GHOST_LEAD = 0.25  # en modo ritmo, el aviso aparece este tiempo antes del blanco
TRACK_PATHS = [[0.0, 1.0, 2.0, 3.0], [1.7, 4.0, 0.6, 5.1], [3.3, 2.2, 4.4, 1.1], [5.0, 0.4, 3.9, 2.6]]
V_RATIOS = [0.7, 0.85, 1.0, 1.2, 1.4]
FOCUS_KEYS = ["mix", "flick", "track"]
FRAME = 1 / 360  # tope de fps durante el juego

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass
user32 = ctypes.windll.user32

GRID_VEC = [(math.cos(math.radians(e)) * math.sin(math.radians(a)), math.sin(math.radians(e)),
             math.cos(math.radians(e)) * math.cos(math.radians(a)), a % 45 == 0 and e % 30 == 0)
            for a in range(-180, 180, 15) for e in range(-60, 61, 15)]

KIND_INFO = {
    "flick": ("FLICKS", "Dispara a cada blanco lo mas rapido y preciso que puedas.",
              "Se mide la velocidad y la precision. Un blanco lejano vale mas que uno cercano."),
    "track": ("TRACKING", "Manten la mira sobre el blanco que se mueve. No hace falta hacer click.",
              "Se mide el porcentaje de tiempo que tu mira esta dentro del blanco."),
    "vflick": ("AJUSTE VERTICAL", "Flicks con mas movimiento vertical. Dispara a cada blanco.",
               "Sirve para saber si te va mejor mover el mouse hacia arriba/abajo mas rapido o mas lento."),
}


def project(az, el, yaw, pitch, f, w, h):
    """Proyecta la direccion (az, el) a pantalla. (x, y, z) o None si esta detras."""
    x = math.cos(el) * math.sin(az)
    y = math.sin(el)
    z = math.cos(el) * math.cos(az)
    x1 = x * math.cos(yaw) - z * math.sin(yaw)
    z1 = x * math.sin(yaw) + z * math.cos(yaw)
    y2 = y * math.cos(pitch) - z1 * math.sin(pitch)
    z2 = y * math.sin(pitch) + z1 * math.cos(pitch)
    if z2 <= 0.05:
        return None
    return w / 2 + f * x1 / z2, h / 2 - f * y2 / z2, z2


class Field:
    def __init__(self, kind, value=0, idx=0, on=True):
        self.kind, self.text, self.idx, self.on = kind, str(value), idx, on

    def value(self):
        try:
            return float(self.text)
        except ValueError:
            return 0.0


class App:
    def __init__(self):
        sys.setswitchinterval(0.001)  # hilos de audio sin trabar el render
        self.root = tk.Tk()
        self.root.title("SensFinder")
        self.root.geometry("1280x720")
        self.root.minsize(960, 540)
        self.root.configure(bg=C["bg"])
        self.cv = tk.Canvas(self.root, bg=C["bg"], highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.ui = Ui(self.cv)
        self.raw = RawMouse(self.root)
        gdi.timer_resolution(True)
        self.view = gdi.GdiView(self.cv.winfo_id(), C["bg"])
        self.gdi_dirty = False
        self.next_frame = 0.0
        sounds.init()
        self.music = mus.MusicListener()
        self.music.start()
        self.np_text = ""
        self.music_offset = 0.0
        self.taps, self.beat_ref = [], time.perf_counter()
        self.F = {
            "dpi": Field("num", 800), "fov": Field("num", 103), "cur": Field("num", 0),
            "lo": Field("num", 15), "hi": Field("num", 80), "bpm": Field("num", 0),
            "focus": Field("seg", idx=0), "music": Field("seg", idx=0), "prec": Field("seg", idx=1),
            "game": Field("seg", idx=0),
            "vert": Field("tog", on=True), "next": Field("tog", on=True), "sound": Field("tog", on=True),
        }
        self.sel = "dpi"
        self.msg, self.state, self.dirty, self.last_live = "", "menu", True, 0.0
        self.w, self.h = 1280, 720
        self.fullscreen = False
        self.hist = self.load_history()
        self.mode, self.bpm, self.rhythm, self.show_next = 0, 0.0, False, True
        self.target = self.next_t = None
        self.kind = "flick"
        self.root.bind("<Key>", self.on_key)
        self.root.bind("<F11>", self.toggle_fs)
        self.cv.bind("<Button-1>", self.on_click)
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Configure>", lambda e: setattr(self, "dirty", True))
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        threading.Thread(target=self.np_loop, daemon=True).start()
        self.tick()

    # --------------------------------------------------------------- utilidades
    def np_loop(self):
        while self.music.running:
            if self.state in ("menu", "ready") and self.F["music"].idx == 1:
                self.np_text = mus.now_playing()
            time.sleep(6)

    def quit(self):
        RawMouse.unclip()
        gdi.timer_resolution(False)
        self.music.running = False
        self.root.destroy()

    def toggle_fs(self, _=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    @staticmethod
    def load_history():
        try:
            return json.load(open(HISTORY, encoding="utf-8"))
        except Exception:
            return []

    # --------------------------------------------------------------- sesion
    def start_session(self):
        F = self.F
        self.dpi = F["dpi"].value()
        lo, hi = F["lo"].value(), F["hi"].value()
        self.fov = F["fov"].value()
        self.game = en.GAME_LIST[F["game"].idx]
        self.cur_val = F["cur"].value()
        current = en.game_to_cm(self.game, self.cur_val, self.dpi) if self.cur_val > 0 else 0.0
        self.focus = FOCUS_KEYS[F["focus"].idx]
        self.do_vert = F["vert"].on
        self.show_next = F["next"].on
        sounds.enabled = F["sound"].on
        self.mode = F["music"].idx
        self.bpm = F["bpm"].value()
        if self.dpi < 100 or not (1 <= lo < hi) or not (60 <= self.fov <= 140):
            self.msg = "Revisa los valores: DPI >= 100, minimo < maximo, FOV entre 60 y 140."
            self.dirty = True
            return
        if self.mode == 1 and not self.music.ready:
            self.msg = "No detecto una musica estable. Pon algo en Spotify/YouTube y espera a ver los BPM en 'Musica y ritmo'."
            self.dirty = True
            return
        if self.mode == 2 and self.bpm < 40:
            self.msg = "Escribe los BPM de tu musica (o pulsa T varias veces al compas)."
            self.dirty = True
            return
        self.msg = ""
        self.f = (self.w / 2) / math.tan(math.radians(self.fov) / 2)
        prior, prior_obs = None, []
        same = [h for h in self.hist if h.get("dpi") == self.dpi and h.get("enfoque") == self.focus
                and h.get("ritmo", 0) == self.mode]
        if same:
            prior = same[-1].get("recomendado_cm360")
        for h in same[-3:]:  # el historial propio actua como informacion previa (pesa poco)
            prior_obs += [tuple(o) for o in h.get("obs", [])]
        self.sess = en.Session(lo, hi, self.focus, prior, prior_obs=prior_obs, current=current or None,
                               preset=F["prec"].idx)
        self.lo0, self.hi0 = lo, hi
        self.phase, self.steps, self.res = "main", [], {}
        self.yratio, self.warm, self.vres, self.vqueue = 1.0, True, {}, []
        self.track_n = 0
        self.t_session = time.time()
        self.start_trial(math.sqrt(lo * hi), True, self.focus_steps())

    def focus_steps(self):
        return {"flick": ["flick"], "track": ["track"]}.get(self.focus, ["flick", "track"])

    def start_trial(self, cm, warm, steps, yratio=1.0):
        self.cm, self.warm, self.steps, self.yratio = cm, warm, list(steps), yratio
        self.res = {}
        self.dpc = math.radians(en.deg_per_count(cm, self.dpi))
        self.advance()

    def advance(self):
        if self.steps:
            self.kind = self.steps.pop(0)
            self.set_state("ready")
            return
        if self.phase == "main":
            if not self.warm:
                self.sess.add(self.cm, self.res.get("flick"), self.res.get("track"), self.res.get("over"))
            if self.sess.trials and self.sess.done():
                self.final = self.sess.final()
                self.best_cm = self.final["best"]
                if not self.do_vert:
                    return self.finish()
                self.phase = "vert"
                self.vqueue = [r for r in V_RATIOS for _ in range(2)]
                random.shuffle(self.vqueue)
                self.vres = {}
            else:
                return self.start_trial(self.sess.next_cm(), False, self.focus_steps())
        if self.phase == "vert":
            if "vflick" in self.res:
                self.vres.setdefault(self.yratio, []).append(self.res["vflick"])
            if self.vqueue:
                return self.start_trial(self.best_cm, False, ["vflick"], self.vqueue.pop(0))
            self.finish()

    def setup_block(self):
        self.hits = self.misses = 0
        self.overs = []
        self.target = self.next_t = None
        self.n_targets = VFLICK_TARGETS if self.kind == "vflick" else FLICK_TARGETS
        self.az_rng, self.el_rng = (25, 30) if self.kind == "vflick" else (45, 18)
        self.thr, self.expired, self.spawned = [], 0, 0
        self.rhythm = self.mode != 0 and self.kind != "track"
        base = 60.0 / self.bpm if self.mode == 2 and self.bpm > 0 else (self.music.tracker.iv or 0.5)
        iv = base
        while iv < 0.45:
            iv *= 2
        while iv > 1.2:
            iv /= 2
        self.iv, self.biv = iv, base
        if self.kind == "track":
            self.path = TRACK_PATHS[self.track_n % len(TRACK_PATHS)]
            self.track_n += 1
        self.on_t = self.tot_t = 0.0
        self.t_last = None

    def begin_block(self):
        self.setup_block()
        self.yaw = self.pitch = 0.0
        self.t_block = time.perf_counter()
        self.recenter()
        self.set_state("countdown")

    def set_state(self, st):
        self.state = st
        self.dirty = True
        playing = st in ("countdown", "playing")
        self.cv.configure(cursor="none" if playing else "")
        # el analisis de audio solo corre fuera del juego, salvo en bloques con ritmo automatico
        self.music.heavy_ok = not playing
        self.music.active = (not playing) or (self.mode == 1 and self.rhythm)
        if not playing:
            RawMouse.unclip()
            if self.gdi_dirty:
                self.view.clear_window()
                self.gdi_dirty = False
        else:
            self.cv.delete("all")

    def recenter(self):
        self.cx = self.root.winfo_rootx() + self.cv.winfo_width() // 2
        self.cy = self.root.winfo_rooty() + self.cv.winfo_height() // 2
        user32.SetCursorPos(self.cx, self.cy)
        if self.raw.ok:
            RawMouse.clip_to(self.cx, self.cy)
            self.raw.consume()

    def poll_mouse(self):
        if self.raw.ok:
            dx, dy = self.raw.consume()
        else:  # respaldo sin raw input: aproxima con la posicion del cursor
            p = ctypes.wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(p))
            dx, dy = p.x - self.cx, p.y - self.cy
            if dx or dy:
                user32.SetCursorPos(self.cx, self.cy)
        if dx or dy:
            self.yaw += dx * self.dpc
            self.pitch = max(-1.4, min(1.4, self.pitch - dy * self.dpc * self.yratio))
            if self.target and self.kind != "track":
                self.upd_over()

    def upd_over(self):
        az, el = self.target
        vx, vy = az - self.start[0], el - self.start[1]
        d2 = vx * vx + vy * vy
        if d2 > 1e-9:
            p = ((self.yaw - self.start[0]) * vx + (self.pitch - self.start[1]) * vy) / d2
            self.maxp = max(self.maxp, p)

    def gen_target(self, ref_az, ref_el):
        while True:
            az = ref_az + math.radians(random.uniform(-self.az_rng, self.az_rng))
            el = math.radians(random.uniform(-self.el_rng, self.el_rng))
            if math.hypot(az - ref_az, el - ref_el) > math.radians(10):
                return az, el

    def spawn(self):
        if self.next_t is None:
            self.next_t = self.gen_target(self.yaw, self.pitch)
        self.target = self.next_t
        self.next_t = self.gen_target(*self.target)  # el siguiente ya queda decidido (aviso)
        self.start, self.maxp = (self.yaw, self.pitch), 0.0
        self.t_spawn = time.perf_counter()
        self.cur_D = math.degrees(math.hypot(self.target[0] - self.yaw, self.target[1] - self.pitch))
        if self.rhythm:
            sounds.play("beat")

    def track_pos(self, t):
        p = self.path
        az = 22 * math.sin(0.9 * t + p[0]) + 10 * math.sin(2.3 * t + p[1])
        el = 8 * math.sin(1.3 * t + p[2]) + 4 * math.sin(2.9 * t + p[3])
        return math.radians(az), math.radians(el)

    def finish_block(self, t_end):
        if self.kind == "track":
            score = self.on_t / self.tot_t if self.tot_t else 0.0
        else:
            score = en.flick_score(self.thr, self.hits, self.misses, self.expired)
            if self.kind == "flick" and self.overs:
                self.res["over"] = sum(self.overs) / len(self.overs)
        self.res[self.kind] = score
        sounds.play("done")
        self.advance()

    def finish(self):
        fin, e = self.final, self.final["est"]
        self.vratio, self.vgain = en.best_ratio(self.vres) if self.vres else (1.0, 0.0)
        self.elapsed = time.time() - self.t_session
        self.prev_same = [h["recomendado_cm360"] for h in self.hist if h.get("dpi") == self.dpi
                          and h.get("enfoque") == self.focus and h.get("ritmo", 0) == self.mode][-3:]
        self.hist.append({
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "dpi": self.dpi, "fov": self.fov, "enfoque": self.focus, "ritmo": self.mode, "juego": self.game["key"],
            "recomendado_cm360": round(fin["best"], 2),
            "intervalo90_cm360": [round(x, 2) for x in fin["ci"]],
            "zona_optima_cm360": [round(x, 2) for x in fin["zone"]],
            "perdida_esperada": round(fin["regret"], 4),
            "pruebas": len(self.sess.trials),
            "ratio_vertical": self.vratio,
            "obs": [[round(e["lo"] * math.exp(x * math.log(e["hi"] / e["lo"])), 2), round(y, 4)]
                    for x, y in zip(e["xs"], e["ys"])],
        })
        try:
            json.dump(self.hist, open(HISTORY, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        except OSError:
            pass
        self.set_state("results")

    # --------------------------------------------------------------- ritmo
    def tap(self):
        now = time.perf_counter()
        if self.mode == 1 and self.music.ready:  # calibra el desfase audio->oido
            b = self.music.next_beat(now - self.music.tracker.iv / 2 - self.music_offset)
            if b is not None:
                delta = now - (b + self.music_offset)
                delta = (delta + self.music.tracker.iv / 2) % self.music.tracker.iv - self.music.tracker.iv / 2
                self.music_offset = max(-0.1, min(0.35, self.music_offset + 0.5 * delta))
                self.msg = "Desfase de audio ajustado: %+d ms" % round(self.music_offset * 1000)
            self.dirty = True
            return
        if self.taps and now - self.taps[-1] > 2.0:
            self.taps = []
        self.taps.append(now)
        self.beat_ref = now
        if len(self.taps) >= 4:
            ivs = sorted(b - a for a, b in zip(self.taps, self.taps[1:]))
            med = ivs[len(ivs) // 2]
            if med > 0:
                self.F["bpm"].text = "%.0f" % (60.0 / med)
        self.taps = self.taps[-12:]
        self.dirty = True

    def first_spawn_time(self, now):
        if self.mode == 1 and self.music.ready:
            nb = self.music.next_beat(now - self.music_offset)
            if nb is not None:
                return nb + self.music_offset
        k = math.ceil((now - self.beat_ref) / self.iv)
        return self.beat_ref + k * self.iv

    def next_spawn_time(self, now):
        if self.mode == 1 and self.music.ready:
            nb = self.music.next_beat(now + self.iv - self.biv / 2 - self.music_offset)
            if nb is not None:
                return nb + self.music_offset
        return self.next_spawn + self.iv

    # --------------------------------------------------------------- eventos
    NUM_ORDER = ["dpi", "fov", "cur", "lo", "hi", "bpm"]

    def on_key(self, ev):
        k = ev.keysym
        if k in ("t", "T") and self.state != "playing":
            self.tap()
            return
        self.dirty = True
        if self.state == "menu":
            fld = self.F[self.sel]
            if k == "Escape":
                self.quit()
            elif k in ("Tab", "Down"):
                self.sel = self.NUM_ORDER[(self.NUM_ORDER.index(self.sel) + 1) % len(self.NUM_ORDER)]
            elif k == "Up":
                self.sel = self.NUM_ORDER[(self.NUM_ORDER.index(self.sel) - 1) % len(self.NUM_ORDER)]
            elif k == "BackSpace":
                fld.text = fld.text[:-1]
            elif k == "Return":
                self.start_session()
            elif ev.char and (ev.char.isdigit() or ev.char == ".") and len(fld.text) < 7:
                fld.text += ev.char
        elif self.state == "ready":
            if k == "Escape":
                self.set_state("paused")
            elif k in ("Return", "space"):
                self.begin_block()
        elif self.state in ("countdown", "playing"):
            if k == "Escape":
                self.set_state("paused")
        elif self.state == "paused":
            if k == "Escape":
                self.set_state("menu")
            elif k in ("Return", "space"):
                self.begin_block()
        elif self.state == "results":
            if k in ("Escape", "Return"):
                self.set_state("menu")

    def on_motion(self, ev):
        if self.state in ("playing", "countdown"):
            return
        rid = self.ui.at(ev.x, ev.y)
        if rid != self.ui.hover:
            self.ui.hover = rid
            self.dirty = True
            self.cv.configure(cursor="hand2" if rid else "")

    def on_click(self, ev):
        if self.state == "playing" and self.kind != "track":
            self.poll_mouse()
            self.shoot()
            return
        if self.state in ("playing", "countdown"):
            return
        rid = self.ui.at(ev.x, ev.y)
        if self.state == "ready" and rid is None:
            rid = "go"
        if rid:
            self.on_action(rid)

    def on_action(self, rid):
        self.dirty = True
        parts = rid.split(":")
        if parts[0] == "f":
            self.sel = parts[1]
        elif parts[0] == "t":
            self.F[parts[1]].on = not self.F[parts[1]].on
        elif parts[0] == "g":
            n = len(en.GAME_LIST)
            self.F["game"].idx = (self.F["game"].idx + (1 if parts[1] == "next" else -1)) % n
            gg = en.GAME_LIST[self.F["game"].idx]
            self.F["fov"].text = str(gg["fov"])
            self.F["cur"].text = "0"
        elif parts[0] == "s":
            self.F[parts[1]].idx = int(parts[2])
            self.msg = ""
        elif rid == "start":
            self.start_session()
        elif rid in ("go", "resume"):
            self.begin_block()
        elif rid in ("quit_test", "menu"):
            self.set_state("menu")
        elif rid == "again":
            self.start_session()
        elif rid == "tap":
            self.tap()
        elif rid == "copy":
            self.root.clipboard_clear()
            self.root.clipboard_append(self.summary_text())
            self.msg = "Copiado al portapapeles"

    def shoot(self):
        if self.target is None:
            return
        p = project(*self.target, self.yaw, self.pitch, self.f, self.w, self.h)
        hit = False
        if p:
            r = self.f * math.tan(math.radians(TARGET_RADIUS_DEG)) / p[2]
            hit = math.hypot(p[0] - self.w / 2, p[1] - self.h / 2) <= r
        if not hit:
            self.misses += 1
            sounds.play("miss")
            return
        now = time.perf_counter()
        self.hits += 1
        sounds.play("hit")
        self.overs.append(min(1.0, max(0.0, self.maxp - 1.0)))
        d_id = math.log2(self.cur_D / (2 * TARGET_RADIUS_DEG) + 1)
        self.thr.append(d_id / max(MIN_TTH, now - self.t_spawn))
        if self.rhythm:
            self.target = None  # espera al siguiente beat
        elif self.hits >= self.n_targets:
            self.finish_block(now)
        else:
            self.spawn()

    # ------------------------------------------------------------------- loop
    def tick(self):
        now = time.perf_counter()
        if self.state in ("playing", "countdown"):
            if now < self.next_frame:  # tope de fps: espera sin dormir de mas
                self.root.after(0 if self.next_frame - now < 0.002 else 1, self.tick)
                return
            self.next_frame = max(self.next_frame + FRAME, now - FRAME)
            self.w, self.h = self.cv.winfo_width(), self.cv.winfo_height()
            self.f = (self.w / 2) / math.tan(math.radians(self.fov) / 2)
            self.poll_mouse()
            if self.state == "countdown" and now - self.t_block >= COUNTDOWN:
                self.t_first = now
                if self.rhythm:
                    self.next_spawn = self.first_spawn_time(now)
                elif self.kind != "track":
                    self.spawn()
                self.set_state("playing")
            elif self.state == "playing":
                if self.kind == "track":
                    self.tick_track(now)
                elif self.rhythm:
                    self.tick_rhythm(now)
                elif now - self.t_first > FLICK_CAP:
                    self.finish_block(now)
            self.draw()
            self.root.after(0, self.tick)
            return
        self.w, self.h = self.cv.winfo_width(), self.cv.winfo_height()
        self.draw()
        self.root.after(10, self.tick)

    def tick_rhythm(self, now):
        if now < self.next_spawn:
            return
        if self.target is not None:  # el blanco anterior no se acerto a tiempo
            self.expired += 1
            self.target = None
            sounds.play("miss")
        if self.spawned >= self.n_targets:
            self.finish_block(now)
            return
        self.spawn()
        self.spawned += 1
        self.next_spawn = max(self.next_spawn_time(now), now + 0.3)

    def tick_track(self, now):
        t = now - self.t_first
        self.target = self.track_pos(t)
        if t >= TRACK_WARMUP:
            p = project(*self.target, self.yaw, self.pitch, self.f, self.w, self.h)
            r = self.f * math.tan(math.radians(TRACK_RADIUS_DEG)) / p[2] if p else 0
            dt = now - self.t_last if self.t_last else 0.0
            if p and math.hypot(p[0] - self.w / 2, p[1] - self.h / 2) <= r:
                self.on_t += dt
            self.tot_t += dt
        self.t_last = now
        if t >= TRACK_TIME + TRACK_WARMUP:
            self.finish_block(now)

    # ----------------------------------------------------------------- dibujo
    def draw(self):
        if self.state in ("playing", "countdown"):
            self.draw_scene()
            return
        now = time.perf_counter()
        if self.state in ("menu", "ready") and self.mode_live() and now - self.last_live > 0.12:
            self.dirty = True
        if not self.dirty or self.w < 200 or self.h < 200:
            return
        self.dirty, self.last_live = False, now
        self.ui.layout(self.w, self.h)
        self.ui.clear(self.w, self.h)
        getattr(self, "draw_" + self.state)()

    def mode_live(self):
        return self.F["music"].idx == 1 if self.state == "menu" else self.mode == 1

    # ---- cabecera comun
    def header(self, subtitle):
        ui = self.ui
        ui.oval(66, 48, 17, fill=C["accent_dim"], outline=C["accent"], width=2)
        ui.oval(66, 48, 5, fill=C["accent"])
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ui.line(66 + dx * 8, 48 + dy * 8, 66 + dx * 21, 48 + dy * 21, C["accent"], 2)
        ui.text(98, 38, "SensFinder", 24, bold=True)
        ui.text(98, 62, subtitle, 12, C["muted"])

    # ---- MENU
    def draw_menu(self):
        ui, F = self.ui, self.F
        g = en.GAME_LIST[F["game"].idx]
        self.header("Calibrador inteligente de sensibilidad para FPS")
        ui.button("start", 1010, 26, 1240, 70, "Empezar test", "primary", 17)
        if self.raw.ok:
            ui.chip(990, 34, "Raw input activo", C["good"], anchor="e")
        else:
            ui.chip(990, 34, "Raw input no disponible", C["warn"], anchor="e")

        ui.card(40, 96, 620, 364, "1 · Tu equipo y tu juego")
        self.num_row(40, 620, 148, "DPI de tu mouse", "Lo ves en el software de tu mouse (G Hub, Synapse...).", "dpi", "DPI")
        self.row_label(40, 200, "Juego principal", "Ajusta el FOV y las unidades de sensibilidad.")
        ui.cycler("g", 620 - 22 - 250, 206, g["name"], 250)
        self.num_row(40, 620, 252, "FOV horizontal", "Preajustado por juego (aprox.). Puedes cambiarlo.", "fov", "°")
        self.num_row(40, 620, 304, "Tu sens actual en " + self.short(g),
                     "Opcional: te digo cuanto mejora la nueva (0 = omitir).", "cur", g["unit"] or "sens")

        ui.card(40, 376, 620, 644, "2 · Que quieres medir")
        self.row_label(40, 428, "Tipo de prueba", "Mixto mide flicks y tracking: lo mas completo.")
        ui.segmented("s:focus", 620 - 22 - 252, 433, ["Mixto", "Flicks", "Tracking"], F["focus"].idx, width=252)
        self.row_label(40, 480, "Precision del test", "Rapido ≈ 4 min · Normal ≈ 6 · Preciso ≈ 10.")
        ui.segmented("s:prec", 620 - 22 - 252, 485, ["Rapido", "Normal", "Preciso"], F["prec"].idx, width=252)
        self.row_label(40, 532, "Ajustar tambien la sens. vertical", "10 bloques cortos extra al final.")
        ui.switch("t:vert", 620 - 22 - 52, 536, F["vert"].on)
        self.row_label(40, 584, "Rango a probar", "Se amplia solo si tu optimo cae fuera.")
        ui.numbox("f:lo", 370, 592, 470, F["lo"].text, "", self.sel == "lo")
        ui.text(480, 610, "a", 14, C["dim"], "center")
        ui.numbox("f:hi", 490, 592, 598, F["hi"].text, "cm/360", self.sel == "hi")

        ui.card(660, 96, 1240, 270, "3 · Ayudas durante el test")
        self.row_label(660, 150, "Avisar donde sale el siguiente blanco", "Aro dorado, o flecha si esta fuera de pantalla.")
        ui.switch("t:next", 1240 - 22 - 52, 152, F["next"].on)
        self.row_label(660, 210, "Sonidos", "Acierto, fallo y fin de bloque.")
        ui.switch("t:sound", 1240 - 22 - 52, 212, F["sound"].on)

        ui.card(660, 286, 1240, 586, "4 · Musica y ritmo")
        self.row_label(660, 340, "Sincronizar blancos con la musica", "Los blancos salen al ritmo del tema que escuchas.")
        ui.segmented("s:music", 682, 392, ["Libre", "Mi musica", "BPM manual"], F["music"].idx, width=536)
        self.music_panel(660, 1240, 442)

        ui.card(660, 602, 1240, 696, None)
        if self.hist:
            h = self.hist[-1]
            hg = next((x for x in en.GAME_LIST if x["key"] == h.get("juego")), en.GAME_LIST[0])
            ui.text(682, 624, "ULTIMO RESULTADO · " + h["fecha"][:10], 11, C["dim"], bold=True)
            ui.text(682, 662, "%.1f" % h["recomendado_cm360"], 30, C["gold"], bold=True)
            ui.text(682 + ui.measure("%.1f" % h["recomendado_cm360"], 30, True) + 8, 668,
                    "cm/360 · %d DPI" % h["dpi"], 13, C["muted"])
            ui.text(1218, 662, "%s %s" % (self.short(hg), self.fmt_game(hg, h["recomendado_cm360"], h["dpi"])),
                    15, C["text"], "e", bold=True)
        else:
            ui.text(682, 649, "Todavia no has hecho el test. Tarda entre 4 y 10 minutos.", 14, C["muted"])

        if self.msg:
            ui.rr(40, 656, 620, 704, 10, "#3a2a14", C["warn"])
            ui.text(54, 680, self.msg, 11.5, C["warn"], wrap=550)
        else:
            ui.text(40, 668, "Enter: empezar  ·  Esc: pausar el test  ·  F11: pantalla completa  ·  Tab: campos", 11.5, C["dim"])

    @staticmethod
    def short(g):
        return g["name"].split(" /")[0].split(" (")[0]

    @staticmethod
    def fmt_game(g, cm, dpi):
        v, ok = en.game_value(g, cm, dpi)
        if not ok:
            return "fuera de rango"
        return g["fmt"] % (round(v) if g["key"] == "minecraft" else v)

    def row_label(self, x0, y, label, hint):
        self.ui.text(x0 + 22, y + 10, label, 15, bold=True)
        self.ui.text(x0 + 22, y + 32, hint, 11.5, C["muted"], wrap=300)

    def num_row(self, x0, x1, y, label, hint, key, unit):
        self.row_label(x0, y, label, hint)
        self.ui.numbox("f:" + key, x1 - 22 - 150, y + 8, x1 - 22, self.F[key].text, unit, self.sel == key)

    def music_panel(self, x0, x1, y):
        ui, m = self.ui, self.music
        idx = self.F["music"].idx
        if idx == 0:
            ui.text(x0 + 22, y + 10, "Modo libre: el siguiente blanco aparece en cuanto aciertas el anterior.", 13, C["muted"], wrap=520)
            ui.text(x0 + 22, y + 50, "Es el modo mas fiel para medir tu punteria pura.", 13, C["dim"], wrap=520)
            return
        if idx == 2:
            ui.text(x0 + 22, y + 14, "BPM de tu cancion", 15, bold=True)
            ui.text(x0 + 22, y + 36, "Escribe el valor, o pulsa T varias veces al compas.", 11.5, C["muted"])
            ui.numbox("f:bpm", x1 - 22 - 150, y + 6, x1 - 22, self.F["bpm"].text, "BPM", self.sel == "bpm")
            ui.button("tap", x1 - 22 - 150, y + 52, x1 - 22, y + 88, "Tap  (T)", "ghost", 14)
            return
        ui.text(x0 + 22, y + 10, "Audio del sistema", 15, bold=True)
        if m.error:
            ui.chip(x1 - 22, y - 2, "Sin acceso al audio", C["bad"], anchor="e")
            ui.text(x0 + 22, y + 40, "No pude abrir el audio: " + m.error, 12, C["bad"], wrap=520)
            return
        if m.ready:
            ui.chip(x1 - 22, y - 2, "%.0f BPM · ritmo estable" % (m.bpm or 0), C["good"], anchor="e")
        elif m.listening:
            ui.chip(x1 - 22, y - 2, "Escuchando... analizando ritmo", C["warn"], anchor="e")
        else:
            ui.chip(x1 - 22, y - 2, "Sin audio", C["dim"], anchor="e")
        ui.progress(x0 + 22, y + 40, x1 - 22, y + 50, min(1.0, m.level), C["good"] if m.listening else C["dim"])
        ui.text(x0 + 22, y + 66, "Sonando: " + (self.np_text or "(reproduce algo en Spotify, YouTube, etc.)"), 12, C["muted"], wrap=520)
        ui.text(x0 + 22, y + 94, "Detecta lo que suena en cualquier app. Para ajustar el desfase del audio, pulsa T cuando OIGAS un golpe.",
                11.5, C["dim"], wrap=520)
        if m.ready:
            ui.text(x0 + 22, y + 126, "Desfase actual: %+d ms" % round(self.music_offset * 1000), 12, C["muted"])

    # ---- LISTO PARA EMPEZAR
    def draw_ready(self):
        ui = self.ui
        self.header("Preparate para el siguiente bloque")
        name, desc, how = KIND_INFO[self.kind]
        ui.rr(300, 96, 980, 690, 20, C["card"], C["border"])
        if self.phase == "vert":
            done = 2 * len(V_RATIOS) - len(self.vqueue)
            head, frac = "AJUSTE VERTICAL  %d / %d" % (done, 2 * len(V_RATIOS)), done / (2 * len(V_RATIOS))
        elif self.warm:
            head, frac = "CALENTAMIENTO  (no puntua)", 0.0
        else:
            head = "PRUEBA %d" % (len(self.sess.trials) + 1)
            frac = min(1.0, len(self.sess.trials) / self.sess.max_trials)
        ui.text(340, 130, head, 13, C["accent"], bold=True)
        ui.progress(340, 150, 940, 158, frac, C["accent"])
        if self.phase == "main" and not self.warm and self.sess.regret is not None:
            ui.text(940, 130, "Confianza del modelo  %d%%" % round(self.sess.confidence() * 100), 12, C["muted"], "e")
        ui.rr(340, 180, 940, 340, 14, C["panel"], C["border"])
        if self.kind == "track":
            self.illus_track(640, 260)
        else:
            self.illus_flick(640, 260)
        ui.text(340, 372, name, 30, bold=True)
        ui.text(340, 412, desc, 15, C["text"], wrap=600)
        ui.text(340, 462, how, 12.5, C["muted"], wrap=600)
        x = 340
        x = ui.chip(x, 515, "Aviso de blanco " + ("ON" if self.show_next else "OFF"), C["good"] if self.show_next else C["dim"]) + 10
        x = ui.chip(x, 515, "Sonido " + ("ON" if sounds.enabled else "OFF"), C["good"] if sounds.enabled else C["dim"]) + 10
        if self.kind != "track":
            if self.mode == 1:
                col = C["good"] if self.music.ready else C["warn"]
                lab = "Ritmo: %.0f BPM" % (self.music.bpm or 0) if self.music.ready else "Ritmo: buscando..."
            elif self.mode == 2:
                col, lab = C["good"], "Ritmo manual: %.0f BPM" % self.bpm
            else:
                col, lab = C["dim"], "Ritmo: libre"
            ui.chip(x, 515, lab, col)
            if self.mode:
                ui.text(340, 556, "Pulsa T cuando oigas un golpe de la musica para sincronizar mejor.", 11.5, C["dim"])
        ui.button("go", 340, 596, 940, 662, "Empezar  (click o Enter)", "primary", 19)
        ui.text(640, 680, "Esc para pausar", 11, C["dim"], "center")
        if self.msg:
            ui.text(640, 578, self.msg, 12, C["good"], "center")

    def illus_flick(self, cx, cy):
        ui = self.ui
        ui.oval(cx - 190, cy, 20, fill=None, outline=C["gold"], width=3)
        ui.line(cx - 160, cy, cx + 120, cy, C["dim"], 2, dash=(6, 6))
        ui.oval(cx + 160, cy - 6, 20, fill="#ff465a")
        ui.oval(cx + 160, cy - 6, 9, fill="#ffbec3")
        ui.oval(cx - 190, cy, 4, fill=C["good"])
        ui.text(cx - 190, cy + 46, "mira", 11, C["muted"], "center")
        ui.text(cx + 160, cy + 46, "blanco", 11, C["muted"], "center")

    def illus_track(self, cx, cy):
        ui = self.ui
        pts = [(cx - 250 + i * 12.5, cy + 34 * math.sin(i / 3.0) + 14 * math.sin(i / 1.3)) for i in range(41)]
        ui.poly(pts, C["dim"], 2, smooth=True)
        ui.oval(pts[-1][0], pts[-1][1], 18, fill="#ff465a")
        ui.oval(pts[-1][0], pts[-1][1], 28, outline=C["good"], width=2)
        ui.text(pts[-1][0], pts[-1][1] + 52, "sigue el blanco", 11, C["muted"], "center")

    # ---- PAUSA
    def draw_paused(self):
        ui = self.ui
        self.header("Test en pausa")
        ui.rr(400, 190, 880, 530, 20, C["card"], C["border"])
        ui.text(640, 240, "Pausa", 32, C["text"], "center", bold=True)
        ui.text(640, 295, "Tu progreso se conserva.\nEl bloque actual se repite desde cero.", 14, C["muted"],
                "center", wrap=400, justify="center")
        ui.button("resume", 440, 360, 840, 420, "Continuar", "primary", 18)
        ui.button("quit_test", 440, 440, 840, 500, "Abandonar test (se pierde el progreso)", "danger", 14)

    # ---- JUEGO (GDI)
    def draw_scene(self):
        v, w, h = self.view, self.w, self.h
        v.resize(w, h)
        self.gdi_dirty = True
        v.begin()
        f, cy, sy = self.f, math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        hw, hh = w / 2, h / 2
        for x, y, z, major in GRID_VEC:
            x1 = x * cy - z * sy
            z1 = x * sy + z * cy
            z2 = y * sp + z1 * cp
            if z2 <= 0.05:
                continue
            px = hw + f * x1 / z2
            py = hh - f * (y * cp - z1 * sp) / z2
            if 0 <= px < w and 0 <= py < h:
                v.circle(px, py, 3 if major else 2, v.brush("#464c5f" if major else "#2c303f"))
        if self.target:
            tgt = project(*self.target, self.yaw, self.pitch, f, w, h)
            if tgt:
                deg = TRACK_RADIUS_DEG if self.kind == "track" else TARGET_RADIUS_DEG
                r = max(3, f * math.tan(math.radians(deg)) / tgt[2])
                on = self.kind == "track" and math.hypot(tgt[0] - hw, tgt[1] - hh) <= r
                v.circle(tgt[0], tgt[1], r, v.brush("#32c864" if on else "#ff465a"))
                v.circle(tgt[0], tgt[1], r * .45, v.brush("#ffbec3"))
        self.draw_ghost(v, w, h, hw, hh)
        g = v.brush("#50ff8c")
        v.rect(hw - 14, hh - 1, hw - 5, hh + 1, g)
        v.rect(hw + 5, hh - 1, hw + 14, hh + 1, g)
        v.rect(hw - 1, hh - 14, hw + 1, hh - 5, g)
        v.rect(hw - 1, hh + 5, hw + 1, hh + 14, g)
        self.draw_hud(v, w, h, hw)
        v.present()

    def draw_hud(self, v, w, h, hw):
        s = v.scale
        name = KIND_INFO[self.kind][0]
        if self.state == "countdown":
            left = COUNTDOWN - (time.perf_counter() - self.t_block)
            n = max(1, min(3, math.ceil(left / (COUNTDOWN / 3))))
            v.text(hw, h * 0.27, str(n), "#ffffff", "xl", True)
            v.text(hw, h * 0.27 + 105 * s, name, "#8a93a8", "md", True)
            return
        if self.kind == "track":
            t = time.perf_counter() - self.t_first
            frac = min(1.0, t / (TRACK_TIME + TRACK_WARMUP))
            info = "%.1f s" % max(0.0, TRACK_TIME + TRACK_WARMUP - t)
        elif self.rhythm:
            frac = min(1.0, self.spawned / self.n_targets)
            info = "%d acertados / %d" % (self.hits, self.n_targets)
        else:
            frac = self.hits / self.n_targets
            info = "%d / %d" % (self.hits, self.n_targets)
        bar_h = max(4, int(6 * s))
        v.rect(0, 0, w, bar_h, v.brush("#1d2230"))
        v.rect(0, 0, w * frac, bar_h, v.brush("#4f8cff"))
        v.text(24 * s, 22 * s, name, "#8a93a8", "sm")
        v.text(24 * s, 48 * s, info, "#eef1f7", "md")
        if self.rhythm:
            v.text(w - 100 * s, 22 * s, "RITMO", "#7aa2ff", "sm")

    def draw_ghost(self, v, w, h, hw, hh):
        """Avisa donde saldra el siguiente blanco: aro si esta en pantalla, flecha en el borde si no."""
        if not self.show_next or self.kind == "track" or self.next_t is None or self.state != "playing":
            return
        if self.rhythm and (self.spawned >= self.n_targets or self.next_spawn - time.perf_counter() > GHOST_LEAD):
            return
        az, el = self.next_t
        p = project(az, el, self.yaw, self.pitch, self.f, w, h)
        if p and 0 <= p[0] < w and 0 <= p[1] < h:
            r = max(5, self.f * math.tan(math.radians(TARGET_RADIUS_DEG)) / p[2])
            v.ring(p[0], p[1], r + 6, "#ffc850", 2)
            return
        if p:
            dx, dy = p[0] - hw, p[1] - hh
        else:  # detras de la camara: apunta hacia el lado mas corto
            rel = (az - self.yaw + math.pi) % (2 * math.pi) - math.pi
            dx, dy = (1.0 if rel > 0 else -1.0), 0.0
        n = math.hypot(dx, dy) or 1.0
        dx, dy = dx / n, dy / n
        k = min((hw - 40) / abs(dx) if dx else 1e9, (hh - 40) / abs(dy) if dy else 1e9)
        ax, ay = hw + dx * k, hh + dy * k
        px_, py_ = -dy, dx
        v.polygon([(ax + dx * 16, ay + dy * 16), (ax - dx * 8 + px_ * 11, ay - dy * 8 + py_ * 11),
                   (ax - dx * 8 - px_ * 11, ay - dy * 8 - py_ * 11)], v.brush("#ffc850"))

    # ---- RESULTADOS
    def summary_text(self):
        fin = self.final
        zl, zh = fin["zone"]
        lines = ["SensFinder - %s" % datetime.now().strftime("%Y-%m-%d"),
                 "Recomendado: %.1f cm/360 (%d DPI)" % (fin["best"], self.dpi),
                 "Zona optima: %.1f - %.1f cm/360" % (zl, zh), ""]
        for gm in en.GAME_LIST:
            lines.append("%s: %s  (agil %s / precisa %s)" % (
                gm["name"], self.fmt_game(gm, fin["best"], self.dpi),
                self.fmt_game(gm, zl, self.dpi), self.fmt_game(gm, zh, self.dpi)))
        if self.vres:
            lines.append("Vertical: x%.2f" % self.vratio)
        return "\n".join(lines)

    @staticmethod
    def nice_ticks(lo, hi):
        cand = [3, 5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250]
        t = [c for c in cand if lo * 1.04 <= c <= hi / 1.04]
        return t if len(t) >= 3 else [lo * (hi / lo) ** (i / 4) for i in range(5)]

    def draw_results(self):
        ui = self.ui
        fin, e = self.final, self.final["est"]
        self.header("Resultado del test")
        ui.button("copy", 760, 28, 910, 68, "Copiar valores", "ghost", 13)
        ui.button("menu", 920, 28, 1030, 68, "Menu", "ghost", 13)
        ui.button("again", 1040, 28, 1240, 68, "Repetir test", "primary", 14)

        conf = max(0.0, min(1.0, 1 - fin["regret"] / 0.05))
        label, ccol = (("ALTA", C["good"]) if conf > 0.7 else ("MEDIA", C["warn"]) if conf > 0.4 else ("BAJA", C["bad"]))
        g = self.game
        ui.rr(40, 96, 440, 300, 16, C["card"], C["border"])
        ui.text(62, 120, "SENSIBILIDAD RECOMENDADA", 11, C["dim"], bold=True)
        val = "%.1f" % fin["best"]
        ui.text(62, 168, val, 52, C["gold"], bold=True)
        ui.text(62 + ui.measure(val, 52, True) + 10, 184, "cm/360", 17, C["muted"], bold=True)
        x = ui.chip(62, 204, "Confianza " + label, ccol) + 8
        if self.vres:
            ui.chip(x, 204, "Vertical ×%.2f" % self.vratio, C["accent"])
        ui.text(62, 252, "En %s:" % self.short(g), 14, C["muted"])
        ui.text(62 + ui.measure("En %s:  " % self.short(g), 14), 252, self.fmt_game(g, fin["best"], self.dpi), 22, C["gold"], bold=True)
        ui.text(62, 280, "Intervalo de confianza 90%%:  %.1f – %.1f cm/360" % fin["ci"], 11.5, C["dim"])

        ui.rr(40, 312, 440, 696, 16, C["card"], C["border"])
        ui.text(62, 334, "TUS JUEGOS · %d DPI" % self.dpi, 11, C["dim"], bold=True)
        zl, zh = fin["zone"]
        cols = [("Agil", 262, zl), ("Equilibrada", 340, fin["best"]), ("Precisa", 418, zh)]
        for lab, cx, _ in cols:
            ui.text(cx, 358, lab, 10.5, C["gold"] if lab == "Equilibrada" else C["dim"], "e", bold=True)
        ui.line(62, 370, 418, 370, C["border"])
        y = 392
        for gm in en.GAME_LIST:
            if gm["key"] == g["key"]:
                ui.rr(52, y - 13, 428, y + 13, 7, C["card2"])
            nm = gm["name"] + (" ≈" if gm.get("approx") else "")
            ui.text(62, y, nm, 12.5, C["text"] if gm["key"] == g["key"] else C["muted"], bold=gm["key"] == g["key"])
            for lab, cx, cmv in cols:
                v, ok = en.game_value(gm, cmv, self.dpi)
                txt = "—" if not ok else gm["fmt"] % (round(v) if gm["key"] == "minecraft" else v)
                ui.text(cx, y, txt, 12.5, C["gold"] if lab == "Equilibrada" else C["text"], "e", bold=lab == "Equilibrada")
            y += 25
        ui.text(62, 664, "Agil = mas rapida · Precisa = mas lenta.  ≈ valor aproximado: verifica con un giro de 360°.",
                10, C["dim"], wrap=360)

        ui.rr(460, 96, 1240, 478, 16, C["card"], C["border"])
        ui.text(482, 120, "RENDIMIENTO SEGUN TU SENSIBILIDAD", 11, C["dim"], bold=True)
        x = 482
        for lab, col in (("Modelo", C["accent"]), ("Tus pruebas", C["good"]), ("Zona optima", "#2f6f55"), ("Intervalo 90%", C["warn"])):
            x = ui.chip(x, 134, lab, col, 11) + 8
        px0, py0, px1, py1 = 530, 190, 1214, 420
        lo, hi = e["lo"], e["hi"]
        lx0, lx1 = math.log(lo), math.log(hi)
        band_hi = [m + s for m, s in zip(e["mu"], e["sd"])]
        band_lo = [m - s for m, s in zip(e["mu"], e["sd"])]
        smax, smin = max(band_hi + e["ys"]), min(band_lo + e["ys"])
        pad = (smax - smin) * 0.06 or 0.1
        peak = max(e["mu"])

        def X(cm):
            return px0 + (math.log(max(cm, 1e-9)) - lx0) / (lx1 - lx0) * (px1 - px0)

        def Y(v_):
            return py1 - (v_ - smin + pad) / (smax - smin + 2 * pad) * (py1 - py0)

        for k in range(5):
            yy = py0 + (py1 - py0) * k / 4
            ui.line(px0, yy, px1, yy, C["border"], 1)
            v_ = smax + pad - (smax - smin + 2 * pad) * k / 4
            ui.text(px0 - 10, yy, "%d%%" % round(v_ / peak * 100), 10.5, C["dim"], "e")
        for t in self.nice_ticks(lo, hi):
            ui.line(X(t), py0, X(t), py1, C["border"], 1, dash=(2, 4))
            ui.text(X(t), py1 + 16, "%g" % t, 11, C["muted"], "center")
        ui.text((px0 + px1) / 2, py1 + 40, "cm por 360°   (menos = mas rapido  ·  mas = mas lento)", 11, C["dim"], "center")
        ui.rr(X(fin["zone"][0]), py0, X(fin["zone"][1]), py1, 6, "#1b3a2f")
        ui.poly([(X(c), Y(m)) for c, m in zip(e["grid_cm"], band_hi)] +
                [(X(c), Y(m)) for c, m in reversed(list(zip(e["grid_cm"], band_lo)))], None, fill="#223a66")
        ui.poly([(X(c), Y(m)) for c, m in zip(e["grid_cm"], e["mu"])], C["accent"], 3)
        cur = self.sess.current
        if cur and lo <= cur <= hi:
            ui.line(X(cur), py0, X(cur), py1, C["muted"], 2, dash=(5, 4))
            ui.text(X(cur), py0 - 10, "Tu sens actual", 11, C["muted"], "center")
        ui.line(X(fin["best"]), py0, X(fin["best"]), py1, C["gold"], 2)
        near = cur and abs(X(cur) - X(fin["best"])) < 90
        ui.text(X(fin["best"]), py0 - 26 if near else py0 - 10, "%.1f" % fin["best"], 12, C["gold"], "center", bold=True)
        for xs_, y_ in zip(e["xs"], e["ys"]):
            ui.oval(X(lo * math.exp(xs_ * (lx1 - lx0))), Y(y_), 4.5, fill=C["good"], outline=C["card"], width=1)
        ui.line(X(max(fin["ci"][0], lo)), py1 - 6, X(min(fin["ci"][1], hi)), py1 - 6, C["warn"], 5)

        ui.rr(460, 494, 1240, 696, 16, C["card"], C["border"])
        ui.text(482, 516, "QUE SIGNIFICA", 11, C["dim"], bold=True)
        tips = []
        extended = lo < self.lo0 - 1e-6 or hi > self.hi0 + 1e-6
        z_lo, z_hi = fin["zone"]
        gn = self.short(g)
        tips.append((ccol, "Con %d pruebas la confianza es %s: tu zona ideal va de %.1f a %.1f cm/360 (%s %s – %s)%s." % (
            len(self.sess.trials), label.lower(), z_lo, z_hi, gn, self.fmt_game(g, z_hi, self.dpi),
            self.fmt_game(g, z_lo, self.dpi), ". Amplie el rango porque tu optimo estaba cerca del borde" if extended else "")))
        vc = fin.get("vs_current")
        if vc:
            tips.append((C["good"] if vc["gain"] > 0.02 else C["muted"],
                         "Frente a tu sens actual (%.4g en %s = %.1f cm/360): %+d%% esperado, con %d%% de probabilidad de ser mejor." % (
                             self.cur_val, gn, vc["cur"], round(vc["gain"] * 100), round(vc["p_better"] * 100))))
        if "flick" in fin:
            a_, b_ = fin["flick"], fin["track"]
            note = ("tu tracking prefiere algo mas lento" if b_ > a_ * 1.12 else
                    "tu tracking prefiere algo mas rapido" if b_ < a_ * 0.89 else "los dos coinciden")
            tips.append((C["accent"], "Flicks piden %.1f cm/360 y tracking %.1f (%s). El resultado es un punto medio." % (a_, b_, note)))
        if fin.get("over_at_best") is not None and fin["over_at_best"] > 0.15:
            tips.append((C["warn"], "En flicks te pasas del blanco ~%d%% de la distancia. Si lo notas en partida, usa la opcion Precisa." % round(fin["over_at_best"] * 100)))
        d = e.get("drift", 0.0)
        if d > 0.01:
            tips.append((C["warn"], "Fuiste mejorando durante el test (aprendizaje). Ya lo descuente, pero repite otro dia para afinar."))
        elif d < -0.01:
            tips.append((C["warn"], "Tu rendimiento bajo durante el test (cansancio). Ya lo descuente; para otra vez haz una pausa a mitad."))
        if getattr(self, "prev_same", None):
            ps = self.prev_same
            inside = z_lo * 0.97 <= ps[-1] <= z_hi * 1.03
            tips.append((C["good"] if inside else C["warn"],
                         "Tu sesion anterior dio %.1f cm/360: %s." % (
                             ps[-1], "coincide con esta zona, buena senal" if inside else "cae fuera de esta zona; repite el test para confirmar")))
        tips.append((C["muted"], "Pruebala unos dias en partida. Si te pasas de largo, elige la opcion Precisa; si te quedas corto, la Agil."))
        yy = 546
        for col, t in tips[:6]:
            ui.oval(488, yy + 2, 4, fill=col)
            ui.text(502, yy - 6, t, 12, C["text"], wrap=710)
            yy += 36 if len(t) > 105 else 24
        if self.msg:
            ui.text(1218, 516, self.msg, 11, C["good"], "e")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
