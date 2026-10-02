"""OpenRouter as a fallback provider when every Gemini key fails.

Gemini stays the primary path — it is already wired, cached, and its output is
stored in ``*_ai_narratives``. This module is only reached after that whole key
x model loop has been exhausted, so adding a backup never changes the common
path or the cached-narrative behaviour.

Why a hand-rolled ``urllib`` call instead of the ``openai`` SDK: OpenRouter
speaks the OpenAI chat-completions schema, but the SDK would be a new dependency
and a new auth surface for one endpoint. ``requests`` is already installed and
``read_secret`` already exists.

Config (``.streamlit/secrets.toml``, git-ignored)::

    OPENROUTER_API_KEY = "sk-or-v1-..."
    OPENROUTER_MODELS = ["openrouter/free", "nvidia/nemotron-3.5-lightning:free"]

The model list is tried in order and the first non-empty response wins. Free
tiers are rate-limited and occasionally return an empty ``content`` block, so an
empty reply is treated as a miss and the chain continues.
"""
import logging
import os

import requests

from modules.config import read_secret

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 90

# Used when OPENROUTER_MODELS is absent from secrets so the backup still works
# after a config restore that predates this feature. Ids verified against
# https://openrouter.ai/api/v1/models — a typo here fails silently as a 404.
DEFAULT_MODELS = [
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-26b-a4b-it:free",
]


def get_openrouter_key():
    return read_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")


def get_openrouter_models():
    raw = read_secret("OPENROUTER_MODELS") or os.getenv("OPENROUTER_MODELS")
    if isinstance(raw, str):
        models = [m.strip() for m in raw.split(",") if m.strip()]
    elif raw:
        models = [str(m).strip() for m in raw if str(m).strip()]
    else:
        models = list(DEFAULT_MODELS)
    return models or list(DEFAULT_MODELS)


def openrouter_generate(prompt, temperature=0.3):
    """Return text from the first OpenRouter model that answers, else None.

    ``None`` means "every configured model failed"; callers already handle that
    by falling through to their deterministic template narrative.
    """
    key = get_openrouter_key()
    if not key:
        return None

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    for model in get_openrouter_models():
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=TIMEOUT)
            if r.status_code != 200:
                logger.warning("OpenRouter %s gagal: HTTP %s", model, r.status_code)
                continue
            content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content")
            if content and str(content).strip():
                logger.info("OpenRouter fallback berhasil via %s", model)
                return str(content).strip()
            logger.warning("OpenRouter %s balik kosong", model)
        except requests.RequestException as exc:
            logger.warning("OpenRouter %s error: %s", model, exc)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("OpenRouter %s balasan tidak terparse: %s", model, exc)

    return None