import io
import logging

import numpy as np
import pandas as pd
from sqlalchemy import text

# Setup standard logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ETLEngine:
    """Tourism (pariwisata) accommodation-data ETL engine.

    Originally wrote to its own private SQLite file via raw sqlite3. Now
    shares the single SQLAlchemy engine used across the whole app (see
    modules/database.py), so both the transportasi and pariwisata tables
    live in the same database. All transform/query logic is unchanged.
    """

    def __init__(self, engine, general_table_name='all_data'):
        self.engine = engine
        self.general_table_name = general_table_name
        self._initialize_db()

    def _initialize_db(self):
        """Ensures base data and AI narrative cache tables exist."""
        with self.engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self.general_table_name} (
                    kd_prov TEXT,
                    kd_kab TEXT,
                    jenis_akomodasi TEXT,
                    kelas_akomodasi INTEGER,
                    mktj REAL,
                    mkts REAL,
                    mtgab REAL,
                    tpk REAL,
                    rlmtgab REAL,
                    year INTEGER,
                    month INTEGER
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pariwisata_ai_narratives (
                    province TEXT,
                    year INTEGER,
                    month INTEGER,
                    jenis_akomodasi TEXT,
                    indicator TEXT,
                    narrative TEXT,
                    PRIMARY KEY (province, year, month, jenis_akomodasi, indicator)
                )
            """))

    def _transform_data(self, df, year=None, month=None):
        df_transformed = df.copy()
        df_transformed.columns = df_transformed.columns.astype(str).str.strip().str.lower()

        prov_col_candidates = ['kd_prov', 'kd_provinsi', 'kode_prov', 'provinsi']
        actual_prov_col = next((col for col in prov_col_candidates if col in df_transformed.columns), None)

        for base_col in ['mktj', 'mkts', 'mtgab', 'tpk', 'rlmtgab']:
            col_b = f'{base_col}_b'
            col_nb = f'{base_col}_nb'
            if col_b in df_transformed.columns and col_nb in df_transformed.columns:
                df_transformed[base_col] = (
                    pd.to_numeric(df_transformed[col_b], errors='coerce').fillna(0) +
                    pd.to_numeric(df_transformed[col_nb], errors='coerce').fillna(0)
                )

        desired_cols = [
            'kd_kab', 'jenis_akomodasi', 'kelas_akomodasi',
            'mktj', 'mkts', 'mtgab', 'tpk', 'rlmtgab'
        ]
        if actual_prov_col:
            desired_cols.append(actual_prov_col)

        if year is not None:
            df_transformed['year'] = year
            desired_cols.append('year')
        if month is not None:
            df_transformed['month'] = month
            desired_cols.append('month')

        existing_cols = [col for col in desired_cols if col in df_transformed.columns]
        df_transformed = df_transformed[existing_cols]

        if actual_prov_col and actual_prov_col != 'kd_prov':
            df_transformed = df_transformed.rename(columns={actual_prov_col: 'kd_prov'})

        if 'kd_prov' in df_transformed.columns:
            df_transformed['kd_prov'] = pd.to_numeric(df_transformed['kd_prov'], errors='coerce')
            valid_provinces = [94, 95, 96, 97]

            df_transformed = df_transformed[df_transformed['kd_prov'].isin(valid_provinces)]

            prov_mapping = {
                94: 'Papua',
                95: 'Papua Selatan',
                96: 'Papua Tengah',
                97: 'Papua Pegunungan',
            }
            df_transformed['kd_prov'] = df_transformed['kd_prov'].map(prov_mapping)

        if 'jenis_akomodasi' in df_transformed.columns:
            jenis_mapping = {1: 'Hotel Bintang', 2: 'Hotel Non Bintang'}
            df_transformed['jenis_akomodasi'] = (
                pd.to_numeric(df_transformed['jenis_akomodasi'], errors='coerce')
                .map(jenis_mapping)
                .fillna(df_transformed['jenis_akomodasi'].astype(str))
            )

        numeric_cols = ['mktj', 'mkts', 'mtgab', 'tpk', 'rlmtgab', 'kelas_akomodasi']
        for col in numeric_cols:
            if col in df_transformed.columns:
                df_transformed[col] = pd.to_numeric(df_transformed[col], errors='coerce').fillna(0)

        subset_cols = [col for col in ['kd_prov', 'kd_kab', 'jenis_akomodasi', 'kelas_akomodasi', 'year', 'month'] if col in df_transformed.columns]
        if subset_cols:
            df_transformed = df_transformed.drop_duplicates(subset=subset_cols, keep='last')

        return df_transformed.reset_index(drop=True)

    def etl_pipeline(self, uploaded_file, sheet_name='Prov_Jenis_Kelas', year=None, month=None):
        filename = getattr(uploaded_file, 'name', 'uploaded_file.xlsx')
        try:
            bytes_data = uploaded_file.getvalue() if hasattr(uploaded_file, 'getvalue') else uploaded_file.read()
            buffer = io.BytesIO(bytes_data)
            excel_file = pd.ExcelFile(buffer)
            if sheet_name not in excel_file.sheet_names:
                logger.error(f"Sheet '{sheet_name}' not found in '{filename}'.")
                return
            df_extracted = pd.read_excel(excel_file, sheet_name=sheet_name)
        except Exception as e:
            logger.error(f'Error extracting data from {filename}: {e}')
            return

        df_transformed = self._transform_data(df_extracted, year=year, month=month)
        if df_transformed.empty:
            logger.warning(f"File '{filename}' yielded 0 rows.")
            return

        with self.engine.begin() as conn:
            if year is not None and month is not None:
                conn.execute(
                    text(f"DELETE FROM {self.general_table_name} WHERE year = :year AND month = :month"),
                    {"year": year, "month": month},
                )
            df_transformed.to_sql(self.general_table_name, conn, if_exists='append', index=False)
            logger.info(f"Successfully loaded {len(df_transformed)} rows.")
