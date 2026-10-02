"""Live smoke check for the OpenRouter backup.

Not part of the test suite -- it spends real quota and needs a real key.
Run manually:  python tests/smoke_openrouter_live.py
Exits non-zero if no configured model answers, so a broken chain is noticed
before it silently degrades every narrative on the page.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

PROMPT = "Tulis satu kalimat singkat tentang Papua dalam bahasa Indonesia."


def main():
    # Use the real Streamlit secrets if the app can reach them, else a bare
    # module import; read_secret swallows the failure either way.
    from modules.ai_backup import get_openrouter_key, get_openrouter_models, openrouter_generate

    key = get_openrouter_key()
    models = get_openrouter_models()

    print(f"key configured : {'yes' if key else 'NO'}")
    print(f"model chain    : {len(models)}")
    for m in models:
        print(f"  - {m}")

    if not key:
        print("\nFAIL: OPENROUTER_API_KEY tidak ada di .streamlit/secrets.toml")
        return 1

    print("\nmengirim request...")
    text = openrouter_generate(PROMPT, temperature=0.2)
    if not text:
        print("\nFAIL: tidak ada model yang merespons (chain habis)")
        return 1

    print(f"\nOK: {text[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())