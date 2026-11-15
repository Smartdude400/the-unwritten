"""
agent.py - The StoryWeaver agent.

Runs on a background thread. Given a context dict (the story so far + the
player's action), it asks the model for the next story "beat", streaming the raw
output so the UI can show what the AI is thinking, parses the JSON, and hands the
beat back to the main thread. Provider-agnostic: it just needs a client with
chat()/is_up().
"""

import json
import queue
import re
import threading

import prompts
from story import fallback_beat

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK_TAG = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)


class Agent:
    def __init__(self, client, temperature=1.0):
        self.client = client
        self.temperature = temperature
        self.requests = queue.Queue()
        self.results = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        # observable state for the UI (plain attrs; GIL makes reads safe enough)
        self.status = "idle"
        self.live_text = ""        # raw streamed output (the "thinking view")
        self.last_thinking = ""    # parsed thinking note of the last beat
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

    # ---- request API ----------------------------------------------------
    def request_beat(self, context):
        self.requests.put(context)

    def drain_results(self, limit=8):
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
                self.status = "checking model..."
                self.online = self.client.is_up()
                self.status = "idle"
            try:
                ctx = self.requests.get(timeout=0.2)
            except queue.Empty:
                continue
            self.busy = True
            try:
                self._do_beat(ctx)
            except Exception as e:  # noqa: BLE001 - never let the worker die
                self.online = self.client.is_up()
                self.results.put({"kind": "beat", "beat": fallback_beat(ctx),
                                  "error": str(e)[:120]})
                self.status = "idle"
            finally:
                self.busy = False

    def _call(self, messages, temperature):
        buf = []

        def on_token(tok):
            buf.append(tok)
            self.live_text = "".join(buf)[-4000:]

        text = self.client.chat(messages, temperature=temperature,
                                stream=True, on_token=on_token)
        self.online = True
        return text

    def _do_beat(self, ctx):
        self.status = "dreaming the next moment..."
        self.live_text = ""
        if not self.online:
            beat = fallback_beat(ctx)
            self.last_thinking = beat.get("thinking", "")
            self.results.put({"kind": "beat", "beat": beat})
            self.status = "idle"
            return

        raw = self._call(prompts.messages_for_beat(ctx), self.temperature)
        beat = _parse_beat(raw)
        if beat is None:
            raw = self._call(prompts.messages_for_fix(ctx, raw),
                             max(0.4, self.temperature - 0.4))
            beat = _parse_beat(raw)
        if beat is None:
            beat = fallback_beat(ctx)
        self.last_thinking = str(beat.get("thinking", "")).strip()
        self.results.put({"kind": "beat", "beat": beat})
        self.status = "idle"


def _parse_beat(text):
    """Pull a single JSON object out of the model's reply, tolerating <think>
    blocks, code fences, and surrounding prose."""
    if not text:
        return None
    cleaned = _THINK_TAG.sub("", text).strip()
    for candidate in _candidates(cleaned):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (ValueError, json.JSONDecodeError):
            continue
    return None


def _candidates(text):
    yield text
    m = _JSON_FENCE.search(text)
    if m:
        yield m.group(1).strip()
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        yield text[i:j + 1]
