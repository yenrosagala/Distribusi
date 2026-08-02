# 📊 Dashboard Integrasi Papua — Pariwisata & Transportasi

A single Streamlit app combining two previously separate dashboards into one
shell:

- **Pariwisata** (from `dash-pariwisata`) — hotel occupancy (TPK) and length-of-stay
  (RLMTGAB) analytics: Home Dashboard, Infographic Stat Map, Trends
  Visualizations, Report (AI-narrated), Admin ETL Uploads.
- **Transportasi** (from `StaTransportasi`) — sea/air transport statistics:
  Dashboard Statistik, Laporan Komparatif (Word export), Admin & Analisis
  Series.

The UI/UX shell (theme, login screen, sidebar navigation, `style.css`) comes
from **dash-pariwisata**. The database layer (SQLAlchemy engine, configurable
via `DATABASE_URL`) comes from **StaTransportasi** — both domains now share
one database, one engine, one connection config.

## Project structure

```
.
├── app.py                     # Unified shell: page config, login, nav, routing
├── style.css                  # Theme (from dash-pariwisata, unchanged)
├── logo.png                   # Unused brand asset carried over (unused in original too)
├── papua_provinces.parquet    # Map geometry for Infographic Stat Map
├── requirements.txt
├── data/
│   └── app_data.db            # Local SQLite fallback DB (seeded from both repos' original data)
├── pariwisata/                # From dash-pariwisata, adapted to the shared DB
│   ├── etl_engine.py          # ETLEngine — same transform logic, now runs on the shared SQLAlchemy engine
│   ├── ai.py                  # Gemini narrative generation for the Report page
│   └── pages.py               # The 5 page-render functions (unwrapped from the original if/elif block)
└── modules/                   # From StaTransportasi, unchanged except database.py
    ├── database.py            # get_engine()/init_db() — added a local SQLite fallback (see below)
    ├── config.py               # Papua wilayah/kabupaten mapping
    ├── etl_engine.py           # BPS Excel parser for transport data
    ├── dashboard_page.py       # show_dashboard_page()
    ├── report_page.py         # show_report_page()
    └── admin_page.py           # show_series_admin_page()
```

`StaTransportasi`'s original `app.py` was dead code (leftover Dash
boilerplate that didn't import any of its own modules), and `maps.py` in
`dash-pariwisata` is an offline one-time script that generated the `.parquet`
file — neither is part of the running app, so neither was carried over.

## Running locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Login: `admin` / `admin123` (full access) or `user` / `user123` (no Admin pages).

## Database

`modules/database.py`'s `get_engine()` still reads `DATABASE_URL` from
Streamlit secrets/env exactly as StaTransportasi did. **New:** if no
`DATABASE_URL` is configured, it now falls back to a local SQLite file at
`data/app_data.db` instead of crashing — this file has already been seeded
with both repos' original demo data (240 sea-transport rows, 504
air-transport rows, 87 tourism rows, 5 cached AI narratives) so the app runs
out of the box.

For production, set `DATABASE_URL` in `.streamlit/secrets.toml` (or as an
env var) to a Postgres connection string, e.g.:

```toml
DATABASE_URL = "postgresql://user:password@host:5432/dbname"
GEMINI_API_KEY = "your-key"
# or a rotation list:
# GEMINI_API_KEYS = ["key1", "key2"]
```

Both the tourism tables (`all_data`, `ai_narratives`) and the transport
tables (`wilayah`, `transportasi_laut`, `transportasi_udara`,
`ai_narratives_cache`) live in this one database.

## Integration notes / things worth knowing

- **Tourism ETL engine was rewritten**, not just copied: it originally used
  raw `sqlite3` against its own private `etl_data.db` file. It now runs on
  the shared SQLAlchemy engine (named `:param` bind parameters instead of
  `?`, portable `DELETE`+`INSERT` instead of SQLite-only
  `INSERT OR REPLACE`) so it works on both SQLite and Postgres. The actual
  transform/query *logic* is unchanged — verified against the original
  `_transform_data` behavior.
- **Transportasi's admin page has a double gate.** Its `show_series_admin_page()`
  function is carried over unmodified, including its own internal
  `papua123` password prompt (which is how it originally worked, since the
  repo had no app-level login at all). On top of that, the sidebar now only
  shows the "Admin & Analisis Series" nav entry to users with the unified
  `admin` role, per your request to gate it consistently. That means an
  admin still has to type `papua123` a second time once they land on the
  page — a bit redundant, but intentionally left as-is to keep that page's
  function identical to the original. Say the word if you'd like that inner
  password prompt removed now that there's a real login.
- Its "Log Out Admin" button (inside that page, only clears its own inner
  `admin_logged_in` flag) will appear in the sidebar alongside the app's
  main "Log out" button when you're on that page — again, unmodified
  original behavior, just worth knowing it's not a bug.
- `requirements.txt` drops a few unused/incorrect entries from
  dash-pariwisata's original file (`Dash` — never imported at runtime,
  `gunicorn` — not used by Streamlit, `datetime` — a stdlib module, not a
  pip package).

## Not fully tested end-to-end

This was built and verified in a sandbox with no internet access, so I
could not `pip install streamlit`/`geopandas`/etc. and actually launch the
app. I did:
- Syntax-check every file (`py_compile`).
- Validate every DDL statement against real SQLite.
- Re-run the tourism ETL's transform logic against sample data and confirm
  identical output to the original.

But I have **not** run `streamlit run app.py` myself. Please run it locally
and let me know if anything breaks — happy to fix.
