"""Regression test for the VFX stamp/draw path (calamitas_vfx.py): PIL's resize(box=...) validates the source
rectangle, and the visible-canvas-rect mapping used for the big unrotated draws (blooms, pulse rings) could hand
it a rectangle that starts outside the texture.

Two shapes of input used to raise ValueError and disable the whole VFX layer for the session:
  * box offset negative            -- the quad's left/top edge is inside the canvas at a fractional pixel
                                     (ax0 - x0 lands in (-1, 0] after floor)
  * box can't exceed original size -- the quad is clipped by an edge, so ceil pushes ax1 - x0 past w
Both are now clamped into the texture. The checks below are:
  1. the inputs that used to raise no longer raise, through the real PulseRing/Bloom draw calls
  2. every draw the old code *could* render is bit-identical now, so nothing that worked changed
  3. positions/sizes around every canvas edge and corner survive, paint the overlap and no more
  4. the mapping still lands where it should (vs a 4x supersampled reference of the same quad)
  5. a run of the real fight keeps the layer enabled and draws the rings
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calamitas_vfx as V

W, H = 640, 360
_M = np.array([0.0, 0.0, 1.0], np.float32)          # additive multiplier, BGR order -> the R channel


def blank(h=H, w=W):
    """A fully transparent premultiplied BGRA canvas."""
    return np.zeros((h, w, 4), dtype=np.uint8)


def _add_scaled_pre_fix(self, canvas, tex, cx, cy, w, h, m):
    """The pre-fix body of VFX._add_scaled, kept here to prove what the bug was."""
    Hc, Wc = canvas.shape[:2]
    x0, y0 = cx - w / 2.0, cy - h / 2.0
    ax0, ay0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
    ax1, ay1 = min(Wc, int(math.ceil(x0 + w))), min(Hc, int(math.ceil(y0 + h)))
    if ax0 >= ax1 or ay0 >= ay1:
        return
    tw, th = tex.size
    box = ((ax0 - x0) / w * tw, (ay0 - y0) / h * th, (ax1 - x0) / w * tw, (ay1 - y0) / h * th)
    im = tex.resize((ax1 - ax0, ay1 - ay0), V._BIL, box=box)
    V._blend_add(canvas, np.asarray(im, np.float32)[..., ::-1], ax0, ay0, m)


def test_box_was_rejected():
    """The reported crash and its sibling must both be gone, through the particle draw calls that hit them."""
    fx = V.VFX(1.5)
    assert fx.ok, f"textures missing: {fx.notes}"

    # PulseRing mid-screen: the traceback in the report -- a small ring early in its life, left edge on a
    # fractional pixel. Bloom() takes the same unrotated big path.
    cases = [("PulseRing", "ring", 320.3, 180.7, 94.0, 94.0),
             ("PulseRing", "ring", 40.2, 30.4, 120.0, 90.0),
             ("Bloom", "bloom", -30.7, 20.3, 200.0, 200.0),
             ("Bloom", "bloom", 632.4, 352.6, 180.0, 180.0)]
    for label, key, cx, cy, w, h in cases:
        try:
            _add_scaled_pre_fix(fx, blank(), fx.tex[key], cx, cy, w, h, _M)
            old = "ok"
        except ValueError as exc:
            old = f"{type(exc).__name__}: {exc}"
        tex = fx.tex[key]
        new = blank()
        fx._add_scaled(new, tex, cx, cy, w, h, _M)
        assert new.any(), f"{label} at ({cx}, {cy}) drew nothing"
        print(f"  {label:10s} ({cx:6.1f},{cy:6.1f})  pre-fix: {old:48s} now: ok")

    # ... and through the real particle path: rings and blooms, stepped over their whole lifetime, at a position
    # that used to raise on the first tick.
    for cls in (V.PulseRing, V.Bloom):
        p = cls(320.3, 180.7, V.RED, (1.0, 0.6), 0.0, 0.1, 5.0, 15) if cls is V.PulseRing \
            else cls(632.4, 180.7, V.RED, 0.1, 0.85, 30)
        canvas = blank()
        for _ in range(p.life + 2):
            p.update()
            p.draw(fx, canvas)
            canvas[:] = 0
        assert fx.ok, f"{cls.__name__}.draw disabled the VFX layer:\n{fx.error}"
    print("  PulseRing/Bloom particle draw over a full lifetime: OK (layer still enabled)")


def test_previously_valid_draws_are_unchanged():
    """The clamp must only affect draws that used to raise: everything else must come out bit-identical."""
    fx = V.VFX(1.5)
    same = 0
    for key in ("ring", "bloom"):
        tex = fx.tex[key]
        tw, th = tex.size
        for cx in np.arange(-900.0, 1200.0, 17.0):
            for cy in (-800.3, -0.5, 0.0, 0.5, 180.5, 359.5, 360.0, 800.7):
                for w in (60.0, 141.0, 333.0, 1700.0):
                    h = w * 0.6
                    x0, y0 = cx - w / 2.0, cy - h / 2.0
                    bx0 = (max(0, int(math.floor(x0))) - x0) / w * tw
                    by0 = (max(0, int(math.floor(y0))) - y0) / h * th
                    bx1 = (min(W, int(math.ceil(x0 + w))) - x0) / w * tw
                    by1 = (min(H, int(math.ceil(y0 + h))) - y0) / h * th
                    if not (0.0 <= bx0 < bx1 <= tw and 0.0 <= by0 < by1 <= th):
                        continue                      # exactly the inputs the pre-fix code raised on
                    new, old = blank(), blank()
                    fx._add_scaled(new, tex, cx, cy, w, h, _M)
                    _add_scaled_pre_fix(fx, old, tex, cx, cy, w, h, _M)
                    assert np.array_equal(new, old), \
                        f"draw changed at ({cx:.1f}, {cy:.1f}) {w:.0f}x{h:.0f} for {key}"
                    same += 1
    assert same > 500, f"only {same} previously-valid cases exercised -- loosen the grid"
    print(f"  {same} previously-valid draws are bit-identical to the pre-fix output")


def test_stamp_survives_every_position():
    """Every texture at every edge position/size, rotated and unrotated, through the public stamp()."""
    fx = V.VFX(1.5)
    rng = np.random.default_rng(4)
    n = 0

    def stamp(key, pw, ph, cx, cy, rot):
        tw, th = fx.tex[key].size                     # stamp()'s sx/sy are texture scales
        fx.stamp(blank(), key, cx, cy, pw / tw / fx.k, ph / th / fx.k, rot, (255, 40, 60, 255))
        nonlocal n
        n += 1

    for key in fx.tex:
        for px in (0.5, 0.8, 1.0, 96.0, 97.0, 156.0, 1600.0, 4000.0):
            for cx in (-4000.0, -0.5, 0.0, 0.5, 320.0, 320.5, W - 0.5, W, W + 0.5, 4000.0):
                for rot in (0.0, 0.05, 1.0, 3.14):
                    stamp(key, px, px, cx, 180.3, rot)
                    stamp(key, px, px, 320.3, cx, rot)
    for key in fx.tex:                                # plus random on-screen sizes
        for _ in range(60):
            w = float(rng.uniform(1.0, 1600.0))
            stamp(key, w, w * float(rng.uniform(0.05, 1.0)),
                  float(rng.uniform(-400, W + 400)), float(rng.uniform(-400, H + 400)),
                  0.0 if rng.random() < 0.5 else float(rng.uniform(-3.2, 3.2)))
    assert fx.ok, f"stamp() disabled the VFX layer:\n{fx.error}"
    print(f"  {n} stamps across {len(fx.tex)} textures: no exception, layer still enabled")


def test_clipping():
    """A clipped draw covers its visible part of the quad and paints nothing outside it; fully off-canvas
    draws paint nothing at all."""
    fx = V.VFX(1.5)
    tex = fx.tex["bloom"]
    cases = [(0.0, 0.0, 500.0, 500.0), (W, 0.0, 500.0, 500.0), (0.0, H, 500.0, 500.0), (W, H, 500.0, 500.0),
             (-3.0, H / 2, 500.0, 400.0), (W + 3.0, H / 2, 500.0, 400.0), (W / 2, -3.0, 400.0, 500.0),
             (W / 2, H + 3.0, 400.0, 500.0), (-0.5, -0.5, 500.0, 500.0), (W + 0.5, H + 0.5, 500.0, 500.0),
             (0.0, H / 2, 640.0, 360.0), (W, H / 2, 640.0, 360.0), (W / 2, 0.0, 640.0, 360.0)]
    for cx, cy, w, h in cases:
        c = blank()
        fx._add_scaled(c, tex, cx, cy, w, h, _M)
        x0, y0, x1, y1 = cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0
        ax0, ay0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
        ax1, ay1 = min(W, int(math.ceil(x1))), min(H, int(math.ceil(y1)))
        assert ax0 < ax1 and ay0 < ay1, f"test case ({cx}, {cy}) is off-canvas, use the off-canvas check"
        assert c[ay0:ay1, ax0:ax1].any(), f"visible part of the quad at ({cx}, {cy}) was not painted"
        outside = c.copy()
        outside[ay0:ay1, ax0:ax1] = 0
        assert not outside.any(), f"draw at ({cx}, {cy}) painted outside the quad"
    for cx, cy in ((-5000.0, 0.0), (5000.0, 0.0), (0.0, -5000.0), (0.0, 5000.0)):
        c = blank()
        fx._add_scaled(c, fx.tex["ring"], cx, cy, 300.0, 300.0, _M)
        assert not c.any(), f"off-canvas draw at ({cx}, {cy}) painted something"
    print(f"  {len(cases)} clipped quads: visible part covered, nothing outside, off-canvas draws are no-ops")


def test_geometry():
    """Ground truth is the same quad rendered 4x and box-downsampled. A hard-edged ring swings 0..255 within a
    pixel, so a 1x render matches it only up to sub-pixel rim aliasing -- compare centroid, energy and the share
    of pixels that are off by more than 24/255."""
    fx = V.VFX(1.5)
    tex = fx.tex["ring"]
    SS = 4
    for cx, cy, sc in ((320.3, 180.7, 0.9), (120.0, 90.0, 2.0), (600.5, 40.25, 3.0), (300.0, 300.0, 0.62)):
        w = h = tex.size[0] * sc
        a = blank()
        fx._add_scaled(a, tex, cx, cy, w, h, _M)
        big = blank(H * SS, W * SS)
        fx._add_scaled(big, tex, cx * SS, cy * SS, w * SS, h * SS, _M)
        truth = big.reshape(H, SS, W, SS, 4).mean(axis=(1, 3))

        def centroid(c):
            s = c[:, :, 2].astype(np.float64)
            tot = s.sum()
            ys, xs = np.mgrid[0:c.shape[0], 0:c.shape[1]]
            return (xs * s).sum() / tot, (ys * s).sum() / tot, tot

        cax, cay, ea = centroid(a)
        cbx, cby, eb = centroid(truth)
        dcen = max(abs(cax - cbx), abs(cay - cby))
        dene = abs(ea - eb) / eb
        dfrac = (np.abs(a.astype(np.float32) - truth)[:, :, 2] > 24).mean()
        assert a[:, :, 3].any(), "nothing was drawn"
        assert dcen < 0.75, f"({cx}, {cy}) centroid off by {dcen:.2f}px -- wrong position"
        # a hard-edged ring aliases ~2% of its energy under a big BILINEAR downscale (0.62/0.9 are downscales
        # of the 156px source; at 2.0/3.0, i.e. upscales, the ratio is within 0.2%)
        assert dene < 0.03, f"({cx}, {cy}) energy off by {dene:.1%} -- wrong scale/mapping"
        assert dfrac < 0.005, f"({cx}, {cy}) {dfrac:.2%} of pixels differ from the reference -- wrong shape"
    print("  position, scale and shape match the 4x reference to sub-pixel accuracy (4 quads): OK")


def test_fight_run():
    """The real fight, every frame drawn: the layer must stay enabled, and the ring draw must be reached."""
    cp = __import__(os.environ.get("CALAMITAS_MODULE", "calamitas_pet_ai"))

    class SilentAudio:
        def play(self, name, volume=None):
            pass

    cal = cp.Calamitas(W, H, SilentAudio())
    canvas = blank()
    counts = {"big": 0, "ring": 0}
    real_add, real_stamp = V.VFX._add_scaled, V.VFX.stamp

    def add_scaled(self, c, tex, cx, cy, w, h, m):
        counts["big"] += 1
        return real_add(self, c, tex, cx, cy, w, h, m)

    def stamp(self, c, key, x, y, sx, sy, rot, color, alpha_factor=True):
        counts["ring"] += key == "ring"
        return real_stamp(self, c, key, x, y, sx, sy, rot, color, alpha_factor)

    V.VFX._add_scaled, V.VFX.stamp = add_scaled, stamp
    try:
        for step in range(60 * 60):
            cursor = (W * 0.5 + math.cos(step * 0.004) * W * 0.8, H * 0.5 + math.sin(step * 0.009) * H * 0.8)
            cal.update(1 / 60.0, cursor)
            canvas[:] = 0
            cal.draw(canvas)
            fx = getattr(cal, "_vfx", None)
            assert fx is None or fx.ok, f"VFX disabled at step {step}:\n{fx.error}"

        # the pulse rings of the bullethell cast, at the pet's own position, stepped to completion
        fx = cal._vfx
        for _ in range(20):
            fx._bh1_end(cal)
            fx.tick(cal)
            canvas[:] = 0
            fx.draw(canvas)
            assert fx.ok, f"VFX disabled while drawing the bullethell rings:\n{fx.error}"
    finally:
        V.VFX._add_scaled, V.VFX.stamp = real_add, real_stamp

    assert cal._vfx.ok and cal._vfx.error is None, f"VFX disabled:\n{cal._vfx.error}"
    assert counts["ring"] > 0, "no pulse ring was ever drawn -- the path that used to crash was not reached"
    print(f"  60s of fight + the bullethell rings: layer still enabled "
          f"({counts['ring']} ring stamps, {counts['big']} big unrotated draws)")


def main():
    print("box validation:")
    test_box_was_rejected()
    print("unchanged behaviour:")
    test_previously_valid_draws_are_unchanged()
    print("robustness:")
    test_stamp_survives_every_position()
    test_clipping()
    test_geometry()
    print("integration:")
    test_fight_run()
    print("\nall VFX stamp checks passed")


if __name__ == "__main__":
    main()
