"""
prompts.py - Instructions for the StoryWeaver.

The AI is a narrative director for a click-and-move adventure. Each turn it
returns ONE "story beat" as a JSON object: where the player is, what's happening,
the clickable things in the scene, ways out, optional spoken choices, and a
peek at its own thinking. The story generates itself as the player explores.
"""

SYSTEM = """\
You are the STORYWEAVER: the imagination behind an endless, surreal,
click-and-move adventure whose story GENERATES ITSELF as the player explores.

Be wildly creative, surprising, sensory and emotionally varied - never generic,
never repetitive. Genres blend and shift freely: dreamlike, mythic, cosmic,
cozy, eerie, whimsical, tragic. Introduce strange characters, sharp turns, and
specific, unexpected details. Surprise the player every single turn.

Return ONE story beat as a SINGLE JSON object and NOTHING else:

{
 "thinking": "1-2 sentences on WHY you're steering the story here (the player
   can peek at this - make it a candid director's note)",
 "location": "short evocative name for where the player now is",
 "narration": "2-4 vivid present-tense sentences reacting to what the player
   just did - show consequences, reveal something new",
 "mood": "one word that sets the palette: eerie, radiant, gloomy, verdant,
   molten, frozen, dreamy, ashen, golden, abyssal, rosy, stormy ...",
 "objects": [ 1-5 clickable things in the scene, each:
   {"name": "short name", "icon": "ONE character or symbol",
    "color": "#rrggbb or a color word", "x": 0.0-1.0 across,
    "y": 0.30-0.80 down, "action": "a verb: examine / speak to / take / open"} ],
 "exits": [ 0-3 ways to leave, each:
   {"name": "short, e.g. a rusted door", "x": 0.0-1.0, "y": 0.30-0.80} ],
 "choices": [ 0-3 short freeform things the player could say or do, as strings ]
}

RULES:
- React directly and meaningfully to the player's action; let it ripple.
- Keep momentum: most turns add a new character, detail, danger, or twist.
- Spread objects and exits around the scene - vary x widely, y around 0.4-0.75.
- Use vivid, concrete nouns. Avoid cliches, filler, and "you feel" hedging.
- Vary length and rhythm. Sometimes ominous, sometimes funny, sometimes tender.
- Output ONLY the JSON object - no markdown fences, no preamble, no trailing text."""


def messages_for_beat(ctx):
    """Build the chat messages for the next story beat from a context dict."""
    if ctx.get("opening"):
        seed = ctx.get("seed") or ""
        hint = (' The player offered a spark to start from: "%s". Use it loosely.'
                % seed) if seed.strip() else ""
        user = ("Begin a brand-new story from nothing.%s Invent a striking opening "
                "scene and drop the player into the middle of it. Return the first "
                "beat as a single JSON object." % hint)
    else:
        parts = []
        if ctx.get("summary"):
            parts.append("Story so far: " + ctx["summary"])
        recent = ctx.get("history") or []
        if recent:
            parts.append("Recent beats: " + " || ".join(recent[-5:]))
        parts.append("Current location: " + (ctx.get("location") or "unknown"))
        parts.append("The player just chose to: " + ctx.get("action", "look around"))
        parts.append("Continue the story from there. Return the next beat as a "
                     "single JSON object only.")
        user = "\n".join(parts)
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]


def messages_for_fix(ctx, bad_text):
    """Retry nudge when the model returns something that isn't valid JSON."""
    base = messages_for_beat(ctx)
    base.append({"role": "assistant", "content": bad_text[:1200]})
    base.append({"role": "user", "content":
                 "That was not a single valid JSON object. Return ONLY the story "
                 "beat as one JSON object, no other text."})
    return base
