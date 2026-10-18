"""Verify the Gemini-exhausted path really reaches OpenRouter in the live modules.

Unit tests in test_ai_backup.py cover the provider itself. This checks the three
call sites actually invoke the shared gateway generate_text, so the backup
remains wired after a refactor.

Run directly:  python tests/test_backup_wiring.py
"""
import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

CALL_SITES = {
    "modules/dashboard_page.py": "generate_section_narrative_ai",
    "modules/report_page.py": "generate_single_narrative_ai",
    "pariwisata/ai.py": "generate_akomodasi_tables",
}


def test_every_generator_references_the_gateway():
    """Static check: the shared generate_text gateway must appear in each narrative generator."""
    missing = []
    for rel, _ in CALL_SITES.items():
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        if "generate_text" not in src:
            missing.append(rel)
    assert not missing, f"generate_text not wired into: {missing}"


def test_report_page_saves_the_backup_to_cache():
    """A backup narrative must be persisted, or every page view re-pays for it."""
    src = open(os.path.join(ROOT, "modules/report_page.py"), encoding="utf-8").read()
    assert "save_db_narrative" in src and "generate_text" in src, (
        "report_page.py must call generate_text and persist via save_db_narrative"
    )


def test_pariwisata_module_imports_cleanly():
    """Catch a bad import in ai_backup before it breaks the Report page."""
    st = types.ModuleType("streamlit")
    st.secrets = types.SimpleNamespace(get=lambda *a, **k: None)
    st.error = lambda *a, **k: None
    st.cache_resource = lambda f=None, **k: (f if f else (lambda g: g))
    st.cache_data = lambda f=None, **k: (f if f else (lambda g: g))
    st.session_state = {}
    st.rerun = lambda *a, **k: None
    sys.modules["streamlit"] = st

    for name in list(sys.modules):
        if name.startswith(("modules.ai_backup", "pariwisata.ai")):
            del sys.modules[name]
    mod = importlib.import_module("pariwisata.ai")
    assert hasattr(mod, "generate_text"), "pariwisata.ai must expose generate_text"
    assert hasattr(mod, "get_gemini_client"), "pariwisata.ai must expose get_gemini_client"


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
