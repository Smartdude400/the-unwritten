"""
dungeon_main.py - AI-generated roguelite dungeon shooter.

Top-down twin-stick-ish combat: WASD to move, mouse to aim, hold to fire. The AI
generates the content in the background - guns found in chests, the monster
types that fill each room, and the room themes - while you fight. Clear a room
to open the exit; descend; monsters get harder and stranger every floor, with a
boss every 5th.

Run:  python dungeon_main.py     (set ANTHROPIC_API_KEY for the Claude provider)
Keys: WASD move · mouse aim · click/hold fire · E open chest · M model
      · R restart (when dead) · Esc quit
"""

import math
import os
import sys
import threading

import pygame

from engine import Engine
import dungeon
from dungeon import World
from dungeon_agent import ContentAgent

WIN_W, WIN_H = 1100, 720
HUD_H = 64
OX = (WIN_W - dungeon.ROOM_PX_W) // 2
OY = HUD_H + (WIN_H - HUD_H - dungeon.ROOM_PX_H) // 2

DEFAULT_PROVIDER = os.environ.get("AIGAME_PROVIDER", "ollama").lower()
HOST = os.environ.get("AIGAME_HOST", "http://localhost:11434")
CLAUDE_MODELS = ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"]

# how full to keep each content buffer
TARGETS = {"gun": 3, "monster": 9, "room": 2}


def _fallback_model(p):
    return "qwen2.5-coder:7b-instruct-q4_K_M" if p == "ollama" else "claude-haiku-4-5"


def _initial_model(p):
    return os.environ.get("AIGAME_MODEL") or _fallback_model(p)


def _build_client(provider, model):
    if provider == "ollama":
        from ollama_client import OllamaClient
        return OllamaClient(HOST, model, timeout=120)
    from anthropic_client import AnthropicClient
    return AnthropicClient(model=model)


def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("DESCENT - an AI-generated dungeon shooter")
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.clock = pygame.time.Clock()
        self.engine = Engine(self.screen)
        pygame.mouse.set_visible(False)

        self.provider = DEFAULT_PROVIDER
        self.current_model = _initial_model(self.provider)
        self.agent = ContentAgent(_build_client(self.provider, self.current_model))

        # AI content buffers + in-flight request counts
        self.gun_buf, self.monster_buf, self.room_buf = [], [], []
        self.pending = {"gun": 0, "monster": 0, "room": 0}
        self.world = World(self._provide_gun, self._provide_room)

        self.running = True
        self.t = 0.0
        self.last_depth = 0          # to trigger per-level invention
        self.toasts = []             # [label, tags, color, life] - "AI invented X"
        # model picker
        self.available_models = []
        self._models_loading = False
        self.model_menu_open = False
        self._model_rows = []
        self._model_panel = None
        self._chip_rect = None
        self._reload_models_async()

    # ---- content providers (called by the world) ------------------------
    def _provide_gun(self):
        return self.gun_buf.pop(0) if self.gun_buf else \
            dungeon.fallback_gun(self.world.depth if hasattr(self, "world") else 1)

    def _provide_room(self, depth):
        theme = self.room_buf.pop(0) if self.room_buf else dungeon.fallback_theme(depth)
        pool = []
        for _ in range(3):
            if self.monster_buf:
                pool.append(self.monster_buf.pop(0))
        if not pool:
            pool = [dungeon.fallback_monster(depth)]
        return {"theme": theme, "monsters": pool, "boss": depth % 5 == 0}

    # ---- main loop ------------------------------------------------------
    def run(self):
        self.agent.start()
        while self.running:
            dt = min(0.04, self.clock.tick(60) / 1000.0)
            self.t += dt
            events = pygame.event.get()
            self._handle_events(events)
            self.engine.begin_frame(events, dt)
            self._consume_results()
            self._refill()
            if not self.model_menu_open:
                self.world.update(dt, self._intent())
            if self.world.depth != self.last_depth:   # new floor -> invent for it
                self.last_depth = self.world.depth
                self._invent_for_level()
            for tt in self.toasts:
                tt[3] -= dt
            self.toasts = [tt for tt in self.toasts if tt[3] > 0]
            self._draw()
            pygame.display.flip()
        self.agent.stop()
        pygame.quit()
        sys.exit(0)

    def _intent(self):
        if self.world.over:
            return {}
        k = pygame.key.get_pressed()
        mx = (k[pygame.K_d] or k[pygame.K_RIGHT]) - (k[pygame.K_a] or k[pygame.K_LEFT])
        my = (k[pygame.K_s] or k[pygame.K_DOWN]) - (k[pygame.K_w] or k[pygame.K_UP])
        mxp, myp = pygame.mouse.get_pos()
        aim = (mxp - OX, myp - OY)
        fire = pygame.mouse.get_pressed()[0] or k[pygame.K_SPACE]
        return {"move": (mx, my), "aim": aim, "fire": fire,
                "interact": self._interact_edge}

    # ---- events ---------------------------------------------------------
    def _handle_events(self, events):
        self._interact_edge = False
        for e in events:
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if self.model_menu_open:
                        self.model_menu_open = False
                    else:
                        self.running = False
                elif e.key == pygame.K_m:
                    self.model_menu_open = not self.model_menu_open
                    if self.model_menu_open:
                        self._reload_models_async()
                elif e.key == pygame.K_e:
                    self._interact_edge = True
                elif e.key == pygame.K_r and self.world.over:
                    self.world.restart()
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if self.model_menu_open:
                    self._click_model_menu(e.pos)
                elif self._chip_rect and self._chip_rect.collidepoint(e.pos):
                    self.model_menu_open = True
                    self._reload_models_async()

    # ---- AI buffers -----------------------------------------------------
    def _consume_results(self):
        for res in self.agent.drain_results():
            kind, depth, raw = res["kind"], res["depth"], res.get("raw")
            self.pending[kind] = max(0, self.pending[kind] - 1)
            if kind == "gun":
                g = dungeon.normalize_gun(raw, depth) if raw else \
                    dungeon.fallback_gun(depth)
                self.gun_buf.append(g)
                self._toast("GUN  " + g["name"], dungeon.gun_tags(g), g["color"])
            elif kind == "monster":
                m = dungeon.normalize_monster(raw, depth) if raw else \
                    dungeon.fallback_monster(depth)
                self.monster_buf.append(m)
                self._toast("FOE  " + m["name"], dungeon.monster_tags(m),
                            m["color"])
            elif kind == "room":
                self.room_buf.append(dungeon.normalize_theme(raw, depth) if raw
                                     else dungeon.fallback_theme(depth))

    def _toast(self, label, tags, color):
        self.toasts.append([label, tags, color, 5.0])
        self.toasts = self.toasts[-6:]

    def _invent_for_level(self):
        """Each new floor, ask the AI to dream up fresh things for what's ahead."""
        if not self.agent.online:
            return
        depth = self.world.depth + 1
        for kind in ("monster", "monster", "monster", "gun", "room"):
            if self.agent.requests.qsize() > 9:
                break
            self.pending[kind] += 1
            self.agent.request(kind, depth)

    def _refill(self):
        if self.agent.requests.qsize() > 5:
            return
        depth = self.world.depth + 1
        for kind, buf in (("gun", self.gun_buf), ("monster", self.monster_buf),
                          ("room", self.room_buf)):
            if len(buf) + self.pending[kind] < TARGETS[kind]:
                self.pending[kind] += 1
                self.agent.request(kind, depth)
                return   # one request per frame keeps it gentle

    # =====================================================================
    # Rendering
    # =====================================================================
    def _draw(self):
        self.screen.fill((10, 10, 14))
        self._draw_room()
        self._draw_toasts()
        self._draw_hud()
        self._draw_banner()
        self._draw_cursor()
        if self.world.over:
            self._draw_gameover()
        self._draw_chip()
        if self.model_menu_open:
            self._draw_model_menu()

    def _draw_room(self):
        w = self.world
        th = w.theme
        view = pygame.Rect(OX, OY, dungeon.ROOM_PX_W, dungeon.ROOM_PX_H)
        self.screen.fill(th["floor"], view)
        # walls / obstacles
        for ty in range(dungeon.RH):
            for tx in range(dungeon.RW):
                if w.room[ty][tx] == 1:
                    r = pygame.Rect(OX + tx * dungeon.TILE, OY + ty * dungeon.TILE,
                                    dungeon.TILE, dungeon.TILE)
                    pygame.draw.rect(self.screen, th["wall"], r)
                    pygame.draw.rect(self.screen, tuple(min(255, c + 20)
                                     for c in th["wall"]), r, 1)
        self._draw_exit_door(th)
        if w.chest:
            self._draw_chest(w)
        for it in w.items:
            self._draw_pickup(it)
        for h in w.hazards:
            self._draw_hazard(h)
        for pt in w.particles:
            a = max(0, min(255, int(pt[4] * 700)))
            s = pygame.Surface((6, 6), pygame.SRCALPHA)
            pygame.draw.circle(s, (*pt[5], a), (3, 3), 3)
            self.screen.blit(s, (OX + pt[0] - 3, OY + pt[1] - 3))
        for m in w.monsters:
            self._draw_monster(m)
        for b in w.bullets:
            self._draw_bullet(b)
        self._draw_player(w.player)

    def _draw_exit_door(self, th):
        w = self.world
        tx, ty = dungeon.RW - 1, dungeon.RH // 2
        r = pygame.Rect(OX + tx * dungeon.TILE, OY + (ty - 1) * dungeon.TILE,
                        dungeon.TILE, dungeon.TILE * 3)
        if w.cleared:
            pulse = 120 + int(80 * (0.5 + 0.5 * math.sin(self.t * 6)))
            glow = pygame.Surface((r.w + 30, r.h), pygame.SRCALPHA)
            pygame.draw.rect(glow, (*th["accent"], pulse), (0, 0, r.w + 30, r.h))
            self.screen.blit(glow, (r.x - 15, r.y))
            self.engine.text("EXIT →", r.x - 70, r.centery - 10, size=16,
                             color=th["accent"])
        else:
            pygame.draw.rect(self.screen, (40, 40, 50), r)
            pygame.draw.rect(self.screen, th["accent"], r, 2)

    def _draw_chest(self, w):
        cx = OX + (w.chest[0] + 0.5) * dungeon.TILE
        cy = OY + (w.chest[1] + 0.5) * dungeon.TILE
        col = (120, 90, 50) if not w.chest_open else (70, 60, 50)
        pygame.draw.rect(self.screen, col, (cx - 16, cy - 12, 32, 24),
                         border_radius=4)
        pygame.draw.rect(self.screen, (210, 180, 90), (cx - 16, cy - 12, 32, 24),
                         2, border_radius=4)
        if not w.chest_open:
            pygame.draw.circle(self.screen, (240, 210, 110), (int(cx), int(cy)), 3)
            pd = (self.world.player.x - w.chest[0] * dungeon.TILE)
            near = (self.world.player.x - (w.chest[0] + .5) * dungeon.TILE) ** 2 + \
                   (self.world.player.y - (w.chest[1] + .5) * dungeon.TILE) ** 2
            if near < (dungeon.TILE * 1.4) ** 2:
                self.engine.text("E", cx, cy - 34, size=18, center=True,
                                 color=(240, 230, 140), bold=True)

    def _draw_pickup(self, it):
        x, y = OX + it["x"], OY + it["y"]
        bob = math.sin(self.t * 5) * 3
        pygame.draw.circle(self.screen, it["gun"]["color"], (int(x), int(y + bob)), 9)
        pygame.draw.circle(self.screen, (255, 255, 255), (int(x), int(y + bob)), 9, 2)
        self.engine.text(it["gun"]["name"], x, y + bob - 26, size=13, center=True,
                         color=(235, 235, 245))

    def _draw_monster(self, m):
        x, y = int(OX + m.x), int(OY + m.y)
        if getattr(m, "is_boss", False):
            glow = pygame.Surface((m.r * 4, m.r * 4), pygame.SRCALPHA)
            pygame.draw.circle(glow, (*m.color, 60), (m.r * 2, m.r * 2), m.r * 2)
            self.screen.blit(glow, (x - m.r * 2, y - m.r * 2))
        pygame.draw.circle(self.screen, m.color, (x, y), m.r)
        ring = (120, 200, 255) if m.shielded else (255, 255, 255)
        pygame.draw.circle(self.screen, ring, (x, y), m.r, 2)
        ic = (20, 20, 26) if _lum(m.color) > 150 else (245, 245, 250)
        self.engine.text(m.glyph, x, y, size=int(m.r * 1.3), center=True,
                         color=ic, bold=True)
        if m.hp < m.max_hp:
            f = max(0, m.hp / m.max_hp)
            pygame.draw.rect(self.screen, (40, 20, 20), (x - m.r, y - m.r - 8,
                             m.r * 2, 4))
            pygame.draw.rect(self.screen, (230, 80, 80), (x - m.r, y - m.r - 8,
                             int(m.r * 2 * f), 4))

    def _draw_bullet(self, b):
        x, y = int(OX + b.x), int(OY + b.y)
        col = (255, 120, 110) if not b.friendly else b.color
        r = b.r
        glow = pygame.Surface((r * 4, r * 4), pygame.SRCALPHA)
        pygame.draw.circle(glow, (*col, 80), (r * 2, r * 2), r * 2)
        self.screen.blit(glow, (x - r * 2, y - r * 2))
        sh = b.shape
        if sh == "ring":
            pygame.draw.circle(self.screen, col, (x, y), r + 1, 2)
        elif sh == "bolt":
            a = math.atan2(b.vy, b.vx)
            dx, dy = math.cos(a) * r * 1.7, math.sin(a) * r * 1.7
            pygame.draw.line(self.screen, col, (x - dx, y - dy), (x + dx, y + dy),
                             max(2, r))
        elif sh == "diamond":
            pygame.draw.polygon(self.screen, col, [(x, y - r), (x + r, y),
                                                   (x, y + r), (x - r, y)])
        elif sh == "star":
            for k in range(4):
                a = k * math.pi / 2 + self.t * 6
                pygame.draw.line(self.screen, col, (x, y),
                                 (x + math.cos(a) * (r + 2),
                                  y + math.sin(a) * (r + 2)), 2)
            pygame.draw.circle(self.screen, col, (x, y), max(2, r - 1))
        else:  # dot / blob
            pygame.draw.circle(self.screen, col, (x, y), r + (2 if sh == "blob" else 0))

    def _draw_hazard(self, h):
        x, y = int(OX + h[0]), int(OY + h[1])
        rad = int(h[2])
        a = max(0, min(120, int(h[4] * 55)))
        s = pygame.Surface((rad * 2, rad * 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (210, 90, 60, a), (rad, rad), rad)
        pygame.draw.circle(s, (240, 140, 90, a), (rad, rad), rad, 2)
        self.screen.blit(s, (x - rad, y - rad))

    def _draw_toasts(self):
        y = HUD_H + 10
        f1 = self.engine._font(15, True)
        f2 = self.engine._font(12)
        for label, tags, color, life in self.toasts[-6:]:
            a = int(255 * min(1.0, life / 0.9))
            bw = max(f1.size(label)[0], f2.size(tags)[0]) + 24
            panel = pygame.Surface((bw, 42), pygame.SRCALPHA)
            panel.fill((10, 10, 16, int(a * 0.6)))
            pygame.draw.rect(panel, (*color, a), (0, 0, 3, 42))
            ls = f1.render(label, True, color)
            ls.set_alpha(a)
            panel.blit(ls, (12, 4))
            gs = f2.render(tags, True, (185, 185, 200))
            gs.set_alpha(a)
            panel.blit(gs, (12, 23))
            self.screen.blit(panel, (12, y))
            y += 48

    def _draw_player(self, p):
        x, y = int(OX + p.x), int(OY + p.y)
        mxp, myp = pygame.mouse.get_pos()
        ang = math.atan2((myp - OY) - p.y, (mxp - OX) - p.x)
        pygame.draw.circle(self.screen, (245, 245, 252), (x, y), p.r)
        pygame.draw.circle(self.screen, p.gun["color"], (x, y), p.r, 2)
        bx = x + math.cos(ang) * (p.r + 12)
        by = y + math.sin(ang) * (p.r + 12)
        pygame.draw.line(self.screen, p.gun["color"], (x, y), (bx, by), 5)

    # ---- HUD ------------------------------------------------------------
    def _draw_hud(self):
        pygame.draw.rect(self.screen, (18, 18, 26), (0, 0, WIN_W, HUD_H))
        pygame.draw.line(self.screen, (50, 50, 66), (0, HUD_H), (WIN_W, HUD_H), 1)
        p = self.world.player
        # hp bar
        bar = pygame.Rect(16, 18, 230, 26)
        pygame.draw.rect(self.screen, (40, 20, 22), bar, border_radius=6)
        f = max(0, p.hp) / p.max_hp
        col = (110, 210, 130) if f > 0.5 else (235, 195, 80) if f > 0.25 else (220, 90, 90)
        pygame.draw.rect(self.screen, col, (bar.x, bar.y, int(bar.w * f), bar.h),
                         border_radius=6)
        self.engine.text("HP %d" % max(0, int(p.hp)), bar.centerx, 23, size=16,
                         center=True, color=(20, 20, 24), bold=True)
        self.engine.text("DEPTH %d" % self.world.depth, 270, 12, size=16,
                         color=(200, 200, 230), bold=True)
        self.engine.text("kills %d" % p.kills, 270, 36, size=14, color=(150, 150, 170))
        self.engine.text("score %d" % self.world.score, 372, 36, size=14,
                         color=(235, 195, 80))
        g = p.gun
        self.engine.text(g["name"], 500, 12, size=17, color=g["color"], bold=True)
        self.engine.text("dmg %.0f · rof %.1f · %s" %
                         (g["damage"], g["fire_rate"], dungeon.gun_tags(g)),
                         500, 36, size=13, color=(170, 170, 185))
        st = self.agent.status if self.agent.busy else (
            "AI ready" if self.agent.online else "AI offline (procedural)")
        self.engine.text(st, WIN_W - 360, 38, size=13, color=(150, 150, 170))

    def _draw_banner(self):
        if self.world._banner_t <= 0:
            return
        a = int(255 * min(1.0, self.world._banner_t))
        surf = self.engine._font(24, True).render(self.world.banner, True,
                                                  (240, 240, 250))
        surf.set_alpha(a)
        self.screen.blit(surf, surf.get_rect(center=(WIN_W // 2, OY + 28)))

    def _draw_cursor(self):
        mx, my = pygame.mouse.get_pos()
        pygame.draw.circle(self.screen, (240, 240, 250), (mx, my), 9, 2)
        pygame.draw.circle(self.screen, (240, 240, 250), (mx, my), 1)

    def _draw_gameover(self):
        s = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        s.fill((10, 6, 8, 200))
        self.screen.blit(s, (0, 0))
        cx = WIN_W // 2
        self.engine.text("YOU DIED", cx, WIN_H * 0.34, size=56, center=True,
                         bold=True, color=(220, 80, 80))
        self.engine.text("reached depth %d · %d kills · score %d" %
                         (self.world.depth, self.world.player.kills,
                          self.world.score), cx, WIN_H * 0.34 + 64, size=22,
                         center=True, color=(230, 230, 240))
        self.engine.text("press R to descend again", cx, WIN_H * 0.34 + 110,
                         size=18, center=True, color=(170, 170, 190))

    # =====================================================================
    # Model picker (press M)
    # =====================================================================
    def _draw_chip(self):
        label = "model: %s (M)" % self.current_model
        if len(label) > 40:
            label = "model: %s… (M)" % self.current_model[:30]
        w = self.engine._font(14).size(label)[0] + 24
        rect = pygame.Rect(WIN_W - w - 12, 12, w, 26)
        s = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        s.fill((20, 20, 28, 200))
        self.screen.blit(s, (rect.x, rect.y))
        pygame.draw.rect(self.screen, (70, 70, 92), rect, 1, border_radius=6)
        dot = (110, 210, 130) if self.agent.online else (170, 120, 90)
        self.engine.circle(rect.x + 12, rect.y + 13, 4, dot)
        self.engine.text(label, rect.x + 22, rect.y + 5, size=14, color=(190, 190, 205))
        self._chip_rect = rect

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
        if name != self.current_model:
            self.current_model = name
            self.agent.set_model(name)

    def _click_model_menu(self, pos):
        if self._model_panel and not self._model_panel.collidepoint(pos):
            self.model_menu_open = False
            return
        for rect, kind, value in self._model_rows:
            if rect.collidepoint(pos):
                self._switch_provider(value) if kind == "provider" else \
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
        e.text("CHOOSE THE GENERATOR", panel.centerx, y, size=23,
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
            e.text("loading…" if self._models_loading else
                   ("no models - is Ollama running?" if self.provider == "ollama"
                    else "no models"), x, y, size=16, color=(150, 150, 165))
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
