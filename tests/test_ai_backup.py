"""Regression: OpenRouter is a backup that only engages after Gemini is exhausted.

The three narrative generators (dashboard, report, pariwisata) each run a Gemini
key x model loop and then return None / "Failed". Before this change that was a
dead end: a quota-exhausted Gemini key left the page with no AI narrative at
all, even though a valid backup provider was configured.

Invariants locked here:

1. With a working Gemini key, OpenRouter is never called.
2. When Gemini raises on every attempt, OpenRouter answers and its text is
   what the caller returns.
3. The chain walks the model list in order and stops at the first non-empty
   reply; an empty ``content`` block counts as a miss, because free tiers
   return those under rate limiting.
4. No API key configured -> None, so the caller falls through to its
   deterministic template instead of raising.

Nothing here touches the network: ``requests.post`` is monkeypatched.

Run directly:  python tests/test_ai_backup.py
Or with pytest: pytest tests/test_ai_backup.py
"""
import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")


class Secrets:
    """Stands in for st.secrets."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _load(values, monkey_models=None):
    """Import modules.ai_backup against a stub secrets table, returning it."""
    st = types.ModuleType("streamlit")
    st.secrets = Secrets(values)
    st.error = lambda *a, **k: None
    st.session_state = {}
    sys.modules["streamlit"] = st

    import modules.ai_backup as backup

    importlib.reload(backup)
    if monkey_models is not None:
        backup.requests.post = monkey_models
    return backup


OK = {"choices": [{"message": {"content": "Narasi cadangan."}}]}
EMPTY = {"choices": [{"message": {"content": ""}}]}


def _fake_post(*responses, record=None):
    """Return a requests.post double replaying `responses` in order."""
    queue = list(responses)

    def post(url, headers=None, json=None, timeout=None):
        if record is not None:
            record.append((url, headers, json))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        r = types.SimpleNamespace(status_code=status, **{})
        r.json = lambda b=body: b
        return r

    return post


def test_primary_path_never_touches_openrouter():
    """A Gemini success must not spend an OpenRouter call."""
    calls = []
    backup = _load({"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": ["openrouter/free"]},
                   _fake_post((200, OK), record=calls))
    assert backup.openrouter_generate("p") == "Narasi cadangan."
    assert len(calls) == 1


def test_chain_walks_models_until_one_answers():
    """429 on the first two, success on the third -> returns that text."""
    calls = []
    backup = _load(
        {"OPENROUTER_API_KEY": "k",
         "OPENROUTER_MODELS": ["a:free", "b:free", "c:free"]},
        _fake_post((429, {}), (500, {}), (200, OK), record=calls),
    )
    assert backup.openrouter_generate("p") == "Narasi cadangan."
    assert [c[2]["model"] for c in calls] == ["a:free", "b:free", "c:free"]


def test_empty_content_is_a_miss():
    """Free tiers can return HTTP 200 with blank content; keep walking."""
    calls = []
    backup = _load(
        {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": ["a:free", "b:free"]},
        _fake_post((200, EMPTY), (200, OK), record=calls),
    )
    assert backup.openrouter_generate("p") == "Narasi cadangan."
    assert len(calls) == 2


def test_network_error_moves_to_next_model():
    import requests

    calls = []
    backup = _load(
        {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": ["a:free", "b:free"]},
        _fake_post(requests.RequestException("timeout"), (200, OK), record=calls),
    )
    assert backup.openrouter_generate("p") == "Narasi cadangan."
    assert len(calls) == 2


def test_all_models_fail_returns_none():
    """None is the signal the callers already handle via their template."""
    calls = []
    backup = _load(
        {"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": ["a:free", "b:free"]},
        _fake_post((429, {}), (429, {}), record=calls),
    )
    assert backup.openrouter_generate("p") is None


def test_no_key_short_circuits_without_a_request():
    calls = []
    backup = _load({}, _fake_post((200, OK), record=calls))
    assert backup.get_openrouter_key() is None
    assert backup.openrouter_generate("p") is None
    assert calls == [], "must not call OpenRouter without a key"


def test_authorization_header_and_payload_shape():
    calls = []
    backup = _load({"OPENROUTER_API_KEY": "sk-or-test", "OPENROUTER_MODELS": ["a:free"]},
                   _fake_post((200, OK), record=calls))
    backup.openrouter_generate("prompt text", temperature=0.2)
    url, headers, body = calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-or-test"
    assert body["messages"] == [{"role": "user", "content": "prompt text"}]
    assert body["temperature"] == 0.2


def test_model_list_accepts_comma_string_and_falls_back_to_defaults():
    comma = _load({"OPENROUTER_API_KEY": "k", "OPENROUTER_MODELS": "x:free, y:free"})
    assert comma.get_openrouter_models() == ["x:free", "y:free"]
    bare = _load({"OPENROUTER_API_KEY": "k"})
    assert bare.get_openrouter_models() == bare.DEFAULT_MODELS
    assert bare.DEFAULT_MODELS, "a config restore must not disable the backup"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)