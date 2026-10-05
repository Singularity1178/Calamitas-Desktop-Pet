"""Full-pet on-screen check: run the real loop, let her attack, then read the
desktop back to prove sprites AND projectiles are both visible.
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# CALAMITAS_MODULE selects which implementation to test (default: original pet)
_MOD = os.environ.get("CALAMITAS_MODULE", "calamitas_pet")
cp = __import__(_MOD)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


def grab(x, y, w, h):
    src = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    old = gdi32.SelectObject(mem, bmp)

    class BI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biX", ctypes.c_long),
                    ("biY", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biImp", wintypes.DWORD)]
    bi = BI()
    bi.biSize = ctypes.sizeof(BI)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.BitBlt(mem, 0, 0, w, h, src, x, y, cp.SRCCOPY)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, src)
    return np.array(np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0]])


def main():
    win = cp.LayeredWindow()
    audio = cp.Audio()
    cal = cp.Calamitas(win.w, win.h, audio)

    print("diagnostics:", win.diagnostics())

    # park the cursor to her right and force a barrage, so projectiles fly out
    target = (1100, 540)
    cal.cooldowns = {a["key"]: 0.0 for a in cp.ATTACKS}
    cal.next_at = 0.0

    best = None
    for i in range(240):
        win.pump()
        cal.update(1 / 60.0, target)
        win.clear()
        cal.draw(win.buf)
        win.present()
        time.sleep(1 / 60.0)
        if cal.bullets and best is None:
            best = i
        if i in (60, 120, 180):
            time.sleep(0.2)
            reg = grab(0, 0, win.w, win.h)
            r, g, b = reg[:, :, 0].astype(int), reg[:, :, 1].astype(int), reg[:, :, 2].astype(int)
            # No colour key any more: per-pixel alpha composites her over the
            # desktop, so look for saturated warm pixels that aren't there yet.
            warm = int(((r > 80) & (r > b + 40) & (g < r)).sum())
            print(f"  frame {i:3d}: state={cal.current:22} bullets={len(cal.bullets)} "
                  f"on-screen warm px={warm}")
            if warm > 3000:
                from PIL import Image
                Image.fromarray(reg).resize((win.w // 2, win.h // 2), Image.NEAREST)\
                     .save(f"onscreen_{i}.png")
                print(f"    saved onscreen_{i}.png")

    print(f"\npresents ok={win._ulw_ok} fail={win._ulw_fail}")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())