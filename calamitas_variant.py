"""calamitas_variant.py -- the two startup questions: which sprite sheet, and which AI.

Q1 (both pets) -- hooded or unhooded. Both sheets ship the same 7 animations x 6 frames and the same
per-frame timings, so this is a pure presentation choice. In the game it is decided in `PreDraw`
(`DownedBossSystem.downedCalamitas ? NpcTexture : HoodedTexture`); the pet has no downed-boss state,
so it is asked at startup instead.

Q2 (the AI pet only) -- how her AI behaves:
  faithful  the Calamity Mod source as written, with the cursor standing in for the player
  desktop   the same AI with the parts that assume a moving, arena-sized player re-aimed at the screen
            (currently: the opening bullet hell enters from offscreen instead of from the cursor)

Kept in its own module (like calamitas_vfx.py / calamitas_forcefield.py) so both pet versions prompt
identically and there is one place to change the wording. Import is guarded by the callers: if this
file is missing they fall back to the hooded sheet and the faithful AI, and never prompt.

Public: `choose(argv, stream)` and `choose_ai(argv, stream)`, both returning a plain string.
"""

import sys

# ----------------------------------------------------------------------------------------------
# Q1 -- body sheet
# ----------------------------------------------------------------------------------------------
# answer -> folder under assets/frames
VARIANTS = {
    "hooded": "SupremeCalamitasHooded",
    "unhooded": "SupremeCalamitas",
}
DEFAULT = "hooded"

# what the prompt offers, in order: answer, label, note. Each row is keyed by the answer's first
# letter, so Q1 and Q2 do not both offer [1].
_CHOICES = (
    ("hooded", "hooded   ", "the sprite the game swaps in for the fight"),
    ("unhooded", "unhooded ", "hood off, as in the downed sprite"),
)

_ALIASES = {
    "1": "hooded", "h": "hooded", "hooded": "hooded", "y": "hooded", "yes": "hooded",
    "2": "unhooded", "u": "unhooded", "unhooded": "unhooded", "n": "unhooded", "no": "unhooded",
}

# ----------------------------------------------------------------------------------------------
# Q2 -- AI mode. The strings are the same values as calamitas_pet_ai.AI_FAITHFUL / AI_DESKTOP, so
# this module owns the wording and the pet module owns the behaviour.
# ----------------------------------------------------------------------------------------------
AI_MODES = ("faithful", "desktop")
AI_DEFAULT = "faithful"

AI_CHOICES = (
    ("faithful", "faithful  ", "the Calamity Mod source as written, aimed at your cursor"),
    ("desktop", "desktop   ", "same AI, tuned for a desktop screen"),
)

_AI_ALIASES = {
    "1": "faithful", "f": "faithful", "faithful": "faithful", "mod": "faithful", "vanilla": "faithful",
    "2": "desktop", "d": "desktop", "desktop": "desktop",
}


def _flag(argv, names, aliases):
    """First `--flag value`, `--flag=value` or bare `--name` hit in `argv` that is *ours*.

    `names` are the accepted long names for one question (`("--variant",)`, `("--ai",)`). Returns
    (flag, value), (bare_flag, None) for a flag with no value, or (None, None) if `argv` has nothing
    of the sort. `aliases` decides what a bare flag means here: a bare flag that is an answer to this
    question (`--unhooded`, `--desktop`) is returned, and anything else is skipped rather than
    guessed at -- both questions read the same argv, so `--variant unhooded --ai desktop` has to
    leave the other question's flag alone, and `--verbose` is nobody's.
    """
    argv = list(argv)
    i = 0
    while i < len(argv):
        a = argv[i].lower()
        value = None
        if a in names:
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                value = argv[i + 1]
                i += 1
        elif "=" in a:
            head, _, tail = a.partition("=")
            if head in names:
                value = tail
        else:
            bare = a.lstrip("-")
            if bare and bare in aliases and ("--" + bare) not in names:
                return "--" + bare, None
        if value is not None:
            return a, value
        i += 1
    return None, None


def _interactive():
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


def _blurb(choices, answer):
    for a, _label, note in choices:
        if a == answer:
            return note
    return answer


def _ask(title, choices, aliases, default, argv, flag_names, noun, stream):
    """One question: an explicit flag wins, then the prompt, then `default`.

    `choices` is (answer, label, note). `noun` only shapes the no-console message. Returns
    (answer, say), where `say` tells the caller to print its own confirmation line -- true for a flag
    and for a typed answer, false for the no-console fallback, which has already said what it did.
    Raises SystemExit on a flag value that is not an answer.
    """
    flag, value = _flag(argv, flag_names, aliases)
    if flag is not None:
        answer = aliases.get((value if value is not None else flag.lstrip("-")).strip().lower())
        if not answer:
            raise SystemExit("unknown %s %r (pick one of: %s)"
                             % (flag_names[0], value, ", ".join(a for a, _l, _n in choices)))
        return answer, True

    if not _interactive():
        print("no console to ask on -- using the %s %s" % (default, noun), file=stream)
        return default, False
    print(title, file=stream)
    for a, label, note in choices:
        print("  [%s] %s  %s" % (a[0], label, note), file=stream)
    keys = ", ".join("%s/%s" % (a[0], a) for a, _l, _n in choices)
    while True:
        try:
            raw = input("%s [%s]: " % (default, default[0])).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(file=stream)
            return default, True
        if not raw:
            return default, True
        hit = aliases.get(raw)
        if hit:
            return hit, True
        print("  %r is not one of: %s (Enter = %s)" % (raw, keys, default), file=stream)


def choose(argv=None, stream=None):
    """Return the folder name under `assets/frames` for this run: hooded or unhooded.

    Accepts `--variant hooded|unhooded`, `--variant=...`, `--hooded` or `--unhooded`. Order: the
    flag, then the prompt. With no console to ask on (piped input, a service, a test importing the
    module) it takes DEFAULT and says so rather than blocking forever.
    """
    argv = sys.argv[1:] if argv is None else argv
    out = sys.stdout if stream is None else stream

    answer, say = _ask("Supreme Calamitas -- which sprite?", _CHOICES, _ALIASES, DEFAULT,
                       argv, ("--variant",), "sprite", out)
    folder = VARIANTS[answer]
    if say:
        print("using the %s sprite (%s)" % (answer, folder), file=out)
    return folder


def choose_ai(argv=None, stream=None):
    """Return the AI mode for this run: `calamitas_pet_ai.AI_FAITHFUL` or `AI_DESKTOP`.

    Accepts `--ai faithful|desktop`, `--ai=...`, `--mod` or `--desktop`. Same order and same
    no-console fallback as `choose`, so neither prompt can hang a headless run.
    """
    argv = sys.argv[1:] if argv is None else argv
    out = sys.stdout if stream is None else stream
    answer, say = _ask("Supreme Calamitas -- which AI?", AI_CHOICES, _AI_ALIASES, AI_DEFAULT,
                       argv, ("--ai",), "AI", out)
    if say:
        print("using the %s AI (%s)" % (answer, _blurb(AI_CHOICES, answer)), file=out)
    return answer
