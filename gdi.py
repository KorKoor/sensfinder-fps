"""Render rapido por GDI (doble buffer) sobre la ventana de un widget de tkinter."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
winmm = ctypes.WinDLL("winmm")

V = ctypes.c_void_p
I = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [V], V
gdi32.CreateCompatibleBitmap.argtypes, gdi32.CreateCompatibleBitmap.restype = [V, I, I], V
gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [V, V], V
gdi32.DeleteObject.argtypes = [V]
gdi32.DeleteDC.argtypes = [V]
gdi32.CreateSolidBrush.argtypes, gdi32.CreateSolidBrush.restype = [wintypes.DWORD], V
gdi32.GetStockObject.argtypes, gdi32.GetStockObject.restype = [I], V
gdi32.Ellipse.argtypes = [V, I, I, I, I]
gdi32.CreatePen.argtypes, gdi32.CreatePen.restype = [I, I, wintypes.DWORD], V
gdi32.Polygon.argtypes = [V, ctypes.c_void_p, I]
gdi32.BitBlt.argtypes = [V, I, I, I, I, V, I, I, wintypes.DWORD]
gdi32.SetBkMode.argtypes = [V, I]
gdi32.SetTextColor.argtypes = [V, wintypes.DWORD]
gdi32.TextOutW.argtypes = [V, I, I, wintypes.LPCWSTR, I]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


gdi32.GetTextExtentPoint32W.argtypes = [V, wintypes.LPCWSTR, I, ctypes.POINTER(SIZE)]
gdi32.CreateFontW.argtypes = [I] * 5 + [wintypes.DWORD] * 3 + [wintypes.DWORD] * 5 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = V
user32.GetDC.argtypes, user32.GetDC.restype = [V], V
user32.ReleaseDC.argtypes = [V, V]
user32.FillRect.argtypes = [V, ctypes.POINTER(wintypes.RECT), V]

SRCCOPY = 0x00CC0020
NULL_PEN = 8
NULL_BRUSH = 5


def rgb(h):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


class GdiView:
    def __init__(self, hwnd, bg):
        self.hwnd, self.bg = hwnd, rgb(bg)
        self.w = self.h = 0
        self.mem = self.bmp = self.old = None
        self.brushes, self.pens = {}, {}
        self.bg_brush = self.brush(bg)
        self.scale = 1.0
        self.fonts = {}
        self.cur_font = None

    def brush(self, color):
        if color not in self.brushes:
            self.brushes[color] = gdi32.CreateSolidBrush(rgb(color))
        return self.brushes[color]

    def resize(self, w, h):
        if (w, h) == (self.w, self.h) or w < 2 or h < 2:
            return
        dc = user32.GetDC(self.hwnd)
        if self.mem:
            gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mem)
        self.mem = gdi32.CreateCompatibleDC(dc)
        self.bmp = gdi32.CreateCompatibleBitmap(dc, w, h)
        self.old = gdi32.SelectObject(self.mem, self.bmp)
        gdi32.SelectObject(self.mem, gdi32.GetStockObject(NULL_PEN))
        gdi32.SetBkMode(self.mem, 1)
        self.scale = max(0.5, min(w / 1280, h / 720))
        self.cur_font = None
        user32.ReleaseDC(self.hwnd, dc)
        self.w, self.h = w, h

    def begin(self):
        self.rect(0, 0, self.w, self.h, self.bg_brush)

    def rect(self, l, t, r, b, brush):
        user32.FillRect(self.mem, ctypes.byref(wintypes.RECT(int(l), int(t), int(r), int(b))), brush)

    def circle(self, x, y, r, brush):
        gdi32.SelectObject(self.mem, brush)
        gdi32.Ellipse(self.mem, int(x - r), int(y - r), int(x + r) + 1, int(y + r) + 1)

    def ring(self, x, y, r, color, width=2):
        """Circulo hueco (solo contorno)."""
        if (color, width) not in self.pens:
            self.pens[(color, width)] = gdi32.CreatePen(0, width, rgb(color))
        null_pen = gdi32.GetStockObject(NULL_PEN)
        gdi32.SelectObject(self.mem, self.pens[(color, width)])
        gdi32.SelectObject(self.mem, gdi32.GetStockObject(NULL_BRUSH))
        gdi32.Ellipse(self.mem, int(x - r), int(y - r), int(x + r) + 1, int(y + r) + 1)
        gdi32.SelectObject(self.mem, null_pen)

    def polygon(self, pts, brush):
        arr = (I * (2 * len(pts)))(*[int(v) for p in pts for v in p])
        gdi32.SelectObject(self.mem, brush)
        gdi32.Polygon(self.mem, arr, len(pts))

    def font(self, name):
        px = {"sm": 20, "md": 28, "lg": 44, "xl": 150}[name]
        key = (name, round(px * self.scale))
        if key not in self.fonts:
            self.fonts[key] = gdi32.CreateFontW(-key[1], 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
        return self.fonts[key]

    def text(self, x, y, s, color="#c8cdd7", size="md", center=False):
        gdi32.SelectObject(self.mem, self.font(size))
        if center:
            sz = SIZE()
            gdi32.GetTextExtentPoint32W(self.mem, s, len(s), ctypes.byref(sz))
            x, y = x - sz.cx / 2, y - sz.cy / 2
        gdi32.SetTextColor(self.mem, rgb(color))
        gdi32.TextOutW(self.mem, int(x), int(y), s, len(s))

    def present(self):
        dc = user32.GetDC(self.hwnd)
        gdi32.BitBlt(dc, 0, 0, self.w, self.h, self.mem, 0, 0, SRCCOPY)
        user32.ReleaseDC(self.hwnd, dc)

    def clear_window(self):
        """Pinta la ventana con el fondo (para que tkinter repinte limpio despues)."""
        dc = user32.GetDC(self.hwnd)
        r = wintypes.RECT(0, 0, 4000, 3000)
        user32.FillRect(dc, ctypes.byref(r), self.bg_brush)
        user32.ReleaseDC(self.hwnd, dc)


def timer_resolution(on):
    (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
