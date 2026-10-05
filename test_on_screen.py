"""End-to-end check: is the pet ACTUALLY visible on the desktop?

Runs the real LayeredWindow + Calamitas, then reads the screen back with
BitBlt and looks for non-key pixels where she was drawn. This is the only
reliable proof, because earlier per-pixel-alpha attempts reported success at
the API level while nothing was ever shown.
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


# Screen read-back needs its own GDI signatures; declare before use.
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


def grab_region(x, y, w, h):
    """Read a rectangle of the screen as an RGB numpy array."""
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
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0]]
    return np.array(arr)


def main():
    win = cp.LayeredWindow()
    cal = cp.Calamitas(win.w, win.h, cp.Audio())
    print("diagnostics:", win.diagnostics())

    # pin her near the middle-left so we know where to look
    cal.x, cal.y = 400, 300
    found_at = None
    # Baseline: the bare desktop in the region we will inspect. With per-pixel
    # alpha there is no key colour to filter on, so we diff against this.
    win.clear()
    win.present()
    time.sleep(0.3)
    baseline = grab_region(200, 120, 500, 400)
    for i in range(40):
        win.clear()
        cal.update(1 / 60.0, (400, 300))
        cal.draw(win.buf)
        win.present()
        time.sleep(0.05)
        if i == 20:
            cal.x, cal.y = 400, 300
            win.clear()
            cal.draw(win.buf)
            win.present()
            time.sleep(0.3)
            region = grab_region(200, 120, 500, 400)
            r, g, b = region[:, :, 0], region[:, :, 1], region[:, :, 2]
            # There is no colour key any more, so "not the desktop" has to be
            # measured as a change against a baseline grab taken before she was
            # drawn. Anything that differs is something we put there.
            changed = int((np.abs(region.astype(np.int32) - baseline.astype(np.int32))
                           .max(axis=2) > 8).sum())
            # reddish pixels = her robe
            red = int(((r > 90) & (r.astype(int) > b.astype(int) + 30)).sum())
            print(f"  screen region: changed px={changed}  reddish px={red}")
            if changed > 200 and red > 200:
                found_at = (200, 120)
                Image = __import__("PIL.Image", fromlist=["Image"]).fromarray(region)
                Image.save("pet_on_screen.png")
                print("  wrote pet_on_screen.png")

    print(f"\nRESULT: {'VISIBLE on screen' if found_at else 'NOT visible on screen'}")
    print(f"  UpdateLayeredWindow presents ok={win._ulw_ok} fail={win._ulw_fail}")
    win.close()
    return 0 if found_at else 1


if __name__ == "__main__":
    sys.exit(main())