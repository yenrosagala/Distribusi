import os
import logging
from pathlib import Path

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import NoSuchTableError
import pandas as pd
import streamlit as st

from modules.config import load_project_env, read_secret

load_project_env()

# Single local SQLite file used whenever no DATABASE_URL secret/env var is
# configured (e.g. local development, or a fresh Streamlit Cloud instance
# before an external Postgres database is wired up). Both the transportasi
# tables (this module) and the pariwisata tables (pariwisata/etl_engine.py)
# live in this one file/engine.
DEFAULT_LOCAL_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app_data.db"


def get_engine():
    # Ambil connection string dari st.secrets, environment variable, atau fallback lokal
    db_url = read_secret("DATABASE_URL") or os.getenv("DATABASE_URL")

    # Fallback jika menggunakan format postgres:// ubah jadi postgresql://
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    if not db_url:
        # No cloud database configured: use one shared local SQLite file for
        # both the transportasi and pariwisata tables.
        DEFAULT_LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{DEFAULT_LOCAL_DB_PATH}"

    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args)


def is_local_dummy_db():
    """True when get_engine() fell back to the local SQLite file.

    That file holds deterministic placeholder rows, not published BPS figures.
    A report generated from it must be labelled as dummy rather than shipped,
    so callers check this at the point of export.
    """
    db_url = read_secret("DATABASE_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        return True
    return db_url.startswith("sqlite")


def init_db():
    """Initializes the required database tables if they do not exist."""
    engine = get_engine()
    with engine.begin() as conn:
        # Create wilayah table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wilayah (
                kode_kabkota bigint NOT NULL,
                nama_kabkota text,
                kode_provinsi bigint,
                nama_provinsi text,
                CONSTRAINT wilayah_pkey PRIMARY KEY (kode_kabkota)
            );
        """))

        # Create transportasi_laut table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transportasi_laut (
                tahun bigint NOT NULL,
                bulan text NOT NULL,
                kode_provinsi bigint,
                nama_provinsi text,
                kode_kabkota bigint,
                nama_kabkota text,
                kode_pelabuhan bigint NOT NULL,
                nama_pelabuhan text,
                dn_penumpang_turun bigint,
                dn_penumpang_naik bigint,
                dn_bongkar_barang_ton double precision,
                dn_muat_barang_ton double precision,
                ln_penumpang_turun bigint,
                ln_penumpang_naik bigint,
                ln_bongkar_barang_ton double precision,
                ln_muat_barang_ton double precision,
                CONSTRAINT transportasi_laut_pkey PRIMARY KEY (tahun, bulan, kode_pelabuhan)
            );
        """))

        # Create transportasi_udara table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS transportasi_udara (
                tahun bigint NOT NULL,
                bulan text NOT NULL,
                kode_provinsi bigint,
                nama_provinsi text,
                kode_kabkota bigint,
                nama_kabkota text,
                kode_bandara bigint NOT NULL,
                nama_bandara text,
                pesawat_berangkat bigint,
                pesawat_datang bigint,
                penumpang_berangkat bigint,
                penumpang_datang bigint,
                penumpang_transit bigint,
                barang_muat_kg double precision,
                barang_bongkar_kg double precision,
                bagasi_muat_kg double precision,
                bagasi_bongkar_kg double precision,
                pos_muat_kg double precision,
                pos_bongkar_kg double precision,
                CONSTRAINT transportasi_udara_pkey PRIMARY KEY (tahun, bulan, kode_bandara)
            );
        """))


def init_narrative_table():
    """Narrative cache shared by the dashboard and report pages.

    Keyed by (report_type, period_key) — the exact pair both pages already
    pass to get_db_narrative/save_db_narrative.
    """
    engine = get_engine()
    # ponytail: inspect() is SQLAlchemy's own portable schema reader — a raw
    # PRAGMA is SQLite-only and raised SyntaxError on Postgres, which broke
    # startup outright. Duck-typed: only report_type/period_key are consulted.
    try:
        cols = {
            c["name"] for c in inspect(engine).get_columns("ai_narratives_cache")
        }
    except NoSuchTableError:
        cols = set()
    with engine.begin() as conn:
        # An older build created this table with a Transport-shaped schema
        # (provinsi/moda/indikator) that no caller ever queried, so the cache
        # could never hit. Drop that orphan; it holds no live data.
        if cols and not {"report_type", "period_key"} <= cols:
            conn.execute(text("DROP TABLE ai_narratives_cache"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_narratives_cache (
                report_type TEXT NOT NULL,
                period_key TEXT NOT NULL,
                narrative_text TEXT,
                updated_at TIMESTAMP,
                PRIMARY KEY (report_type, period_key)
            )
        """))


def get_db_narrative(report_type, period_key):
    """Returns the cached narrative, or None on miss/error."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT narrative_text FROM ai_narratives_cache "
                     "WHERE report_type = :rt AND period_key = :pk"),
                {"rt": report_type, "pk": period_key},
            ).fetchone()
            return row[0] if row else None
    except Exception as e:
        logging.getLogger(__name__).warning("Gagal retrieve narasi dari database: %s", e)
        return None


def delete_db_narrative(report_type, period_key):
    """Invalidates one cached narrative (used by the Regenerasi button)."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ai_narratives_cache "
                     "WHERE report_type = :rt AND period_key = :pk"),
                {"rt": report_type, "pk": period_key},
            )
    except Exception as e:
        logging.getLogger(__name__).error("Gagal menghapus cache narasi: %s", e)


def save_db_narrative(report_type, period_key, narrative_text):
    """Upserts a narrative. Silently logs on failure (cache is best-effort)."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ai_narratives_cache "
                     "WHERE report_type = :rt AND period_key = :pk"),
                {"rt": report_type, "pk": period_key},
            )
            conn.execute(
                text("INSERT INTO ai_narratives_cache "
                     "(report_type, period_key, narrative_text, updated_at) "
                     "VALUES (:rt, :pk, :txt, CURRENT_TIMESTAMP)"),
                {"rt": report_type, "pk": period_key, "txt": narrative_text},
            )
    except Exception as e:
        logging.getLogger(__name__).error("Gagal menyimpan narasi ke database: %s", e)


def delete_db():
    """Drops all tables from the database."""
    engine = get_engine()
    suffix = " CASCADE" if engine.dialect.name == "postgresql" else ""
    tables = [
        "transportasi_udara",
        "transportasi_laut",
        "wilayah",
        "ai_narratives_cache",
        "all_data",
        "ai_narratives",
        # The pariwisata-side AI cache, same regenerable class as
        # ai_narratives_cache; "total reset" used to skip it and leave rows.
        "pariwisata_ai_narratives",
    ]
    try:
        with engine.begin() as conn:
            for t in tables:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}{suffix};"))
        return True
    except Exception as e:
        st.error(f"Failed to delete database tables: {e}")
        return False
