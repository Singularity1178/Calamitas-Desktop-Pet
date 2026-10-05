"""Test for the AI mode choice (calamitas_pet_ai.py AI_FAITHFUL / AI_DESKTOP).

The reported problem: the opening bullet hell (BH1, SupremeCalamitas.cs:~1075-1115) offsets every
spawn off the *player*, ~1000 px in each direction. In game that is always offscreen because the
player roams an arena wider than a screen. On a desktop the player is the cursor, it is fixed, and
1000 px is about half a 1080p screen -- so shots materialise in the middle of the desktop, on the
far side of wherever the cursor happens to be parked.

The checks below:
  1. AI_FAITHFUL is the default and still reproduces that (shots appear on screen), so the port is
     untouched -- this test would notice if the faithful path had been quietly "fixed"
  2. AI_DESKTOP spawns every BH1 shot outside the screen, from every cursor position tested
  3. it spawns the same number of shots, in the same windows, with the same speeds and directions:
     the desktop mode moves spawn positions, nothing else
  4. the sweep that runs along the cursor's lane keeps that lane (only its x moved offscreen)
  5. the two mode answers survive the command line, and no console to ask on falls back to faithful
"""

import io
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calamitas_pet_ai as cp
import calamitas_variant as cv

W, H = 1920, 1080
E = cp._BH_EDGE
# a cursor parked on a corner, an edge, the middle, and the other two corners
CURSORS = [(0, 0), (W, 0), (0, H), (W, H), (W / 2, H / 2), (60, H - 40), (W - 60, 40)]


class SilentAudio:
    def __init__(self):
        self.played = []

    def play(self, name, volume=None):
        self.played.append(name)


def on_screen(p):
    """True if the projectile's sprite overlaps the visible screen."""
    f = p.frames[p.frame % len(p.frames)]
    h, w = f.shape[:2]
    return (p.x + w / 2 > 0 and p.x - w / 2 < W and
            p.y + h / 2 > 0 and p.y - h / 2 < H)


def entry_clearance(p):
    """How far outside the screen a shot waits, measured along the axis it flies along."""
    if abs(p.vx) >= abs(p.vy):                       # enters from a side
        return -p.x if p.vx > 0 else p.x - W
    return -p.y if p.vy > 0 else p.y - H              # enters from above or below


def stream(mode, cursor, ticks=900, t0=0, force=True):
    """Run BH1's spawn stream for `ticks` ticks and return every projectile it creates, in order.

    Only the spawn call is exercised: the projectile is not stepped, so what is collected is exactly
    the stream's cadence, counts, positions and velocities, with no flight or homing mixed in. `t0`
    starts the tick counter elsewhere, which is how the three source windows are sampled. `force`
    holds the gate open every tick (one spawn window per tick, so a short run still yields plenty of
    shots); pass force=False to let the real _BH_GATE tick, which is what the window counts want.
    """
    cal = cp.Calamitas(W, H, SilentAudio(), ai_mode=mode)
    cal._intro_done = True
    cal.attack = cp._BY_KEY["bullethell"]
    cal._atk_len = 10 ** 6
    cal.current = "02_Casting"
    born = []
    for e in range(t0, t0 + ticks):
        cal.cx, cal.cy = cursor
        if force:
            cal._bh_counter = cp._BH_GATE - 1
        n = len(cal.bullets)
        cal._bh1_stream(e)
        born.extend(cal.bullets[n:])
    return born


def test_faithful_is_the_default_and_still_spawns_inside():
    assert cp.AI_MODE == cp.AI_FAITHFUL, f"module default is {cp.AI_MODE!r}, expected faithful"
    cal = cp.Calamitas(W, H, SilentAudio())
    assert cal.ai_mode == cp.AI_FAITHFUL, f"default instance is {cal.ai_mode!r}"
    total = inside = 0
    for cursor in CURSORS:
        born = stream(cp.AI_FAITHFUL, cursor)
        bad = [p for p in born if on_screen(p)]
        total += len(born)
        inside += len(bad)
        print(f"  cursor {cursor[0]:7.0f},{cursor[1]:7.0f}: {len(bad):4d} of {len(born):4d} "
              f"spawn on screen  <- the reported problem, kept on purpose")
    assert total, "the faithful stream spawned nothing"
    assert inside > 0, "the faithful stream no longer spawns inside the screen -- " \
                       "AI_FAITHFUL must stay exactly as the source has it"
    return total


def test_desktop_always_spawns_offscreen():
    total = 0
    for cursor in CURSORS:
        born = stream(cp.AI_DESKTOP, cursor)
        assert born, f"no shots at cursor {cursor}"
        for p in born:
            assert not on_screen(p), \
                f"cursor {cursor}: shot spawned on screen at ({p.x:.0f}, {p.y:.0f})"
        # and it waits clear of the edge, so it slides in rather than popping into existence
        for p in born:
            clear = entry_clearance(p)
            assert clear >= E - 0.5, \
                f"cursor {cursor}: shot at ({p.x:.0f}, {p.y:.0f}) waits only {clear:.1f}px " \
                f"outside its edge, want {E:.0f}"
        total += len(born)
        print(f"  cursor {cursor[0]:7.0f},{cursor[1]:7.0f}: {len(born):4d} shots, all offscreen")
    return total


def test_desktop_only_moves_spawn_positions():
    """Same shots, same speeds, same rhythm -- only the spawn positions may differ.

    Index-by-index equality is not the right check: the two branches draw their randomness from the
    same generator differently (the source uses randint for its cross-axis jitter, the desktop branch
    uniform for its lanes), so which side a sweep comes from does not line up shot for shot. What
    must hold is the same multiset of projectiles and speeds, the same per-window counts, and that
    every desktop shot waits outside the edge it is aimed in from.
    """
    assert set(cp.AI_MODES) == {cp.AI_FAITHFUL, cp.AI_DESKTOP}
    cal = cp.Calamitas(W, H, SilentAudio())
    k = cal.k
    speeds = lambda shots: sorted(round(math.hypot(p.vx, p.vy) / k, 4) for p in shots)
    for cursor in CURSORS:
        a = stream(cp.AI_FAITHFUL, cursor)
        b = stream(cp.AI_DESKTOP, cursor)
        assert len(a) == len(b), f"cursor {cursor}: {len(b)} shots vs {len(a)} in the source"
        assert [p.name for p in a] == [p.name for p in b], f"cursor {cursor}: different projectiles"
        assert speeds(a) == speeds(b), f"cursor {cursor}: speed mix changed"
        for p in b:
            # a source shot is always axis-aligned (one of 4/3.5/3 px per tick, one axis)
            assert abs(p.vx) < 1e-9 or abs(p.vy) < 1e-9, f"({p.vx}, {p.vy}) is diagonal"
            # and every desktop shot is aimed inward from the edge it waits outside
            assert (p.vx > 0) == (p.x < W / 2) or abs(p.vx) < 1e-9, \
                f"shot at ({p.x:.0f}, {p.y:.0f}) moving at {p.vx:.2f} is not aimed inward"
            assert (p.vy > 0) == (p.y < H / 2) or abs(p.vy) < 1e-9, \
                f"shot at ({p.x:.0f}, {p.y:.0f}) moving at {p.vy:.2f} is not aimed inward"

    # the three source windows keep their own counts, so the rhythm of the attack is intact:
    # a spawn window every _BH_GATE (8) ticks, one extra sweep every 48 (BH_GATE * 6), and 1 / 2 / 3
    # shots per window for ticks 1-300 / 301-600 / 601-900 (SupremeCalamitas.cs:~1090-1113). One
    # continuous 900-tick stream, bucketed, because the source only opens a window on c2 % 8 == 0 --
    # so the sweep at c2 % 48 == 0 only lands if the stream is aligned the way the real fight is.
    want = (43, 83, 117)
    got = {}
    for mode in cp.AI_MODES:
        cal = cp.Calamitas(W, H, SilentAudio(), ai_mode=mode)
        cal._intro_done = True
        cal.attack, cal._atk_len = cp._BY_KEY["bullethell"], 10 ** 6
        cal.current = "02_Casting"
        counts = [0, 0, 0]
        for e in range(900):
            cal.cx, cal.cy = W / 2, H / 2
            n = len(cal.bullets)
            cal._bh1_stream(e)
            counts[0 if e < 300 else 1 if e < 600 else 2] += len(cal.bullets) - n
        got[mode] = tuple(counts)
        assert got[mode] == want, f"{mode}: window counts {got[mode]}, expected {want}"
    print(f"  same shots, same speed mix, every shot axis-aligned and aimed inward; "
          f"per-window counts {got[cp.AI_FAITHFUL]} in both modes")


def test_sweep_keeps_the_cursor_lane():
    """The one shot that shares an axis with the cursor keeps it -- only its x moved offscreen.

    The sweep is the extra horizontal blast on `c2 % 48 == 0` (4 px/tick); the other horizontal
    shots are the 3.5 px/tick side pair, whose rows are jittered individually, as in the source.
    """
    k = cp.Calamitas(W, H, SilentAudio()).k
    sweep_vx = 4.0 * k
    for cursor in ((0, 123), (W, 900), (W / 2, H / 2)):
        sweeps = [p for p in stream(cp.AI_DESKTOP, cursor)
                  if abs(p.vy) < 1e-9 and abs(abs(p.vx) - sweep_vx) < 1e-6]
        assert sweeps, f"no horizontal sweep at cursor {cursor}"
        for p in sweeps:
            assert abs(p.y - cursor[1]) < 1e-6, f"sweep left the cursor's lane: {p.y} vs {cursor[1]}"
            assert not on_screen(p), f"sweep spawned on screen at ({p.x:.0f}, {p.y:.0f})"
        sides = sorted({-1 if p.x < 0 else 1 for p in sweeps})
        assert sides == [-1, 1], f"sweeps only ever came from one side: {sides}"
        print(f"  cursor y={cursor[1]:6.0f}: {len(sweeps):2d} sweeps, all on that row, "
              f"entering from both sides")


def test_shots_are_not_mirrored():
    """Shots that leave together must not share a cross-axis position.

    Regression: the first desktop version drew one jitter per window and fired the whole window
    along it, so the left and right pair came in as a mirror image of itself -- two shots in a
    perfect line, which reads as one shot sliding sideways rather than as a barrage. The source
    jitters every shot separately (`Main.rand.Next(-1000, 1001)` is inside each spawn call), so both
    modes have to keep them apart; see the coincidence allowance in the check itself.
    """
    for mode in cp.AI_MODES:
        pairs = coincident = 0
        smallest = None
        for cursor in CURSORS:
            for t0 in (0, 300, 600):       # all three source windows: 1, 2 and 3 shots a window
                cal = cp.Calamitas(W, H, SilentAudio(), ai_mode=mode)
                cal._intro_done = True
                cal.attack, cal._atk_len = cp._BY_KEY["bullethell"], 10 ** 6
                cal.current = "02_Casting"
                for e in range(t0, t0 + 300):
                    cal.cx, cal.cy = cursor
                    n = len(cal.bullets)
                    cal._bh1_stream(e)
                    shots = cal.bullets[n:]
                    # the shots of one window are the same kind, all axis-aligned, so compare the
                    # cross axis of each against every other one in the window
                    for i, a in enumerate(shots):
                        for b in shots[i + 1:]:
                            if abs(a.vx) < 1e-9:      # both from above: compare columns
                                gap = abs(a.x - b.x)
                            else:                     # side shots: compare rows
                                gap = abs(a.y - b.y)
                                pairs += 1
                            if gap < 1.0:
                                coincident += 1
                            smallest = gap if smallest is None else min(smallest, gap)
        assert pairs, f"{mode}: no side pairs found to check"
        # Independent jitter can coincide now and then -- the source's is an integer draw over 2001
        # values, so ~1 in 2000 pairs lands on the same row by luck. One shared jitter per window
        # would make every single pair coincide.
        assert coincident * 100 <= pairs, \
            f"{mode}: {coincident} of {pairs} pairs share a line -- the jitter is not per shot"
        print(f"  {mode:9s}: {pairs} shots fired in pairs, {coincident} coincident by luck "
              f"({coincident / pairs:.2%}), closest pair {smallest:.0f} px apart")


def test_fight_runs_in_both_modes():
    """Both modes play a whole fight: every attack fires, and nothing raises."""
    for mode in cp.AI_MODES:
        random.seed(11)
        cal = cp.Calamitas(W, H, SilentAudio(), ai_mode=mode)
        seen, peak = set(), 0
        for step in range(60 * 90):
            cursor = (W * 0.5 + math.cos(step * 0.01) * W * 0.75,
                      H * 0.5 + math.sin(step * 0.017) * H * 0.75)
            cal.update(1 / 60.0, cursor)
            if cal.attack:
                seen.add(cal.attack["key"])
            peak = max(peak, len(cal.bullets))
        want = {"bullethell", "barrage", "fireblast", "wave", "hellblast", "gigablast", "bash"}
        missing = want - seen
        assert not missing, f"{mode}: never fired {sorted(missing)}"
        assert peak > 0, f"{mode}: no projectiles at all"
        print(f"  {mode:9s}: {len(seen)} attacks, peak {peak} projectiles")


def test_mode_flags():
    """Both answers are reachable from the command line, and the no-console default is faithful."""
    def ask(argv):
        out = io.StringIO()
        real, cv._interactive = cv._interactive, lambda: False
        try:
            return out, cv.choose_ai(argv, out)
        finally:
            cv._interactive = real

    for argv, want in (([], cv.AI_DEFAULT),
                       (["--ai", "faithful"], "faithful"),
                       (["--ai", "desktop"], "desktop"),
                       (["--ai=desktop"], "desktop"),
                       (["--ai", "mod"], "faithful"),
                       (["--desktop"], "desktop"),
                       (["--variant", "unhooded"], cv.AI_DEFAULT),
                       (["--variant", "unhooded", "--ai", "desktop"], "desktop")):
        out, got = ask(argv)
        assert got == want, f"{argv} -> {got!r}, expected {want!r}"
    for bad in (["--ai", "nonsense"], ["--ai=nonsense"]):
        try:
            ask(bad)
        except SystemExit as exc:
            assert "nonsense" in str(exc), str(exc)
        else:
            raise AssertionError(f"{bad} should have been rejected")
    print("  --ai faithful|desktop / --mod / --desktop all resolve; no console -> faithful")


def main():
    print("AI_FAITHFUL (the source, unchanged):")
    total = test_faithful_is_the_default_and_still_spawns_inside()
    print("AI_DESKTOP (BH1 enters from offscreen):")
    same = test_desktop_always_spawns_offscreen()
    print(f"  {same} shots checked, none on screen ({total} in the same stream, faithful)")
    print("unchanged apart from spawn positions:")
    test_desktop_only_moves_spawn_positions()
    test_sweep_keeps_the_cursor_lane()
    test_shots_are_not_mirrored()
    print("integration:")
    test_fight_runs_in_both_modes()
    test_mode_flags()
    print("\nall AI mode checks passed")


if __name__ == "__main__":
    main()
