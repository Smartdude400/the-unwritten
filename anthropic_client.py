"""
anthropic_client.py - Claude API client (defaults to Haiku 4.5).

Exposes the same small interface as OllamaClient (`chat`, `is_up`, `.model`) so
the rest of the game doesn't care which provider is generating code. Uses the
official `anthropic` SDK (the recommended path for Python), which reads the key
from the ANTHROPIC_API_KEY environment variable.
"""

import os

import anthropic


class AnthropicClient:
    def __init__(self, model="claude-haiku-4-5", max_tokens=8192):
        self.model = model
        self.max_tokens = max_tokens
        # The SDK raises at construction if no key is available, so only build
        # the client when a key is present; otherwise stay gracefully "offline".
        key = os.environ.get("ANTHROPIC_API_KEY")
        self._client = anthropic.Anthropic(api_key=key) if key else None

    # ---- connectivity ---------------------------------------------------
    def is_up(self):
        if self._client is None:
            return False
        try:
            # cheap, token-free check that the key + model are valid
            self._client.with_options(timeout=8, max_retries=0).models.retrieve(
                self.model)
            return True
        except Exception:  # noqa: BLE001 - any failure means "not ready"
            return False

    # ---- chat -----------------------------------------------------------
    def chat(self, messages, temperature=0.5, stream=True, on_token=None):
        """Match OllamaClient.chat: returns the full assistant text, streaming
        tokens to on_token. Raises RuntimeError with a clear message on failure."""
        if self._client is None:
            raise RuntimeError(
                "no API key - set ANTHROPIC_API_KEY to use the Claude API")

        system, convo = _split_system(messages)
        if not convo:
            convo = [{"role": "user", "content": "Begin."}]

        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": float(temperature),
            "messages": convo,
        }
        if system:
            kwargs["system"] = system

        try:
            if stream:
                parts = []
                with self._client.messages.stream(**kwargs) as s:
                    for text in s.text_stream:
                        parts.append(text)
                        if on_token:
                            on_token(text)
                return "".join(parts)
            resp = self._client.messages.create(**kwargs)
            return "".join(b.text for b in resp.content if b.type == "text")
        except anthropic.AuthenticationError:
            raise RuntimeError("Claude API: invalid ANTHROPIC_API_KEY (401)")
        except anthropic.PermissionDeniedError:
            raise RuntimeError("Claude API: key lacks access to %s (403)"
                               % self.model)
        except anthropic.NotFoundError:
            raise RuntimeError("Claude API: model '%s' not found (404)" % self.model)
        except anthropic.RateLimitError:
            raise RuntimeError("Claude API: rate limited (429) - wait a moment")
        except anthropic.APIConnectionError:
            raise RuntimeError("Claude API: can't connect - check your network")
        except anthropic.APIStatusError as e:
            raise RuntimeError("Claude API error %s" % e.status_code)


def _split_system(messages):
    """Anthropic takes the system prompt as a top-level param, not a message.
    Pull any system-role messages out and concatenate them."""
    system_parts = []
    convo = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content)
        else:
            convo.append({"role": role, "content": content})
    return "\n\n".join(system_parts), convo
