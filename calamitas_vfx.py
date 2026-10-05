#!/usr/bin/env python3
"""calamitas_vfx.py -- Supreme Calamitas' pre-brothers projectile + casting VFX as numpy/PIL per-pixel alpha.

PROVENANCE NOTES (same convention as calamitas_forcefield.py: the file carries its own write-up)
* Sources read, CalamityModPublic branch 1.4.4, commit 1a8cebd2:
    Particles/Particle.cs, SparkParticle.cs, PointParticle.cs, GlowOrbParticle.cs, BloomParticle.cs,
    DirectionalPulseRing.cs; Projectiles/Boss/BrimstoneBarrage.cs, BrimstoneHellblast.cs, BrimstoneHellblast2.cs,
    SCalBrimstoneFireblast.cs, SCalBrimstoneGigablast.cs, BrimstoneWave.cs; Utilities/DrawingUtils.cs:97
    (DrawAfterimagesCentered); NPCs/SupremeCalamitas/SupremeCalamitas.cs. `SCal.cs:N` line numbers are EXACT
    for that commit. Projectile files are cited by identifier.
* This module is VISUAL ONLY. It reads Calamitas/Projectile state and never writes it, and it uses its own
  random.Random, so the fight's RNG stream is unchanged.
* Particle handler convention (Particle.cs doc: "velocity gets automatically added to its position, and its time
  automatically increases"): per tick spawn -> pos += vel; time += 1; update() -> drop when time >= Lifetime.
  GeneralParticleHandler.cs itself was not read; the ParticleLimit default of 500 is assumed.
* Blending: every particle here has UseAdditiveBlend. tModLoader premultiplies textures on load and
  BlendState.Additive is (SourceAlpha, One), so a texel adds tex.rgb * tex.a^2 * col.rgb * col.a. XNA `Color * f`
  scales all four channels and `Color.Lerp(c, Transparent, t)` fades A too; both are kept, so fades are quadratic
  exactly as in-game.
* Vanilla dust textures are not available: dust is a procedural 8x8 blob drawn additively (torch-type dusts have
  GetAlpha alpha 0, which under alpha blend IS additive). Dust decay (vel*0.92, scale-0.05/tick) is an
  approximation of vanilla's noGravity torch-dust update, written from memory: SUBSTITUTE.
* Lighting.AddLight and the lightColor G/B world-light channels have no equivalent: dropped.
* Projectile state the pet does not expose (Opacity, timeLeft, withinRange) is mirrored from each projectile's
  own AI formulas using this module's age counter + the cursor distance (Player.Center == cursor).
"""
import math
import random
import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
VFX_DIR = HERE / "assets" / "vfx"

# ----------------------------------------------------------------------------------------------
# Presentation knobs (all invented -- presentation / performance only)
# ----------------------------------------------------------------------------------------------
TRUE_ADDITIVE = False   # True: add light without raising alpha (rgb > a). False: alpha = max(alpha, brightest channel)
PARTICLE_LIMIT = 500    # assumed CalamityConfig ParticleLimit default; Important particles bypass it
DUST_LIMIT = 1500       # perf cap (vanilla Main.maxDust is 6000)
_ANGLE_BINS = 64        # rotation quantisation of cached stamps
_STAMP_CACHE = 4096     # cached stamp count before the cache is flushed
_BIG_PX = 96 * 96       # unrotated draws larger than this are resized per frame, visible part only
_RNG_SEED = 0x5CA1
_HAND_SIGN = 1.0        # flip to -1.0 if the hand bursts come out of the wrong side (see handPosition note)

_RES = getattr(Image, "Resampling", Image)
_BIL = _RES.BILINEAR


# ----------------------------------------------------------------------------------------------
# XNA Color helpers (byte RGBA tuples)
# ----------------------------------------------------------------------------------------------
def _lerp_col(c1, c2, t):
    """Color.Lerp -- per-channel, truncated to byte."""
    return tuple(max(0, min(255, int(c1[i] + (c2[i] - c1[i]) * t))) for i in range(4))


def _mul_col(c, f):
    """Color * float -- scales all four channels (including A), clamped to [0, 255]."""
    return tuple(max(0, min(255, int(c[i] * f))) for i in range(4))


RED = (255, 0, 0, 255)                     # Color.Red
MAGENTA = (255, 0, 255, 255)               # Color.Magenta
WHITE = (255, 255, 255, 255)               # Color.White
TRANSPARENT = (0, 0, 0, 0)                 # Color.Transparent
RED_MAG_05 = _lerp_col(RED, MAGENTA, 0.5)  # Color.Lerp(Color.Red, Color.Magenta, 0.5f)
RED_MAG_03 = _lerp_col(RED, MAGENTA, 0.3)  # Color.Lerp(Color.Red, Color.Magenta, 0.3f)
BLOOM_WINE = (121, 21, 77, 255)            # Fireblast/Gigablast `new Color(121, 21, 77)`

# Dust colours: SUBSTITUTE (vanilla / Calamity dust textures unavailable). Alpha 255 = full additive weight.
_DUST_COL = {
    60: (255, 40, 40, 255),          # vanilla dust 60 (red torch-style)
    114: (235, 60, 215, 255),        # vanilla dust 114 (guessed magenta/pink -- pairs with Lerp(Red, Magenta))
    "destroyer": (255, 30, 30, 255),  # DustID.TheDestroyer, source also sets dust.color = Color.Red
    "brimstone": (255, 85, 45, 255),  # CalamityDusts.Brimstone (guessed orange-red)
}

# Projectile constants mirrored from the projectile sources (timeLeft in SetDefaults, initial Opacity)
_LIFE = {"BrimstoneBarrage": 690, "BrimstoneHellblast": 255, "SCalBrimstoneFireblast": 150,
         "SCalBrimstoneGigablast": 120, "BrimstoneWave": 1200}
_OP0 = {"BrimstoneBarrage": 1.0, "BrimstoneHellblast": 1.0, "SCalBrimstoneFireblast": 0.0,
        "SCalBrimstoneGigablast": 0.0, "BrimstoneWave": 0.0}
# ProjectileID.Sets.TrailCacheLength[Type] = 2 + DrawAfterimagesCentered(..., TrailingMode 0)
_AFTERIMAGE = {"BrimstoneBarrage", "BrimstoneHellblast"}


# ----------------------------------------------------------------------------------------------
# Textures
# ----------------------------------------------------------------------------------------------
def _intensity_image(rgba):
    """Straight RGBA -> additive contribution per texel: rgb * a (premultiplied on load) * a (SourceAlpha)."""
    a = np.asarray(rgba.convert("RGBA"), np.float32) / 255.0
    inten = a[..., :3] * (a[..., 3:4] ** 2)
    return Image.fromarray((inten * 255.0 + 0.5).astype(np.uint8), "RGB")


def _dust_texture():
    """SUBSTITUTE for vanilla dust frames (8x8): an opaque blob with a soft rim. Dust is drawn with alpha-0
    colour, i.e. additive with a single texture-alpha factor, so the stamp is just rgb."""
    rows = ["..####..", ".#OOOO#.", "#OOOOOO#", "#OOOOOO#", "#OOOOOO#", "#OOOOOO#", ".#OOOO#.", "..####.."]
    lut = {".": 0.0, "#": 0.45, "O": 1.0}
    a = np.array([[lut[ch] for ch in r] for r in rows], np.float32)
    rgb = np.repeat(a[..., None], 3, axis=2)
    return Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8), "RGB")


def _load_textures():
    tex, notes = {}, []

    def load(name):
        p = VFX_DIR / name
        if not p.exists():
            return None
        return Image.open(p)

    for key, name in (("spark", "StarProj.png"),           # SparkParticle.Texture  = Projectiles/StarProj
                      ("bloom", "BloomCircle.png"),        # BloomParticle.Texture  = Particles/BloomCircle
                      ("ring", "HollowCircleHardEdge.png")):  # DirectionalPulseRing   = Particles/HollowCircleHardEdge
        im = load(name)
        if im is None:
            notes.append(f"missing assets/vfx/{name}")
        else:
            tex[key] = _intensity_image(im)
    im = load("PointParticle.png")                         # PointParticle.Texture = Particles/PointParticle
    if im is not None:
        tex["point"] = _intensity_image(im)
    elif "spark" in tex:
        tex["point"] = tex["spark"]                        # fallback: identical draw code, different texture
        notes.append("PointParticle.png missing -> StarProj.png stand-in")
    im = load("GlowOrbParticle.png")                       # GlowOrbParticle.Texture = Particles/GlowOrbParticle
    if im is not None:
        tex["glow"] = _intensity_image(im)
    elif "bloom" in tex:
        tex["glow"] = tex["bloom"].resize((32, 32), _BIL)  # fallback: 32px is INVENTED
        notes.append("GlowOrbParticle.png missing -> BloomCircle.png @32px stand-in")
    tex["dust"] = _dust_texture()
    return tex, notes


# ----------------------------------------------------------------------------------------------
# Compositing into the premultiplied BGRA canvas
# ----------------------------------------------------------------------------------------------
def _blend_add(canvas, src, x0, y0, m):
    """Additive: canvas.rgb += src(BGR intensity 0..255) * m."""
    H, W = canvas.shape[:2]
    h, w = src.shape[:2]
    ax0, ay0 = max(0, x0), max(0, y0)
    ax1, ay1 = min(W, x0 + w), min(H, y0 + h)
    if ax0 >= ax1 or ay0 >= ay1:
        return
    s = src[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]
    dst = canvas[ay0:ay1, ax0:ax1]
    rgb = dst[..., :3] + s * m
    np.minimum(rgb, 255.0, out=rgb)
    rgb8 = (rgb + 0.5).astype(np.uint8)
    dst[..., :3] = rgb8
    if not TRUE_ADDITIVE:
        np.maximum(dst[..., 3], rgb8.max(axis=2), out=dst[..., 3])


def _blend_over(canvas, src, x0, y0, f):
    """Premultiplied source-over with a global factor f (XNA `color * interpolant` under AlphaBlend)."""
    H, W = canvas.shape[:2]
    h, w = src.shape[:2]
    ax0, ay0 = max(0, x0), max(0, y0)
    ax1, ay1 = min(W, x0 + w), min(H, y0 + h)
    if ax0 >= ax1 or ay0 >= ay1:
        return
    s = src[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]
    if not s[..., 3].any():
        return
    s = s.astype(np.float32) * f
    dst = canvas[ay0:ay1, ax0:ax1]
    out = s + dst.astype(np.float32) * (1.0 - s[..., 3:4] / 255.0)
    dst[...] = np.clip(out + 0.5, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------------------------------
# Particles (Particles/*.cs)
# ----------------------------------------------------------------------------------------------
class _Particle:
    important = False

    def __init__(self, x, y, vx, vy, life, scale, color):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life, self.time, self.scale = int(life), 0, scale
        self.color = color
        self.rot = 0.0

    def completion(self):
        """Particle.LifetimeCompletion."""
        return self.time / self.life if self.life else 0.0


class Spark(_Particle):
    """SparkParticle.cs (FadeIn=false, AffectedByLight=false path)."""
    tex = "spark"

    def __init__(self, x, y, vx, vy, life, scale, color, k, gravity=False):
        super().__init__(x, y, vx, vy, life, scale, color)
        self.init, self.k, self.gravity = color, k, gravity

    def update(self):
        self.scale *= 0.95
        self.color = _lerp_col(self.init, TRANSPARENT, self.completion() ** 3)
        self.vx *= 0.95
        self.vy *= 0.95
        if self.gravity and math.hypot(self.vx, self.vy) < 12.0 * self.k:
            self.vx *= 0.94
            self.vy += 0.25 * self.k
        self.rot = math.atan2(self.vy, self.vx) + math.pi / 2

    def draw(self, fx, canvas):
        s = self.scale   # scale = Vector2(0.5, 1.6) * Scale, then a second pass * Vector2(0.45, 1)
        fx.stamp(canvas, self.tex, self.x, self.y, 0.5 * s, 1.6 * s, self.rot, self.color)
        fx.stamp(canvas, self.tex, self.x, self.y, 0.225 * s, 1.6 * s, self.rot, self.color)


class Point(Spark):
    """PointParticle.cs -- Update and CustomDraw are line-for-line SparkParticle's; only the texture differs.
    UseAltVisual (additive) defaults to true."""
    tex = "point"


class GlowOrb(_Particle):
    """GlowOrbParticle.cs."""

    def __init__(self, x, y, vx, vy, life, scale, color, k, important=False, glow_center=True):
        super().__init__(x, y, vx, vy, life, scale, color)
        self.init, self.k = color, k
        self.important, self.glow_center = important, glow_center
        self.fade = 1.0

    def update(self):
        self.fade -= 0.1
        self.scale *= 0.93
        self.color = _lerp_col(self.init, _mul_col(self.init, 0.2), self.completion() ** 3)
        self.vx *= 0.95
        self.vy *= 0.95
        self.rot = math.atan2(self.vy, self.vx) + math.pi / 2

    def draw(self, fx, canvas):
        s = self.scale
        fx.stamp(canvas, "glow", self.x, self.y, s, s, self.rot, self.color)
        if self.glow_center and self.fade > 0.0:   # Color.White * fadeOut (clamps to 0 once fadeOut < 0)
            fx.stamp(canvas, "glow", self.x, self.y, 0.5 * s, 0.5 * s, self.rot, _mul_col(WHITE, self.fade))


def _poly_out4(t):
    """PiecewiseAnimation(t, CurveSegment(EasingType.PolyOut, 0, 0, 1, 4)) = 1 - (1 - t)^4."""
    return 1.0 - (1.0 - t) ** 4


class Bloom(_Particle):
    """BloomParticle.cs."""

    def __init__(self, x, y, color, s0, s1, life, fade=True):
        super().__init__(x, y, 0.0, 0.0, life, s0, color)
        self.base, self.s0, self.s1, self.fade = color, s0, s1, fade
        self.opacity = 0.0

    def update(self):
        self.scale = self.s0 + (self.s1 - self.s0) * _poly_out4(self.completion())
        if self.fade:
            self.opacity = math.sin(math.pi / 2 + self.completion() * math.pi / 2)
        self.color = _mul_col(self.base, self.opacity if self.fade else 1.0)

    def draw(self, fx, canvas):
        col = _mul_col(self.color, self.opacity) if self.fade else self.color
        fx.stamp(canvas, "bloom", self.x, self.y, self.scale, self.scale, 0.0, col)


class PulseRing(_Particle):
    """DirectionalPulseRing.cs."""

    def __init__(self, x, y, color, squish, rot, s0, s1, life):
        super().__init__(x, y, 0.0, 0.0, life, s0, color)
        self.base, self.squish, self.s0, self.s1 = color, squish, s0, s1
        self.rot = rot
        self.opacity = 0.0

    def update(self):
        self.scale = self.s0 + (self.s1 - self.s0) * _poly_out4(self.completion())
        self.opacity = math.sin(math.pi / 2 + self.completion() * math.pi / 2)
        self.color = _mul_col(self.base, self.opacity)
        self.vx *= 0.95
        self.vy *= 0.95

    def draw(self, fx, canvas):   # drawn with Color * opacity -> base * opacity^2
        fx.stamp(canvas, "ring", self.x, self.y, self.scale * self.squish[0], self.scale * self.squish[1],
                 self.rot, _mul_col(self.color, self.opacity))


class _Dust:
    """SUBSTITUTE for a noGravity vanilla dust."""
    __slots__ = ("x", "y", "vx", "vy", "scale", "col")

    def __init__(self, x, y, vx, vy, scale, col):
        self.x, self.y, self.vx, self.vy, self.scale, self.col = x, y, vx, vy, scale, col

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.vx *= 0.92
        self.vy *= 0.92
        self.scale -= 0.05


class _Track:
    """Per-projectile mirror of the state the pet does not expose."""
    __slots__ = ("ref", "name", "x", "y", "dx", "dy", "tl", "op", "within", "set_life")

    def __init__(self, p):
        self.ref, self.name = p, p.name
        self.x, self.y = p.x, p.y
        vx, vy = getattr(p, "vx", None), getattr(p, "vy", None)
        ok = isinstance(vx, (int, float)) and isinstance(vy, (int, float))
        self.dx, self.dy = (float(vx), float(vy)) if ok else (None, None)
        self.tl = _LIFE.get(p.name, 0)
        self.op = _OP0.get(p.name, 1.0)
        self.within = False
        self.set_life = False


def _safe_norm(x, y, fx, fy):
    n = math.hypot(x, y)
    return (x / n, y / n) if n > 1e-9 else (fx, fy)


# ----------------------------------------------------------------------------------------------
# The effects layer
# ----------------------------------------------------------------------------------------------
class VFX:
    def __init__(self, k):
        self.k = float(k)
        self.rng = random.Random(_RNG_SEED)   # private: never touches the fight's RNG streams
        self.particles = []
        self.dust = []
        self._cache = {}
        self._track = {}
        self._prev_attack = None
        self._last_atk_tick = -1
        self._fired_seen = []
        self._dash_e0 = None
        self._dash_counter = 0
        self._prev_xy = None
        self.error = None
        try:
            self.tex, self.notes = _load_textures()
        except Exception as exc:   # unreadable assets -> run without VFX
            self.tex, self.notes = {}, [f"texture load failed: {exc}"]
        self.ok = all(key in self.tex for key in ("spark", "bloom", "ring", "point", "glow"))
        if self.notes:
            print("calamitas_vfx:", "; ".join(self.notes))

    # ------------------------------------------------------------------ rng helpers (Terraria semantics)
    def _f(self, a, b):                 # Main.rand.NextFloat(a, b)
        return a + (b - a) * self.rng.random()

    def _nb(self, n=2):                 # Main.rand.NextBool(n)
        return self.rng.randrange(n) == 0

    def _rot_rand(self, x, y, maxr):    # Vector2.RotatedByRandom(max) = RotatedBy(NextDouble*max - max/2)
        a = self.rng.random() * maxr - maxr / 2.0
        c, s = math.cos(a), math.sin(a)
        return x * c - y * s, x * s + y * c

    def _circ(self, rx, ry):            # Main.rand.NextVector2Circular(rx, ry) = unit * (rx, ry) * NextFloat()
        a = self.rng.random() * 2.0 * math.pi
        r = self.rng.random()
        return math.cos(a) * rx * r, math.sin(a) * ry * r

    # ------------------------------------------------------------------ spawning
    def _spawn(self, p):
        if len(self.particles) >= PARTICLE_LIMIT and not p.important:
            return
        self.particles.append(p)

    def _dust_at(self, x, y, vx, vy, scale, kind):
        if len(self.dust) < DUST_LIMIT:
            self.dust.append(_Dust(x, y, vx, vy, scale, _DUST_COL[kind]))

    def _fail(self):
        self.ok = False
        self.error = traceback.format_exc()
        print("calamitas_vfx disabled after an error:\n" + self.error, file=sys.stderr)

    # ------------------------------------------------------------------ per tick
    def tick(self, cal):
        if not self.ok:
            return
        try:
            self._observe_casts(cal)
            self._observe_projectiles(cal)
            self._observe_body(cal)
            for p in self.particles:
                p.x += p.vx
                p.y += p.vy
                p.time += 1
                p.update()
            self.particles = [p for p in self.particles if p.time < p.life]
            for d in self.dust:
                d.update()
            self.dust = [d for d in self.dust if d.scale >= 0.1]
        except Exception:
            self._fail()

    # ------------------------------------------------------------------ her casting
    def _origin(self, cal):
        """NPC.Center at AI time = before this tick's `x += vx` (the pet moves after _ai)."""
        return cal.x - cal.vx, cal.y - cal.vy

    def _hand(self, cal, off):
        """handPosition = NPC.Center + new Vector2(NPC.spriteDirection * -off, 2f)  (SCal.cs:2431, :2516, :2966, :3059)."""
        sd = (-1.0 if cal.facing_left else 1.0) * _HAND_SIGN
        ox, oy = self._origin(cal)
        return ox + sd * -off * self.k, oy + 2.0 * self.k

    def _aim(self, cal, ox, oy):
        """NPC.DirectionTo(player.Center) / (player.Center - NPC.Center).SafeNormalize(Vector2.UnitY)."""
        return _safe_norm(cal.cx - ox, cal.cy - oy, 0.0, 1.0)

    def _observe_casts(self, cal):
        a = cal.attack
        atk_tick = getattr(cal, "_atk_tick", 0)
        restarted = a is not None and a is self._prev_attack and atk_tick < self._last_atk_tick
        if a is not self._prev_attack or restarted:
            prev = self._prev_attack
            if prev is not None and prev.get("key") == "bullethell":
                self._bh1_end(cal)
            self._prev_attack = a
            self._fired_seen = []
            self._dash_e0 = None
        self._last_atk_tick = atk_tick
        if a is None:
            return
        fired = getattr(cal, "_fired", None) or []
        shots = getattr(cal, "_shots", None) or []
        if len(self._fired_seen) != len(fired):
            self._fired_seen = [False] * len(fired)
        for i, f in enumerate(fired):
            if f and not self._fired_seen[i]:
                self._fired_seen[i] = True
                if i < len(shots):
                    self._cast(cal, a, shots[i][1])
        if a.get("phase") == 3:
            self._hand_spray(cal, a)
        if a.get("key") == "bash_p2":
            self._dash_sparks(cal)

    def _bh1_end(self, cal):
        """SCal.cs:995-1010 `else if (!startBattle)` -- the cast that closes BH1 (the Sepulcher it summons is out of
        scope; the flash is hers): two pulse rings + 100 dust."""
        k = self.k
        x, y = cal.x, cal.y
        self._spawn(PulseRing(x, y, RED, (1.0, 1.0), 0.0, 0.1, 5.0, 15))         # :999
        self._spawn(PulseRing(x, y, RED_MAG_03, (1.0, 1.0), 0.0, 0.05, 4.0, 18))  # :1001
        for _ in range(100):                                                     # :1003-1010
            dvx, dvy = self._rot_rand(15.0 * k, 15.0 * k, 100.0)
            m = self._f(0.3, 1.3)
            self._dust_at(x + dvx * 3, y + dvy * 3, dvx * m, dvy * m, self._f(2.0, 3.2), 60 if self._nb(3) else 114)

    def _cast(self, cal, a, shot):
        name, speed = shot[0], shot[2]
        k = self.k
        stage2 = a.get("stage") == 2
        ox, oy = self._origin(cal)
        dx, dy = self._aim(cal, ox, oy)
        muzzle = a.get("muzzle", 0.0) * k
        sx, sy = ox + dx * muzzle, oy + dy * muzzle          # projectileSpawn
        phase = a.get("phase")
        if phase == 0 and name == "BrimstoneBarrage":
            # SCal.cs:2276-2283 per projectile j (8): 6 GlowOrbs, dustVel = (projectileVelocity*2).RotatedByRandom(0.9)*NextFloat(0.5,1.9)
            # LastStage :2845 -> scale NextFloat(0.75, 1) (loop counts assumed equal)
            pvx, pvy = dx * speed * k, dy * speed * k
            for _ in range(a.get("fan", (8, 0))[0]):
                for _ in range(6):
                    vx, vy = self._rot_rand(pvx * 2, pvy * 2, 0.9)
                    m = self._f(0.5, 1.9)
                    sc = self._f(0.75, 1.0) if stage2 else self._f(0.65, 0.9)
                    col = RED if self._nb() else RED_MAG_03
                    self._spawn(GlowOrb(sx, sy, vx * m, vy * m, 15, sc, col, k, important=stage2))
        elif phase == 0 and name in ("SCalBrimstoneFireblast", "SCalBrimstoneGigablast"):
            # SCal.cs:2237-2241 (gigablast, velOffset*0.7) / :2256-2260 (fireblast, velOffset*0.8)
            vm = 0.8 if name == "SCalBrimstoneFireblast" else 0.7
            for _ in range(9):
                vx, vy = self._rot_rand(dx, dy, 0.6)
                m = self._f(5.0, 13.0) * k
                vx, vy = vx * m, vy * m
                col = RED_MAG_03 if self._nb(3) else RED
                self._spawn(GlowOrb(sx + vx * 2, sy + vy * 2, vx * vm, vy * vm, 30, self._f(0.4, 0.65), col, k))
        elif phase == 3:
            # SCal.cs:2450-2455 hellblast burst: 6 PointParticles from the hand. LastStage :2988 -> life 9, scale 0.5-0.75
            hx, hy = self._hand(cal, 18.0)
            hdx, hdy = self._aim(cal, *self._origin(cal))
            for _ in range(6):
                vx, vy = self._rot_rand(hdx, hdy, 0.6)
                m = self._f(5.0, 13.0) * k
                vx, vy = vx * m, vy * m
                col = RED_MAG_03 if self._nb(3) else RED
                life, sc = (9, self._f(0.5, 0.75)) if stage2 else (18, self._f(0.4, 0.65))
                self._spawn(Point(hx + vx * 2, hy + vy * 2, vx * 1.5, vy * 1.5, life, sc, col, k))
        elif phase == 4:
            # SCal.cs:2516-2522 gigablast punch: 25 GlowOrbs from handPosition (-22). velOffset definition not read:
            # assumed identical to the other bursts (DirectionTo(player).RotatedByRandom(0.6) * NextFloat(5, 13)).
            hx, hy = self._hand(cal, 22.0)
            for _ in range(25):
                vx, vy = self._rot_rand(dx, dy, 0.6)
                m = self._f(5.0, 13.0) * k
                vx, vy = vx * m, vy * m
                col = RED_MAG_03 if self._nb(3) else RED
                self._spawn(GlowOrb(hx + vx * 2, hy + vy * 2, vx * 1.5, vy * 1.5, 9, self._f(0.4, 0.65), col, k))

    def _hand_spray(self, cal, a):
        """SCal.cs:2468-2472 `if (Main.rand.NextBool()) // Hand spray magic` every tick of phase 3.
        LastStage :3006 -> scale NextFloat(0.95, 1.45), colour Lerp(Red, Magenta, 0.5)."""
        if not self._nb():
            return
        k = self.k
        stage2 = a.get("stage") == 2
        hx, hy = self._hand(cal, 18.0)
        vx, vy = self._rot_rand(0.0, -6.0 * k, 0.4)
        m = self._f(0.8, 1.4)
        sc = self._f(0.95, 1.45) if stage2 else self._f(0.85, 1.2)
        col = RED if self._nb() else (RED_MAG_05 if stage2 else RED_MAG_03)
        self._spawn(GlowOrb(hx, hy, vx * m, vy * m, 15, sc, col, k, important=True))

    def _dash_sparks(self, cal):
        """SCal.cs:3094-3106 (LastStage charge only): dashVisualCounter climbs to 9, then two helix sparks per tick.
        dashVisualCounter's reset site was not read: reset at each new charge here."""
        e0 = getattr(cal, "_dash_e0", None)
        if e0 is None:
            return
        if e0 != self._dash_e0:
            self._dash_e0, self._dash_counter = e0, 0
        if self._dash_counter < 9:
            self._dash_counter += 1
            return
        k = self.k
        lerp = (self._dash_counter - 120.0) / (0.0 - 120.0)          # Utils.GetLerpValue(120, 0, counter)
        m = min(1.1, max(0.5, lerp))
        ai2 = max(0, getattr(cal, "_atk_tick", 0) - 1 - e0)          # NPC.ai[2] of the current charge
        sine = math.sin(ai2 * (0.975 * m) / math.pi)
        vx, vy = cal.vx, cal.vy
        nx, ny = _safe_norm(vx, vy, 1.0, 0.0)
        px, py = -ny, nx                                             # RotatedBy(PiOver2)
        bx, by = _safe_norm(vx, vy, 0.0, 1.0)
        cx, cy = self._origin(cal)
        ox, oy = px * sine * 33.0 * k, py * sine * 33.0 * k
        for sgn in (1.0, -1.0):
            col = RED if self._nb() else RED_MAG_05
            self._spawn(Spark(cx + sgn * ox - bx * 15.0 * k, cy + sgn * oy - by * 15.0 * k,
                              -vx * 0.85, -vy * 0.85, 10, 1.9 * m, col, k))

    def _observe_body(self, cal):
        """SCal.cs:595-605 -- while the shield is up and she is charging, 15 dust per tick off the skull's eye socket,
        spread along her motion since last tick (Lerp(position, oldPosition, n/16))."""
        cur = (cal.x, cal.y)
        prev = self._prev_xy if self._prev_xy is not None else cur
        self._prev_xy = cur
        if not (getattr(cal, "_shield_active", False) and getattr(cal, "_dashing", False)):
            return
        k = self.k
        rot = cal.shield_rot
        vrot = math.atan2(cal.vy, cal.vx)
        ex, ey = math.cos(rot) * 42.0 * k, math.sin(rot) * 42.0 * k
        q = math.cos(vrot) * -4.0 * k
        jx, jy = math.cos(rot - math.pi / 2) * q, math.sin(rot - math.pi / 2) * q
        for n in range(1, 16):
            t = n / 16.0
            x = cur[0] + (prev[0] - cur[0]) * t + ex + jx
            y = cur[1] + (prev[1] - cur[1]) * t + ey + jy
            self._dust_at(x, y, cal.vx, cal.vy, 0.6 + (0.85 - 0.6) * (1.0 - t), "destroyer")

    # ------------------------------------------------------------------ projectiles
    def _observe_projectiles(self, cal):
        seen = set()
        for p in cal.bullets:
            pid = id(p)
            t = self._track.get(pid)
            if t is None or t.ref is not p:
                t = _Track(p)
                self._track[pid] = t
            else:
                t.dx, t.dy = p.x - t.x, p.y - t.y
                t.x, t.y = p.x, p.y
            seen.add(pid)
            if t.name in _LIFE:
                self._proj_ai(cal, p, t)
        for pid in [q for q in self._track if q not in seen]:
            t = self._track.pop(pid)
            if not getattr(t.ref, "alive", True):    # natural death (not an off-canvas cull)
                self._on_kill(t)

    def _proj_ai(self, cal, p, t):
        """One AI update of the projectile's own source, VFX lines only. t.tl is timeLeft during this AI call."""
        k = self.k
        tl = t.tl
        name = t.name
        ai1 = getattr(p, "ai1", 2.0)
        x, y = p.x, p.y
        dist = math.hypot(cal.cx - x, cal.cy - y)
        vx, vy = (t.dx, t.dy) if t.dx is not None else (0.0, 0.0)
        have_v = t.dx is not None
        if name == "BrimstoneBarrage":
            if tl < 60:
                t.op = min(1.0, max(0.0, tl / 60.0))
            if ai1 == 2.0 and dist < 1400.0 * k and have_v:
                life = int(min(9.0, max(2.0, 9.0 * ((tl - 630) / 60.0))))   # Clamp(9 * GetLerpValue(630, 690, tl), 2, 9)
                col = _mul_col(RED if self._nb() else RED_MAG_05, t.op * 0.85)
                m = self._f(0.1, 0.6)
                self._spawn(Spark(x - vx * 0.5, y - vy * 0.5, -vx * m, -vy * m, life, 1.1, col, k))
        elif name == "BrimstoneHellblast":
            if dist < 1400.0 * k and ai1 == 2.0 and have_v:
                sine = math.sin(tl * 0.575 / math.pi)                       # helix
                nx, ny = _safe_norm(vx, vy, 1.0, 0.0)
                ox, oy = -ny * sine * 16.0 * k, nx * sine * 16.0 * k
                for sgn in (1.0, -1.0):
                    col = RED if self._nb() else RED_MAG_05
                    self._spawn(Spark(x + sgn * ox, y + sgn * oy, -vx * 0.05, -vy * 0.05, 8, 0.8, col, k))
            if tl < 51:
                t.op -= 0.02
        elif name in ("SCalBrimstoneFireblast", "SCalBrimstoneGigablast"):
            giga = name == "SCalBrimstoneGigablast"
            if not t.within:
                t.op = min(1.0, max(0.0, 1.0 - ((tl - 130) / 20.0)))
            if ai1 == 2.0 and not t.within and self._nb() and have_v:
                r = 30.0 if giga else 20.0
                cx, cy = self._circ(r * k, r * k)
                m = self._f(0.1, 1.0)
                sc = self._f(0.5, 0.75) if giga else self._f(0.35, 0.6)
                col = _mul_col(RED_MAG_05 if self._nb() else RED, t.op)
                self._spawn(Spark(x - vx + cx, y - vy + cy, -vx * m, -vy * m, 14, sc, col, k))
            if (tl == 1 and not t.within) or (dist < 224.0 * k and t.op == 1.0):
                if not t.set_life:
                    t.tl = tl = 60
                    t.set_life = True
                t.within = True
            if t.within:
                for _ in range(2):
                    dvx, dvy = self._rot_rand(4.0 * k, 4.0 * k, 100.0)
                    m = self._f(0.5, 1.3)
                    self._dust_at(x, y, dvx * m, dvy * m, self._f(0.7, 1.8), 60 if self._nb(3) else 114)
                if tl <= 40 and t.op > 0:
                    t.op -= 0.05
                if tl == 30:
                    t.op = 0.0
                    for _ in range(2):
                        self._spawn(Bloom(x, y, BLOOM_WINE, 0.1, 0.85 if giga else 0.7, 30, False))
                if tl == 15:
                    self._spawn(Bloom(x, y, RED, 0.1, 0.8 if giga else 0.65, 15, False))
                if tl == 8:
                    self._spawn(Bloom(x, y, WHITE, 0.1, 0.7 if giga else 0.5, 8, False))
        elif name == "BrimstoneWave":
            if have_v:
                m = self._f(0.1, 0.7)
                self._dust_at(x, y, vx * m, vy * m, self._f(0.9, 1.8), 60 if self._nb(3) else 114)
        t.tl -= 1

    def _on_kill(self, t):
        """OnKill VFX. The fight-side OnKill (impact sound, barrage ring) stays in the pet, unchanged."""
        k = self.k
        x, y = t.x, t.y
        p = t.ref
        if t.name == "BrimstoneHellblast":
            # Dust.NewDust(position + velocity, 40, 40, Brimstone) x6: random point in the hitbox, NewDust's default
            # random velocity (+-2) and scale (1 +- 0.2).
            vx, vy = (t.dx or 0.0), (t.dy or 0.0)
            for _ in range(6):
                self._dust_at(x + vx + self._f(-20.0, 20.0) * k, y + vy + self._f(-20.0, 20.0) * k,
                              self._f(-2.0, 2.0) * k, self._f(-2.0, 2.0) * k, self._f(0.8, 1.2), "brimstone")
        elif t.name in ("SCalBrimstoneFireblast", "SCalBrimstoneGigablast") and getattr(p, "ai1", 2.0) == 2.0:
            giga = t.name == "SCalBrimstoneGigablast"
            n, sv, dv = (25, 15.0, 20.0) if giga else (18, 12.0, 16.0)
            for _ in range(n):
                vx, vy = self._rot_rand(sv * k, sv * k, 100.0)
                m = self._f(0.3, 1.0)
                col = _mul_col(RED_MAG_05 if self._nb() else RED, 0.6)
                self._spawn(Point(x + vx, y + vy, vx * m, vy * m, 15, 1.25 if giga else 1.1, col, k))
            for _ in range(n):
                vx, vy = self._rot_rand(dv * k, dv * k, 100.0)
                m = self._f(0.5, 1.3)
                sc = self._f(0.9, 1.8) if giga else self._f(0.75, 1.3)
                self._dust_at(x, y, vx * m, vy * m, sc, 60 if self._nb(3) else 114)

    # ------------------------------------------------------------------ drawing
    def stamp(self, canvas, key, x, y, sx, sy, rot, color, alpha_factor=True):
        """spriteBatch.Draw(tex, pos, null, color, rot, tex.Size()/2, (sx, sy), ...) under additive blending."""
        tex = self.tex.get(key)
        if tex is None:
            return
        r, g, b, a = color
        f = (a / 255.0) if alpha_factor else 1.0
        if f <= 0.0 or (r | g | b) == 0:
            return
        m = np.array((b, g, r), np.float32) * np.float32(f / 255.0)
        tw, th = tex.size
        w, h = tw * sx * self.k, th * sy * self.k
        if w < 0.75 or h < 0.75:
            return
        if rot == 0.0 and w * h > _BIG_PX:
            self._add_scaled(canvas, tex, x, y, w, h, m)
            return
        iw, ih = max(1, int(round(w))), max(1, int(round(h)))
        ab = int(round(rot / (2.0 * math.pi) * _ANGLE_BINS)) % _ANGLE_BINS
        ck = (key, iw, ih, ab)
        st = self._cache.get(ck)
        if st is None:
            im = tex.resize((iw, ih), _BIL)
            if ab:   # XNA rotation is clockwise on a y-down screen; PIL's is counter-clockwise
                im = im.rotate(-ab * 360.0 / _ANGLE_BINS, resample=_BIL, expand=True)
            st = np.ascontiguousarray(np.asarray(im, np.float32)[..., ::-1])
            if len(self._cache) >= _STAMP_CACHE:
                self._cache.clear()
            self._cache[ck] = st
        sh, sw = st.shape[:2]
        _blend_add(canvas, st, int(round(x - sw / 2.0)), int(round(y - sh / 2.0)), m)

    def _add_scaled(self, canvas, tex, cx, cy, w, h, m):
        """Large unrotated draw (blooms, pulse rings): resample only the on-canvas part of the quad."""
        H, W = canvas.shape[:2]
        x0, y0 = cx - w / 2.0, cy - h / 2.0
        ax0, ay0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
        ax1, ay1 = min(W, int(math.ceil(x0 + w))), min(H, int(math.ceil(y0 + h)))
        if ax0 >= ax1 or ay0 >= ay1:
            return
        tw, th = tex.size
        # The visible canvas rect maps back to a slice of the texture, but floor/ceil leave that slice up to a texel
        # outside the texture: ax0 - x0 lands in (-1, 0] whenever the quad's left (top) edge is inside the canvas,
        # and ax1 - x0 exceeds w whenever the right (bottom) edge is. PIL rejects both -- "box offset can't be
        # negative" / "box can't exceed original image size" -- so clamp the slice into the texture. The far-end
        # clamps only keep the span non-empty: ax1 > ax0 (w > 0, and the early return above covers a quad that is
        # off-canvas), so a box that used to be valid comes out of these four lines unchanged.
        bx0 = max((ax0 - x0) / w * tw, 0.0)
        by0 = max((ay0 - y0) / h * th, 0.0)
        bx1 = min(max((ax1 - x0) / w * tw, bx0), float(tw))
        by1 = min(max((ay1 - y0) / h * th, by0), float(th))
        im = tex.resize((ax1 - ax0, ay1 - ay0), _BIL, box=(bx0, by0, bx1, by1))
        _blend_add(canvas, np.asarray(im, np.float32)[..., ::-1], ax0, ay0, m)

    def draw_afterimage(self, canvas, p, cal):
        """DrawingUtils.cs:97 DrawAfterimagesCentered mode 0, TrailCacheLength 2: oldPos[0] at full alpha, oldPos[1]
        at (2-1)/2 = 0.5, drawn after it. The pet already drew the full-alpha one; this adds the 0.5 copy one tick
        back, rendered by the pet's own Projectile.draw into a scratch buffer."""
        if not self.ok or getattr(p, "name", None) not in _AFTERIMAGE or canvas.shape[2] != 4:
            return
        t = self._track.get(id(p))
        if t is None or t.ref is not p or t.dx is None:
            return
        try:
            frames = cal.proj.get(p.name)
            fh, fw = frames[0].shape[:2]
            R = int(math.hypot(fw, fh) / 2.0) + 4
            bx, by = int(round(p.x - t.dx)) - R, int(round(p.y - t.dy)) - R
            H, W = canvas.shape[:2]
            if bx >= W or by >= H or bx + 2 * R <= 0 or by + 2 * R <= 0:
                return
            buf = np.zeros((2 * R, 2 * R, 4), np.uint8)
            ox, oy = p.x, p.y
            try:
                p.x, p.y = ox - t.dx - bx, oy - t.dy - by
                p.draw(buf)
            finally:
                p.x, p.y = ox, oy
            _blend_over(canvas, buf, bx, by, 0.5)
        except Exception:
            self._fail()

    def draw(self, canvas):
        """Dust, then particles (Particle.DrawLayer defaults to AfterDusts) -- both after projectiles."""
        if not self.ok or canvas.shape[2] != 4:
            return
        try:
            for d in self.dust:
                self.stamp(canvas, "dust", d.x, d.y, d.scale, d.scale, 0.0, d.col, alpha_factor=False)
            for p in self.particles:
                p.draw(self, canvas)
        except Exception:
            self._fail()
