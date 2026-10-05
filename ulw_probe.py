"""Empirical probe: does UpdateLayeredWindow actually work on this machine?

The README claims ULW "fails every call with ERROR_GEN_FAILURE /
ERROR_INVALID_PARAMETER on this machine". That claim is inherited, not
reproducible from the current code (present() counts BitBlt, not ULW). This
script re-tests it from scratch: it builds a layered window, tries the
documented call variants, and BitBlts the real desktop back to confirm the
resulting pixels actually reached the screen.

Run:  python ulw_probe.py
"""

import ctypes as c
from ctypes import wintypes

import numpy as np

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
LWA_COLORKEY = 0x00000001
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SW_SHOWNA = 8
HWND_TOPMOST = -1
WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
SRCCOPY = 0x00CC0020

# the square we paint, in screen coords (window sits at 0,0)
SQ = 64


# ---------------------------------------------------------------------------
# win32 declarations
# ---------------------------------------------------------------------------
class BITMAPINFOHEADER(c.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", c.c_long), ("biHeight", c.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", c.c_long), ("biYPelsPerMeter", c.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(c.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MSG(c.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", wintypes.POINT),
    ]


WNDPROC = c.WINFUNCTYPE(c.c_longlong, wintypes.HWND, wintypes.UINT,
                        wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(c.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", c.c_int),
        ("cbWndExtra", c.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


user32 = c.windll.user32
gdi32 = c.windll.gdi32

user32.RegisterClassW.argtypes = [c.POINTER(WNDCLASSW)]
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    c.c_int, c.c_int, c.c_int, c.c_int, wintypes.HWND, c.c_void_p,
    wintypes.HINSTANCE, c.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, c.POINTER(wintypes.POINT), c.POINTER(wintypes.SIZE),
    wintypes.HDC, c.POINTER(wintypes.POINT), wintypes.DWORD, c.c_void_p, wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = c.c_int
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = c.c_ssize_t
user32.ShowWindow.argtypes = [wintypes.HWND, c.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PeekMessageW.argtypes = [c.POINTER(MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [c.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [c.POINTER(MSG)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, c.c_int, c.c_int,
                                c.c_int, c.c_int, wintypes.UINT]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, c.POINTER(BITMAPINFO), wintypes.UINT,
                                   c.POINTER(c.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC, c.c_int, c.c_int, c.c_int, c.c_int,
                         wintypes.HDC, c.c_int, c.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

try:
    c.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()


# ---------------------------------------------------------------------------
# window + DIB plumbing
# ---------------------------------------------------------------------------
def _wndproc(hwnd, msg, wp, lp):
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


# must stay alive for the life of the class registration
PROC = WNDPROC(_wndproc)
CLS_NAME = c.create_unicode_buffer("UlwProbeCls")
INST = c.windll.kernel32.GetModuleHandleW(None)

_wc = WNDCLASSW()
_wc.lpfnWndProc = PROC
_wc.hInstance = INST
_wc.lpszClassName = c.cast(CLS_NAME, wintypes.LPCWSTR)
_wc.hbrBackground = 0
if not user32.RegisterClassW(c.byref(_wc)):
    raise c.WinError()


def make_dib(w, h, dc):
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = c.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0     # BI_RGB
    ppv = c.c_void_p()
    hbmp = gdi32.CreateDIBSection(dc, c.byref(bmi), 0, c.byref(ppv), None, 0)
    if not hbmp:
        raise c.WinError()
    buf = np.frombuffer((c.c_ubyte * (w * h * 4)).from_address(ppv.value),
                        dtype=np.uint8).reshape(h, w, 4)
    return hbmp, buf


def paint(buf):
    """Opaque magenta square, fully transparent elsewhere (premult == straight
    here because alpha is only ever 0 or 255)."""
    buf[:] = 0
    buf[100:100 + SQ, 100:100 + SQ] = (255, 0, 255, 255)   # B, G, R, A


def readback(x, y, w, h):
    """BitBlt the real composited desktop back and return it."""
    scr = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(scr)
    hbmp, buf = make_dib(w, h, scr)
    old = gdi32.SelectObject(mem, hbmp)
    gdi32.BitBlt(mem, 0, 0, w, h, scr, x, y, SRCCOPY)
    gdi32.GdiFlush()
    out = buf.copy()
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, scr)
    return out


def screen_has_square():
    """True if the centre of our 64x64 square is opaque magenta on screen."""
    px = readback(120, 120, 8, 8)
    r, g, b, a = px[4, 4]
    return bool(r > 200 and b > 200 and g < 60), (int(r), int(g), int(b), int(a))


# ---------------------------------------------------------------------------
# the matrix
# ---------------------------------------------------------------------------
FULL_EX = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

# ---------------------------------------------------------------------------
# matrix 2: only the ULW_ALPHA path, varying everything that could plausibly
# matter for the alpha/blend path specifically.
# ---------------------------------------------------------------------------
class BLENDFUNCTION(c.Structure):
    _fields_ = [("BlendOp", c.c_ubyte), ("BlendFlags", c.c_ubyte),
                ("SourceConstantAlpha", c.c_ubyte), ("AlphaFormat", c.c_ubyte)]


AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01


def run_alpha_case(label, pblend=False, ppt_src=True, dib_from="screen", twice=False,
                   flush_first=False, small=0):
    sw, sh = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    w, h = (sw, sh) if not small else (small, small)

    hwnd = user32.CreateWindowExW(FULL_EX, "UlwProbeCls", "UlwProbe", WS_POPUP,
                                  0, 0, w, h, None, None, INST, None)
    if not hwnd:
        return (label, "CreateWindowExW failed", "", "")
    user32.ShowWindow(hwnd, SW_SHOWNA)
    user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    if flush_first:
        c.windll.dwmapi.DwmFlush()

    base = user32.GetDC(None) if dib_from == "screen" else user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(base)
    hbmp, buf = make_dib(w, h, base)
    old = gdi32.SelectObject(mem, hbmp)
    paint(buf)

    pt = wintypes.POINT(0, 0)
    sz = wintypes.SIZE(w, h)
    bf = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA) if pblend else None

    res, errname, pix = "-", "-", "-"
    for attempt in range(2 if twice else 1):
        c.windll.kernel32.SetLastError(0)
        ok = user32.UpdateLayeredWindow(
            hwnd, base, c.byref(pt), c.byref(sz), mem,
            c.byref(pt) if ppt_src else None,
            0, c.byref(bf) if bf else None, 0x02)
        err = c.windll.kernel32.GetLastError()
        res = "ok" if ok else "FAIL"
        errname = {0: "OK", 31: "ERROR_GEN_FAILURE",
                   87: "ERROR_INVALID_PARAMETER"}.get(err, str(err))
        if ok:
            gdi32.GdiFlush()
            msg = MSG()
            for _ in range(20):
                while user32.PeekMessageW(c.byref(msg), None, 0, 0, 1):
                    user32.TranslateMessage(c.byref(msg))
                    user32.DispatchMessageW(c.byref(msg))
            _, pix = screen_has_square()

    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None if dib_from == "screen" else hwnd, base)
    user32.DestroyWindow(hwnd)
    return (label, res, errname, pix if res == "ok" else "")


ALPHA_CASES = [
    ("ULW_ALPHA, pblend=NULL", dict(pblend=False, ppt_src=True, dib_from="screen")),
    ("ULW_ALPHA, pblend=BLENDFUNCTION", dict(pblend=True, ppt_src=True, dib_from="screen")),
    ("ULW_ALPHA, pptSrc=NULL", dict(pblend=False, ppt_src=False, dib_from="screen")),
    ("ULW_ALPHA, DIB from window DC", dict(pblend=False, ppt_src=True, dib_from="wnd")),
    ("ULW_ALPHA, DwmFlush first", dict(pblend=False, ppt_src=True, dib_from="screen",
                                       flush_first=True)),
    ("ULW_ALPHA, called twice", dict(pblend=False, ppt_src=True, dib_from="screen", twice=True)),
    ("ULW_ALPHA, 64x64 window", dict(pblend=False, ppt_src=True, dib_from="screen", small=64)),
]

# keyword args after the first seven positional slots
CASES = [
    # label, positional(exstyles, size, dst_dc, pptDst, flags, colorkey, show), kwargs
    ("flags=0  (no ULW_ALPHA)", (FULL_EX, "full", "screen", True, 0x00, False, True), {}),
    ("flags=ULW_ALPHA (0x02)", (FULL_EX, "full", "screen", True, 0x02, False, True), {}),
    ("flags=0x01", (FULL_EX, "full", "screen", True, 0x01, False, True), {}),
    ("flags=0x04", (FULL_EX, "full", "screen", True, 0x04, False, True), {}),
    ("flags=0x08", (FULL_EX, "full", "screen", True, 0x08, False, True), {}),
    ("flags=0x10", (FULL_EX, "full", "screen", True, 0x10, False, True), {}),
    ("flags=0x20", (FULL_EX, "full", "screen", True, 0x20, False, True), {}),
    ("flags=0x10000", (FULL_EX, "full", "screen", True, 0x10000, False, True), {}),
    ("ULW_ALPHA + premult 50%", (FULL_EX, "full", "screen", True, 0x02, False, True),
     dict(alpha_mode="premult")),
    ("ULW_ALPHA + FRAMECHANGED", (FULL_EX, "full", "screen", True, 0x02, False, True),
     dict(frame_changed=True)),
    ("ULW_ALPHA + 512x512", (FULL_EX, "small", "screen", True, 0x02, False, True), {}),
    ("ULW_ALPHA, not shown", (FULL_EX, "full", "screen", True, 0x02, False, False), {}),
    ("ULW_ALPHA, layered only", (WS_EX_LAYERED, "full", "screen", True, 0x02, False, True), {}),
    ("colorkey then ULW_ALPHA", (FULL_EX, "full", "screen", True, 0x02, True, True), {}),
    ("colorkey then flags=0", (FULL_EX, "full", "screen", True, 0x00, True, True), {}),
]


def session_facts():
    """Is this an RDP / remote session? ULW_ALPHA needs DWM on the *display*
    device, and that is the usual reason it fails while flags=0 works."""
    out = {}
    out["SM_REMOTESESSION"] = user32.GetSystemMetrics(0x1000)      # 0 = local
    out["SM_REMOTECONTROL"] = user32.GetSystemMetrics(0x1001)
    out["SM_CLEANBOOT"] = user32.GetSystemMetrics(0x0022)           # 1 = booted safe

    buf = c.create_unicode_buffer(256)
    n = wintypes.DWORD(0)
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, c.c_int, c.c_void_p,
                                                 wintypes.DWORD, c.POINTER(wintypes.DWORD)]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    hws = user32.GetProcessWindowStation()
    ok = user32.GetUserObjectInformationW(hws, 2, buf, c.sizeof(buf), c.byref(n))
    out["window station"] = buf.value if ok else f"<query failed, err={c.get_last_error()}>"

    k32 = c.windll.kernel32
    k32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, c.POINTER(wintypes.DWORD)]
    k32.ProcessIdToSessionId.restype = wintypes.BOOL
    k32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    pid = wintypes.DWORD(k32.GetCurrentProcessId())
    sid = wintypes.DWORD(0xFFFFFFFF)
    ok = k32.ProcessIdToSessionId(pid, c.byref(sid))
    out["process session"] = f"{sid.value} (ok={bool(ok)})"
    cons = k32.WTSGetActiveConsoleSessionId()
    out["console session"] = cons
    out["same as console"] = (sid.value == cons)
    out["integrity"] = _integrity()
    return out


def _integrity():
    """Coarse integrity proxy: a medium-integrity process can read HKLM
    Computer\\System. A low/medium-mandatory sandbox cannot, and low-integrity
    processes cannot composite DWM surfaces -- which is the usual reason
    ULW_ALPHA fails while flags=0 still works."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control"):
            return ">= medium (can read HKLM\\SYSTEM)"
    except Exception as e:
        return f"< restricted: {type(e).__name__}>"


def graphics_facts():
    """Driver/DWM settings that are known to interfere with per-pixel-alpha
    layered windows. Read-only."""
    out = {}

    def reg(path, name, hive="HKLM"):
        try:
            import winreg
            h = {"HKLM": winreg.HKEY_LOCAL_MACHINE,
                 "HKCU": winreg.HKEY_CURRENT_USER}[hive]
            k = winreg.OpenKey(h, path)
            v, _ = winreg.QueryValueEx(k, name)
            return v
        except Exception:
            return "<unset>"

    g = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
    out["HwSchMode (2=HAGS on)"] = reg(g, "HwSchMode")
    out["DWM composition disabled"] = reg(g, "DisableDWMComposition")
    out["OverlayTestMode (MPO)"] = reg(g, "DWMOverlayTestMode")
    out["DirectX UserGpuPreference"] = reg(r"SOFTWARE\Microsoft\DirectX", "UserGpuPreference", "HKCU")

    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics")
        v, _ = winreg.QueryValueEx(k, "AppliedDPI")
        out["AppliedDPI"] = v
    except Exception:
        out["AppliedDPI"] = "<unset>"
    out["logpixels (awareness)"] = gdi32.GetDeviceCaps(user32.GetDC(None), 88)  # LOGPIXELSX
    out["virtual screen"] = (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                             user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))
    return out


def blend_proof():
    """Definitive test that per-pixel alpha is really working, not just that
    ULW returned success.

    Paint a horizontal alpha gradient (premultiplied magenta: R=B=A, G=0) over
    whatever is already on the desktop, then read the composited screen back.
    If the result equals a*1.0 + dest*(1 - a/255) per channel, the alpha channel
    genuinely blended.
    """
    sw, sh = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    hwnd = user32.CreateWindowExW(FULL_EX, "UlwProbeCls", "UlwProbe", WS_POPUP,
                                  0, 0, sw, sh, None, None, INST, None)
    user32.ShowWindow(hwnd, SW_SHOWNA)
    user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    scr = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(scr)
    hbmp, buf = make_dib(sw, sh, scr)
    old = gdi32.SelectObject(mem, hbmp)

    x0, y0, n = 300, 300, 64
    bg = readback(x0, y0, n, 1)[0].astype(np.int32)[:, :3]     # what is underneath

    # alpha ramp across the strip, as premultiplied magenta (R=B=A, G=0)
    alphas = (np.arange(n) * 255 // (n - 1)).astype(np.uint8)
    src_premult = np.zeros((n, 3), dtype=np.float32)      # BGRA
    src_premult[:, 0] = alphas                            # B
    src_premult[:, 1] = 0                                 # G  <- must stay 0
    src_premult[:, 2] = alphas                            # R
    buf[:] = 0
    strip = np.zeros((1, n, 4), dtype=np.uint8)
    strip[0, :, 0] = alphas
    strip[0, :, 2] = alphas
    strip[0, :, 3] = alphas
    buf[y0:y0 + 1, x0:x0 + n] = strip

    pt = wintypes.POINT(0, 0)
    sz = wintypes.SIZE(sw, sh)
    bf = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    ok = user32.UpdateLayeredWindow(hwnd, scr, c.byref(pt), c.byref(sz), mem,
                                    c.byref(pt), 0, c.byref(bf), 0x02)
    err = c.windll.kernel32.GetLastError()
    gdi32.GdiFlush()
    c.windll.dwmapi.DwmFlush()
    msg = MSG()
    for _ in range(30):
        while user32.PeekMessageW(c.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(c.byref(msg))
            user32.DispatchMessageW(c.byref(msg))

    after = readback(x0, y0, n, 1)[0].astype(np.float32)[:, :3]
    # premultiplied source over destination:  out = src_premult + dst*(1 - a)
    a = alphas[:, None].astype(np.float32) / 255.0
    expected = src_premult + bg.astype(np.float32) * (1.0 - a)
    diff = np.abs(after - expected)

    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, scr)
    user32.DestroyWindow(hwnd)

    print(f"  ULW_ALPHA ok={bool(ok)} lastError={err}")
    print(f"  background under strip (BGRA)  : {bg[0].tolist()}")
    print(f"  alpha ramp, first 6             : {alphas[:6].tolist()} ... last {alphas[-1]}")
    print(f"  {'idx':>4} {'alpha':>6} {'expected BGRA':>22} {'actual BGRA':>22} {'maxdiff':>8}")
    for i in (0, 13, 26, 39, 50, 57, 63):
        print(f"  {i:>4} {int(alphas[i]):>6} "
              f"{str(expected[i].round().astype(int).tolist()):>22} "
              f"{str(after[i].tolist()):>22} {diff[i].max():>8.1f}")
    worst = int(diff.max(axis=1).argmax())
    print(f"  worst index {worst}: alpha={int(alphas[worst])} "
          f"expected={expected[worst].round().astype(int).tolist()} "
          f"actual={after[worst].tolist()}")
    good = ok and diff.max() <= 12
    print(f"  max |expected - actual|         : {diff.max():.1f}  (mean {diff.mean():.2f})")
    print(f"  => {'PER-PIXEL ALPHA VERIFIED' if good else 'ALPHA DID NOT BLEND AS EXPECTED'}")
    return good


def ab_repeat(rounds=4):
    """Interleaved A/B so the result cannot be an ordering artefact."""
    print(f"{'round':<8}{'pblend=NULL':<28}{'pblend=BLENDFUNCTION':<28}")
    print("-" * 64)
    results = []
    for i in range(rounds):
        a = run_alpha_case("null", pblend=False)[1:3]
        b = run_alpha_case("blend", pblend=True)[1:3]
        results.append((a, b))
        print(f"{i:<8}{a[0] + ' / ' + a[1]:<28}{b[0] + ' / ' + b[1]:<28}")
    return results


def run_case(label, exstyles, size, dst_dc, give_pt, flags, colorkey_first, show_first,
             alpha_mode="square", frame_changed=False):
    sw, sh = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    w, h = (sw, sh) if size == "full" else (512, 512)

    hwnd = user32.CreateWindowExW(exstyles, "UlwProbeCls", "UlwProbe", WS_POPUP,
                                  0, 0, w, h, None, None, INST, None)
    if not hwnd:
        return (label, "CreateWindowExW failed", "", "")

    if colorkey_first:
        user32.SetLayeredWindowAttributes(hwnd, 0x00FF00, 0, LWA_COLORKEY)
    if show_first:
        user32.ShowWindow(hwnd, SW_SHOWNA)
        user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    if frame_changed:
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    scr = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(scr)
    hbmp, buf = make_dib(w, h, scr)
    old = gdi32.SelectObject(mem, hbmp)

    if alpha_mode == "opaque":
        buf[:] = (255, 0, 255, 255)
    elif alpha_mode == "zero":
        buf[:] = 0
    elif alpha_mode == "premult":
        buf[:] = 0
        buf[100:100 + SQ, 100:100 + SQ] = (128, 0, 128, 128)   # premultiplied
    else:
        paint(buf)

    if dst_dc == "null":
        hdc_dst = None
    elif dst_dc == "wnd":
        hdc_dst = user32.GetDC(hwnd)
    else:
        hdc_dst = scr
    pt = wintypes.POINT(0, 0) if give_pt else None
    sz = wintypes.SIZE(w, h)

    c.windll.kernel32.SetLastError(0)
    ok = user32.UpdateLayeredWindow(hwnd, hdc_dst,
                                    c.byref(pt) if give_pt else None,
                                    c.byref(sz), mem,
                                    c.byref(wintypes.POINT(0, 0)),
                                    0, None, flags)
    err = c.windll.kernel32.GetLastError()
    gdi32.GdiFlush()
    user32.UpdateWindow(hwnd)

    vis, pix = "-", "-"
    if ok:
        # pump a moment for DWM to composite, then confirm on the real desktop
        msg = MSG()
        for _ in range(20):
            while user32.PeekMessageW(c.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(c.byref(msg))
                user32.DispatchMessageW(c.byref(msg))
        vis, pix = screen_has_square()

    errname = {0: "OK", 5: "ERROR_ACCESS_DENIED", 31: "ERROR_GEN_FAILURE",
               87: "ERROR_INVALID_PARAMETER", 1004: "ERROR_GEN_FAILURE"}.get(err, str(err))

    if dst_dc == "wnd":
        user32.ReleaseDC(hwnd, hdc_dst)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, scr)
    user32.DestroyWindow(hwnd)
    return (label, ("ok" if ok else "FAIL"), errname, pix if ok else "")


def main():
    print(f"screen = {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}")
    try:
        en = c.c_int(0)
        c.windll.dwmapi.DwmIsCompositionEnabled(c.byref(en))
        print(f"DWM composition = {bool(en.value)}")
    except Exception as e:
        print(f"DWM query failed: {e}")
    for k, v in session_facts().items():
        print(f"  {k} = {v}")

    hdr = f"{'case':<32} {'ULW':<5} {'lastError':<26} pixels on screen"
    print()
    print(hdr)
    print("-" * len(hdr))
    for label, pos, kw in CASES:
        res, err, pix = run_case(label, *pos, **kw)[1:]
        print(f"{label:<32} {res:<5} {err:<26} {pix}")
    print()

    print("ULW_ALPHA path only (what differs from the working flags=0 calls)")
    print("-" * 78)
    for label, kw in ALPHA_CASES:
        res, err, pix = run_alpha_case(label, **kw)[1:]
        print(f"{label:<32} {res:<5} {err:<26} {pix}")
    print()

    print("interleaved A/B: does pblend decide the ULW_ALPHA result?")
    print("-" * 64)
    ab_repeat(4)
    print()

    print("does ULW_ALPHA actually blend? (gradient proof)")
    print("-" * 64)
    blend_proof()
    print()

    print("DWM / graphics configuration")
    print("-" * 78)
    for k, v in graphics_facts().items():
        print(f"{k:<32} {v}")
    print()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "blend":
        blend_proof()
    else:
        main()
