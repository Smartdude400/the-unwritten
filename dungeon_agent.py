"""
dungeon_agent.py - Background content generator for the shooter.

Takes ("gun"|"monster"|"room", depth) requests and asks the model for one JSON
object of that kind, parsing it out of whatever the model returns. Hands the raw
dict back to the main thread, which normalizes and buffers it (with procedural
fallbacks). Provider-agnostic: needs a client with chat()/is_up().
"""

import json
import queue
import re
import threading

import dungeon_prompts as P

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)

_BUILDERS = {
    "gun": P.messages_for_gun,
    "monster": P.messages_for_monster,
    "room": P.messages_for_room,
}


class ContentAgent:
    def __init__(self, client, temperature=0.95):
        self.client = client
        self.temperature = temperature
        self.requests = queue.Queue()
        self.results = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self.status = "idle"
        self.busy = False
        self.online = False
        self._reprobe = False

    # ---- lifecycle ------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def set_client(self, client):
        self.client = client
        self._reprobe = True

    def set_model(self, name):
        self.client.model = name
        self._reprobe = True

    def request(self, kind, depth):
        self.requests.put((kind, depth))

    def drain_results(self, limit=12):
        out = []
        for _ in range(limit):
            try:
                out.append(self.results.get_nowait())
            except queue.Empty:
                break
        return out

    # ---- worker ---------------------------------------------------------
    def _run(self):
        self.online = self.client.is_up()
        while not self._stop.is_set():
            if self._reprobe:
                self._reprobe = False
                self.online = self.client.is_up()
            try:
                kind, depth = self.requests.get(timeout=0.2)
            except queue.Empty:
                continue
            self.busy = True
            try:
                self._do(kind, depth)
            except Exception as e:  # noqa: BLE001 - never let the worker die
                self.online = self.client.is_up()
                self.results.put({"kind": kind, "depth": depth, "raw": None,
                                  "error": str(e)[:120]})
            finally:
                self.busy = False

    def _do(self, kind, depth):
        self.status = "forging a %s (depth %d)..." % (kind, depth)
        if not self.online:
            self.results.put({"kind": kind, "depth": depth, "raw": None})
            self.status = "idle"
            return
        text = self.client.chat(_BUILDERS[kind](depth),
                                temperature=self.temperature, stream=False)
        self.online = True
        self.results.put({"kind": kind, "depth": depth, "raw": _parse(text)})
        self.status = "idle"


def _parse(text):
    if not text:
        return None
    cleaned = _THINK.sub("", text).strip()
    cands = [cleaned]
    m = _FENCE.search(cleaned)
    if m:
        cands.append(m.group(1).strip())
    i, j = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= i < j:
        cands.append(cleaned[i:j + 1])
    for c in cands:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except (ValueError, json.JSONDecodeError):
            continue
    return None
