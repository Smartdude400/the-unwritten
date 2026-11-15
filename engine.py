"""
engine.py - The drawing/input API handed to the AI-generated code.

The AI never touches pygame's event loop or display flip; it only calls methods
on an `engine` instance. This keeps the model's job small (draw ONE frame) and
keeps the harness in control of the loop, scene switching and safety.
"""

import pygame


class Engine:
    def __init__(self, screen):
        self.screen = screen
        self.W, self.H = screen.get_size()
        self.dt = 0.0
        self.mouse = (0, 0)
        self.state = {}            # AI's free-form persistent game data
        self.palette = {
            "bg": (16, 16, 24), "panel": (30, 30, 42), "text": (232, 232, 240),
            "dim": (150, 150, 165), "accent": (120, 160, 240),
            "accent2": (200, 130, 220), "good": (110, 210, 130),
            "bad": (220, 90, 90), "warn": (235, 195, 80),
        }
        self._scene = "menu"
        self._next_scene = None
        self._clicks = []
        self._keydowns = set()
        self._fonts = {}

    # ---- per-frame plumbing (called by the harness) ---------------------
    def begin_frame(self, events, dt):
        self.dt = dt
        self.mouse = pygame.mouse.get_pos()
        self._clicks = [e.pos for e in events
                        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1]
        self._keydowns = {e.key for e in events if e.type == pygame.KEYDOWN}

    def _font(self, size, bold=False):
        key = (int(size), bool(bold))
        f = self._fonts.get(key)
        if f is None:
            f = pygame.font.SysFont("segoeui,arial", int(size), bold=bold)
            self._fonts[key] = f
        return f

    def _mono(self, size):
        key = ("mono", int(size))
        f = self._fonts.get(key)
        if f is None:
            f = pygame.font.SysFont("consolas,couriernew", int(size))
            self._fonts[key] = f
        return f

    # =====================================================================
    # API exposed to the AI-generated code
    # =====================================================================
    def fill(self, color):
        self.screen.fill(_col(color))

    def text(self, s, x, y, size=24, color=(232, 232, 240), center=False,
             bold=False):
        surf = self._font(size, bold).render(str(s), True, _col(color))
        r = surf.get_rect()
        if center:
            r.center = (int(x), int(y))
        else:
            r.topleft = (int(x), int(y))
        self.screen.blit(surf, r)
        return r

    def rect(self, x, y, w, h, color, width=0, radius=0):
        pygame.draw.rect(self.screen, _col(color),
                         pygame.Rect(int(x), int(y), int(w), int(h)),
                         width=int(width), border_radius=int(radius))

    def circle(self, x, y, r, color, width=0):
        pygame.draw.circle(self.screen, _col(color), (int(x), int(y)),
                           int(r), int(width))

    def line(self, x1, y1, x2, y2, color, width=1):
        pygame.draw.line(self.screen, _col(color), (int(x1), int(y1)),
                         (int(x2), int(y2)), int(width))

    def button(self, label, x, y, w=220, h=52, color=(120, 160, 240)):
        """Draw a button centered at (x, y). Returns True the frame it's clicked."""
        rect = pygame.Rect(0, 0, int(w), int(h))
        rect.center = (int(x), int(y))
        hovered = rect.collidepoint(self.mouse)
        pygame.draw.rect(self.screen, self.palette["panel"] if not hovered
                         else (52, 52, 70), rect, border_radius=10)
        pygame.draw.rect(self.screen, _col(color), rect, width=2,
                         border_radius=10)
        surf = self._font(22, True).render(str(label), True,
                                           self.palette["text"])
        self.screen.blit(surf, surf.get_rect(center=rect.center))
        return any(rect.collidepoint(p) for p in self._clicks)

    def key_down(self, name):
        """True only on the frame the key is first pressed."""
        code = _keycode(name)
        return code is not None and code in self._keydowns

    def key_held(self, name):
        """True while the key is held down."""
        code = _keycode(name)
        if code is None:
            return False
        return bool(pygame.key.get_pressed()[code])

    def goto(self, scene):
        self._next_scene = str(scene)

    def quit(self):
        self._next_scene = "quit"


def _col(c):
    try:
        return (int(c[0]) & 255, int(c[1]) & 255, int(c[2]) & 255)
    except (TypeError, ValueError, IndexError):
        return (232, 232, 240)


def _keycode(name):
    try:
        return pygame.key.key_code(str(name))
    except (ValueError, TypeError):
        return None
