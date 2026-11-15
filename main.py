"""
main.py - A click-and-move story game whose tale the AI writes as you go.

Click the ground to walk. Click a glowing thing to act on it, or a doorway to
travel - and the AI weaves the next beat of the story around what you did. The
story generates itself, scene by scene.

Keys:  M = model picker   T = thinking view   N = new story   Esc = skip text
Run:   python main.py     (set ANTHROPIC_API_KEY for the Claude provider)
"""

import math
import os
import random
import sys
import threading
import time

import pygame

from engine import Engine
from agent import Agent
from story import Story, palette_for

DEFAULT_PROVIDER = os.environ.get("AIGAME_PROVIDER", "ollama").lower()
HOST = os.environ.get("AIGAME_HOST", "http://localhost:11434")
WIN_W, WIN_H = 1100, 720
NARR_H = 188
TPANEL_W = 360
CLAUDE_MODELS = ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"]


def _fallback_model(provider):
    return "qwen2.5-coder:7b-instruct-q4_K_M" if provider == "ollama" \
        else "claude-haiku-4-5"


def _initial_model(provider):
    return os.environ.get("AIGAME_MODEL") or _fallback_model(provider)


def _build_client(provider, model):
    if provider == "ollama":
        from ollama_client import OllamaClient
        return OllamaClient(HOST, model, timeout=300)
    from anthropic_client import AnthropicClient
    return AnthropicClient(model=model)


def _offline_hint(provider):
    if provider == "ollama":
        return "Ollama not responding - is it running? (press M to pick a model)"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "set the ANTHROPIC_API_KEY environment variable to use Claude"
    return "can't reach the Claude API - check your connection or key"


def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _article(name):
    first = name.lower().split(" ")[0] if name else ""
    return "" if first in ("a", "an", "the", "some", "my", "your", "his",
                           "her", "their") else "the "


class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("THE UNWRITTEN - a story that tells itself")
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.clock = pygame.time.Clock()
        self.engine = Engine(self.screen)

        self.provider = DEFAULT_PROVIDER
        self.current_model = _initial_model(self.provider)
        self.agent = Agent(_build_client(self.provider, self.current_model),
                           temperature=1.0)
        self.story = Story()

        self.awaiting = False           # a beat is being generated
        self.pending = None             # action to fire when the avatar arrives
        self.thinking_on = False
        self.seed = os.environ.get("AIGAME_SEED", "")
        self.t = 0.0
        self.running = True

        # hit-test caches refreshed every draw
        self._obj_hits = []
        self._exit_hits = []
        self._choice_hits = []
        self._chip_rect = None
        self._think_chip = None
        self._bg_cache = {}
        self._motes = [(random.random(), random.random(), random.random())
                       for _ in range(26)]

        # model picker state
        self.available_models = []
        self._models_loading = False
        self.model_menu_open = False
        self._model_rows = []
        self._model_panel = None
        self._reload_models_async()

    # ---- run ------------------------------------------------------------
    def run(self):
        self.agent.start()
        self._begin_story(new=True)
        while self.running:
            dt = min(0.05, self.clock.tick(60) / 1000.0)
            self.t += dt
            events = pygame.event.get()
            self._handle_input(events)
            self.engine.begin_frame(events, dt)
            self._consume_results()
            self.story.update(dt)
            self._maybe_fire_pending()
            self._draw()
            pygame.display.flip()
        self.agent.stop()
        pygame.quit()
        sys.exit(0)

    # ---- story flow -----------------------------------------------------
    def _begin_story(self, new=False):
        if new:
            self.story = Story()
        self.pending = None
        self.awaiting = True
        self.agent.request_beat(self.story.context("", opening=True,
                                                   seed=self.seed))

    def _act(self, action_text):
        self.awaiting = True
        self.agent.request_beat(self.story.context(action_text))

    def _maybe_fire_pending(self):
        if not self.pending or self.awaiting:
            return
        ax, ay = self.story.avatar
        tx, ty = self.story.target
        if math.hypot(tx - ax, ty - ay) < 0.012:
            action = self.pending
            self.pending = None
            self._act(action)

    def _consume_results(self):
        for res in self.agent.drain_results():
            if res.get("kind") == "beat" and res.get("beat"):
                self.story.apply_beat(res["beat"])
                self.awaiting = False
                self.pending = None

    # ---- input ----------------------------------------------------------
    def _handle_input(self, events):
        for e in events:
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                self._on_key(e)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self._on_click(e.pos)

    def _on_key(self, e):
        if e.key == pygame.K_ESCAPE:
            if self.model_menu_open:
                self.model_menu_open = False
            elif self.story.revealing:
                self.story.finish_reveal()
            else:
                self.running = False
        elif e.key == pygame.K_m:
            self.model_menu_open = not self.model_menu_open
            if self.model_menu_open:
                self._reload_models_async()
        elif e.key == pygame.K_t:
            self.thinking_on = not self.thinking_on
        elif e.key == pygame.K_n and not self.model_menu_open:
            self._begin_story(new=True)

    def _on_click(self, pos):
        if self.model_menu_open:
            self._click_model_menu(pos)
            return
        if self._chip_rect and self._chip_rect.collidepoint(pos):
            self.model_menu_open = True
            self._reload_models_async()
            return
        if self._think_chip and self._think_chip.collidepoint(pos):
            self.thinking_on = not self.thinking_on
            return
        if self.thinking_on and pos[0] >= WIN_W - TPANEL_W:
            return
        self._click_story(pos)

    def _click_story(self, pos):
        # first click just finishes revealing the narration
        if self.story.revealing:
            self.story.finish_reveal()
            return
        if self.awaiting or not self.story.has_scene:
            return
        # choices
        for rect, text in self._choice_hits:
            if rect.collidepoint(pos):
                self.pending = None
                self._act(text)
                return
        # objects
        for cx, cy, r, obj in self._obj_hits:
            if (pos[0] - cx) ** 2 + (pos[1] - cy) ** 2 <= (r + 10) ** 2:
                self.story.target = [obj["x"], min(0.9, obj["y"] + 0.14)]
                self.pending = "%s %s%s" % (obj["action"], _article(obj["name"]),
                                            obj["name"])
                return
        # exits
        for cx, cy, r, ex in self._exit_hits:
            if (pos[0] - cx) ** 2 + (pos[1] - cy) ** 2 <= (r + 14) ** 2:
                self.story.target = [ex["x"], min(0.92, ex["y"] + 0.1)]
                self.pending = "leave through %s" % ex["name"]
                return
        # otherwise: walk to where you clicked (within the scene)
        sr = self._scene_rect()
        if sr.collidepoint(pos):
            self.story.target = [(pos[0] - sr.x) / sr.w, (pos[1] - sr.y) / sr.h]
            self.pending = None

    # ---- layout ---------------------------------------------------------
    def _scene_w(self):
        return WIN_W - (TPANEL_W if self.thinking_on else 0)

    def _scene_rect(self):
        return pygame.Rect(0, 0, self._scene_w(), WIN_H - NARR_H)

    # =====================================================================
    # Drawing
    # =====================================================================
    def _draw(self):
        sr = self._scene_rect()
        self._draw_scene(sr)
        self._draw_narration(pygame.Rect(0, WIN_H - NARR_H, sr.w, NARR_H))
        if self.thinking_on:
            self._draw_thinking(pygame.Rect(sr.w, 0, TPANEL_W, WIN_H))
        if not self.story.has_scene:
            self._draw_booting(sr)
        self._draw_chips()
        if self.model_menu_open:
            self._draw_model_menu()

    # ---- scene ----------------------------------------------------------
    def _gradient(self, mood, w, h):
        key = (mood, w, h)
        surf = self._bg_cache.get(key)
        if surf is None:
            top, bot, _ = palette_for(mood)
            surf = pygame.Surface((w, h))
            for y in range(h):
                f = y / max(1, h - 1)
                surf.fill((int(top[0] + (bot[0] - top[0]) * f),
                           int(top[1] + (bot[1] - top[1]) * f),
                           int(top[2] + (bot[2] - top[2]) * f)), (0, y, w, 1))
            if len(self._bg_cache) > 8:
                self._bg_cache.clear()
            self._bg_cache[key] = surf
        return surf

    def _draw_scene(self, sr):
        self._obj_hits = []
        self._exit_hits = []
        self.screen.set_clip(sr)
        self.screen.blit(self._gradient(self.story.mood, sr.w, sr.h), (sr.x, sr.y))
        accent = palette_for(self.story.mood)[2]
        # drifting motes for atmosphere
        for i, (mx, my, sp) in enumerate(self._motes):
            yy = (my - (self.t * (0.02 + sp * 0.05))) % 1.0
            x = sr.x + mx * sr.w
            y = sr.y + yy * sr.h
            rad = 1 + int(sp * 2)
            dot = pygame.Surface((rad * 2, rad * 2), pygame.SRCALPHA)
            pygame.draw.circle(dot, (*accent, 60), (rad, rad), rad)
            self.screen.blit(dot, (x, y))
        # soft ground band
        gb = pygame.Surface((sr.w, 70), pygame.SRCALPHA)
        gb.fill((0, 0, 0, 60))
        self.screen.blit(gb, (sr.x, sr.bottom - 70))

        if self.story.has_scene:
            for ex in self.story.exits:
                self._draw_exit(sr, ex, accent)
            for obj in self.story.objects:
                self._draw_object(sr, obj)
            self._draw_avatar(sr)
        self.screen.set_clip(None)
        self._draw_scene_hint(sr)

    def _draw_scene_hint(self, sr):
        loc = self.story.location or "..."
        self.engine.text(loc, sr.x + 16, sr.y + 12, size=22, bold=True,
                         color=(245, 245, 250))
        self.engine.text("click to move · click glowing things to act · "
                         "M model · T thoughts · N new", sr.x + 16, sr.y + 40,
                         size=13, color=(220, 220, 230))
        if self.awaiting and self.story.has_scene:
            g = "|/-\\"[int(self.t * 8) % 4]
            self.engine.text("%s the story turns..." % g, sr.right - 190,
                             sr.y + 14, size=15, color=(235, 235, 245))

    def _draw_object(self, sr, obj):
        cx = int(sr.x + obj["x"] * sr.w)
        cy = int(sr.y + obj["y"] * sr.h)
        r = 26
        hover = (self.engine.mouse[0] - cx) ** 2 + \
                (self.engine.mouse[1] - cy) ** 2 <= (r + 12) ** 2
        # shadow
        sh = pygame.Surface((r * 2, r), pygame.SRCALPHA)
        pygame.draw.ellipse(sh, (0, 0, 0, 90), (0, 0, r * 2, r))
        self.screen.blit(sh, (cx - r, cy + r - 6))
        col = obj["color"]
        if hover:
            pygame.draw.circle(self.screen, (255, 255, 255), (cx, cy), r + 7, 3)
        pygame.draw.circle(self.screen, col, (cx, cy), r)
        pygame.draw.circle(self.screen, (255, 255, 255), (cx, cy), r, 2)
        ic = (25, 25, 32) if _lum(col) > 150 else (245, 245, 250)
        self.engine.text(obj["icon"], cx, cy, size=26, color=ic, center=True,
                         bold=True)
        self._label(cx, cy + r + 4, obj["name"])
        if hover:
            self.engine.text(obj["action"], cx, cy - r - 22,
                             size=14, color=(255, 255, 255), center=True)
        self._obj_hits.append((cx, cy, r, obj))

    def _draw_exit(self, sr, ex, accent):
        cx = int(sr.x + ex["x"] * sr.w)
        cy = int(sr.y + ex["y"] * sr.h)
        r = 30
        hover = (self.engine.mouse[0] - cx) ** 2 + \
                (self.engine.mouse[1] - cy) ** 2 <= (r + 16) ** 2
        glow = pygame.Surface((r * 3, r * 4), pygame.SRCALPHA)
        a = 120 if hover else 70
        pygame.draw.ellipse(glow, (*accent, a),
                            (r * 0.4, 0, r * 2.2, r * 4))
        self.screen.blit(glow, (cx - r * 1.5, cy - r * 2))
        pygame.draw.ellipse(self.screen, (245, 245, 255),
                            (cx - r, cy - r * 1.6, r * 2, r * 3.2),
                            3 if hover else 2)
        self.engine.text("›", cx, cy, size=30, color=(245, 245, 255),
                         center=True, bold=True)
        self._label(cx, cy + r * 1.6 + 2, ex["name"], accent=True)
        self._exit_hits.append((cx, cy, r, ex))

    def _label(self, cx, top, text, accent=False):
        text = text if len(text) <= 22 else text[:21] + "…"
        f = self.engine._font(14, True)
        w = f.size(text)[0] + 14
        bg = pygame.Surface((w, 22), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 130))
        self.screen.blit(bg, (cx - w // 2, top))
        self.engine.text(text, cx, top + 3, size=14, center=True,
                         color=(220, 235, 255) if accent else (240, 240, 245))

    def _draw_avatar(self, sr):
        x = int(sr.x + self.story.avatar[0] * sr.w)
        base = int(sr.y + self.story.avatar[1] * sr.h)
        bob = math.sin(self.t * 4) * 2
        # shadow
        sh = pygame.Surface((40, 16), pygame.SRCALPHA)
        pygame.draw.ellipse(sh, (0, 0, 0, 110), (0, 0, 40, 16))
        self.screen.blit(sh, (x - 20, base + 8))
        accent = palette_for(self.story.mood)[2]
        y = base + bob
        pygame.draw.rect(self.screen, (244, 244, 250),
                         (x - 9, int(y) - 18, 18, 24), border_radius=7)
        pygame.draw.circle(self.screen, (250, 250, 255), (x, int(y) - 24), 9)
        pygame.draw.circle(self.screen, accent, (x, int(y) - 24), 9, 2)

    # ---- narration ------------------------------------------------------
    def _draw_narration(self, nr):
        panel = pygame.Surface((nr.w, nr.h), pygame.SRCALPHA)
        panel.fill((10, 10, 16, 220))
        self.screen.blit(panel, (nr.x, nr.y))
        pygame.draw.line(self.screen, (60, 60, 80), (nr.x, nr.y),
                         (nr.right, nr.y), 1)
        self._choice_hits = []
        has_choices = bool(self.story.choices) and not self.awaiting
        choice_w = 286 if has_choices else 0
        text_w = nr.w - 40 - choice_w
        body = self.story.revealed() or ("..." if self.awaiting else "")
        y = nr.y + 22
        for line in self._wrap(body, self.engine._font(20), text_w):
            self.engine.text(line, nr.x + 24, y, size=20, color=(235, 235, 245))
            y += 27
            if y > nr.bottom - 30:
                break
        if self.story.revealing:
            self.engine.text("▸ click / Esc to reveal", nr.x + 24,
                             nr.bottom - 26, size=13, color=(140, 140, 160))
        if has_choices:
            self._draw_choices(pygame.Rect(nr.right - choice_w - 14, nr.y + 14,
                                           choice_w, nr.h - 28))

    def _draw_choices(self, cr):
        n = len(self.story.choices)
        gap = 8
        h = min(40, (cr.h - (n - 1) * gap) // max(1, n))
        for i, text in enumerate(self.story.choices):
            rect = pygame.Rect(cr.x, cr.y + i * (h + gap), cr.w, h)
            hover = rect.collidepoint(self.engine.mouse)
            pygame.draw.rect(self.screen, (44, 44, 60) if hover else (28, 28, 40),
                             rect, border_radius=8)
            pygame.draw.rect(self.screen, (120, 160, 240) if hover else (70, 70, 96),
                             rect, width=2, border_radius=8)
            label = text if len(text) <= 34 else text[:33] + "…"
            self.engine.text(label, rect.x + 12, rect.centery - 10, size=16,
                             color=(232, 232, 242))
            self._choice_hits.append((rect, text))

    # ---- thinking panel -------------------------------------------------
    def _draw_thinking(self, tr):
        pygame.draw.rect(self.screen, (14, 14, 20), tr)
        pygame.draw.line(self.screen, (60, 60, 80), (tr.x, 0), (tr.x, WIN_H), 1)
        self.engine.text("THE AI IS THINKING", tr.x + 16, 16, size=18, bold=True,
                         color=(200, 130, 220))
        self.engine.text("(T to hide)", tr.right - 90, 20, size=13,
                         color=(140, 140, 160))
        body = self.agent.live_text if self.agent.busy else \
            (self.story.thinking or self.agent.last_thinking or
             "Waiting for your move...")
        mono = self.engine._mono(14)
        y = 52
        for line in self._wrap_mono(body, mono, tr.w - 32):
            self.screen.blit(mono.render(line, True, (170, 200, 175)),
                             (tr.x + 16, y))
            y += 19
            if y > WIN_H - 24:
                break

    # ---- overlay (first weave) -----------------------------------------
    def _draw_booting(self, sr):
        shade = pygame.Surface((sr.w, sr.h), pygame.SRCALPHA)
        shade.fill((10, 10, 16, 180))
        self.screen.blit(shade, (sr.x, sr.y))
        cx = sr.w // 2
        g = "|/-\\"[int(self.t * 8) % 4]
        self.engine.text("THE UNWRITTEN", cx, sr.h * 0.32, size=46, bold=True,
                         color=(200, 160, 245), center=True)
        online = self.agent.online
        msg = ("%s weaving your story..." % g) if online else _offline_hint(
            self.provider)
        self.engine.text(msg, cx, sr.h * 0.32 + 64, size=20,
                         color=(232, 232, 245), center=True)
        self.engine.text("press M to choose the storyteller model · "
                         "T to watch it think", cx, sr.h * 0.32 + 100, size=14,
                         color=(160, 160, 180), center=True)

    # ---- chips ----------------------------------------------------------
    def _draw_chips(self):
        # thinking chip
        tlabel = "thoughts: %s (T)" % ("on" if self.thinking_on else "off")
        tw = self.engine._font(14).size(tlabel)[0] + 18
        # model chip
        mlabel = "model: %s (M)" % self.current_model
        if len(mlabel) > 40:
            mlabel = "model: %s… (M)" % self.current_model[:30]
        mw = self.engine._font(14).size(mlabel)[0] + 24
        mrect = pygame.Rect(WIN_W - mw - 12, 10, mw, 26)
        trect = pygame.Rect(mrect.x - tw - 8, 10, tw, 26)
        for rect, label, col in ((trect, tlabel,
                                  (200, 130, 220) if self.thinking_on else
                                  (150, 150, 165)),):
            s = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            s.fill((20, 20, 28, 190))
            self.screen.blit(s, (rect.x, rect.y))
            pygame.draw.rect(self.screen, (70, 70, 92), rect, 1, border_radius=6)
            self.engine.text(label, rect.x + 9, rect.y + 5, size=14, color=col)
        s = pygame.Surface((mrect.w, mrect.h), pygame.SRCALPHA)
        s.fill((20, 20, 28, 190))
        self.screen.blit(s, (mrect.x, mrect.y))
        pygame.draw.rect(self.screen, (70, 70, 92), mrect, 1, border_radius=6)
        dot = (110, 210, 130) if self.agent.online else (170, 120, 90)
        self.engine.circle(mrect.x + 12, mrect.y + 13, 4, dot)
        self.engine.text(mlabel, mrect.x + 22, mrect.y + 5, size=14,
                         color=(190, 190, 205))
        self._chip_rect = mrect
        self._think_chip = trect

    # ---- text helpers ---------------------------------------------------
    def _wrap(self, text, font, width):
        out = []
        for para in text.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = (cur + " " + word).strip()
                if font.size(trial)[0] <= width:
                    cur = trial
                else:
                    if cur:
                        out.append(cur)
                    cur = word
            out.append(cur)
        return out

    def _wrap_mono(self, text, font, width):
        out = []
        for raw in text.splitlines() or [""]:
            while raw:
                cut = len(raw)
                while cut > 1 and font.size(raw[:cut])[0] > width:
                    cut -= 1
                out.append(raw[:cut])
                raw = raw[cut:]
        return out

    # =====================================================================
    # Model picker (press M)
    # =====================================================================
    def _reload_models_async(self):
        self._models_loading = True
        provider = self.provider

        def work():
            if provider == "ollama":
                try:
                    from ollama_client import OllamaClient
                    models = OllamaClient(HOST, self.current_model).list_models()
                except Exception:  # noqa: BLE001
                    models = []
            else:
                models = list(CLAUDE_MODELS)
            if self.current_model not in models:
                models = [self.current_model] + models
            self.available_models = models
            self._models_loading = False

        threading.Thread(target=work, daemon=True).start()

    def _switch_provider(self, provider):
        if provider == self.provider:
            return
        self.provider = provider
        self.current_model = _initial_model(provider)
        self.agent.set_client(_build_client(provider, self.current_model))
        self._reload_models_async()
        self.model_menu_open = False

    def _select_model(self, name):
        self.model_menu_open = False
        if name == self.current_model:
            return
        self.current_model = name
        self.agent.set_model(name)

    def _click_model_menu(self, pos):
        if self._model_panel and not self._model_panel.collidepoint(pos):
            self.model_menu_open = False
            return
        for rect, kind, value in self._model_rows:
            if rect.collidepoint(pos):
                if kind == "provider":
                    self._switch_provider(value)
                else:
                    self._select_model(value)
                return

    def _draw_model_menu(self):
        e = self.engine
        shade = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        shade.fill((6, 6, 10, 210))
        self.screen.blit(shade, (0, 0))
        panel = pygame.Rect(0, 0, 580, 470)
        panel.center = (WIN_W // 2, WIN_H // 2)
        pygame.draw.rect(self.screen, (24, 24, 32), panel, border_radius=12)
        pygame.draw.rect(self.screen, (120, 160, 240), panel, 2, border_radius=12)
        self._model_panel = panel
        self._model_rows = []
        x, y = panel.x + 26, panel.y + 18
        e.text("CHOOSE THE STORYTELLER", panel.centerx, y, size=23,
               color=(120, 160, 240), center=True, bold=True)
        y += 44
        e.text("Source", x, y, size=13, color=(150, 150, 165))
        y += 22
        for i, (label, val) in enumerate((("Local (Ollama)", "ollama"),
                                          ("Claude API", "anthropic"))):
            r = pygame.Rect(x + i * 262, y, 244, 36)
            active = self.provider == val
            pygame.draw.rect(self.screen, (50, 60, 86) if active else (30, 30, 40),
                             r, border_radius=8)
            pygame.draw.rect(self.screen, (120, 160, 240) if active else
                             (70, 70, 90), r, 2, border_radius=8)
            e.text(label, r.centerx, r.y + 9, size=16,
                   color=(232, 232, 240) if active else (160, 160, 175),
                   center=True)
            self._model_rows.append((r, "provider", val))
        y += 54
        e.text("Model  (click to use)", x, y, size=13, color=(150, 150, 165))
        y += 24
        models = self.available_models
        if not models:
            msg = "loading…" if self._models_loading else \
                ("no models found - is Ollama running?" if
                 self.provider == "ollama" else "no models")
            e.text(msg, x, y, size=16, color=(150, 150, 165))
        else:
            rh = 32
            for m in models[:(panel.bottom - 44 - y) // rh]:
                r = pygame.Rect(x, y, panel.w - 52, rh - 4)
                cur = m == self.current_model
                if cur:
                    pygame.draw.rect(self.screen, (34, 56, 38), r, border_radius=6)
                elif r.collidepoint(e.mouse):
                    pygame.draw.rect(self.screen, (36, 36, 48), r, border_radius=6)
                e.text(("● " if cur else "   ") + m, r.x + 8, r.y + 5, size=16,
                       color=(140, 230, 150) if cur else (222, 222, 232))
                self._model_rows.append((r, "model", m))
                y += rh
        e.text("M / Esc to close", panel.centerx, panel.bottom - 28, size=13,
               color=(150, 150, 165), center=True)


if __name__ == "__main__":
    Game().run()
