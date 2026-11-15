"""
ollama_client.py - Tiny dependency-free client for a local Ollama server.

Only uses the standard library (urllib), so pygame is the project's single
third-party dependency. Supports streaming so the UI can show the AI writing
code token-by-token.
"""

import json
import urllib.request
import urllib.error


class OllamaClient:
    def __init__(self, host="http://localhost:11434", model="mistral",
                 timeout=240):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    # ---- connectivity ---------------------------------------------------
    def is_up(self):
        try:
            req = urllib.request.Request(self.host + "/api/tags")
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def list_models(self):
        try:
            req = urllib.request.Request(self.host + "/api/tags")
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
                return [m.get("name", "") for m in data.get("models", [])]
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return []

    # ---- chat -----------------------------------------------------------
    def chat(self, messages, temperature=0.5, stream=True, on_token=None):
        """Send a chat request. Returns the full assistant text.

        If stream=True, on_token(str) is called for each chunk as it arrives.
        Raises RuntimeError on connection/parse failure.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": bool(stream),
            "options": {"temperature": float(temperature)},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.host + "/api/chat", data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                if not stream:
                    body = json.loads(r.read().decode("utf-8"))
                    return (body.get("message") or {}).get("content", "")
                parts = []
                for raw in r:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        chunk = json.loads(raw.decode("utf-8"))
                    except (ValueError, json.JSONDecodeError):
                        continue
                    tok = (chunk.get("message") or {}).get("content", "")
                    if tok:
                        parts.append(tok)
                        if on_token:
                            on_token(tok)
                    if chunk.get("done"):
                        break
                return "".join(parts)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:200]
            except OSError:
                pass
            if e.code == 404:
                raise RuntimeError(
                    "model '%s' not found (404). Check the exact name with "
                    "`ollama list` or pull it. %s" % (self.model, detail))
            raise RuntimeError("Ollama HTTP %s: %s" % (e.code, detail or e.reason))
        except urllib.error.URLError as e:
            raise RuntimeError("can't reach Ollama at %s (%s)"
                               % (self.host, getattr(e, "reason", e)))
        except (OSError, ValueError) as e:
            raise RuntimeError("request failed: %s" % e)
