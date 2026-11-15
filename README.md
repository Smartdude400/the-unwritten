# THE UNWRITTEN — a story that tells itself

A **click-and-move story game** whose tale is **generated as you play** by an AI
acting as a *StoryWeaver*. You walk a little figure through dreamlike scenes;
click a glowing thing to act on it, or a doorway to travel — and the AI weaves
the next beat of the story around whatever you did. Every playthrough is a
different, surreal, branching tale.

```
click the ground   → your character walks there
click a glowing object → you act on it → the AI writes what happens next
click a doorway    → you travel → the AI invents a whole new scene
choices (bottom)   → say/do something → the story responds
```

- **Press T** to open the **thinking view** — watch the AI's reasoning and its
  words stream in live as it composes each beat.
- **Press M** to pick the model (any local **Ollama** model, or the **Claude
  API**) — live, mid-story.
- **Press N** for a brand-new story. **Esc** skips the typewriter text.

It runs **fully local by default**. No menus to click through — it drops you
straight into a generated opening scene.

## How the AI drives the story

Each turn the AI returns one **story beat** as JSON — a location, a few vivid
sentences of narration, the clickable objects in the scene (name, icon, color,
position, an action verb), the exits, optional spoken choices, and a candid
`thinking` note. A fixed, atmospheric engine renders that beat (mood-driven
gradient backdrops, walking avatar, glowing hotspots, streaming narration). When
you act, your action plus the story-so-far is sent back for the next beat, so the
narrative stays coherent while constantly surprising you.

The AI never executes code — it only writes story. That makes it reliable
*and* lets it be as creative as it wants. If the model is offline, a built-in
surreal storyteller keeps the tale going.

### Files

```
main.py             the game: scene rendering, click-and-move, panels, picker
story.py            the story model: beats, palettes, avatar, narration reveal
agent.py            background StoryWeaver: prompt -> model -> JSON beat (streamed)
prompts.py          the StoryWeaver's instructions + the beat schema
engine.py           low-level drawing/input helpers
anthropic_client.py Claude API client   ·  ollama_client.py  local Ollama client
```

## Setup

Default is **local via Ollama** — no API key, no cost.

```powershell
pip install -r requirements.txt    # pygame-ce + anthropic
python main.py
```

Press **M** in-game to choose which of your installed Ollama models writes the
story (defaults to `qwen2.5-coder:7b-instruct-q4_K_M`; the picker lists whatever
`ollama list` shows). For a richer, more reliable storyteller, a 7B+ model — or
Claude — is noticeably better.

### Using Claude instead

Pick **Claude API** in the M dropdown (or set `AIGAME_PROVIDER=anthropic`) and
provide a key:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # from https://console.anthropic.com
python main.py
```

Default Claude model is `claude-haiku-4-5` (fast, cheap, very capable at
storytelling); `claude-sonnet-4-6` / `claude-opus-4-8` are in the picker too.

### The thinking view (press T)

A side panel that shows **what the AI is thinking**: while it composes a beat you
see its raw output stream in token-by-token; when idle it shows the beat's
`thinking` note. Local *reasoning* models that emit `<think>…</think>` show their
actual chain-of-thought here (it's stripped before the story JSON is parsed).

### Config via environment variables (optional)

| Variable | Default | Meaning |
|----------|---------|---------|
| `AIGAME_PROVIDER` | `ollama` | starting provider: `ollama` or `anthropic` |
| `AIGAME_MODEL`  | per-provider | starting model id (override the picker default) |
| `AIGAME_HOST`   | `http://localhost:11434` | Ollama server URL (ollama only) |
| `AIGAME_SEED`   | — | optional word/theme to seed the opening story |

## Controls

| Input | Action |
|-------|--------|
| Left-click ground | walk there |
| Left-click a glowing object | walk to it, then act on it (story advances) |
| Left-click a doorway/portal | travel — the AI invents a new scene |
| Choice buttons (bottom-right) | say/do something specific |
| T | toggle the thinking view |
| M | open the model / provider picker |
| N | start a brand-new story |
| Esc | skip the typewriter reveal · (again) quit |

## Notes

- Generation takes a moment per beat — the avatar walks while you wait, and the
  thinking view (T) lets you watch it compose.
- Story quality and creativity scale with the model. Haiku/Sonnet or a 7B+ local
  coder model give the most vivid, coherent tales; tiny models still work but
  ramble more.
- Icons are single characters drawn on colored tokens, so scenes look consistent
  regardless of which model (or emoji support) you have.

---

# DESCENT — an AI-generated dungeon shooter

A second game in this folder: a fast top-down roguelite where the AI **invents the
content as you descend**. Run it with:

```powershell
python dungeon_main.py
```

`WASD` move · mouse aim · hold to fire · `E` open chest · `M` model · `R` restart.

### The AI invents *kinds*, not just stats

Weapons and monsters are **composed from mechanics the engine runs**, so the AI
mixes them into genuinely new things — not numeric reskins:

- **Guns** = a firing *pattern* (`single, spread, ring, spiral, wave, arc, beam,
  orbit, burst`) + up to 3 *mods* (`pierce, bounce, explode, home, split, poison,
  freeze, chain, boomerang, giant`) + a bullet shape. So an "orbit shield‑gun" or
  a "spiral + poison plague‑thrower" actually play differently.
- **Monsters** = a *movement* (`chase, kite, orbit, zigzag, charger, teleporter,
  hover, wander`) + 1–3 *attacks* (`aimed, ring, spiral, spread, lob, beam, trail,
  summon`) + *on‑death* effects (`split, explode, summon, hazard`) + *traits*
  (`shielded, fast, armored, regen, phasing, giant`). A "teleporting priest that
  fires spirals, drops hazard trails and splits on death" is a real, distinct foe.

### It keeps thinking every floor

A background agent **invents a fresh batch each level** (new monster types, a gun,
a theme) scaled to your depth, and tops up buffers continuously so combat never
waits on the model. Each invention pops up in a small **feed** in the corner
("FOE  Blink Priest — teleporter · spiral · shielded"). Difficulty climbs every
floor and a **boss** appears every 5th. Same provider/model picker as the story
game (press `M`); works offline with a procedural composer too.

### Dungeon files

```
dungeon.py          the simulation: composable guns/monsters, rooms, combat
dungeon_prompts.py  instructions telling the AI how to combine mechanics
dungeon_agent.py    background content generator (gun / monster / room JSON)
dungeon_main.py     the game: input, rendering, buffers, per-level invention
```
