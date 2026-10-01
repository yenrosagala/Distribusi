# 📊 Dashboard Integrasi Papua — Pariwisata & Transportasi

A single Streamlit app combining two previously separate dashboards into one
shell:

- **Pariwisata** (from `dash-pariwisata`) — hotel occupancy (TPK) and length-of-stay
  (RLMTGAB) analytics: Home Dashboard, Infographic Stat Map, Trends
  Visualizations, Report (AI-narrated), Admin ETL Uploads.
- **Transportasi** (from `StaTransportasi`) — sea/air transport statistics:
  Dashboard Statistik, Laporan Komparatif (Word + InDesign/BRS export), Admin &
  Analisis Series.

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
├── .env.example               # Copy to .env and fill in ADMIN_PASSWORD
├── .gitignore                 # Excludes .env, .streamlit/secrets.toml, __pycache__
├── .devcontainer/             # Devcontainer config
├── data/
│   └── app_data.db            # Local SQLite fallback DB (seeded from both repos' original data)
├── templates/
│   └── BRS_Transportasi_Template Folder/   # InDesign BRS package (.idml/.indd/.pdf + fonts/links)
├── pariwisata/                # From dash-pariwisata, adapted to the shared DB
│   ├── etl_engine.py          # ETLEngine — same transform logic, now runs on the shared SQLAlchemy engine
│   ├── ai.py                  # Gemini narrative generation for the Report page
│   └── pages.py               # The 5 page-render functions (unwrapped from the original if/elif block)
├── modules/                   # From StaTransportasi
│   ├── database.py            # get_engine()/init_db() — local SQLite fallback (see below)
│   ├── config.py              # Papua wilayah/kabupaten mapping + env/secret loading
│   ├── etl_engine.py          # BPS Excel parser for transport data
│   ├── dashboard_page.py      # show_dashboard_page()
│   ├── report_page.py         # show_report_page() + Word/BRS export UI
│   ├── admin_page.py          # show_series_admin_page()
│   └── indesign_export.py     # Fills the BRS .idml — no streamlit import, unit-testable
└── tests/                     # pytest suite (10 files, 57 tests)
```

`StaTransportasi`'s original `app.py` was dead code (leftover Dash
boilerplate that didn't import any of its own modules), and `maps.py` in
`dash-pariwisata` is an offline one-time script that generated the `.parquet`
file — neither is part of the running app, so neither was carried over.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env      # then set ADMIN_PASSWORD in .env
streamlit run app.py
```

Login: `admin` / `admin123` (full access) or `user` / `user123` (no Admin pages).
These two shell accounts are still hardcoded in `app.py`'s `USERS` dict — the
`ADMIN_PASSWORD` env var guards the *second* gate inside
`show_series_admin_page()`, which is separate (see Integration notes).

## Database

`modules/database.py`'s `get_engine()` still reads `DATABASE_URL` from
Streamlit secrets/env exactly as StaTransportasi did. **New:** if no
`DATABASE_URL` is configured, it now falls back to a local SQLite file at
`data/app_data.db` instead of crashing — this file has already been seeded
with both repos' original demo data (240 sea-transport rows, 504
air-transport rows, 87 tourism rows, 6 cached AI narratives) so the app runs
out of the box. The `wilayah` table ships empty — it is a province/city lookup
used by the ETL admin pages, so populate it (or point at a real Postgres) if
you rely on those filters.

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
- **Transportasi's admin page still has a double gate.** The sidebar only shows
  the "Admin & Analisis Series" nav entry to users with the unified `admin`
  role, and `show_series_admin_page()` additionally asks for a password. An
  admin therefore authenticates twice — redundant, but left as-is to keep that
  function identical to the original.
- **The admin password is no longer hardcoded.** It is read from
  `ADMIN_PASSWORD` in the project-root `.env` (gitignored) or the real
  environment; a real env var takes precedence over `.env`. If it is unset,
  admin access is denied outright rather than falling back to a known value.
  Copy `.env.example` to `.env` on a fresh checkout. The comparison uses
  `secrets.compare_digest` so it is not vulnerable to timing leaks. `.env` can
  also carry `DATABASE_URL` to point the app at Postgres instead of
  `data/app_data.db`.
- Its "Log Out Admin" button (inside that page, only clears its own inner
  `admin_logged_in` flag) will appear in the sidebar alongside the app's
  main "Log out" button when you're on that page — again, unmodified
  original behavior, just worth knowing it's not a bug.
- `requirements.txt` drops a few unused/incorrect entries from
  dash-pariwisata's original file (`Dash` — never imported at runtime,
  `gunicorn` — not used by Streamlit, `datetime` — a stdlib module, not a
  pip package).

## Verification status

- `python -m pytest tests -q` → **57 passed**.
- `python -m compileall -q modules app.py` → clean.
- Streamlit health endpoint returns `200 ok`; a live Gemini narrative request
  returns HTTP 200.
- A real `fill_brs_template()` run against the shipped template produces a
  valid 558 KB `.idml` (zip integrity passes) with no residual template
  province text — verified by `tests/test_template_guard.py`.

**Still unverified:** the generated `.idml` has not been opened in Adobe
InDesign, so table overset/red-plus markers and page alignment need a human
look after export. The app was also last exercised through scoped Streamlit
probes rather than a full click-through of every page.

## Tests

```bash
python -m pytest tests -q
```

| File | Covers |
| --- | --- |
| `test_template_guard.py` | template/story-ID validation, province replacement, orphan-narrative cleanup |
| `test_idml_qr.py` | QR embedding + relative link rewrite in the `.idml` package |
| `test_infografis_upload.py` | infographic embedding + relative link rewrite |
| `test_narrative_format.py` | 0 decimals for passengers, 2 for cargo/percent, `%` → `persen` |
| `test_prev_period.py` | comparison period comes from the DB, not a guess |
| `test_zero_prev_period.py` | previous-period totals when there is no prior data |
| `test_report_totals.py` | report totals match the underlying rows |
| `test_meta_filter_sync.py` | sidebar filter state syncs with page filters |
| `test_location_years_narrative.py` | location/year selection feeds the narrative |
| `test_admin_auth.py` | admin fails closed when `ADMIN_PASSWORD` is unset |

Story and spread IDs are read from the template at test time rather than
hardcoded, so the suite survives the next InDesign re-export.

## Export ke template InDesign (BRS)

Di halaman **Laporan Komparatif Strategis**, setelah laporan ditampilkan (Show Report), bagian
**"Isi Template InDesign (BRS)"** mengisi template bawaan
`templates/BRS_Transportasi_Template Folder/BRS_Transportasi_Template.idml`
(atau file .idml yang diunggah) dengan provinsi, tahun, dan bulan terpilih: 8 tabel, narasi 2 paragraf per
tabel, poin utama tiap bab, ringkasan cover, judul, dan header/footer halaman.

- Logika ada di `modules/indesign_export.py` (tanpa dependensi streamlit, bisa diuji terpisah).
- Path template ada di satu konstanta, `DEFAULT_TEMPLATE`; `report_page.py` mengimpornya,
  jadi tidak ada lagi path template yang ditulis ulang di dua tempat.
- **Otomatis:** nomor & tanggal rilis, QR code, dan gambar infografis (keduanya ditulis
  ke dalam paket `Links/` dan tautannya diubah jadi relatif, supaya tidak jadi broken
  link di mesin lain).
- **Tidak otomatis:** gambar infografis dan QR bawaan template hanya diganti kalau kamu
  mengunggah yang baru — kalau tidak, keduanya masih milik periode template dan
  hasilnya muncul sebagai peringatan.
- Peta story (`TABLE_STORIES`, `NARR_SLOTS`, `POINTER_STORIES`, `COVER_STORY`,
  `PROTO_TABLE_STORY`) terikat ke ID story milik template ini. Kalau template
  di-export ulang dari InDesign, ID story bisa berubah: ekspor ulang lalu sesuaikan
  peta tersebut. `fill_brs_template()` akan menolak template yang tidak cocok dengan
  menyebutkan ID story yang hilang, bukan crash dengan `KeyError`.
- Nama provinsi di dalam teks template (`Papua Selatan` / `Papua Tengah`) diganti ke
  provinsi terpilih, jadi satu template bisa dipakai untuk provinsi mana pun.
- Jumlah baris tabel mengikuti data provinsi; cek teks overset dan tinggi frame di InDesign.

> Catatan lisensi: folder `Document fonts/` berisi font Arial, yang lisensinya
> melarang redistribusi. Font itu hanya perlu untuk membuka `.indd` di InDesign —
> aplikasi sendiri tidak membutuhkannya.
