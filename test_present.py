"""Check that UpdateLayeredWindow actually succeeds at runtime.

If present() silently returns 0 every frame, the simulation runs but nothing
ever reaches the screen -- which looks identical from the console.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# CALAMITAS_MODULE selects which implementation to test (default: original pet)
_MOD = os.environ.get("CALAMITAS_MODULE", "calamitas_pet")
cp = __import__(_MOD)


def main():
    win = cp.LayeredWindow()
    d = win.diagnostics()
    for k, v in d.items():
        print(f"  {k} = {v}")

    cal = cp.Calamitas(win.w, win.h, cp.Audio())
    ok = bad = 0
    t0 = time.time()
    while time.time() - t0 < 3.0:
        win.clear()
        cal.update(1 / 60.0, (900, 500))
        cal.draw(win.buf)
        if win.present():
            ok += 1
        else:
            bad += 1
    print(f"presents (UpdateLayeredWindow): ok={ok} fail={bad} lastError={win._ulw_err}")
    if bad:
        print("FAIL: frames were not presented to the screen")
    else:
        print("OK: every frame was presented")
    win.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())