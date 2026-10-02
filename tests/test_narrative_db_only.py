"""Regression: narratives are read from the database, only admins may write.

The report used to call Gemini on every page view whenever the narratives table
had no matching row, so an uncached indicator cost an API call per visitor.
Now the table is the source of truth:

1. A saved narrative is served from the database for every user.
2. On a cache miss only an admin sees a Generate button; a regular user gets a
   short notice and no Gemini call.
3. Admins get Regenerate, which deletes the cached row and re-runs rather than
   regenerating inline, so the new text always goes through the same
   read-then-render path.

The function binds ``import streamlit as st`` at import time, so each case
reloads the module against a fresh stub. Tests run against a throwaway copy of
the local SQLite database and never touch the real one.

Run directly:  python tests/test_narrative_db_only.py
Or with pytest: pytest tests/test_narrative_db_only.py
"""
import contextlib
import importlib
import os
import shutil
import sys
import tempfile

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

PROV, YEAR, MONTH = "Papua", 2026, 8
JENIS = ("Hotel Bintang", "Hotel Non Bintang")
INDICATORS = ("tpk", "rlmtgab")
SAVED = "NARASI TERSIMPAN"
FRESH = "NARASI DARI MODEL"


class Rerun(Exception):
    """Stands in for st.rerun(), which restarts the script."""


class Spy:
    """Gemini client double that counts calls."""

    def __init__(self):
        self.calls = 0
        self.models = self  # code calls client.models.generate_content(...)

    def generate_content(self, model=None, contents=None):
        self.calls += 1
        return type("R", (), {"text": FRESH})()


class Stub:
    """Minimal Streamlit surface: records what the page rendered."""

    def __init__(self, role, clicked=frozenset()):
        self.session_state = {"role": role}
        self.clicked = set(clicked)
        self.spy = Spy()
        self.markdowns, self.infos, self.errors = [], [], []
        self.buttons, self.warnings = [], []

    def button(self, label, **kw):
        self.buttons.append(label)
        return kw.get("key") in self.clicked

    def rerun(self, *a, **k):
        raise Rerun()

    def markdown(self, s="", **k):
        self.markdowns.append(s)

    def info(self, s="", **k):
        self.infos.append(s)

    def error(self, s="", **k):
        self.errors.append(s)

    def warning(self, s="", **k):
        self.warnings.append(s)

    def dataframe(self, *a, **k):
        pass

    def caption(self, *a, **k):
        pass

    def divider(self):
        pass

    def container(self, **k):
        return contextlib.nullcontext()

    def columns(self, n, **k):
        return [contextlib.nullcontext() for _ in range(n)]

    def spinner(self, *a, **k):
        return contextlib.nullcontext()

    def cache_data(self, *a, **k):
        if len(a) == 1 and not k and callable(a[0]):
            return a[0]
        return lambda fn: fn

    cache_resource = cache_data

    def selectbox(self, *a, **k):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _engine(tmp, cached):
    eng = create_engine(f"sqlite:///{tmp}")
    with eng.begin() as c:
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS pariwisata_ai_narratives ("
            "province TEXT, year INTEGER, month INTEGER, jenis_akomodasi TEXT,"
            "indicator TEXT, narrative TEXT)"))
        c.execute(text("DELETE FROM pariwisata_ai_narratives"))
        if cached:
            for j in JENIS:
                for i in INDICATORS:
                    c.execute(text(
                        "INSERT INTO pariwisata_ai_narratives VALUES "
                        "(:p, :y, :m, :j, :i, :n)"),
                        {"p": PROV, "y": YEAR, "m": MONTH, "j": j, "i": i, "n": SAVED})
    return eng


def render(role, cached, clicked=frozenset()):
    """Run the report tables against a temp copy of the database."""
    src = os.path.join(ROOT, "data", "app_data.db")
    tmp = os.path.join(tempfile.gettempdir(), "narr_db_only_test.db")
    shutil.copy2(src, tmp)

    eng = _engine(tmp, cached)
    with eng.connect() as c:
        assert pd.read_sql_query(
            "SELECT DISTINCT kd_prov, year, month FROM all_data", c
        ).shape[0] > 0, "source db must have data for the query to render"

    shim = type("E", (), {"general_table_name": "all_data", "engine": eng})()
    stub = Stub(role, clicked)
    sys.modules["streamlit"] = stub

    # ai.py binds `import streamlit as st` on import, so reload against the stub.
    ai = importlib.import_module("pariwisata.ai")
    importlib.reload(ai)
    ai.get_gemini_client = lambda: stub.spy

    try:
        ai.generate_akomodasi_tables(shim, PROV, YEAR, MONTH)
        reran = False
    except Rerun:
        reran = True

    with eng.connect() as c:
        written = c.execute(text(
            "SELECT COUNT(*) FROM pariwisata_ai_narratives WHERE narrative = :n"),
            {"n": FRESH}).scalar()
    eng.dispose()
    os.remove(tmp)
    return stub, reran, written


def test_regular_user_never_calls_ai():
    for cached in (False, True):
        stub, _, written = render("user", cached)
        assert stub.spy.calls == 0, f"user spent an API call (cached={cached})"
        assert stub.buttons == [], f"user sees buttons {stub.buttons}"
        assert written == 0, "user wrote to the narratives table"


def test_regular_user_is_told_to_ask_admin():
    stub, _, _ = render("user", cached=False)
    assert len(stub.infos) == len(JENIS) * len(INDICATORS)
    assert all("admin" in i.lower() for i in stub.infos), stub.infos


def test_saved_narrative_is_served_from_database():
    for role in ("user", "admin"):
        stub, _, _ = render(role, cached=True)
        served = sum(SAVED in str(m) for m in stub.markdowns)
        assert served == len(JENIS) * len(INDICATORS), f"{role}: {served}"
        assert any("from database" in str(m) for m in stub.markdowns)
        assert stub.spy.calls == 0, f"{role} regenerated on page view"


def test_admin_gets_generate_only_on_cache_miss():
    stub, _, written = render("admin", cached=False)
    assert sum("Generate" in b for b in stub.buttons) == len(JENIS) * len(INDICATORS)
    assert not any("Regenerate" in b for b in stub.buttons)
    assert stub.spy.calls == 0, "must not auto-generate"
    assert written == 0


def test_admin_clicking_generate_writes_once():
    key = f"gen_{PROV}_{YEAR}_{MONTH}_{JENIS[0]}_{INDICATORS[0]}"
    stub, _, written = render("admin", cached=False, clicked={key})
    assert stub.spy.calls == 1, f"calls={stub.spy.calls}"
    assert written == 1, f"written={written}"
    assert any(FRESH in str(m) for m in stub.markdowns)


def test_admin_regenerate_clears_cache_and_reruns():
    key = f"regen_{PROV}_{YEAR}_{MONTH}_{JENIS[0]}_{INDICATORS[0]}"
    stub, reran, written = render("admin", cached=True, clicked={key})
    assert reran, "regenerate must re-run so the cache is re-read"
    assert stub.spy.calls == 0, "regenerate must not call the model inline"
    assert written == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("test_narrative_db_only: all pass")