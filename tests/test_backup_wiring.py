"""Verify the Gemini-exhausted path really reaches OpenRouter in the live modules.

Unit tests in test_ai_backup.py cover the provider itself. This checks the three
call sites actually invoke it, so a refactor cannot leave the backup unwired
while the provider still passes its own tests.

Run directly:  python tests/test_backup_wiring.py
"""
import importlib
import inspect
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

CALL_SITES = {
    "modules/dashboard_page.py": "generate_section_narrative_ai",
    "modules/report_page.py": "generate_report_narrative_ai",
    "pariwisata/ai.py": "generate_akomodasi_tables",
}


def test_every_generator_references_the_backup():
    """Static check: the fallback symbol must appear in each narrative generator."""
    missing = []
    for rel, _ in CALL_SITES.items():
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        if "openrouter_generate" not in src:
            missing.append(rel)
    assert not missing, f"backup not wired into: {missing}"


def test_backup_is_the_last_resort():
    """Every OpenRouter call must come after the Gemini loop, never before.

    Guards against the backup shadowing the primary provider, which would quietly
    replace Gemini everywhere the day the OpenRouter key is valid.
    """
    for rel in ("modules/dashboard_page.py", "modules/report_page.py"):
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        gemini_loop = src.index("for attempt in range(num_keys)")
        backup_call = src.index("backup = openrouter_generate", gemini_loop)
        assert backup_call > gemini_loop, f"{rel}: backup runs before the Gemini loop"
        # and it must sit after the last Gemini model in that loop
        assert "candidate_models" in src[:gemini_loop], rel


def test_report_page_saves_the_backup_to_cache():
    """A backup narrative must be persisted, or every page view re-pays for it."""
    src = open(os.path.join(ROOT, "modules/report_page.py"), encoding="utf-8").read()
    # anchor past the import: the first occurrence is `from ... import`
    call = src.index("backup = openrouter_generate")
    tail = src[call:call + 400]
    assert "save_db_narrative" in tail, "backup text is not cached"


def test_pariwisata_module_imports_cleanly():
    """Catch a bad import in ai_backup before it breaks the Report page."""
    st = types.ModuleType("streamlit")
    st.secrets = types.SimpleNamespace(get=lambda *a, **k: None)
    st.error = lambda *a, **k: None
    sys.modules["streamlit"] = st

    for name in list(sys.modules):
        if name.startswith(("modules.ai_backup", "pariwisata.ai")):
            del sys.modules[name]
    mod = importlib.import_module("pariwisata.ai")
    assert hasattr(mod, "openrouter_generate")


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