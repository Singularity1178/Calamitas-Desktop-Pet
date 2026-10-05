"""
Calamitas desktop pet -- a stand-alone boss fight on your desktop.

Uses the official Calamity Mod (v1.4.4) assets and timings:

  * SupremeCalamitasHooded.png   -> the sprite used in the real fight
                                    (or SupremeCalamitas.png, unhooded --
                                    asked for at startup, or --variant)
  * SCalSounds/*.ogg             -> her sound effects (converted to WAV)
  * frame durations              -> FrameChangeSpeed from SupremeCalamitas.cs

SCOPE, as requested:
  * pre-brothers moveset only. Everything gated behind `permafrost` or
    ai[0] >= 3 (PermafrostMeat, PermafrostAbsoluteZeroProjectile,
    PermafrostBlaster) is deliberately NOT included.
  * no Sepulcher, no Cataclysm / Catastrophe.
  * she targets your cursor and is free to drift partly off-screen.

No third-party packages: the window is a Win32 layered window (real per-pixel
alpha via UpdateLayeredWindow, click-through, always on top) driven through
ctypes, composited with numpy, sprites loaded with Pillow, audio through
winsound.

Controls: ESC quits.
"""

import ctypes
import math
import os
import random
import sys
import time
from ctypes import wintypes

import numpy as np
import winsound
from PIL import Image

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
SPR = os.path.join(HERE, "assets", "sprites")
SFX = os.path.join(HERE, "assets", "sfx")
FRM_ROOT = os.path.join(HERE, "assets", "frames")
# Which body sheet she is cut from: the Hooded sheet (what the game swaps in for
# the fight) or the plain unhooded one. Same 7 animations x 6 frames either way,
# so this is presentation only -- chosen in main(), or --variant on the command line.
BODY_SHEET = "SupremeCalamitasHooded"
FRM = os.path.join(FRM_ROOT, BODY_SHEET)

SCALE = 4
FPS = 60

# Win32 raster op, used by the tests to read the composited desktop back.
SRCCOPY = 0x00CC0020

# Presentation: a per-pixel-alpha layered window (UpdateLayeredWindow).
#
# Two things must hold for this to work, and both are load-bearing:
#
#   1. pblend must be a real BLENDFUNCTION pointer. Passing NULL fails every
#      call with ERROR_GEN_FAILURE on this build (Windows 11 25H2, 26200),
#      even though MSDN says pblend is ignored when ULW_ALPHA is set. That
#      single NULL was the whole reason this used to be a colour-key window.
#   2. SetLayeredWindowAttributes must never be called on this window. Once it
#      has been, every UpdateLayeredWindow call fails with
#      ERROR_INVALID_PARAMETER -- including flags=0.
#
# The buffer holds premultiplied BGRA, which is what ULW_ALPHA expects: the
# colour channels are already scaled by alpha. blit() does that multiply.

# Frame durations in ms, taken from FrameChangeSpeed in SupremeCalamitas.cs:
#   frame_ms = 1000 / (FrameChangeSpeed * 60)
ANIM_MS = {
    "00_UpwardDraft": 111,       # 0.15
    "01_UpwardDraftTall": 111,   # 0.15
    "02_Casting": 111,           # 0.15
    "03_BothArmsRaise": 95,      # 0.175
    "04_BlastPunchCast": 100,    # one-shot lerp in FindFrame
    "05_LeftHandshake": 111,     # 0.15
    "06_Clap": 68,               # 0.245
}

# projFrames[Type] from each projectile's source file
PROJ_FRAMES = {
    "BrimstoneBarrage": 4,
    "BrimstoneWave": 4,
    "SCalBrimstoneFireblast": 5,
    "SCalBrimstoneGigablast": 6,
    "BrimstoneHellblast": 4,
}

# --------------------------------------------------------------------------
# Win32 layered window
# --------------------------------------------------------------------------

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_SHOWNA = 8                    # show without activating
HWND_TOPMOST = -1
WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
DIB_RGB_COLORS = 0

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class BLENDFUNCTION(ctypes.Structure):
    """Passed to UpdateLayeredWindow. Required non-NULL here -- see the note by
    ULW_ALPHA above."""

    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND,
                                          wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Explicit signatures. Without these ctypes assumes c_int, and the extended
# window styles (WS_EX_NOACTIVATE alone is 0x08000000) overflow it.
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.WORD
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,           # dwExStyle
    wintypes.LPCWSTR,         # lpClassName
    wintypes.LPCWSTR,         # lpWindowName
    wintypes.DWORD,           # dwStyle
    ctypes.c_int,             # X
    ctypes.c_int,             # Y
    ctypes.c_int,             # nWidth
    ctypes.c_int,             # nHeight
    wintypes.HWND,            # hWndParent
    ctypes.c_void_p,          # hMenu
    wintypes.HINSTANCE,       # hInstance
    ctypes.c_void_p,          # lpParam
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC,
    ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GdiFlush.restype = wintypes.BOOL
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_ssize_t


class LayeredWindow:
    """Full-screen per-pixel-alpha, click-through, always-on-top window."""

    def __init__(self):
        self.w = user32.GetSystemMetrics(0)
        self.h = user32.GetSystemMetrics(1)

        self._wndproc = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )(self._proc)

        self._name_buf = ctypes.create_unicode_buffer("CalamitasPet")
        self._cls = WNDCLASSW()
        self._cls.lpfnWndProc = self._wndproc
        self._cls.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        self._cls.lpszClassName = ctypes.cast(self._name_buf, wintypes.LPCWSTR)
        user32.RegisterClassW(ctypes.byref(self._cls))

        # CreateWindowExW(exStyle, class, window, style, x, y, w, h,
        #                  parent, menu, instance, param)  -- 12 arguments
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "CalamitasPet", "CalamitasPet", WS_POPUP,
            0, 0, self.w, self.h,
            None, None, self._cls.hInstance, None,
        )
        if not self.hwnd:
            raise RuntimeError("CreateWindowExW failed")

        # Do NOT call SetLayeredWindowAttributes here. It switches the window to
        # colour-key/constant-alpha mode and afterwards every ULW call fails.
        # Transparency comes entirely from the per-pixel alpha in the DIB.
        user32.ShowWindow(self.hwnd, SW_SHOWNA)
        self.raise_topmost()

        # A back buffer we compose into with numpy, then hand to DWM.
        # ULW_ALPHA wants a DC compatible with the *screen*, not the window.
        self.hdc_scr = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_scr)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.w
        bmi.bmiHeader.biHeight = -self.h          # negative => top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bmi.bmiHeader.biSizeImage = self.w * self.h * 4

        self._ppv = ctypes.c_void_p()
        self._bits = gdi32.CreateDIBSection(
            self.hdc_scr, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(self._ppv), None, 0,
        )
        if not self._bits:
            raise RuntimeError("CreateDIBSection failed")

        raw = (ctypes.c_ubyte * (self.w * self.h * 4)).from_address(self._ppv.value)
        self.buf = np.frombuffer(raw, dtype=np.uint8).reshape(self.h, self.w, 4)
        self.clear()
        self._stock = gdi32.GetStockObject(5)       # NULL_BITMAP
        self._old_bmp = gdi32.SelectObject(self.hdc_mem, self._bits)
        self._blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self._pt = wintypes.POINT(0, 0)
        self._size = wintypes.SIZE(self.w, self.h)
        self._ulw_ok = 0
        self._ulw_fail = 0
        self._ulw_err = 0

    def clear(self):
        """Zero the back buffer: alpha 0 means fully transparent."""
        self.buf[:] = 0

    @staticmethod
    def _proc(hwnd, msg, wp, lp):
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT                 # click straight through
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def present(self):
        """Hand the premultiplied BGRA buffer to DWM for per-pixel-alpha output."""
        ok = user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_scr,
            ctypes.byref(self._pt), ctypes.byref(self._size),
            self.hdc_mem, ctypes.byref(self._pt),
            0, ctypes.byref(self._blend), ULW_ALPHA,
        )
        if ok:
            self._ulw_ok += 1
        else:
            self._ulw_fail += 1
            if self._ulw_fail == 1:
                self._ulw_err = ctypes.windll.kernel32.GetLastError()
        return ok

    def cursor(self):
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def raise_topmost(self):
        user32.SetWindowPos(self.hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def diagnostics(self):
        """What Windows thinks of the window, for troubleshooting."""
        style = user32.GetWindowLongW(self.hwnd, -16)      # GWL_STYLE
        exstyle = user32.GetWindowLongW(self.hwnd, -20)     # GWL_EXSTYLE
        return {
            "hwnd": self.hwnd,
            "visible": bool(user32.IsWindowVisible(self.hwnd)),
            "size": (self.w, self.h),
            "style": hex(style),
            "exstyle": hex(exstyle),
            "transparency": "per-pixel alpha (UpdateLayeredWindow)",
            "select_ok": self._old_bmp != self._stock,
        }

    def pump(self):
        """Drain the message queue so the window stays serviced."""
        msg = MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def close(self):
        gdi32.SelectObject(self.hdc_mem, self._old_bmp)
        gdi32.DeleteDC(self.hdc_mem)
        user32.ReleaseDC(None, self.hdc_scr)
        gdi32.DeleteObject(self._bits)
        user32.DestroyWindow(self.hwnd)


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

def blit(canvas, sprite, x, y):
    """Alpha-blend an RGBA sprite into a premultiplied BGRA canvas.

    Sprites come out of PIL with straight (non-premultiplied) alpha, but
    ULW_ALPHA requires premultiplied, so the colour channels are scaled by
    alpha here. Skipping this step is what produces bright halos around soft
    edges. Rounding is +127 before the shift so a=255 round-trips exactly.

    Fully transparent source pixels are left untouched, so layered sprites
    composite over each other instead of erasing one another.
    """
    sh, sw = sprite.shape[:2]
    H, W = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + sw), min(H, y + sh)
    if x0 >= x1 or y0 >= y1:
        return

    sub = sprite[y0 - y:y1 - y, x0 - x:x1 - x]
    a = sub[:, :, 3]
    vis = a > 0
    if not vis.any():
        return

    # uint16 intermediate: rgb*alpha can reach 65025, which overflows uint8.
    # Divide by 255 (not >>8) so alpha=255 round-trips to exactly 255.
    pm = ((sub[:, :, :3].astype(np.uint16) * a[:, :, None].astype(np.uint16) + 127)
          // 255).astype(np.uint8)

    dst = canvas[y0:y1, x0:x1]
    if dst.shape[2] == 4:
        # premultiplied BGRA: swap B/R on the way in, alpha straight across
        out = np.concatenate([pm[:, :, 2::-1], a[:, :, None]], axis=2)
    else:
        out = pm[:, :, 2::-1]
    dst[vis] = out[vis].astype(dst.dtype)


def scaled(path, scale=SCALE):
    im = Image.open(path).convert("RGBA")
    return np.array(im.resize((im.width * scale, im.height * scale), Image.NEAREST),
                    dtype=np.uint8)


def strip_frames(path, n_frames, scale=SCALE):
    """Split a Terraria projectile strip (frames stacked vertically).

    All frames are cropped with ONE shared bounding box computed in cell-local
    space, so every frame keeps the same size and the sprite never jitters as
    it animates.
    """
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    ch = h // n_frames
    cells = [im.crop((0, i * ch, w, (i + 1) * ch)) for i in range(n_frames)]

    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for c in cells:
        a = np.array(c)[:, :, 3]
        ys, xs = np.where(a > 0)
        if len(ys) == 0:
            continue
        x0 = min(x0, int(xs.min()))
        y0 = min(y0, int(ys.min()))
        x1 = max(x1, int(xs.max()) + 1)
        y1 = max(y1, int(ys.max()) + 1)
    box = (x0, y0, x1, y1) if x1 > 0 else (0, 0, w, ch)

    out = []
    for c in cells:
        s = c.crop(box)
        out.append(np.array(s.resize((s.width * scale, s.height * scale), Image.NEAREST),
                            dtype=np.uint8))
    return out


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------

class Audio:
    def __init__(self, enabled=True):
        self.blips = {}
        if not enabled:
            return
        for name in os.listdir(SFX):
            if name.endswith(".wav"):
                with open(os.path.join(SFX, name), "rb") as f:
                    self.blips[name[:-4]] = f.read()

    def play(self, name, volume=None):
        data = self.blips.get(name)
        if not data:
            return
        try:
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        except Exception:
            pass


# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------

class Anim:
    def __init__(self, folder, ms, loop=True):
        d = os.path.join(FRM, folder)
        self.frames = []
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".png"):
                im = Image.open(os.path.join(d, fn)).convert("RGBA")
                self.frames.append(np.array(
                    im.resize((im.width * SCALE, im.height * SCALE), Image.NEAREST),
                    dtype=np.uint8))
        self.ms = ms
        self.loop = loop
        self.t = 0.0
        self.ox = self.frames[0].shape[1] // 2
        self.oy = self.frames[0].shape[0]      # anchor at her feet

    def step(self, dt_ms):
        self.t += dt_ms

    def frame(self):
        n = len(self.frames)
        i = int(self.t // self.ms)
        return self.frames[i % n if self.loop else min(i, n - 1)]

    def restart(self):
        self.t = 0.0


class Projectile:
    """Flies at the cursor and bursts on arrival."""

    def __init__(self, frames, pos, speed, homing, life, spin=0.0, scale=1.0):
        self.frames = frames
        self.x, self.y = pos
        self.speed = speed
        self.homing = homing
        self.life = life
        self.spin = spin
        self.scale = scale
        self.t = random.random() * 0.35
        self.dead = False
        self.rot = 0.0

    def step(self, dt, target):
        self.t += dt
        self.rot += self.spin * dt
        dx, dy = target[0] - self.x, target[1] - self.y
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d
        vx, vy = ux * self.speed, uy * self.speed
        if self.homing > 0:
            k = min(1.0, self.homing * dt)
            vx += (ux * self.speed - vx) * k
            vy += (uy * self.speed - vy) * k
        self.x += vx * dt
        self.y += vy * dt
        if d < max(18, self.speed * 0.10) or self.life <= 0:
            self.dead = True

    def frame(self):
        f = self.frames[int(self.t * 14) % len(self.frames)]
        if self.scale != 1.0:
            f = np.array(Image.fromarray(f).resize(
                (int(f.shape[1] * self.scale), int(f.shape[0] * self.scale)), Image.NEAREST),
                dtype=np.uint8)
        return f

    def draw(self, canvas):
        f = self.frame()
        h, w = f.shape[:2]
        x = int(self.x - w / 2)
        y = int(self.y - h / 2)
        if abs(self.rot) > 1e-3:
            f = np.array(Image.fromarray(f).rotate(
                math.degrees(self.rot), resample=Image.NEAREST, expand=False), dtype=np.uint8)
        blit(canvas, f, x, y)


# --------------------------------------------------------------------------
# the boss
# --------------------------------------------------------------------------

# Pre-brothers moveset only. No Permafrost* attacks, no Sepulcher, no brothers.
ATTACKS = [
    # name            anim                 period  first  sfx                    projectiles
    dict(key="barrage",   anim="03_BothArmsRaise",  cd=7.0, dur=1.6, sfx="BrimstoneShoot",
         shots=[("BrimstoneBarrage", 0.00, 260, 3.2, 0.0), ("BrimstoneBarrage", 0.35, 260, 3.2, 0.0),
                ("BrimstoneBarrage", 0.70, 260, 3.2, 0.0)]),
    dict(key="fireblast", anim="02_Casting",        cd=6.0, dur=1.4, sfx="BrimstoneShoot",
         shots=[("SCalBrimstoneFireblast", 0.15, 420, 2.4, 0.0)]),
    dict(key="wave",      anim="05_LeftHandshake",  cd=8.0, dur=1.8, sfx="BrimstoneShoot",
         shots=[("BrimstoneWave", 0.2, 300, 1.8, 0.0), ("BrimstoneWave", 0.55, 300, 1.8, 0.0)]),
    dict(key="gigablast", anim="02_Casting",        cd=11.0, dur=2.6, sfx="BrimstoneBigShoot",
         shots=[("SCalBrimstoneGigablast", 0.6, 190, 1.1, 0.0)]),
    dict(key="hellblast", anim="03_BothArmsRaise",  cd=9.0, dur=2.2, sfx="BrimstoneHellblastSound",
         shots=[("BrimstoneHellblast", 0.25, 520, 2.6, 0.0), ("BrimstoneHellblast", 0.65, 520, 2.6, 0.0)]),
    dict(key="bash",      anim="04_BlastPunchCast", cd=9.5, dur=1.2, sfx="SCalDash", dash=True,
         shots=[]),
]


class Calamitas:
    HOVER = 1

    def __init__(self, w, h, audio):
        self.audio = audio
        self.anims = {k: Anim(k, ms) for k, ms in ANIM_MS.items()}
        self.proj = {
            n: strip_frames(os.path.join(SPR, n + ".png"), c)
            for n, c in PROJ_FRAMES.items()
        }
        self.shield_top = scaled(os.path.join(SPR, "SupremeShieldTop.png"))
        self.shield_bot = scaled(os.path.join(SPR, "SupremeShieldBottom.png"))

        self.x, self.y = w * 0.5, h * 0.5
        self.vx = self.vy = 0.0
        self.state = self.HOVER
        self.current = "00_UpwardDraft"
        self.t_state = 0.0
        self.next_at = 2.0
        self.attack = None
        self.shots_done = 0
        self.cooldowns = {a["key"]: random.uniform(0, a["cd"]) for a in ATTACKS}
        self.bullets = []
        self.bob = random.random() * math.tau
        self.facing = 1
        self.dash_dir = (0.0, 0.0)
        self.hurt_flash = 0.0
        self.flourish_until = 0.0
        self.cursor = (w * 0.5, h * 0.5)

    # -- helpers ---------------------------------------------------------
    def anim(self):
        return self.anims[self.current]

    def pick_attack(self):
        ready = [a for a in ATTACKS if self.cooldowns[a["key"]] <= 0]
        if not ready:
            return
        self.attack = random.choice(ready)
        # Only the chosen attack goes back on cooldown. Resetting all of them
        # made every other attack unavailable right after, so the shortest
        # cooldown dominated and the rest almost never fired.
        self.cooldowns[self.attack["key"]] = random.uniform(
            self.attack["cd"] * 0.7, self.attack["cd"] * 1.4)
        self.current = self.attack["anim"]
        self.anims[self.current].restart()
        self.t_state = 0.0
        self.flourish_until = 0.0
        self.shots_done = 0
        if self.attack["sfx"]:
            self.audio.play(self.attack["sfx"])
        if self.attack.get("dash"):
            tx, ty = self.cursor
            d = math.hypot(tx - self.x, ty - self.y) or 1.0
            self.dash_dir = ((tx - self.x) / d, (ty - self.y) / d)

    # -- update ----------------------------------------------------------
    def update(self, dt, cursor):
        self.cursor = cursor
        self.bob += dt
        self.hurt_flash = max(0.0, self.hurt_flash - dt)

        for k in self.cooldowns:
            self.cooldowns[k] -= dt

        a = self.anim()
        a.step(dt * 1000.0)

        if self.attack is not None:
            self.t_state += dt
            A = self.attack
            if A.get("dash"):
                sp = 900.0
                self.x += self.dash_dir[0] * sp * dt
                self.y += self.dash_dir[1] * sp * dt
            else:
                # strafe a little while attacking
                self.x += math.cos(self.bob * 0.8) * 55 * dt
                self.y += math.sin(self.bob * 0.6) * 35 * dt

            for (pname, at, speed, homing, spin) in A["shots"]:
                if self.shots_done < len(A["shots"]) and self.t_state >= at:
                    self.spawn(pname, speed, homing, spin)
                    self.shots_done += 1
            if self.t_state >= A["dur"]:
                self.attack = None
                self.current = "00_UpwardDraft"
                self.t_state = 0.0            # clean slate for the next hover phase
        else:
            # hover toward the cursor, bobbing -- mirrors her UpwardDraft drift
            tx, ty = cursor
            dx, dy = tx - self.x, ty - self.y
            d = math.hypot(dx, dy)
            want = 210.0
            pull = (d - want) * 1.5
            if d > 1:
                ux, uy = dx / d, dy / d
            else:
                ux = uy = 0.0
            bobx = math.cos(self.bob * 2.1) * 60
            boby = math.sin(self.bob * 1.7) * 40
            self.vx += (ux * pull + bobx) * dt * 2.4
            self.vy += (uy * pull + boby) * dt * 2.4
            self.vx *= 0.90
            self.vy *= 0.90
            self.x += self.vx * dt
            self.y += self.vy * dt

            if self.t_state < self.flourish_until:
                self.current = "06_Clap"       # idle flourish, no projectiles
            else:
                speed = math.hypot(self.vx, self.vy)
                self.current = "01_UpwardDraftTall" if speed > 260 else "00_UpwardDraft"

            if self.t_state >= self.next_at:
                # t_state resets to 0 after every attack, so next_at has to be
                # a fresh delay from now rather than a leftover absolute stamp.
                self.next_at = random.uniform(1.2, 2.6)
                if random.random() < 0.22:
                    self.current = "06_Clap"
                    self.anims["06_Clap"].restart()
                    self.flourish_until = self.t_state + 0.55
                else:
                    self.pick_attack()
            self.t_state += dt

        if abs(self.vx) > 12:
            self.facing = 1 if self.vx > 0 else -1

        # projectiles
        keep = []
        for p in self.bullets:
            p.life -= dt
            p.step(dt, cursor)
            if p.dead:
                self.hurt_flash = 0.12
                self.audio.play("BrimstoneFireblastImpact")
            else:
                keep.append(p)
        self.bullets = keep

    def spawn(self, name, speed, homing, spin):
        frames = self.proj[name]
        a = self.anim()
        f = a.frame()
        ox = a.ox + (random.randint(-8, 8) * SCALE)
        oy = -f.shape[0] + 14 * SCALE
        scale = {"SCalBrimstoneGigablast": 1.0}.get(name, 1.0)
        self.bullets.append(Projectile(frames, (self.x + ox, self.y + oy),
                                       speed, homing, random.uniform(4.0, 7.0),
                                       spin, scale))

    # -- draw ------------------------------------------------------------
    def draw(self, canvas):
        a = self.anim()
        f = a.frame()
        w, h = f.shape[1], f.shape[0]
        x = int(self.x - a.ox)
        y = int(self.y - h)

        # shield while bashing, and briefly on impact
        if self.attack is not None and self.attack.get("dash"):
            blit(canvas, self.shield_top, x - self.shield_top.shape[1] // 2,
                 y - self.shield_top.shape[0] + 10)
            blit(canvas, self.shield_bot, x - self.shield_bot.shape[1] // 2,
                 y + h - self.shield_bot.shape[0] + 8)

        if self.facing < 0:
            f = np.ascontiguousarray(f[:, ::-1])

        blit(canvas, f, x, y)

        for p in self.bullets:
            p.draw(canvas)


def painted_pixels(canvas):
    """How many pixels have any alpha at all."""
    if canvas.shape[2] == 4:
        return int(np.count_nonzero(canvas[:, :, 3]))
    return int(np.count_nonzero(canvas.any(axis=2)))


def write_debug_png(canvas, path):
    """Composite the back buffer over a checkerboard.

    The canvas is premultiplied BGRA, so the blend is exactly what the
    compositor does: out = premultiplied_source + bg * (1 - alpha). No
    un-premultiply step is needed (or wanted -- it would amplify noise in the
    near-transparent tail).
    """
    a = canvas[:, :, 3].astype(np.float32)
    src = canvas[:, :, 2::-1].astype(np.float32)   # BGRA -> RGB
    H, W = a.shape
    yy, xx = np.mgrid[0:H, 0:W]
    checker = (((xx // 16 + yy // 16) % 2) * 40 + 30).astype(np.float32)
    bg = np.repeat(checker[:, :, None], 3, axis=2)
    out = src + bg * (1.0 - a[:, :, None] / 255.0)
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(path)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    shot_at = None
    for i, a in enumerate(sys.argv):
        if a in ("--shot", "-s") and i + 1 < len(sys.argv):
            try:
                shot_at = float(sys.argv[i + 1])
            except ValueError:
                shot_at = 5.0

    # Asked before anything is loaded: which sprite sheet, hooded or unhooded.
    # Without a console (piped, a service, a test importing this module) the
    # helper falls back to hooded instead of blocking on input().
    global FRM, BODY_SHEET
    try:
        import calamitas_variant
    except Exception:  # file missing -> hooded, never prompt
        pass
    else:
        BODY_SHEET = calamitas_variant.choose(sys.argv[1:])
        FRM = os.path.join(FRM_ROOT, BODY_SHEET)

    if not os.path.isdir(FRM):
        print(f"missing frames at {FRM}")
        return 1

    win = LayeredWindow()
    audio = Audio()
    cal = Calamitas(win.w, win.h, audio)

    diag = win.diagnostics()
    print(f"Calamitas desktop pet -- {win.w}x{win.h}, SCALE={SCALE}, ESC to quit")
    print(f"body sprite sheet: {BODY_SHEET}")
    print(f"window visible={diag['visible']}  exstyle={diag['exstyle']}")
    print("pre-brothers moveset only (no Sepulcher, no brothers, no Permafrost phase)")
    if verbose:
        print("(verbose mode: fps/state logging on)")

    last = time.perf_counter()
    start = last
    acc = 0.0
    frames = 0
    fps_t = last
    painted_max = 0
    try:
        while True:
            now = time.perf_counter()
            dt = now - last
            last = now
            if dt > 0.1:
                dt = 0.1
            acc += dt
            frames += 1

            win.pump()
            if user32.GetAsyncKeyState(0x1B) & 0x8000:
                break

            cursor = win.cursor()
            cal.update(dt, cursor)

            win.clear()
            cal.draw(win.buf)
            painted_max = max(painted_max, painted_pixels(win.buf))
            win.present()

            if shot_at and (now - start) >= shot_at:
                write_debug_png(win.buf, "pet_debug_frame.png")
                print(f"wrote pet_debug_frame.png "
                      f"(alpha pixels in frame: {painted_max})")
                shot_at = None

            target = 1.0 / FPS
            if acc > target:
                acc = acc % target
                time.sleep(max(0.0, target - (time.perf_counter() - now)))
            if verbose and now - fps_t > 2.0:
                print(f"  ~{frames / (now - fps_t):.0f} fps, "
                      f"{len(cal.bullets)} projectiles, state={cal.current}")
                frames = 0
                fps_t = now
    except KeyboardInterrupt:
        pass
    finally:
        win.close()
    print(f"bye (peak painted pixels: {painted_max})")
    print(f"UpdateLayeredWindow ok={win._ulw_ok} fail={win._ulw_fail} "
          f"lastError={win._ulw_err}")
    if win._ulw_fail:
        print("  ^ the window was never actually presented to the screen")
    return 0


if __name__ == "__main__":
    sys.exit(main())

