"""
story.py - The story/world model for the click-and-move adventure.

Holds the running story (summary + history), the current scene (narration,
clickable objects, exits, choices), the player's avatar position, and the
typewriter reveal of the narration. Knows how to fold an AI "beat" into itself.
All of this is mutated on the main thread.
"""

import math
import random

# mood -> (sky_top, sky_bottom, accent) colors
MOODS = {
    "eerie":    ((18, 24, 30), (40, 52, 58), (120, 220, 200)),
    "radiant":  ((255, 226, 150), (255, 170, 120), (255, 255, 240)),
    "gloomy":   ((30, 32, 40), (52, 54, 66), (150, 160, 190)),
    "verdant":  ((30, 70, 50), (60, 110, 70), (200, 240, 160)),
    "molten":   ((60, 18, 18), (150, 50, 24), (255, 180, 80)),
    "frozen":   ((150, 190, 220), (210, 232, 245), (120, 200, 255)),
    "dreamy":   ((60, 50, 100), (140, 110, 180), (230, 200, 255)),
    "ashen":    ((40, 40, 44), (90, 88, 92), (200, 200, 205)),
    "golden":   ((120, 90, 30), (210, 170, 70), (255, 230, 150)),
    "abyssal":  ((6, 10, 26), (18, 26, 54), (90, 130, 230)),
    "rosy":     ((120, 60, 90), (220, 130, 150), (255, 210, 225)),
    "stormy":   ((38, 44, 56), (70, 80, 96), (180, 200, 230)),
    "verminous": ((40, 36, 28), (74, 64, 44), (180, 160, 90)),
}
DEFAULT_MOOD = ("dreamy", MOODS["dreamy"])

_NAMED = {
    "red": (210, 70, 70), "green": (90, 200, 110), "blue": (90, 130, 230),
    "yellow": (235, 210, 90), "purple": (180, 110, 220), "orange": (235, 150, 70),
    "pink": (235, 140, 185), "cyan": (90, 215, 215), "white": (240, 240, 245),
    "black": (40, 40, 48), "brown": (150, 100, 60), "gray": (150, 150, 160),
    "grey": (150, 150, 160), "gold": (235, 200, 90), "silver": (200, 205, 215),
    "teal": (60, 170, 160), "violet": (170, 120, 230), "crimson": (200, 40, 60),
    "amber": (235, 175, 70), "emerald": (60, 200, 130), "azure": (90, 160, 240),
}


def palette_for(mood):
    return MOODS.get(str(mood).strip().lower(), DEFAULT_MOOD[1])


def parse_color(value, default=(220, 220, 230)):
    try:
        if isinstance(value, str):
            s = value.strip().lower()
            if s in _NAMED:
                return _NAMED[s]
            if s.startswith("#"):
                s = s[1:]
            if len(s) == 6:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
            if len(s) == 3:
                return tuple(int(c * 2, 16) for c in s)
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return tuple(max(0, min(255, int(value[i]))) for i in range(3))
    except (ValueError, TypeError):
        pass
    return default


def _f01(v, default=0.5):
    try:
        return max(0.04, min(0.96, float(v)))
    except (TypeError, ValueError):
        return default


def _icon(v):
    s = str(v).strip()
    return s[0] if s else "*"


def normalize_beat(beat):
    """Clamp and clean a raw AI beat into a safe, renderable shape."""
    if not isinstance(beat, dict):
        beat = {}
    objs = []
    for o in (beat.get("objects") or [])[:6]:
        if not isinstance(o, dict):
            continue
        objs.append({
            "name": str(o.get("name", "something"))[:40],
            "icon": _icon(o.get("icon", "?")),
            "color": parse_color(o.get("color"), (210, 200, 180)),
            "x": _f01(o.get("x"), random.uniform(0.2, 0.8)),
            "y": _f01(o.get("y"), random.uniform(0.4, 0.72)),
            "action": str(o.get("action", "examine"))[:20],
        })
    exits = []
    for e in (beat.get("exits") or [])[:4]:
        if not isinstance(e, dict):
            continue
        exits.append({
            "name": str(e.get("name", "a way out"))[:40],
            "x": _f01(e.get("x"), random.uniform(0.1, 0.9)),
            "y": _f01(e.get("y"), random.uniform(0.35, 0.7)),
        })
    choices = [str(c)[:60] for c in (beat.get("choices") or [])[:4]
               if str(c).strip()]
    return {
        "thinking": str(beat.get("thinking", "")).strip(),
        "location": str(beat.get("location", "")).strip()[:48],
        "narration": str(beat.get("narration", "")).strip() or
                     "The world holds its breath, waiting for you.",
        "mood": str(beat.get("mood", "dreamy")).strip().lower(),
        "objects": objs,
        "exits": exits,
        "choices": choices,
    }


def _first_sentence(text):
    for sep in (". ", "! ", "? "):
        if sep in text:
            return text.split(sep)[0] + sep[0]
    return text[:90]


class Story:
    def __init__(self):
        self.summary = ""
        self.history = []
        self.turn = 0
        self.location = ""
        self.mood = "dreamy"
        self.narration = ""
        self.thinking = ""
        self.objects = []
        self.exits = []
        self.choices = []
        self.avatar = [0.5, 0.86]
        self.target = [0.5, 0.86]
        self.reveal = 0.0

    @property
    def has_scene(self):
        return bool(self.narration) and self.turn > 0

    # ---- folding in a beat ---------------------------------------------
    def apply_beat(self, beat):
        b = normalize_beat(beat)
        traveled = bool(b["location"]) and b["location"] != self.location
        if b["location"]:
            self.location = b["location"]
        self.mood = b["mood"]
        self.narration = b["narration"]
        self.thinking = b["thinking"]
        self.objects = b["objects"]
        self.exits = b["exits"]
        self.choices = b["choices"]
        self.reveal = 0.0
        self.turn += 1
        self.history.append("%s: %s" % (self.location or "?",
                                        _first_sentence(self.narration)))
        self.history = self.history[-12:]
        self.summary = " ".join(self.history[-6:])[:900]
        if traveled:
            self.avatar = [0.5, 0.95]   # walk in from the bottom edge
            self.target = [0.5, 0.85]

    # ---- context for the AI --------------------------------------------
    def context(self, action, opening=False, seed=""):
        return {
            "opening": opening,
            "seed": seed,
            "summary": self.summary,
            "history": list(self.history),
            "location": self.location,
            "action": action,
        }

    # ---- per-frame update ----------------------------------------------
    def update(self, dt):
        """Walk the avatar toward its target and reveal narration. Returns True
        on the frame the avatar reaches its target."""
        ax, ay = self.avatar
        tx, ty = self.target
        dx, dy = tx - ax, ty - ay
        dist = math.hypot(dx, dy)
        step = 0.75 * dt
        arrived = False
        if dist <= step or dist < 1e-4:
            self.avatar = [tx, ty]
            arrived = dist > 1e-4 or dist == 0
        else:
            self.avatar = [ax + dx / dist * step, ay + dy / dist * step]
        self.reveal = min(len(self.narration), self.reveal + dt * 42.0)
        return arrived

    def revealed(self):
        return self.narration[:int(self.reveal)]

    @property
    def revealing(self):
        return self.reveal < len(self.narration)

    def finish_reveal(self):
        self.reveal = len(self.narration)


# --------------------------------------------------------------------------
# Offline fallback storyteller (used when no model is reachable)
# --------------------------------------------------------------------------
_F_PLACES = ["The Hollow Market", "A Tilted Library", "The Salt Stair",
             "Garden of Spent Clocks", "The Last Lamplit Pier", "Underbloom",
             "The Cartographer's Throat", "A Room That Remembers You"]
_F_OBJ = [("a humming locket", "o"), ("a one-eyed cat", "c"), ("a leaning door", "D"),
          ("a jar of weather", "J"), ("a stranger in grey", "S"),
          ("a singing kettle", "K"), ("a map of nowhere", "M"), ("a cold candle", "i")]
_F_NARR = [
    "The floor exhales dust and the walls lean closer, curious about you.",
    "Something just changed its mind about being a wall.",
    "A voice you almost recognize finishes a sentence you never started.",
    "The light here is secondhand, borrowed from a brighter, kinder room.",
    "You arrive mid-thought, as if the place had been expecting a different you.",
]
_F_CHOICE = ["Call out into the dark", "Touch nothing and listen", "Turn back",
             "Follow the cold draft", "Say your own name aloud"]


def fallback_beat(ctx):
    """A surreal procedural beat so the game still breathes without a model."""
    r = random
    objs = r.sample(_F_OBJ, k=r.randint(2, 3))
    return {
        "thinking": "(offline) improvising something strange to keep you moving.",
        "location": r.choice(_F_PLACES),
        "narration": r.choice(_F_NARR),
        "mood": r.choice(list(MOODS.keys())),
        "objects": [
            {"name": n, "icon": ic, "color": r.choice(list(_NAMED.values())),
             "x": r.uniform(0.18, 0.82), "y": r.uniform(0.42, 0.72),
             "action": r.choice(["examine", "speak to", "take", "open"])}
            for (n, ic) in objs
        ],
        "exits": [{"name": "an unlikely doorway", "x": r.uniform(0.1, 0.9),
                   "y": r.uniform(0.4, 0.66)}],
        "choices": r.sample(_F_CHOICE, k=2),
    }
