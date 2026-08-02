import os
from pathlib import Path

from sqlalchemy import create_engine, text
import pandas as pd
import streamlit as st

# Single local SQLite file used whenever no DATABASE_URL secret/env var is
# configured (e.g. local development, or a fresh Streamlit Cloud instance
# before an external Postgres database is wired up). Both the transportasi
# tables (this module) and the pariwisata tables (pariwisata/etl_engine.py)
# live in this one file/engine.
DEFAULT_LOCAL_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app_data.db"


def _read_secret(key):
    """st.secrets raises if no secrets.toml exists at all, so this stays safe."""
    try:
        return st.secrets.get(key)
    except Exception:
        return None


def get_engine():
    # Ambil connection string dari st.secrets, environment variable, atau fallback lokal
    db_url = _read_secret("DATABASE_URL") or os.getenv("DATABASE_URL")

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
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_narratives_cache (
                provinsi TEXT,
                moda TEXT,
                indikator TEXT,
                tahun INTEGER,
                bulan TEXT,
                narrative_text TEXT,
                source TEXT,
                PRIMARY KEY (provinsi, moda, indikator, tahun, bulan)
            )
        """))


def delete_db():
    """Drops all tables from the database."""
    engine = get_engine()
    suffix = " CASCADE" if engine.dialect.name == "postgresql" else ""
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS transportasi_udara{suffix};"))
            conn.execute(text(f"DROP TABLE IF EXISTS transportasi_laut{suffix};"))
            conn.execute(text(f"DROP TABLE IF EXISTS wilayah{suffix};"))
            conn.execute(text(f"DROP TABLE IF EXISTS ai_narratives_cache{suffix};"))
        return True
    except Exception as e:
        st.error(f"Failed to delete database tables: {e}")
        return False
