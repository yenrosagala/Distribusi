"""Single AI gateway: Gemini (primary) then OpenRouter (backup).

Every narrative generator — dashboard, report, and pariwisata — goes through
:func:`generate_text`, so the key x model retry ladder, the key discovery, and
the OpenRouter failover live in exactly one place instead of being copy-pasted
per page. Gemini stays the primary path (already cached in ``*_ai_narratives``);
OpenRouter is only reached once every Gemini key x model has been exhausted.

Why a hand-rolled ``requests`` call instead of the ``openai`` SDK: OpenRouter
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
# after a config restore that predates this feature. Ids verified with a live
# probe (2026-10): the `:free` tiers are flaky — nemotron-ultra returns an empty
# content block and gemma 429s — so only the two models that actually answered
# are kept, fastest first.
DEFAULT_MODELS = [
    "openrouter/free",
    "nvidia/nemotron-3.5-lightning:free",
]

# Gemini model ladder, tried in order; first non-empty reply wins. A single
# hardcoded id rots the moment Google retires a version -- gemini-2.5-flash
# already 404s ("no longer available to new users") for accounts created later
# -- so the stable alias goes first and concrete ids back it up. Shared by the
# dashboard, report, and pariwisata narrative generators so they cannot drift.
GEMINI_CANDIDATE_MODELS = [
    "gemini-flash-latest",   # stable alias -- always points at the current flash
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
]


def get_openrouter_key():
    return read_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")


# ---------------------------------------------------------------------------
# Gemini: key discovery, client build, key x model ladder, session rotation
# ---------------------------------------------------------------------------

# Every spelling of a Gemini key used anywhere in the app (or a config restore).
# Reading them all here is what removes the old blind spot where a key set only
# as GOOGLE_API_KEY was visible to the Report page but invisible to Pariwisata.
_GEMINI_KEY_NAMES = (
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "API_GEMINI_KEYS", "API_GEMINI_KEY", "API-GEMINI-KEYS",
)


def get_gemini_api_keys():
    """All configured Gemini keys, de-duplicated, secrets before env.

    A value may be a bare string or a list (``GEMINI_API_KEYS`` is a list in
    secrets); both are flattened. Empty/whitespace entries are dropped.
    """
    keys = []
    for name in _GEMINI_KEY_NAMES:
        raw = read_secret(name)
        if raw in (None, ""):
            raw = os.getenv(name)
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        for value in values:
            value = str(value).strip() if value is not None else ""
            if value:
                keys.append(value)

    seen, unique = set(), []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def get_gemini_client():
    """First client that constructs from the configured keys, else None."""
    try:
        from google import genai
    except Exception:
        return None
    for key in get_gemini_api_keys():
        try:
            return genai.Client(api_key=key)
        except Exception:
            continue
    return None


def _session_state():
    try:
        import streamlit as st
        return st.session_state
    except Exception:
        return None


def _starting_key_index(num_keys):
    """Round-robin start so repeated pages do not always open the same key."""
    if not num_keys:
        return 0
    ss = _session_state()
    if ss is None:
        return 0
    try:
        return int(ss["gemini_key_index"]) % num_keys
    except Exception:
        return 0


def _advance_key_index(next_index):
    ss = _session_state()
    if ss is not None:
        try:
            ss["gemini_key_index"] = next_index
        except Exception:
            pass


def _generate_gemini(prompt, temperature=None, client=None, keys=None):
    """``(text, model)`` of the first non-empty Gemini reply, else ``(None, None)``.

    ``client`` (kept injectable for tests) is tried first; then every configured
    key in rotated order, each across :data:`GEMINI_CANDIDATE_MODELS`. A retired
    model id or an exhausted key therefore only costs one attempt, not the page.
    """
    try:
        from google import genai
    except Exception:
        return None, None

    def attempt(c):
        for model_name in GEMINI_CANDIDATE_MODELS:
            try:
                kwargs = {"model": model_name, "contents": prompt}
                if temperature is not None:
                    kwargs["config"] = genai.types.GenerateContentConfig(temperature=temperature)
                text = c.models.generate_content(**kwargs).text
            except Exception as exc:
                logger.warning("Gemini gagal (%s): %s", model_name, exc)
                continue
            if text and str(text).strip():
                return str(text).strip(), model_name
        return None, None

    if client is not None:
        text, model = attempt(client)
        if text:
            return text, model

    keys = list(keys if keys is not None else get_gemini_api_keys())
    num = len(keys)
    start = _starting_key_index(num)
    for offset in range(num):
        idx = (start + offset) % num
        try:
            c = genai.Client(api_key=keys[idx])
        except Exception:
            continue
        text, model = attempt(c)
        if text:
            _advance_key_index((idx + 1) % num)
            return text, model
    return None, None


def generate_text(prompt, temperature=0.3, client=None):
    """The one AI entry point: Gemini (every key x model) then OpenRouter.

    Returns ``(text, source)``. ``text`` is None when every provider fails so
    callers fall through to their deterministic template; ``source`` names who
    answered for the UI ("Gemini AI (model)", "OpenRouter (Cadangan)",
    "No API Key", "Failed").
    """
    if client is None and not get_gemini_api_keys() and not get_openrouter_key():
        return None, "No API Key"

    text, model = _generate_gemini(prompt, temperature=temperature, client=client)
    if text:
        return text, f"Gemini AI ({model})" if model else "Gemini AI"

    backup = openrouter_generate(prompt, temperature=temperature)
    if backup:
        return backup, "OpenRouter (Cadangan)"
    return None, "Failed"


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