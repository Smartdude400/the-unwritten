"""
dungeon_prompts.py - Instructions for the AI that INVENTS the shooter's content.

The engine runs composable mechanics, so the AI's job is to combine them into
genuinely new *kinds* of weapons and monsters - not just new numbers. Each call
returns ONE JSON object.
"""

GUN_SYS = """\
You INVENT guns for a fast top-down roguelite shooter. Combine mechanics to make
a genuinely NEW kind of weapon every time - not just different numbers. Return
ONE gun as a single JSON object, nothing else:
{
 "name": "short, evocative",
 "description": "one short line describing how it feels to fire",
 "color": "#rrggbb",
 "pattern": ONE firing pattern from:
    ["single","spread","ring","spiral","wave","arc","beam","orbit","burst"],
 "mods": a list of 0-3 projectile powers from:
    ["pierce","bounce","explode","home","split","poison","freeze","chain",
     "boomerang","giant"],
 "shape": bullet look from ["dot","bolt","star","diamond","blob","ring"],
 "damage": 4-40, "fire_rate": shots/sec 1-14, "bullet_speed": 400-1100,
 "count": bullets per shot 1-12, "spread": 0-50, "size": bullet size 0.7-2.2
}
Make the pattern + mods + name tell one coherent idea (e.g. a "spiral + poison"
plague-thrower, an "orbit" shield-gun, a "ring + bounce" pinball gun). Balance
power with fire_rate/count. Deeper floors allow wilder, stronger combos.
Output ONLY the JSON object."""

MONSTER_SYS = """\
You INVENT monsters for a top-down roguelite dungeon shooter. Combine mechanics
to make a genuinely NEW creature - a distinct movement style, attack(s), death
twist and traits - not just a stat change. They MUST get harder and MORE COMPLEX
as depth rises. Return ONE monster type as a single JSON object, nothing else:
{
 "name": "short, evocative",
 "description": "one short line",
 "glyph": "ONE character/symbol",
 "color": "#rrggbb",
 "size": 0.7-2.2,
 "hp": integer (grows a lot with depth),
 "speed": 40-260,
 "touch_damage": 4-25,
 "movement": ONE of
    ["chase","kite","orbit","zigzag","charger","teleporter","hover","wander"],
 "attacks": list of 1-3 from
    ["aimed","ring","spiral","spread","lob","beam","trail","summon"] (or ["none"]),
 "on_death": list of 0-2 from ["split","explode","summon","hazard"],
 "traits": list of 0-3 from
    ["shielded","fast","armored","regen","phasing","giant"],
 "fire_rate": 0.3-4, "bullet_speed": 110-560, "bullet_damage": 4-20,
 "bullet_count": 1-12, "spread": 0-360
}
Shallow floors: simple chasers. Deep floors: combine teleporters that fire
spirals and split on death, summoners that drop hazard trails, shielded orbiters
with ring volleys, etc. Make movement, attacks and name cohere into a concept.
Output ONLY the JSON object."""

ROOM_SYS = """\
You theme ROOMS for a dungeon shooter. Return ONE room theme as a single JSON
object, nothing else:
{"name": "short evocative floor name",
 "floor": "#rrggbb (darkish, readable)",
 "wall":  "#rrggbb (distinct from floor)",
 "accent":"#rrggbb (bright highlight)"}
Output ONLY the JSON object."""


def _depth_note(depth):
    if depth <= 2:
        return "Early floor (depth %d): keep it approachable." % depth
    if depth <= 6:
        return "Depth %d: ramp up threat and combine more mechanics." % depth
    return ("Depth %d: deep and dangerous - invent something genuinely complex "
            "and nasty." % depth)


def messages_for_gun(depth):
    return [{"role": "system", "content": GUN_SYS},
            {"role": "user", "content": "Depth %d. %s Invent one new gun. JSON "
             "only." % (depth, _depth_note(depth))}]


def messages_for_monster(depth):
    return [{"role": "system", "content": MONSTER_SYS},
            {"role": "user", "content": "Depth %d. %s Invent one new monster "
             "type. JSON only." % (depth, _depth_note(depth))}]


def messages_for_room(depth):
    return [{"role": "system", "content": ROOM_SYS},
            {"role": "user", "content": "Depth %d. Theme this floor. JSON only."
             % depth}]
