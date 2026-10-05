# Calamitas Desktop Pet

A stand-alone Supreme Calamitas boss fight on your desktop, built from the
official Calamity Mod assets. She chases your cursor and throws her
pre-brothers moveset at it.

> **Not affiliated with Terraria, Re-Logic, the Calamity Mod team, or Azafure
> LLC.** Supreme Calamitas and all her art, audio and shader code belong to the
> **Calamity Mod** (© the mod authors / Azafure LLC); Terraria and the
> surrounding content belong to **Re-Logic**. Everything under `assets/` is
> redistributed here unmodified except where noted, for **personal,
> non-commercial, fan use only**, with no permission or endorsement from either
> party. Full per-file attribution is in
> [Credits and licensing](#credits-and-licensing); the code/assets licence split
> is in [`LICENSE.md`](LICENSE.md). If you own an asset here and want it
> removed, open an issue and it will be taken down.

## Two versions — pick one

| Launcher | Module | Fight logic |
| --- | --- | --- |
| **`run_pet_ai.bat`** | `calamitas_pet_ai.py` | **Ported from the mod source.** Attack selection, timings, projectile speeds/lifetimes/behaviour and her movement all trace to `SupremeCalamitas.cs` and the projectile classes. |
| `run_pet.bat` | `calamitas_pet.py` | The earlier hand-built version. Attack numbers were invented; kept for comparison. |

Both share the same assets, the same window/audio stack, and the same tests.
Press **ESC** to quit.

```
run_pet_ai.bat          <- the good one
run_pet.bat             <- original, unchanged
```

`calamitas_pet_ai.py` carries its own provenance notes in its module docstring:
per-constant citations back to the mod source, the couplings that were resolved
to constants, and every place a value is invented rather than ported. No separate
upstream copy of the AI is bundled; the citations in that docstring are the
record of what came from where.

## Changes made to the ported AI

Two, and both are presentation flags rather than behaviour changes:

1. **`INCLUDE_POST_BROTHERS_STAGE` made explicit** (it was hardcoded as
   `_STAGE2_FROM = 8`). See the scope note below — this is the flag you may
   want to flip.
2. **`SPRITE_SHRINK` added** (`= 0.5`). She and her projectiles were too big
   relative to the forcefield. See "Sprite size" below.

No behaviour was retuned. Speeds, lifetimes, cadences, cooldowns and the
`phaseChange` table are untouched.

## Later change: real per-pixel alpha

Both versions now present via `UpdateLayeredWindow` + `ULW_ALPHA` instead of a
green colour key. The two "changes" the port used to carry — `blit()` accepting
3- and 4-channel canvases, and `_disc()` likewise — are both **obsolete**: with
the colour key gone there is no 3-channel canvas any more, `buf` is the full
4-channel premultiplied BGRA DIB, and `_disc()` writes an opaque alpha of 255.
Those workarounds have been deleted rather than left in place.

Visible effect: sprite edges are blended instead of hard-cut at `ALPHA_CUTOFF`,
and `KEY_BGR` / `ALPHA_CUTOFF` no longer exist in either module.

## Scope note you should decide on

`04_BlastPunchCast` and `06_Clap` are **only** drawn by the `LastStage`
(`ai[0]==3`) branch, which is post-brothers and outside the requested
pre-brothers scope. To make all 7 sprite animations reachable, the port routes
`phaseChange` table entries `>= 8` through the LastStage timings, so roughly
40% of her behaviour comes from that out-of-scope branch.

It is now a single flag near the top of `calamitas_pet_ai.py`:

```python
INCLUDE_POST_BROTHERS_STAGE = True   # False = strict pre-brothers
```

Set it to `False` and she is purely pre-brothers — but `04_BlastPunchCast` and
`06_Clap` then never play, and `test_headless.py` will fail its
"all 7 animations occur" assertion. That assertion encodes my earlier scoping
mistake, so if you flip the flag, relax that assertion too.

## Running the tests against either version

The suite takes a `CALAMITAS_MODULE` environment variable, defaulting to the
original pet:

```powershell
python test_headless.py                                  # original pet
$env:CALAMITAS_MODULE = "calamitas_pet_ai"; python test_headless.py   # ported AI
Remove-Item Env:\CALAMITAS_MODULE
```

All four windowed/simulation tests currently pass for **both** implementations.

`test_vfx_stamp.py` is AI-version only and takes no `CALAMITAS_MODULE`: it tests
`calamitas_vfx.py` directly, plus one integration run through the ported-AI pet.

No install needed — it uses only the Python standard library plus `numpy` and
`Pillow`, which you already have. (`pygame` has no wheel for Python 3.14, so the
window is a raw Win32 layered window instead.)

Console output is quiet by default. Add `-v` for an fps/state line every 2
seconds, or `--shot <seconds>` to dump one composited frame to
`pet_debug_frame.png`.

```
python calamitas_pet.py            # normal
python calamitas_pet.py -v         # verbose
python calamitas_pet.py --shot 5   # also dump a debug frame after 5s
```

## Transparency: per-pixel alpha

The window is a layered window presented with `UpdateLayeredWindow` +
`ULW_ALPHA`, so the compositor blends each pixel's alpha. Sprite edges are
genuinely blended, and the soft outer glow on the projectiles survives intact.

Two things about this are load-bearing, and both were found the hard way:

**1. `pblend` must be a real `BLENDFUNCTION*`, not `NULL`.** MSDN says `pblend`
is ignored when `ULW_ALPHA` is set, so passing `NULL` is the natural thing to do
— and on this build (Windows 11 25H2, 26200) it fails *every* call with
`ERROR_GEN_FAILURE`. Supplying a `BLENDFUNCTION{AC_SRC_OVER, 0, 255,
AC_SRC_ALPHA}` makes it work. This one `NULL` was the entire reason the pet was
previously a colour-key window.

**2. Never call `SetLayeredWindowAttributes` on this window.** It switches the
window into colour-key / constant-alpha mode, and after that *every*
`UpdateLayeredWindow` call fails with `ERROR_INVALID_PARAMETER` — including
`flags=0`. The two transparency modes are mutually exclusive on one window.

The buffer is **premultiplied BGRA**, which is what `ULW_ALPHA` expects; the
colour channels are already scaled by alpha. PIL hands out straight
(non-premultiplied) alpha, so `blit()` does that multiply. Getting this wrong
is not subtle — sprite edges grow bright halos. `test_headless.py` has a
regression test for exactly that.

### How this was diagnosed

`ulw_probe.py` is the probe that established the above. It builds a layered
window, sweeps the documented call variants, and `BitBlt`s the **real desktop**
back to confirm the resulting pixels actually landed — checking the return code
alone is not enough, since an earlier per-pixel-alpha attempt reported success
at the API level while drawing nothing.

```
flags=0  (no ULW_ALPHA)          ok    OK
flags=ULW_ALPHA (0x02)           FAIL  ERROR_GEN_FAILURE
flags=0x01 / 0x04 / 0x20        ok    OK
colorkey then flags=0           FAIL  ERROR_INVALID_PARAMETER

interleaved A/B on pblend:
round   pblend=NULL              pblend=BLENDFUNCTION
0..3    FAIL / GEN_FAILURE       ok / OK          (4/4)
```

Ruled out along the way, so you don't have to re-check them: not an RDP session
(`SM_REMOTESESSION=0`), not a VM or basic display driver (real GPU, DWM
composition on), not a low-integrity sandbox, and not ctypes marshalling
(`0x01`/`0x04`/`0x20` all succeed through the same call). Alpha was then
verified as genuinely blending by compositing a premultiplied alpha ramp over
the desktop and reading the result back — max error 0.5/255.

## What it does

- Transparent, click-through, always-on-top window over your whole desktop, so
  she never blocks clicks on your other windows
- She hovers and bobs toward your cursor, switching to the taller hover pose
  when she moves fast
- Six attacks on independent cooldowns, all aimed at your cursor
- Her shield goes up during the dash
- A real forcefield sphere around her, from the mod's own shader
- Projectile and casting particles — sparks, glow orbs, detonation blooms, the
  bullethell pulse rings, and afterimages behind the barrage and hellblast
  (ported AI version only)
- Real sound effects
- She is free to drift off the edge of the screen and come back

## Scope (as requested)

### Forcefield

`calamitas_forcefield.py` re-implements `Effects/SupremeShieldShader.fx` as per-pixel
alpha, and the AI version draws it around her. It is a faithful port: the radial
profile matches the `.fx` to ~2.7/255, the shader's own truncated literals (`1.414`,
`3.141`) are preserved rather than "corrected", and `1.414f`/`0.088f`/`1.35f` are
applied per-branch because only the normal branch has the `3f`.

Assets (all from the mod repo, branch `1.4.4`; see "Credits and licensing"
below for per-file attribution):

| Asset | Path |
| --- | --- |
| Shader source | `assets/shaders/SupremeShieldShader.fx` |
| Base texture | `assets/shaders/ForcefieldTexture.png` |
| Branch textures | `assets/shaders/reference/{CentralGold,SemiCircularSmearVertical}.png` |
| Compiled shader | `assets/shaders/reference/SupremeShieldShader.xnb` (reference only) |

**When it is visible.** Faithfully, always. The original gates the sphere on
`forcefieldScale`, not `forcefieldOpacity`: it lerps to `0.45` while the shield
is up (`SupremeCalamitas.cs:612`) and back to `1` otherwise (`:619`), reaching `0`
only in the death phase. `forcefieldOpacity` stays at its `1f` default (`:171`)
for the whole normal fight — it is only lowered at BH4 (`:1304`) and post-music-hit
(`:1325`), both outside the pet's scope. So the sphere is always on, and it
**shrinks during a charge** rather than appearing and disappearing.

**Two mappings are invented**, because the shipped frames give us neither quantity:
the C# quad is 216px against a 152.8px hitbox, but the pet has no hitbox, so the
sphere is sized at `_FF_VS_SPRITE` (1.55) × her sprite height; and the sphere is
centred on `self.x/self.y`, which is already her body centre because `draw()` uses
a centre anchor.

**The sphere ignores `SPRITE_SHRINK`.** `_forcefield_step()` divides the measured
sprite height back out (`shape[0] / SPRITE_SHRINK`) before applying
`_FF_VS_SPRITE`, so shrinking her does not shrink the bubble with her. Verified:
the quad holds at 192px (1080p) for `SPRITE_SHRINK` anywhere from 1.0 to 0.25.

**Known limits.** The noise is a 1/f^1.5 FFT field, not Terraria's real Perlin, so
the cloud pattern inside the rim will not match. `_CHARGE_MIX` (0.5) and the
shimmer `intensity` mapping are invented. At `forcefieldScale = 0.45` the sphere is
smaller than her sprite and hides behind her — which is what the original does too
(62px bubble vs a 68.8px hitbox). Roughly 1.8 ms/frame at normal size.

## Projectile and casting VFX

`calamitas_vfx.py` ports her projectile and casting particles — the last piece of
the fight that was still standing in as a flat procedural disc. It reads fight
state and **never writes it**, and it uses its own `random.Random`, so attack
selection, timings and the RNG streams are untouched.

The port covers `Particles/*.cs` (Spark, Point, GlowOrb, Bloom,
DirectionalPulseRing) and the OnKill/Spawn visuals of the five projectiles she
uses. Details:

- **Blending.** Every particle in the mod has `UseAdditiveBlend`, and tModLoader
  premultiplies textures on load, so a texel adds
  `tex.rgb * tex.a² * col.rgb * col.a`. Both `Color * f` (scales all four
  channels) and `Color.Lerp(c, Transparent, t)` (fades A too) are kept, so fades
  are quadratic exactly as in game.
- **Timing.** Per the `Particle.cs` doc — velocity is added to position and time
  increments before `update()`, so a particle is dropped when `time >= Lifetime`.
  `ParticleLimit` is assumed to be the `CalamityConfig` default of 500.
- **Mirrored state.** `Opacity`, `timeLeft` and `withinRange` are not exposed by
  the pet, so they are rebuilt per projectile from that projectile's own AI
  formulas plus this module's age counter and the cursor distance.
- **The module is self-disabling.** Any exception inside `tick()`/`draw()` prints
  the traceback and sets `self.ok = False`, which turns the whole layer off for
  the session instead of spamming errors every frame. A missing or broken module
  leaves `calamitas_vfx = None` and the pet runs exactly as it did before.

**Substitutes, marked `SUBSTITUTE` in the source.** Vanilla dust textures do not
exist here, so dust is a procedural 8×8 blob drawn additively, and its decay
(`vel*0.92`, `scale-0.05`/tick) is written from memory of vanilla's noGravity
torch-dust update. `Lighting.AddLight` and the lightColor G/B world-light
channels have no equivalent and are dropped.

**Known limits.** `GeneralParticleHandler.cs` was not read, so the particle limit
is assumed rather than ported. The wave's `velocity.Y = 5 * sin(x / 5)` and the
per-tick hand spray are reproduced, but nothing reads the mod's particle draw
layer ordering beyond "dust, then particles, both after projectiles".

## Sprite size

`SPRITE_SHRINK` (`calamitas_pet_ai.py`) scales **sprite art only**:

```python
SCALE = 2                   # sprite upscale applied before the shrink
SPRITE_SHRINK = 0.5         # 1.0 = the old size; 0.5 = native 1:1 resolution
```

Her frames, the shield skull/jaw and all six projectiles are drawn at
`SCALE * SPRITE_SHRINK`. On a 1920×1080 screen:

| | before | after |
| --- | --- | --- |
| Her sprite | 120×124 | 60×62 |
| `BrimstoneBarrage` | 36×88 | 18×44 |
| `SCalBrimstoneGigablast` | 104×164 | 52×82 |
| Shield skull | 152×172 | 76×86 |
| **Forcefield sphere** | **192 px** | **192 px** |

She now fills ~32% of the sphere's height instead of ~65%. In game the 216px
sphere is ~4.15× her 52px sprite height, and `SPRITE_SHRINK = 0.5` reproduces
exactly that ratio.

A separate knob rather than a lower `SCALE`, because `SCALE` sizes only art:
distances, speeds, detonation ranges and the whole `calamitas_vfx` particle layer
are in **world units** driven by `k`, which follows the screen size. Scaling art
by one factor keeps sprite-to-sprite proportions identical to the game's (they
all move together) and leaves the fight geometry and arena density untouched. It
also lands on 1:1 native resolution, so the default state involves no resampling
at all; the fractional step uses BOX (area average), not NEAREST, which would
make the pixel art shimmer between frames.

The shield's jaw offsets (`_SHIELD_FWD`, `_JAW_OFF`) carry the same factor so the
skull stays attached to her body. Setting `SPRITE_SHRINK` back to `1.0` restores
the old size exactly, sphere included.

## A crash worth knowing about

`VFX._add_scaled` resamples only the visible part of a large unrotated sprite
(blooms, pulse rings), mapping the on-canvas rect back into the texture with
PIL's `resize(box=...)`. PIL validates that rectangle, and the `floor`/`ceil`
rounding left it outside the texture in two ways:

- `box offset can't be negative` — whenever the sprite's left/top edge sat on a
  fractional pixel *inside* the canvas, `ax0 - x0` landed in `(-1, 0]`. The
  bullethell pulse rings hit this seconds into that cast.
- `box can't exceed original image size` — when a sprite was clipped by a canvas
  edge, `ceil` pushed `ax1 - x0` past the sprite width.

Either one raised `ValueError`, which `draw()` caught and turned into "VFX
disabled after an error", so the whole layer switched off mid-fight. The box is
now clamped into the texture. `test_vfx_stamp.py` covers both, plus the property
that matters for a fix like this: **every draw the old code could render is
bit-identical to the new output**, so nothing that used to work changed.



| Attack | Animation | Projectile | Sound |
| --- | --- | --- | --- |
| Barrage | `03_BothArmsRaise` | `BrimstoneBarrage` ×3 | `BrimstoneShoot` |
| Fireblast | `02_Casting` | `SCalBrimstoneFireblast` | `BrimstoneShoot` |
| Wave | `05_LeftHandshake` | `BrimstoneWave` ×2 | `BrimstoneShoot` |
| Gigablast | `02_Casting` | `SCalBrimstoneGigablast` | `BrimstoneBigShoot` |
| Hellblast | `03_BothArmsRaise` | `BrimstoneHellblast` ×2 | `BrimstoneHellblastSound` |
| Shield bash | `04_BlastPunchCast` | — (dash + shield) | `SCalDash` |

`06_Clap` is used as an occasional idle flourish.

**Deliberately excluded:**

- The **Sepulcher** worm she summons
- **Cataclysm** and **Catastrophe**
- The whole **Permafrost phase** — `PermafrostMeat`,
  `PermafrostAbsoluteZeroProjectile`, `PermafrostBlaster`. Those are gated
  behind the `permafrost` flag / `ai[0] >= 3`, which only happens *after* the
  brothers are dead, so they are not part of the pre-brothers fight.
- `BrimstoneMonster` (the Whispering Maelstrom), which belongs to the same
  later phase. The sprite is present in `assets/sprites/` but unused.

## Credits and licensing

**This repository is not affiliated with, endorsed by, or sponsored by Terraria,
Re-Logic, the Calamity Mod team, or Azafure LLC.** Every file under `assets/` is
the copyrighted work of someone else and is included here, unmodified except
for the documented audio format conversion and frame slicing, for **personal,
non-commercial, fan use only**. The code in this repository carries its own
license; the assets do not. See `LICENSE.md` for the exact split.

### Calamity Mod

All art, audio and shader code here comes from the official public mirror of the
released mod:

- **Calamity Mod** — <https://github.com/CalamityTeam/CalamityModPublic>, branch `1.4.4`
- Copyright **(c) the Calamity Mod authors / Azafure LLC (Wacht & Associates)**
- `SupremeCalamitas.cs` and the projectile classes are the source the AI port
  reads its timings and behaviour from; `Effects/SupremeShieldShader.fx` is the
  shader `calamitas_forcefield.py` reimplements.
- The mod's own terms restrict use to personal, non-commercial purposes, which
  is the only use this repository makes of it.

Supreme Calamitas, her attacks, the Brimstone projectiles and her shield are
character designs from the **Calamity Mod**. **Terraria** and the surrounding
game content are © **Re-Logic**. Both are used here without permission or
endorsement.

### Per-file attribution

Every asset was verified byte-identical against `CalamityModPublic` branch
`1.4.4` (SHA-256), except the two documented conversions below.

| Asset in this repo | Upstream path in the mod | Notes |
| --- | --- | --- |
| `assets/frames/SupremeCalamitasHooded/**` (42 PNGs) | `NPCs/SupremeCalamitas/SupremeCalamitasHooded.png` | Cropped from the 120×1302 sheet into 7 animations × 6 frames. Verified pixel-exact against the sheet. |
| `assets/sprites/SupremeShieldTop.png`, `SupremeShieldBottom.png` | `NPCs/SupremeCalamitas/SupremeShield{Top,Bottom}.png` | Unmodified |
| `assets/sprites/BrimstoneBarrage.png` | `Projectiles/Boss/BrimstoneBarrage.png` | Unmodified |
| `assets/sprites/BrimstoneWave.png` | `Projectiles/Boss/BrimstoneWave.png` | Unmodified |
| `assets/sprites/BrimstoneHellblast.png`, `BrimstoneHellblast2.png` | `Projectiles/Boss/BrimstoneHellblast{,2}.png` | Unmodified |
| `assets/sprites/SCalBrimstoneFireblast.png` | `Projectiles/Boss/SCalBrimstoneFireblast.png` | Unmodified |
| `assets/sprites/SCalBrimstoneGigablast.png` | `Projectiles/Boss/SCalBrimstoneGigablast.png` | Unmodified |
| `assets/sprites/BrimstoneMonster.png` | `Projectiles/Boss/BrimstoneMonster.png` | Unmodified. The Whispering Maelstrom; deliberately unused (see "Deliberately excluded"). |
| `assets/sfx/*.ogg` (7 files) | `Sounds/Custom/SCalSounds/*.ogg` | Unmodified |
| `assets/sfx/*.wav` (7 files) | derived from the `.ogg` above | Format conversion only. `ffmpeg -i <name>.ogg -c:a pcm_s16le -ar 44100 -ac 1 <name>.wav`, reproducible and verified identical to what is committed. |
| `assets/shaders/SupremeShieldShader.fx` | `Effects/SupremeShieldShader.fx` | Unmodified apart from line endings: upstream commits CRLF, this copy is LF. Identical after normalising. |
| `assets/shaders/reference/SupremeShieldShader.xnb` | `Effects/SupremeShieldShader.xnb` | The mod's **compiled** shader, kept as a reference for `calamitas_forcefield.py` to be checked against. Not read at runtime. |
| `assets/shaders/ForcefieldTexture.png` | `NPCs/SupremeCalamitas/ForcefieldTexture.png` | Unmodified |
| `assets/shaders/reference/CentralGold.png` | `Particles/CentralGold.png` | Unmodified |
| `assets/shaders/reference/SemiCircularSmearVertical.png` | `Particles/SemiCircularSmearVertical.png` | Unmodified |
| `assets/vfx/*.png` (9 files) | see below | Five are read at runtime by `calamitas_vfx.py`; four are staged. |

#### Forcefield shader licence

`SupremeShieldShader.fx` and its compiled `.xnb` are Calamity Mod content and
are **not** covered by this repository's licence. Note that the `.fx` carries no
licence header of its own, so no licence for it is asserted here; it stays under
whatever terms ship with the mod. Terraria-era `.fx` files of this shape often
originate from Microsoft's DirectX Effects samples, but that has not been
verified for this one.

`calamitas_forcefield.py` is an independent NumPy reimplementation of the
shader's radial profile, written from the maths in that file, so it is covered
by the repository's own licence.

#### `assets/vfx/` — particle textures

Five of these are read at runtime by `calamitas_vfx.py`; the other four are
staged for future work.

| File here | Upstream path | Used by |
| --- | --- | --- |
| `BloomCircle.png` | `Particles/BloomCircle.png` | `calamitas_vfx.py` — `Bloom` |
| `GlowOrbParticle.png` | `Particles/GlowOrbParticle.png` | `calamitas_vfx.py` — `GlowOrb` |
| `HollowCircleHardEdge.png` | `Particles/HollowCircleHardEdge.png` | `calamitas_vfx.py` — `PulseRing` |
| `PointParticle.png` | `Particles/PointParticle.png` | `calamitas_vfx.py` — `Point` |
| `StarProj.png` | `Projectiles/StarProj.png` | `calamitas_vfx.py` — `Spark` |
| `Fire.png` | `Particles/Fire.png` | staged |
| `Flames.png` | `Particles/Flames.png` | staged |
| `SmallSmoke.png` | `Particles/SmallSmoke.png` | staged |
| `StarTrail.png` | `Projectiles/StarTrail.png` | staged |

All nine are byte-identical to the mod originals and carry the same
Calamity Mod copyright as everything else above.

`calamitas_vfx.py` converts each RGBA texture to a per-texel additive intensity
(`rgb * a`, premultiplied, times `a` again for the `SourceAlpha` blend) at load
time, so the composite loop is a plain float multiply. If a texture is missing it
falls back to a related one where there is an obvious stand-in (e.g. missing
`PointParticle.png` uses `StarProj.png`, since both share their draw code) and
says so on startup.

### Re-generating the derived assets

```powershell
# .ogg -> .wav, one line per sound
ffmpeg -i assets\sfx\BrimstoneShoot.ogg -c:a pcm_s16le -ar 44100 -ac 1 assets\sfx\BrimstoneShoot.wav

# body frames: re-slice NPCs/SupremeCalamitas/SupremeCalamitasHooded.png
#   120x1302 sheet, 2 cols x 21 rows, 60x62 cells, 6 frames per animation
#   (animation k occupies cells [k*6, k*6+6)), one shared crop box per
#   animation so the sprite cannot jitter between frames
```

### Which sheet she is cut from

The body uses the **Hooded** sheet, which is what the game swaps in for the
actual fight (per `PreDraw`:
`DownedBossSystem.downedCalamitas ? NpcTexture : HoodedTexture`). The cell
layout above comes from `SupremeCalamitas.cs`: `Main.npcFrameCount[Type] = 21`
rows, `frameCounter %= 6` frames per animation, and
`frame.Y = frameCounter + FrameType * 6`, giving 42 cells = 7 animations × 6
frames, matching `enum FrameAnimationType { ... Count = 7 }`.

## Timings

Frame durations come from `FrameChangeSpeed` in `SupremeCalamitas.cs`, where
`frame_ms = 1000 / (FrameChangeSpeed * 60)`:

| Animation | Speed | Frame |
| --- | --- | --- |
| UpwardDraft | 0.15 | 111 ms |
| UpwardDraftTall | 0.15 | 111 ms |
| Casting | 0.15 | 111 ms |
| BothArmsRaise | 0.175 | 95 ms |
| BlastPunchCast | one-shot | 100 ms |
| LeftHandshake | 0.15 | 111 ms |
| Clap | 0.245 | 68 ms |

Projectile frame counts are `Main.projFrames[Type]` from each projectile's
source: Barrage 4, Wave 4, Hellblast 4, Fireblast 5, Gigablast 6.

## Layout

```
desktop_pet/
  calamitas_pet_ai.py  ported-AI pet  (run_pet_ai.bat)
  run_pet_ai.bat       launcher for the ported-AI version
  calamitas_pet.py     original pet   (run_pet.bat)
  run_pet.bat          launcher for the original
  calamitas_forcefield.py  SupremeShieldShader.fx ported to numpy (AI version only)
  calamitas_vfx.py         projectile + casting particles  (AI version only)
  ulw_probe.py         standalone UpdateLayeredWindow diagnostic (see above)
  test_headless.py     simulation tests (no window)
  test_vfx_stamp.py    VFX stamp/draw regression tests (no window)
  test_present.py      per-frame presentation check
  test_on_screen.py    desktop read-back visibility check
  test_full_run.py     full run + on-screen captures
  assets/
    frames/SupremeCalamitasHooded/   7 animations x 6 frames
    sprites/                          projectiles + shield
    sfx/                              WAV conversions of her sounds
    shaders/                          forcefield shader + texture, and reference/
    vfx/                              particle textures (5 used by calamitas_vfx.py)
```

`calamitas_vfx.py` is optional at runtime: the pet guards the import, so deleting
it (or setting `VFX_ENABLED = False`) leaves the old procedural bloom disc in
place and changes nothing else. It only works in the ported-AI version — the
original pet does not import it.

## Tests

```powershell
python test_headless.py     # 90s of simulation, no window
python test_vfx_stamp.py    # VFX stamp/draw regression tests (~2 min)
python test_present.py      # does every frame actually reach the screen?
python test_on_screen.py    # reads the desktop back; is she really visible?
python test_full_run.py     # full run with attacks, saves on-screen captures
```

`test_headless.py` checks the blitter (BGRA channel order, correct
premultiplication, partial alpha preserved rather than cut off, no bright halos
on dim sprites, offscreen no-ops), that every projectile's frames are uniformly
sized so nothing jitters, that all 7 animations and all 6 attacks occur, that
every sound fires, and that she both returns to the screen and leaves it.

`test_vfx_stamp.py` covers the VFX drawing path, and is where the sprite-size
change is guarded too. It asserts that the inputs which used to raise
`ValueError` now draw; that clipped quads cover their visible part and paint
nothing outside it; that position, scale and shape match a 4× supersampled render
of the same quad to sub-pixel accuracy; that a minute of real fight leaves the
layer enabled; and that every draw the pre-fix code could handle is still
bit-identical. Runtime is about two minutes.

`test_on_screen.py` and `test_full_run.py` `BitBlt` the **real desktop** back and
count her pixels. This matters: during development the per-pixel-alpha version
reported success at the API level while drawing nothing at all, and a screenshot
looked like an empty desktop. These tests read the composited screen, so they
cannot be fooled. `test_full_run.py` writes `onscreen_<n>.png` captures.

A screenshot of the pet is a legitimate way to confirm it works: the
per-pixel-alpha window composites into a normal screen capture, with the desktop
showing through behind her.

`test_on_screen.py` no longer filters by key colour — there isn't one. It grabs
the bare desktop first as a baseline, then diffs against it, so "something we
drew is actually there" is measured rather than assumed.

## Notes

- The window is click-through twice over: `WS_EX_TRANSPARENT` plus
  `WM_NCHITTEST -> HTTRANSPARENT`, so it never steals clicks or focus.
- A window must be made visible explicitly — `WS_POPUP` does **not** include
  `WS_VISIBLE`, and an invisible window shows nothing at all.
- `UpdateLayeredWindow` wants a DC compatible with the **screen**, not with the
  window, so `present()` passes `GetDC(None)` rather than `GetDC(hwnd)`.
- `SCALE = 4` in `calamitas_pet.py` / `SCALE = 2` and `SPRITE_SHRINK = 0.5` in
  `calamitas_pet_ai.py` control her size; `FPS = 60` the frame cap. See
  [Sprite size](#sprite-size).
- Sprites, sounds and shader code belong to Azafure LLC and Re-Logic. Personal,
  non-commercial use only. See the credit notice at the top of this file and the
  [Credits and licensing](#credits-and-licensing) section.