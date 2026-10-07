"""Mini kit de interfaz sobre tk.Canvas: tarjetas redondeadas, botones, interruptores, escalado."""
import tkinter as tk
import tkinter.font as tkfont

C = dict(
    bg="#0a0c11", panel="#10131a", card="#161a23", card2="#1d2230", border="#2a3142",
    text="#eef1f7", muted="#9aa4ba", dim="#6a748a",
    accent="#4f8cff", accent_h="#6b9fff", accent_dim="#223a66",
    good="#3ddc97", warn="#ffb454", bad="#ff6b6b", gold="#ffc850",
)
DW, DH = 1280, 720


class Ui:
    def __init__(self, canvas):
        self.cv = canvas
        self.s, self.ox, self.oy = 1.0, 0.0, 0.0
        self.regions, self.hover = [], None
        self._fonts = {}

    # ---------------------------------------------------------- geometria
    def layout(self, w, h):
        self.s = max(0.3, min(w / DW, h / DH))
        self.ox, self.oy = (w - DW * self.s) / 2, (h - DH * self.s) / 2

    def X(self, x):
        return self.ox + x * self.s

    def Y(self, y):
        return self.oy + y * self.s

    def font(self, px, bold=False):
        key = (max(6, round(px * self.s)), bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family="Segoe UI Semibold" if bold else "Segoe UI", size=-key[0])
        return self._fonts[key]

    def measure(self, text, px, bold=False):
        return self.font(px, bold).measure(text) / self.s

    # ------------------------------------------------------------ primitivas
    def clear(self, w, h):
        self.cv.delete("all")
        self.regions = []
        self.cv.create_rectangle(0, 0, w, h, fill=C["bg"], outline="")

    def rr(self, x0, y0, x1, y1, r=12, fill=None, outline="", width=1):
        x0, y0, x1, y1, r = self.X(x0), self.Y(y0), self.X(x1), self.Y(y1), r * self.s
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.cv.create_polygon(pts, smooth=True, fill=fill or "", outline=outline, width=width)

    def text(self, x, y, s, px=14, color=None, anchor="w", bold=False, wrap=None, justify="left"):
        return self.cv.create_text(self.X(x), self.Y(y), text=s, fill=color or C["text"], anchor=anchor,
                                   font=self.font(px, bold), width=(wrap * self.s if wrap else 0), justify=justify)

    def line(self, x0, y0, x1, y1, color, width=1, dash=None, smooth=False):
        return self.cv.create_line(self.X(x0), self.Y(y0), self.X(x1), self.Y(y1), fill=color,
                                   width=max(1, width * self.s), dash=dash)

    def poly(self, pts, color, width=2, fill=None, smooth=False):
        flat = [v for x, y in pts for v in (self.X(x), self.Y(y))]
        if fill:
            return self.cv.create_polygon(flat, fill=fill, outline="", smooth=smooth)
        return self.cv.create_line(flat, fill=color, width=max(1, width * self.s), smooth=smooth)

    def oval(self, cx, cy, r, fill=None, outline="", width=1):
        r *= self.s
        x, y = self.X(cx), self.Y(cy)
        return self.cv.create_oval(x - r, y - r, x + r, y + r, fill=fill or "", outline=outline,
                                   width=max(1, width * self.s))

    # -------------------------------------------------------------- widgets
    def region(self, rid, x0, y0, x1, y1):
        self.regions.append((rid, x0, y0, x1, y1))

    def at(self, px, py):
        dx, dy = (px - self.ox) / self.s, (py - self.oy) / self.s
        for rid, x0, y0, x1, y1 in reversed(self.regions):
            if x0 <= dx <= x1 and y0 <= dy <= y1:
                return rid
        return None

    def card(self, x0, y0, x1, y1, title=None, icon=None):
        self.rr(x0, y0, x1, y1, 16, C["card"], C["border"])
        if title:
            self.text(x0 + 22, y0 + 24, title.upper(), 12, C["dim"], bold=True)
            self.line(x0 + 22, y0 + 42, x1 - 22, y0 + 42, C["border"])

    def button(self, rid, x0, y0, x1, y1, label, kind="primary", px=16, enabled=True):
        hov = self.hover == rid and enabled
        if kind == "primary":
            fill, fg, out = (C["accent_h"] if hov else C["accent"]), "#ffffff", ""
        elif kind == "danger":
            fill, fg, out = ("#3a1f26" if not hov else "#52262f"), C["bad"], C["bad"]
        else:
            fill, fg, out = (C["card2"] if not hov else "#283043"), C["text"], C["border"]
        if not enabled:
            fill, fg, out = C["card2"], C["dim"], ""
        self.rr(x0, y0, x1, y1, 12, fill, out)
        self.text((x0 + x1) / 2, (y0 + y1) / 2, label, px, fg, "center", bold=True)
        if enabled:
            self.region(rid, x0, y0, x1, y1)

    def switch(self, rid, x, y, on):
        """Interruptor con (x, y) = esquina superior izquierda de 52x28."""
        hov = self.hover == rid
        self.rr(x, y, x + 52, y + 28, 14, C["accent"] if on else C["card2"], C["border"] if not on else "")
        kx = x + 38 if on else x + 14
        self.oval(kx, y + 14, 10, "#ffffff" if on or hov else C["muted"])
        self.region(rid, x - 4, y - 4, x + 56, y + 32)

    def segmented(self, rid, x0, y0, options, idx, width=None, px=13):
        """Control segmentado; devuelve x1."""
        widths = [self.measure(o, px, True) + 28 for o in options]
        if width:
            widths = [width / len(options)] * len(options)
        total = sum(widths)
        self.rr(x0, y0, x0 + total, y0 + 34, 10, C["card2"], C["border"])
        x = x0
        for i, (o, w) in enumerate(zip(options, widths)):
            sel = i == idx
            if sel:
                self.rr(x + 3, y0 + 3, x + w - 3, y0 + 31, 8, C["accent"])
            elif self.hover == "%s:%d" % (rid, i):
                self.rr(x + 3, y0 + 3, x + w - 3, y0 + 31, 8, "#283043")
            self.text(x + w / 2, y0 + 17, o, px, "#ffffff" if sel else C["muted"], "center", bold=True)
            self.region("%s:%d" % (rid, i), x, y0, x + w, y0 + 34)
            x += w
        return x0 + total

    def numbox(self, rid, x0, y0, x1, value, unit, active):
        self.rr(x0, y0, x1, y0 + 36, 10, C["card2"], C["accent"] if active else C["border"], 2 if active else 1)
        self.text(x0 + 14, y0 + 18, value + ("│" if active else ""), 16, C["text"], bold=True)
        if unit:
            self.text(x1 - 12, y0 + 18, unit, 12, C["dim"], "e")
        self.region(rid, x0, y0, x1, y0 + 36)

    def chip(self, x, y, label, color, px=12, anchor="w"):
        w = self.measure(label, px, True) + 30
        x0 = x if anchor == "w" else x - w
        self.rr(x0, y, x0 + w, y + 26, 13, C["card2"], color)
        self.oval(x0 + 13, y + 13, 4, color)
        self.text(x0 + 24, y + 13, label, px, C["text"], bold=True)
        return x0 + w

    def progress(self, x0, y0, x1, y1, frac, color=None):
        self.rr(x0, y0, x1, y1, (y1 - y0) / 2, C["card2"])
        if frac > 0.01:
            self.rr(x0, y0, x0 + (x1 - x0) * min(1.0, frac), y1, (y1 - y0) / 2, color or C["good"])

    def cycler(self, rid, x0, y0, label, width):
        """Selector con flechas: [<  nombre  >]."""
        self.rr(x0, y0, x0 + width, y0 + 36, 10, C["card2"], C["border"])
        for side, ax in (("prev", x0), ("next", x0 + width - 38)):
            if self.hover == "%s:%s" % (rid, side):
                self.rr(ax + 3, y0 + 3, ax + 35, y0 + 33, 8, "#283043")
            cx = ax + 19
            d = -1 if side == "prev" else 1
            self.poly([(cx - 4 * d, y0 + 11), (cx + 4 * d, y0 + 18), (cx - 4 * d, y0 + 25)], C["text"], 2)
            self.region("%s:%s" % (rid, side), ax, y0, ax + 38, y0 + 36)
        self.text(x0 + width / 2, y0 + 18, label, 14, C["text"], "center", bold=True)
