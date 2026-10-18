import io
import logging

import pandas as pd
from sqlalchemy import inspect, text

# Setup standard logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Provincial TPK/RLMTGAB reference (the uploaded file's Prov_Jenis sheet): the
# official per-jenis estimate that replaces the mean in table/chart averages.
PARIWISATA_REF_AVG = 'pariwisata_ref_avg'


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
                    tpk_rse REAL,
                    rlmtgab_rse REAL,
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
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {PARIWISATA_REF_AVG} (
                    kd_prov TEXT,
                    jenis_akomodasi TEXT,
                    year INTEGER,
                    month INTEGER,
                    tpk REAL,
                    rlmtgab REAL
                )
            """))
            self._ensure_rse_columns(conn)

    def _ensure_rse_columns(self, conn):
        """Add RSE columns to a pre-existing all_data table.

        CREATE TABLE IF NOT EXISTS never alters an existing table, so DBs created
        before the RSE feature lack these columns. Add them in place (portable
        across PostgreSQL and SQLite) so old data keeps its rows.
        """
        cols = {c["name"] for c in inspect(conn).get_columns(self.general_table_name)}
        for col in ("tpk_rse", "rlmtgab_rse"):
            if col not in cols:
                conn.execute(text(
                    f"ALTER TABLE {self.general_table_name} ADD COLUMN {col} REAL"))

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
            'mktj', 'mkts', 'mtgab', 'tpk', 'rlmtgab', 'tpk_rse', 'rlmtgab_rse'
        ]
        if actual_prov_col:
            desired_cols.append(actual_prov_col)

        # Period (year/month): a file that carries its own columns -- e.g. the
        # multi-month fill-in template -- wins, so one upload can span several
        # months. Otherwise fall back to the arguments from the Admin form, so
        # plain source files (no year/month columns) keep working unchanged.
        for col, fallback in (('year', year), ('month', month)):
            if col in df_transformed.columns:
                df_transformed[col] = pd.to_numeric(df_transformed[col], errors='coerce')
            elif fallback is not None:
                df_transformed[col] = fallback
            if col in df_transformed.columns:
                desired_cols.append(col)

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

        numeric_cols = ['mktj', 'mkts', 'mtgab', 'tpk', 'rlmtgab',
                        'tpk_rse', 'rlmtgab_rse', 'kelas_akomodasi']
        for col in numeric_cols:
            if col in df_transformed.columns:
                df_transformed[col] = pd.to_numeric(df_transformed[col], errors='coerce').fillna(0)

        # Drop rows with no period and normalise to int so the insert lands in
        # the INTEGER year/month columns.
        for col in ('year', 'month'):
            if col in df_transformed.columns:
                df_transformed = df_transformed[df_transformed[col].notna()]
                df_transformed[col] = df_transformed[col].astype(int)

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

        # Sheet Prov_Jenis carries the official provincial TPK/RLMTGAB estimate
        # per jenis; store it as the reference "average" for this period.
        ref_rows = pd.DataFrame()
        if 'Prov_Jenis' in excel_file.sheet_names:
            ref_rows = self._transform_data(
                pd.read_excel(excel_file, sheet_name='Prov_Jenis'), year=year, month=month,
            )
            # Prov_Jenis also carries mktj/mkts/mtgab; keep only the ref table's
            # columns or the insert fails on "no such column".
            ref_cols = ['kd_prov', 'jenis_akomodasi', 'year', 'month', 'tpk', 'rlmtgab']
            ref_rows = ref_rows[[c for c in ref_cols if c in ref_rows.columns]]
            if not {'year', 'month'} <= set(ref_rows.columns):
                ref_rows = pd.DataFrame()

        # One upload may carry several periods (fill-in template); replace each
        # (year, month) it touches so a re-upload overwrites instead of doubling.
        periods = self._periods(df_transformed)
        ref_periods = self._periods(ref_rows)

        with self.engine.begin() as conn:
            for y, m in periods:
                conn.execute(
                    text(f"DELETE FROM {self.general_table_name} WHERE year = :year AND month = :month"),
                    {"year": y, "month": m},
                )
            for y, m in ref_periods:
                conn.execute(
                    text(f"DELETE FROM {PARIWISATA_REF_AVG} WHERE year = :year AND month = :month"),
                    {"year": y, "month": m},
                )
            df_transformed.to_sql(self.general_table_name, conn, if_exists='append', index=False)
            if not ref_rows.empty:
                ref_rows.to_sql(PARIWISATA_REF_AVG, conn, if_exists='append', index=False)
            logger.info(f"Successfully loaded {len(df_transformed)} rows.")

    @staticmethod
    def _periods(df):
        """Distinct (year, month) pairs present in a transformed frame; [] if none."""
        if df is None or df.empty or 'year' not in df.columns or 'month' not in df.columns:
            return []
        return sorted({(int(y), int(m)) for y, m in zip(df['year'], df['month'])})
