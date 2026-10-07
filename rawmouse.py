"""Entrada raw del mouse en Windows (WM_INPUT) para tkinter, via ctypes.

Da los counts reales del sensor, sin aceleracion ni velocidad de puntero de Windows.
"""
import atexit
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_INPUT = 0x00FF
GWLP_WNDPROC = -4
RID_INPUT = 0x10000003
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("ulButtons", wintypes.ULONG),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CallWindowProcW.restype = LRESULT
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.ClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]


def release_clip():
    user32.ClipCursor(None)


class RawMouse:
    def __init__(self, widget):
        self.dx = self.dy = 0
        self.ok = False
        try:
            widget.update()
            self.hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
            self._proc = WNDPROC(self._wndproc)  # referencia viva
            self._old = user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))
            dev = RAWINPUTDEVICE(0x01, 0x02, 0, self.hwnd)
            self.ok = bool(user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev))) and bool(self._old)
        except Exception:
            self.ok = False
        atexit.register(release_clip)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            try:
                size = wintypes.UINT(0)
                hdr = ctypes.sizeof(RAWINPUTHEADER)
                user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), hdr)
                buf = ctypes.create_string_buffer(size.value)
                if user32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), hdr) == size.value:
                    ri = RAWINPUT.from_buffer_copy(buf.raw[:ctypes.sizeof(RAWINPUT)].ljust(ctypes.sizeof(RAWINPUT), b"\0"))
                    if ri.header.dwType == 0 and not ri.mouse.usFlags & 1:  # mouse, relativo
                        self.dx += ri.mouse.lLastX
                        self.dy += ri.mouse.lLastY
            except Exception:
                pass
        return user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)

    def consume(self):
        dx, dy, self.dx, self.dy = self.dx, self.dy, 0, 0
        return dx, dy

    @staticmethod
    def clip_to(x, y):
        r = wintypes.RECT(x, y, x + 1, y + 1)
        user32.ClipCursor(ctypes.byref(r))

    @staticmethod
    def unclip():
        release_clip()
