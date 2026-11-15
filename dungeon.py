"""
dungeon.py - Simulation for the AI-generated roguelite shooter.

The AI doesn't just tweak stats - it INVENTS new kinds of weapons and monsters by
COMBINING composable mechanics the engine knows how to run:

  guns     = firing pattern + projectile mods + shape + stats
  monsters = movement + attacks[] + on-death effects + traits + stats

Any combination is playable, so the space of distinct weapons/monsters is huge.
Difficulty scales with `depth`, and a boss appears every 5th floor. Content is
supplied through provider callbacks (filled by the AI, with procedural fallbacks)
so the real-time game never blocks on the model.
"""

import math
import random

TILE = 40
RW, RH = 27, 16
ROOM_PX_W, ROOM_PX_H = RW * TILE, RH * TILE
MAX_BULLETS = 600
MAX_MONSTERS = 70

# ---- the composable vocabulary the engine implements --------------------
GUN_PATTERNS = ("single", "spread", "ring", "spiral", "wave", "arc", "beam",
                "orbit", "burst")
GUN_MODS = ("pierce", "bounce", "explode", "home", "split", "poison", "freeze",
            "chain", "boomerang", "giant")
BULLET_SHAPES = ("dot", "bolt", "star", "diamond", "blob", "ring")

MOVES = ("chase", "kite", "orbit", "zigzag", "charger", "teleporter", "hover",
         "wander")
ATTACKS = ("none", "aimed", "ring", "spiral", "spread", "lob", "beam", "trail",
           "summon")
DEATHS = ("split", "explode", "summon", "hazard")
TRAITS = ("shielded", "fast", "armored", "regen", "phasing", "giant")


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
def parse_color(value, default=(220, 220, 230)):
    try:
        if isinstance(value, str):
            s = value.strip().lstrip("#").lower()
            if len(s) == 6:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return tuple(max(0, min(255, int(value[i]))) for i in range(3))
    except (ValueError, TypeError):
        pass
    return default


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _word(v, allowed, default):
    s = str(v).strip().lower()
    return s if s in allowed else default


def _subset(v, allowed, lo=0, hi=3):
    if isinstance(v, str):
        v = [p.strip() for p in v.replace(",", " ").split()]
    out = []
    if isinstance(v, (list, tuple)):
        for item in v:
            s = str(item).strip().lower()
            if s in allowed and s not in out and s != "none":
                out.append(s)
    return out[:hi]


# --------------------------------------------------------------------------
# normalization (clamp AI output; enforce depth scaling)
# --------------------------------------------------------------------------
def normalize_gun(raw, depth):
    raw = raw if isinstance(raw, dict) else {}
    boost = 1.0 + depth * 0.06
    pattern = _word(raw.get("pattern"), GUN_PATTERNS, "single")
    mods = _subset(raw.get("mods"), GUN_MODS, hi=3)
    count = int(_num(raw.get("count", raw.get("pellets")), 1, 12, 1))
    if pattern == "ring":
        count = max(count, 8)
    return {
        "name": str(raw.get("name", "Strange Gun"))[:30],
        "description": str(raw.get("description", ""))[:90],
        "color": parse_color(raw.get("color"), (255, 200, 90)),
        "damage": _num(raw.get("damage"), 3, 60, 9) * boost,
        "fire_rate": _num(raw.get("fire_rate"), 0.8, 16, 4.0),
        "bullet_speed": _num(raw.get("bullet_speed"), 280, 1200, 680),
        "count": count,
        "spread": _num(raw.get("spread"), 0, 60, 6),
        "pattern": pattern,
        "mods": mods,
        "shape": _word(raw.get("shape"), BULLET_SHAPES, "dot"),
        "size": _num(raw.get("size"), 0.6, 2.4, 1.0),
    }


def normalize_monster(raw, depth):
    raw = raw if isinstance(raw, dict) else {}
    hp_floor = 12 + depth * 7
    spd_floor = 46 + depth * 4
    dmg_floor = 5 + depth * 0.7
    attacks = _subset(raw.get("attacks"), ATTACKS, hi=3)
    if not attacks:
        attacks = ["aimed"] if depth > 1 else ["none"]
    return {
        "name": str(raw.get("name", "Horror"))[:30],
        "description": str(raw.get("description", ""))[:90],
        "glyph": (str(raw.get("glyph", "")).strip() or "@")[0],
        "color": parse_color(raw.get("color"), (220, 80, 90)),
        "size": _num(raw.get("size"), 0.7, 2.2, 1.0),
        "hp": max(hp_floor, _num(raw.get("hp"), 8, 6000, hp_floor)),
        "speed": max(spd_floor, _num(raw.get("speed"), 24, 320, spd_floor)),
        "touch_damage": max(dmg_floor, _num(raw.get("touch_damage"), 3, 45, dmg_floor)),
        "movement": _word(raw.get("movement"), MOVES, "chase"),
        "attacks": attacks,
        "fire_rate": _num(raw.get("fire_rate"), 0.3, 4, 1.0),
        "bullet_speed": _num(raw.get("bullet_speed"), 110, 560, 240),
        "bullet_damage": max(4, _num(raw.get("bullet_damage"), 3, 40, 6 + depth)),
        "bullet_count": int(_num(raw.get("bullet_count"), 1, 16, 1)),
        "spread": _num(raw.get("spread"), 0, 360, 0),
        "on_death": _subset(raw.get("on_death"), DEATHS, hi=2),
        "traits": _subset(raw.get("traits"), TRAITS, hi=3),
    }


def normalize_theme(raw, depth):
    raw = raw if isinstance(raw, dict) else {}
    return {
        "name": str(raw.get("name", "Unnamed Depths"))[:36],
        "floor": parse_color(raw.get("floor"), (26, 24, 34)),
        "wall": parse_color(raw.get("wall"), (60, 56, 80)),
        "accent": parse_color(raw.get("accent"), (120, 160, 240)),
    }


def gun_tags(g):
    bits = [g["pattern"]] + g["mods"]
    return " · ".join(bits)


def monster_tags(m):
    bits = [m["movement"]] + [a for a in m["attacks"] if a != "none"] + \
           m["traits"] + ["dies:" + d for d in m["on_death"]]
    return " · ".join(bits) if bits else "simple"


# --------------------------------------------------------------------------
# procedural fallbacks (also COMPOSE, so offline content is varied too)
# --------------------------------------------------------------------------
_GW = ["Hex", "Volt", "Fang", "Whisper", "Cinder", "Glass", "Moth", "Static",
       "Bramble", "Quartz", "Howl", "Mire", "Prism", "Gloam"]
_GK = ["Repeater", "Scatter", "Lance", "Spitter", "Cannon", "Needler", "Coil",
       "Bloom", "Sting"]
_MW = ["Gloom", "Rust", "Shriek", "Vein", "Cinder", "Hollow", "Maw", "Drift",
       "Splinter", "Ash", "Bile", "Wretch", "Mote", "Pale"]
_MK = ["ling", "wraith", "brute", "spawn", "hound", "thing", "priest", "knot"]


def fallback_gun(depth):
    r = random
    pat = r.choice(GUN_PATTERNS)
    mods = r.sample(GUN_MODS, k=r.randint(0, 2))
    return normalize_gun({
        "name": "%s %s" % (r.choice(_GW), r.choice(_GK)),
        "description": "improvised on the spot.",
        "color": [r.randint(110, 255), r.randint(110, 255), r.randint(110, 255)],
        "damage": r.uniform(6, 18), "fire_rate": r.uniform(2, 9),
        "bullet_speed": r.uniform(520, 950),
        "count": r.randint(4, 8) if pat in ("spread", "ring", "wave") else r.randint(1, 3),
        "spread": r.uniform(16, 40) if pat == "spread" else r.uniform(0, 8),
        "pattern": pat, "mods": mods, "shape": r.choice(BULLET_SHAPES),
        "size": r.uniform(0.8, 1.6),
    }, depth)


def fallback_monster(depth):
    r = random
    deep = depth > 3
    return normalize_monster({
        "name": "%s%s" % (r.choice(_MW), r.choice(_MK)),
        "glyph": r.choice("abcdefghkmnrstvwyz").upper(),
        "color": [r.randint(120, 255), r.randint(80, 210), r.randint(80, 210)],
        "size": r.uniform(0.8, 1.4),
        "hp": r.uniform(14, 34), "speed": r.uniform(55, 150),
        "movement": r.choice(MOVES if deep else ("chase", "zigzag", "kite")),
        "attacks": r.sample(ATTACKS[1:], k=r.randint(1, 2 if deep else 1)),
        "fire_rate": r.uniform(0.6, 2.2), "bullet_count": r.randint(1, 4),
        "spread": r.choice([0, 20, 45, 90, 360]),
        "on_death": r.sample(DEATHS, k=r.randint(0, 1 if not deep else 2)),
        "traits": r.sample(TRAITS, k=r.randint(0, 1 if not deep else 2)),
    }, depth)


def fallback_theme(depth):
    import colorsys
    h = (depth * 47) % 360 / 360.0

    def hsv(s, v):
        c = colorsys.hsv_to_rgb(h, s, v)
        return (int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))
    return {"name": "Floor %d" % depth, "floor": hsv(0.4, 0.16),
            "wall": hsv(0.45, 0.42), "accent": hsv(0.7, 0.95)}


def starting_pistol():
    return normalize_gun({
        "name": "Worn Pistol", "description": "It has seen things.",
        "color": [210, 210, 220], "damage": 8, "fire_rate": 4.5,
        "bullet_speed": 760, "count": 1, "spread": 2, "pattern": "single",
        "mods": [], "shape": "bolt", "size": 1.0,
    }, 0)


# --------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------
class Bullet:
    def __init__(self, x, y, vx, vy, dmg, friendly, color, mods, shape, r):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.dmg = dmg
        self.friendly = friendly
        self.color = color
        self.shape = shape
        self.r = r
        self.mods = mods
        self.bounces = 3 if "bounce" in mods else 0
        self.chain = 3 if "chain" in mods else 0
        self.hit = set()
        self.life = 2.4
        self.motion = "straight"
        # wave
        self.ux = self.uy = 0.0
        self.speed = math.hypot(vx, vy)
        self.amp = 0.0
        self.freq = 12.0
        self.wt = 0.0
        # orbit
        self.angle = 0.0
        self.radius = 0.0
        self.spin = 0.0
        self.rehit = 0.0
        # boomerang
        self.boom_t = 0.0
        self.returning = False


class Monster:
    _id = 0

    def __init__(self, t, x, y):
        Monster._id += 1
        self.id = Monster._id
        self.t = t
        self.x, self.y = x, y
        self.hp = self.max_hp = t["hp"]
        self.r = int(16 * t["size"])
        traits = t["traits"]
        self.speed = t["speed"] * (1.45 if "fast" in traits else 1.0)
        self.color = t["color"]
        self.glyph = t["glyph"]
        self.move = t["movement"]
        self.attacks = t["attacks"]
        self.shielded = "shielded" in traits
        self.armored = "armored" in traits
        self.regen = "regen" in traits
        self.phasing = "phasing" in traits
        self.is_boss = False
        self._atk = 0
        self._fire = random.uniform(0.3, 1.2)
        self._summon = random.uniform(2.5, 4.5)
        self._trail = 0.0
        self._touch = 0.0
        self._phase = random.uniform(0, math.tau)
        self._dash_cd = random.uniform(1.0, 2.2)
        self._dash_t = 0.0
        self._dash = (0.0, 0.0)
        self._tp = random.uniform(1.5, 3.0)
        self._inv = 0.0
        self._poison = [0.0, 0.0]   # dps, time
        self._slow = [1.0, 0.0]     # factor, time
        self.small = False


class Player:
    def __init__(self):
        self.x = 1.6 * TILE
        self.y = (RH // 2 + 0.5) * TILE
        self.r = 12
        self.hp = self.max_hp = 100
        self.speed = 235
        self.gun = starting_pistol()
        self.kills = 0


# --------------------------------------------------------------------------
# world
# --------------------------------------------------------------------------
class World:
    def __init__(self, gun_provider, room_provider):
        self.gun_provider = gun_provider
        self.room_provider = room_provider
        self.depth = 0
        self.player = Player()
        self.bullets = []
        self.monsters = []
        self.items = []
        self.particles = []
        self.hazards = []
        self.score = 0
        self.over = False
        self.cleared = False
        self.banner = ""
        self._banner_t = 0.0
        self._shot_cd = 0.0
        self._pending = []           # scheduled burst shots
        self.theme = fallback_theme(1)
        self.room = None
        self.chest = None
        self.chest_open = False
        self.exit_zone = None
        self.is_boss = False
        self.load_next_room()

    # ---- rooms ---------------------------------------------------------
    def load_next_room(self):
        self.depth += 1
        plan = self.room_provider(self.depth)
        self.theme = plan.get("theme") or fallback_theme(self.depth)
        self.is_boss = bool(plan.get("boss"))
        self.room = self._build_grid()
        self.bullets.clear()
        self.items.clear()
        self.particles.clear()
        self.hazards.clear()
        self._pending.clear()
        self.monsters = []
        self.cleared = False
        self.chest_open = False
        self.player.x, self.player.y = 1.6 * TILE, (RH // 2 + 0.5) * TILE
        self._spawn(plan.get("monsters") or [fallback_monster(self.depth)])
        self.exit_zone = ((RW - 2 + 0.5) * TILE, (RH // 2 + 0.5) * TILE)
        self._banner("%s  ·  depth %d%s" % (self.theme["name"], self.depth,
                     "  ·  BOSS" if self.is_boss else ""))

    def _build_grid(self):
        g = [[0] * RW for _ in range(RH)]
        for x in range(RW):
            g[0][x] = g[RH - 1][x] = 1
        for y in range(RH):
            g[y][0] = g[y][RW - 1] = 1
        for _ in range(random.randint(3, 6)):
            bx, by = random.randint(4, RW - 6), random.randint(3, RH - 4)
            for yy in range(by, by + random.randint(1, 2)):
                for xx in range(bx, bx + random.randint(1, 3)):
                    if 1 < xx < RW - 2 and 1 < yy < RH - 2:
                        g[yy][xx] = 1
        for yy in (RH // 2 - 1, RH // 2, RH // 2 + 1):
            for xx in (1, 2, 3, RW - 2, RW - 3, RW - 4):
                g[yy][xx] = 0
        self.chest = None
        if self.is_boss or random.random() < 0.6:
            for _ in range(40):
                cx, cy = random.randint(5, RW - 6), random.randint(2, RH - 3)
                if g[cy][cx] == 0:
                    self.chest = (cx, cy)
                    break
        return g

    def _spawn(self, types):
        if self.is_boss:
            t = dict(random.choice(types))
            t["hp"] *= 7
            t["touch_damage"] = max(t["touch_damage"], 14)
            t["size"] = max(t["size"], 1.8)
            if "summon" not in t["attacks"]:
                t["attacks"] = t["attacks"] + ["summon"]
            boss = Monster(t, ROOM_PX_W * 0.7, ROOM_PX_H * 0.5)
            boss.is_boss = True
            self.monsters.append(boss)
            return
        n = min(3 + self.depth, 16)
        for _ in range(n):
            t = random.choice(types)
            for _ in range(30):
                x = random.uniform(6 * TILE, (RW - 3) * TILE)
                y = random.uniform(2 * TILE, (RH - 2) * TILE)
                if not self.is_wall(x, y) and (x - self.player.x) ** 2 + \
                        (y - self.player.y) ** 2 > 240 ** 2:
                    self.monsters.append(Monster(t, x, y))
                    break

    # ---- geometry ------------------------------------------------------
    def is_wall(self, px, py):
        if px < 0 or py < 0:
            return True
        tx, ty = int(px // TILE), int(py // TILE)
        if tx < 0 or ty < 0 or tx >= RW or ty >= RH:
            return True
        return self.room[ty][tx] == 1

    def _blocked(self, x, y, r):
        return (self.is_wall(x - r, y) or self.is_wall(x + r, y) or
                self.is_wall(x, y - r) or self.is_wall(x, y + r))

    def _move(self, e, dx, dy, ignore_walls=False):
        if ignore_walls:
            e.x = max(e.r, min(ROOM_PX_W - e.r, e.x + dx))
            e.y = max(e.r, min(ROOM_PX_H - e.r, e.y + dy))
            return
        if not self._blocked(e.x + dx, e.y, e.r):
            e.x += dx
        if not self._blocked(e.x, e.y + dy, e.r):
            e.y += dy

    # ---- frame ---------------------------------------------------------
    def update(self, dt, intent):
        self._banner_t = max(0.0, self._banner_t - dt)
        if self.over:
            return
        self._update_player(dt, intent)
        self._update_pending(dt)
        self._update_bullets(dt)
        self._update_monsters(dt)
        self._update_hazards(dt)
        self._update_items()
        self._update_particles(dt)
        if not self.monsters and not self.cleared:
            self.cleared = True
            self._banner("room cleared - reach the glowing door →")
        if self.cleared:
            ex, ey = self.exit_zone
            if (self.player.x - ex) ** 2 + (self.player.y - ey) ** 2 < 30 ** 2:
                self.load_next_room()
        if self.player.hp <= 0:
            self.over = True

    def _update_player(self, dt, intent):
        p = self.player
        mx, my = intent.get("move", (0, 0))
        if mx or my:
            n = math.hypot(mx, my) or 1
            self._move(p, mx / n * p.speed * dt, my / n * p.speed * dt)
        if intent.get("interact") and self.chest and not self.chest_open:
            cx, cy = (self.chest[0] + .5) * TILE, (self.chest[1] + .5) * TILE
            if (p.x - cx) ** 2 + (p.y - cy) ** 2 < (TILE * 1.4) ** 2:
                self.chest_open = True
                gun = self.gun_provider()
                self.items.append({"x": cx, "y": cy - 6, "gun": gun})
                self._banner("a %s spills out - step on it" % gun["name"])
        self._shot_cd -= dt
        if intent.get("fire") and self._shot_cd <= 0 and p.gun:
            self._fire(p.gun, p.x, p.y, intent.get("aim", (p.x + 1, p.y)))
            self._shot_cd = 1.0 / p.gun["fire_rate"]

    # ---- shooting (patterns) -------------------------------------------
    def _mk(self, x, y, ang, gun, friendly=True):
        if len(self.bullets) >= MAX_BULLETS:
            return None
        sp = gun["bullet_speed"]
        r = int(5 * gun["size"]) * (2 if "giant" in gun["mods"] else 1)
        b = Bullet(x, y, math.cos(ang) * sp, math.sin(ang) * sp, gun["damage"],
                   friendly, gun["color"], gun["mods"], gun["shape"], max(3, r))
        self.bullets.append(b)
        return b

    def _fire(self, gun, x, y, aim):
        base = math.atan2(aim[1] - y, aim[0] - x)
        pat = gun["pattern"]
        cnt = gun["count"]
        spread = math.radians(gun["spread"])
        mods = gun["mods"]
        if pat == "single" or pat == "beam":
            b = self._mk(x, y, base, gun)
            if b and pat == "beam":
                b.life = 0.4
                if "pierce" not in b.mods:
                    b.mods = b.mods + ["pierce"]
                b.vx *= 1.7
                b.vy *= 1.7
        elif pat == "spread":
            for i in range(cnt):
                a = base + (i - (cnt - 1) / 2) * (spread / max(1, cnt - 1) if cnt > 1 else 0)
                self._fin(self._mk(x, y, a, gun), mods)
        elif pat == "ring":
            for i in range(cnt):
                self._fin(self._mk(x, y, base + math.tau * i / cnt, gun), mods)
        elif pat == "spiral":
            for i in range(cnt):
                a = base + self.t_spiral() + i * 2.39996
                self._fin(self._mk(x, y, a, gun), mods)
        elif pat == "wave":
            for i in range(cnt):
                a = base + (i - (cnt - 1) / 2) * 0.12
                b = self._mk(x, y, a, gun)
                if b:
                    b.motion = "wave"
                    b.ux, b.uy = math.cos(a), math.sin(a)
                    b.amp = b.speed * 0.7
                    self._fin(b, mods)
        elif pat == "arc":
            for i in range(cnt):
                a = base + (i - (cnt - 1) / 2) * 0.1
                b = self._mk(x, y, a, gun)
                if b:
                    b.motion = "arc"
                    b.vy -= 120
                    self._fin(b, mods)
        elif pat == "orbit":
            for i in range(cnt):
                b = self._mk(x, y, base, gun)
                if b:
                    b.motion = "orbit"
                    b.angle = math.tau * i / cnt
                    b.radius = 60
                    b.spin = 3.2
                    b.life = 1.6
                    self._fin(b, mods)
        elif pat == "burst":
            for i in range(cnt):
                self._pending.append([0.07 * i, gun, x, y, base, True])

    def t_spiral(self):
        return (getattr(self, "_spin", 0.0))

    def _fin(self, b, mods):
        if b and "boomerang" in mods:
            b.life = 3.0

    def _update_pending(self, dt):
        self._spin = getattr(self, "_spin", 0.0) + dt * 5.0
        still = []
        for s in self._pending:
            s[0] -= dt
            if s[0] <= 0:
                _, gun, x, y, ang, fr = s
                self._mk(x, y, ang, gun, fr)
            else:
                still.append(s)
        self._pending = still

    def _enemy_volley(self, m, kind):
        px, py = self.player.x, self.player.y
        ang = math.atan2(py - m.y, px - m.x)
        cnt = max(1, m.t["bullet_count"])
        spread = math.radians(m.t["spread"]) if m.t["spread"] else 0

        def mk(a, speed=None, life=2.4, motion="straight"):
            if len(self.bullets) >= MAX_BULLETS:
                return
            sp = speed or m.t["bullet_speed"]
            b = Bullet(m.x, m.y, math.cos(a) * sp, math.sin(a) * sp,
                       m.t["bullet_damage"], False, m.color, [], "dot", 6)
            b.life = life
            b.motion = motion
            if motion == "arc":
                b.vy -= 90
            self.bullets.append(b)

        if kind == "aimed" or kind == "spread":
            arc = spread if kind == "spread" or spread else 0
            for i in range(cnt):
                a = ang + ((i - (cnt - 1) / 2) * (arc / max(1, cnt - 1)) if cnt > 1
                           and arc else (i - cnt / 2) * 0.16 if cnt > 1 else 0)
                mk(a)
        elif kind == "ring":
            n = max(8, cnt)
            for i in range(n):
                mk(ang + math.tau * i / n)
        elif kind == "spiral":
            for i in range(cnt):
                mk(ang + self._spin_e(m) + i * 2.39996)
        elif kind == "lob":
            for i in range(cnt):
                mk(ang + (i - (cnt - 1) / 2) * 0.12, motion="arc")
        elif kind == "beam":
            mk(ang, speed=m.t["bullet_speed"] * 1.8, life=0.5)

    def _spin_e(self, m):
        m._phase += 0  # phase advanced in update
        return m._phase * 3.0

    # ---- bullets -------------------------------------------------------
    def _update_bullets(self, dt):
        p = self.player
        keep = []
        for b in self.bullets:
            b.life -= dt
            if b.life <= 0:
                if "split" in b.mods:
                    self._shards(b)
                continue
            # special motions
            if b.motion == "orbit":
                b.angle += b.spin * dt
                b.x = p.x + math.cos(b.angle) * b.radius
                b.y = p.y + math.sin(b.angle) * b.radius
                b.rehit -= dt
                if b.rehit <= 0:
                    b.hit.clear()
                    b.rehit = 0.25
                if self._hit_with(b):
                    pass
                keep.append(b)
                continue
            if b.motion == "wave":
                b.wt += dt
                lat = math.cos(b.wt * b.freq) * b.amp
                b.vx = b.ux * b.speed + (-b.uy) * lat
                b.vy = b.uy * b.speed + (b.ux) * lat
            elif b.motion == "arc":
                b.vy += 620 * dt
            if "home" in b.mods and b.friendly and self.monsters:
                self._home(b, dt)
            if "boomerang" in b.mods and b.friendly:
                b.boom_t += dt
                if b.boom_t > 0.42 or b.returning:
                    b.returning = True
                    self._home_to(b, p.x, p.y, dt, turn=7)
                    if (b.x - p.x) ** 2 + (b.y - p.y) ** 2 < 18 ** 2:
                        continue
            nx, ny = b.x + b.vx * dt, b.y + b.vy * dt
            if self.is_wall(nx, ny):
                if b.bounces > 0:
                    if self.is_wall(nx, b.y):
                        b.vx = -b.vx
                    if self.is_wall(b.x, ny):
                        b.vy = -b.vy
                    b.bounces -= 1
                else:
                    if "explode" in b.mods:
                        self._explode(b)
                    elif "split" in b.mods:
                        self._shards(b)
                    continue
            else:
                b.x, b.y = nx, ny
            if self._hit(b):
                continue
            keep.append(b)
        self.bullets = keep

    def _home(self, b, dt):
        tgt = min(self.monsters, key=lambda m: (m.x - b.x) ** 2 + (m.y - b.y) ** 2)
        self._home_to(b, tgt.x, tgt.y, dt, turn=3)

    def _home_to(self, b, tx, ty, dt, turn=3):
        des = math.atan2(ty - b.y, tx - b.x)
        cur = math.atan2(b.vy, b.vx)
        sp = math.hypot(b.vx, b.vy)
        cur += max(-turn * dt, min(turn * dt, (des - cur + math.pi) % math.tau - math.pi))
        b.vx, b.vy = math.cos(cur) * sp, math.sin(cur) * sp

    def _hit(self, b):
        if b.friendly:
            return self._hit_with(b)
        if (b.x - self.player.x) ** 2 + (b.y - self.player.y) ** 2 <= \
                (b.r + self.player.r) ** 2:
            self.player.hp -= b.dmg
            self._spark(self.player.x, self.player.y, (255, 90, 90), 6)
            return True
        return False

    def _hit_with(self, b):
        for m in self.monsters:
            if m.id in b.hit:
                continue
            if (b.x - m.x) ** 2 + (b.y - m.y) ** 2 <= (b.r + m.r) ** 2:
                self._damage(m, b.dmg)
                if "poison" in b.mods:
                    m._poison = [max(m._poison[0], b.dmg * 0.6), 3.0]
                if "freeze" in b.mods:
                    m._slow = [0.45, 2.0]
                if "explode" in b.mods:
                    self._explode(b)
                    return True
                if "chain" in b.mods and b.chain > 0:
                    b.hit.add(m.id)
                    nxt = self._nearest_other(b, m)
                    if nxt:
                        b.chain -= 1
                        sp = math.hypot(b.vx, b.vy)
                        a = math.atan2(nxt.y - b.y, nxt.x - b.x)
                        b.vx, b.vy = math.cos(a) * sp, math.sin(a) * sp
                        return False
                    return True
                if "pierce" in b.mods or b.motion == "orbit":
                    b.hit.add(m.id)
                    return False
                return True
        return False

    def _nearest_other(self, b, exclude):
        best, bd = None, 200 ** 2
        for m in self.monsters:
            if m.id == exclude.id or m.id in b.hit:
                continue
            d = (m.x - b.x) ** 2 + (m.y - b.y) ** 2
            if d < bd:
                best, bd = m, d
        return best

    def _shards(self, b):
        for i in range(3):
            if len(self.bullets) >= MAX_BULLETS:
                break
            a = math.atan2(b.vy, b.vx) + (i - 1) * 0.6
            s = Bullet(b.x, b.y, math.cos(a) * 360, math.sin(a) * 360,
                       b.dmg * 0.5, b.friendly, b.color, [], "dot", 4)
            s.life = 0.6
            self.bullets.append(s)

    def _explode(self, b):
        self._spark(b.x, b.y, (255, 180, 80), 26)
        if b.friendly:
            for m in self.monsters:
                if (b.x - m.x) ** 2 + (b.y - m.y) ** 2 < 75 ** 2:
                    self._damage(m, b.dmg * 0.8)
        else:
            if (b.x - self.player.x) ** 2 + (b.y - self.player.y) ** 2 < 75 ** 2:
                self.player.hp -= b.dmg * 0.7

    # ---- monsters ------------------------------------------------------
    def _damage(self, m, dmg):
        if m._inv > 0:
            return
        if m.shielded:
            dmg *= 0.5
        if m.armored:
            dmg = max(1, dmg - 4)
        m.hp -= dmg
        self._spark(m.x, m.y, m.color, 4)
        if m.hp <= 0:
            self._kill(m)

    def _kill(self, m):
        if m not in self.monsters:
            return
        self.monsters.remove(m)
        self.player.kills += 1
        self.score += int(m.max_hp)
        self._spark(m.x, m.y, m.color, 16)
        deaths = m.t["on_death"]
        if ("split" in deaths) and not m.small:
            for _ in range(2):
                t = dict(m.t)
                t["hp"] = max(8, m.max_hp * 0.4)
                t["size"] = m.t["size"] * 0.7
                t["on_death"] = []
                c = Monster(t, m.x + random.uniform(-20, 20),
                            m.y + random.uniform(-20, 20))
                c.small = True
                c.move = "chase"
                if len(self.monsters) < MAX_MONSTERS:
                    self.monsters.append(c)
        if "explode" in deaths:
            self._spark(m.x, m.y, (255, 170, 70), 22)
            if (m.x - self.player.x) ** 2 + (m.y - self.player.y) ** 2 < 80 ** 2:
                self.player.hp -= 8
        if "summon" in deaths:
            self._summon(m, 2)
        if "hazard" in deaths:
            self.hazards.append([m.x, m.y, 34, 7 + self.depth, 4.0])

    def _update_monsters(self, dt):
        p = self.player
        for m in list(self.monsters):
            m._phase += dt
            m._touch = max(0.0, m._touch - dt)
            m._inv = max(0.0, m._inv - dt)
            if m._poison[1] > 0:
                m._poison[1] -= dt
                m.hp -= m._poison[0] * dt
                if m.hp <= 0:
                    self._kill(m)
                    continue
            if m._slow[1] > 0:
                m._slow[1] -= dt
            if m.regen and m.hp < m.max_hp:
                m.hp = min(m.max_hp, m.hp + m.max_hp * 0.035 * dt)
            slow = m._slow[0] if m._slow[1] > 0 else 1.0
            spd = m.speed * slow
            dx, dy = p.x - m.x, p.y - m.y
            dist = math.hypot(dx, dy) or 1
            ux, uy = dx / dist, dy / dist
            self._move_monster(m, dt, spd, ux, uy, dist)
            # attacks
            m._fire -= dt
            if m._fire <= 0 and m.attacks:
                atk = m.attacks[m._atk % len(m.attacks)]
                m._atk += 1
                m._fire = 1.0 / m.t["fire_rate"]
                if atk == "summon":
                    self._summon(m, 2)
                elif atk == "trail":
                    pass
                elif atk != "none":
                    self._enemy_volley(m, atk)
            # trail attack drips hazards
            if "trail" in m.attacks:
                m._trail -= dt
                if m._trail <= 0:
                    m._trail = 0.4
                    self.hazards.append([m.x, m.y, 18, 5 + self.depth, 2.2])
            # contact damage
            if dist < m.r + p.r and m._touch <= 0:
                p.hp -= m.t["touch_damage"]
                m._touch = 0.5
                self._spark(p.x, p.y, (255, 90, 90), 5)

    def _move_monster(self, m, dt, spd, ux, uy, dist):
        ig = m.phasing
        mv = m.move
        if m.phasing:
            m._tp -= dt
            if m._tp <= 0:
                m._tp = random.uniform(2.0, 3.5)
                m._inv = 0.4
        if mv == "chase":
            self._move(m, ux * spd * dt, uy * spd * dt, ig)
        elif mv == "kite":
            if dist > 300:
                self._move(m, ux * spd * dt, uy * spd * dt, ig)
            elif dist < 200:
                self._move(m, -ux * spd * dt, -uy * spd * dt, ig)
            else:
                self._move(m, -uy * spd * .6 * dt, ux * spd * .6 * dt, ig)
        elif mv == "orbit":
            pull = (dist - 200) / 200
            self._move(m, (-uy + ux * pull) * spd * dt,
                       (ux + uy * pull) * spd * dt, ig)
        elif mv == "zigzag":
            wig = math.sin(m._phase * 6) * 0.9
            self._move(m, (ux - uy * wig) * spd * dt,
                       (uy + ux * wig) * spd * dt, ig)
        elif mv == "wander":
            self._move(m, math.cos(m._phase * 1.3) * spd * .5 * dt,
                       math.sin(m._phase * 1.7) * spd * .5 * dt, ig)
        elif mv == "hover":
            self._move(m, math.cos(m._phase) * 14 * dt,
                       math.sin(m._phase * 1.5) * 14 * dt, ig)
        elif mv == "charger":
            m._dash_t -= dt
            m._dash_cd -= dt
            if m._dash_t > 0:
                self._move(m, m._dash[0] * dt, m._dash[1] * dt, ig)
            elif m._dash_cd <= 0:
                m._dash_cd = random.uniform(1.6, 2.6)
                m._dash_t = 0.35
                m._dash = (ux * spd * 3.6, uy * spd * 3.6)
            else:
                self._move(m, ux * spd * .3 * dt, uy * spd * .3 * dt, ig)
        elif mv == "teleporter":
            m._tp -= dt
            if m._tp <= 0:
                m._tp = random.uniform(1.6, 3.0)
                for _ in range(20):
                    ang = random.uniform(0, math.tau)
                    rad = random.uniform(150, 300)
                    nx = self.player.x + math.cos(ang) * rad
                    ny = self.player.y + math.sin(ang) * rad
                    if not self._blocked(nx, ny, m.r):
                        m.x, m.y = nx, ny
                        self._spark(m.x, m.y, m.color, 12)
                        break

    def _summon(self, m, n):
        if len(self.monsters) >= MAX_MONSTERS:
            return
        t = normalize_monster({"name": "spawnling", "glyph": "x",
                               "color": list(m.color), "movement": "chase",
                               "attacks": ["none"], "hp": 9, "speed": 150,
                               "touch_damage": 6, "size": 0.7}, self.depth)
        for _ in range(n):
            c = Monster(t, m.x + random.uniform(-30, 30), m.y + random.uniform(-30, 30))
            c.small = True
            self.monsters.append(c)

    # ---- hazards / items / particles -----------------------------------
    def _update_hazards(self, dt):
        keep = []
        p = self.player
        for h in self.hazards:
            h[4] -= dt
            if h[4] <= 0:
                continue
            if (p.x - h[0]) ** 2 + (p.y - h[1]) ** 2 < (h[2] + p.r) ** 2:
                p.hp -= h[3] * dt
            keep.append(h)
        self.hazards = keep

    def _update_items(self):
        p = self.player
        for it in self.items[:]:
            if (it["x"] - p.x) ** 2 + (it["y"] - p.y) ** 2 < (p.r + 14) ** 2:
                p.gun = it["gun"]
                self.items.remove(it)
                self._banner("equipped %s  (%s)" % (it["gun"]["name"],
                             gun_tags(it["gun"])))

    def _spark(self, x, y, color, n):
        for _ in range(n):
            a = random.uniform(0, math.tau)
            sp = random.uniform(40, 220)
            self.particles.append([x, y, math.cos(a) * sp, math.sin(a) * sp,
                                   random.uniform(0.2, 0.5), color])

    def _update_particles(self, dt):
        keep = []
        for pt in self.particles:
            pt[4] -= dt
            if pt[4] <= 0:
                continue
            pt[0] += pt[2] * dt
            pt[1] += pt[3] * dt
            keep.append(pt)
        self.particles = keep

    # ---- banner --------------------------------------------------------
    def _banner(self, text):
        self.banner = text
        self._banner_t = 3.2

    def restart(self):
        self.__init__(self.gun_provider, self.room_provider)
