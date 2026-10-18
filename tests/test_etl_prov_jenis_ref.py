"""Runtable check: an upload carrying a Prov_Jenis sheet persists the official
TPK/RLMTGAB reference for that period, a plain monthly upload leaves it alone,
and re-uploading the same period replaces it instead of duplicating.

Run directly:  python tests/test_etl_prov_jenis_ref.py
Or with pytest: pytest tests/test_etl_prov_jenis_ref.py
"""
import io
import os
import sys
import tempfile

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pariwisata.etl_engine import ETLEngine, PARIWISATA_REF_AVG  # noqa: E402

KELAS = pd.DataFrame({
    "kd_prov": [94, 94, 95],
    "jenis_akomodasi": [1, 2, 1],
    "kelas_akomodasi": [1, 1, 1],
    "tpk": [33.7, 24.1, 6.7],
    "rlmtgab": [1.34, 1.35, 1.75],
})
REF = pd.DataFrame({
    "kd_prov": [94, 95, 96, 97],
    "jenis_akomodasi": [1, 2, 1, 2],
    # The real Prov_Jenis sheet also ships mktj/mkts/mtgab + an rse/ket pair per
    # column; only tpk/rlmtgab belong in the ref table. Keep the extras here so
    # the test reproduces the real schema (extra columns must not break insert).
    "mktj": [35375, 12930, 10860, 1325],
    "mkts": [87386, 63193, 30987, 4991],
    "mtgab": [62282, 17822, 29909, 2626],
    "tpk": [40.481313, 20.461127, 35.046955, 10.029102],
    "rlmtgab": [1.681661, 1.586717, 3.340295, 1.264925],
})


class FakeUpload:
    def __init__(self, name, buf):
        self.name = name
        self._buf = buf

    def getvalue(self):
        return self._buf.getvalue()


def _workbook(sheets):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buf


def _engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return ETLEngine(create_engine(f"sqlite:///{path}"))


def _rows(etl, sql, **params):
    with etl.engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or None)


def test_ref_persisted_only_when_prov_jenis_sheet_present():
    etl = _engine()

    etl.etl_pipeline(FakeUpload("vhits.xlsx", _workbook({"Prov_Jenis_Kelas": KELAS, "Prov_Jenis": REF})),
                     year=2026, month=8)
    ref = _rows(etl, f"SELECT * FROM {PARIWISATA_REF_AVG} ORDER BY kd_prov, jenis_akomodasi")
    assert len(ref) == 4, ref
    assert set(ref["kd_prov"]) == {"Papua", "Papua Selatan", "Papua Tengah", "Papua Pegunungan"}, ref
    papua_bintang = ref[(ref.kd_prov == "Papua") & (ref.jenis_akomodasi == "Hotel Bintang")].iloc[0]
    assert abs(papua_bintang.tpk - 40.481313) < 1e-6, papua_bintang.tpk
    assert abs(papua_bintang.rlmtgab - 1.681661) < 1e-6, papua_bintang.rlmtgab
    assert set(zip(ref.year, ref.month)) == {(2026, 8)}, ref

    # A plain monthly file (no Prov_Jenis sheet) must not create any reference.
    etl.etl_pipeline(FakeUpload("juli.xlsx", _workbook({"Prov_Jenis_Kelas": KELAS})), year=2027, month=1)
    periods = _rows(etl, f"SELECT DISTINCT year, month FROM {PARIWISATA_REF_AVG}")
    assert set(zip(periods.year, periods.month)) == {(2026, 8)}, periods


def test_plain_upload_keeps_the_stored_reference():
    etl = _engine()
    etl.etl_pipeline(FakeUpload("vhits.xlsx", _workbook({"Prov_Jenis_Kelas": KELAS, "Prov_Jenis": REF})),
                     year=2026, month=8)
    # Re-ingesting the kelas rows for the same period without the ref sheet.
    etl.etl_pipeline(FakeUpload("ulang.xlsx", _workbook({"Prov_Jenis_Kelas": KELAS})), year=2026, month=8)
    ref = _rows(etl, f"SELECT * FROM {PARIWISATA_REF_AVG}")
    assert len(ref) == 4, "reference harus bertahan saat upload ulang tanpa sheet Prov_Jenis"


def test_reupload_same_period_replaces_reference():
    etl = _engine()
    etl.etl_pipeline(FakeUpload("v1.xlsx", _workbook({"Prov_Jenis_Kelas": KELAS, "Prov_Jenis": REF})),
                     year=2026, month=8)
    ref2 = REF.copy()
    ref2["tpk"] = ref2["tpk"] + 1.0
    etl.etl_pipeline(FakeUpload("v2.xlsx", _workbook({"Prov_Jenis_Kelas": KELAS, "Prov_Jenis": ref2})),
                     year=2026, month=8)
    ref = _rows(etl, f"SELECT * FROM {PARIWISATA_REF_AVG} ORDER BY kd_prov, jenis_akomodasi")
    assert len(ref) == 4, "re-upload tidak boleh menggandakan rujukan"
    assert abs(ref[(ref.kd_prov == "Papua") & (ref.jenis_akomodasi == "Hotel Bintang")].iloc[0].tpk - 41.481313) < 1e-6


def test_file_carried_period_ingests_multiple_months():
    etl = _engine()
    km = KELAS.assign(year=[2024, 2024, 2024], month=[1, 1, 2])
    # No year/month args: the columns in the file define the periods, so one
    # upload lands both January and February.
    etl.etl_pipeline(FakeUpload("multi.xlsx", _workbook({"Prov_Jenis_Kelas": km})))
    rows = _rows(etl, "SELECT year, month, COUNT(*) AS n FROM all_data "
                      "GROUP BY year, month ORDER BY year, month")
    assert set(zip(rows.year, rows.month)) == {(2024, 1), (2024, 2)}, rows

    # Re-uploading only February replaces it and leaves January untouched.
    feb = KELAS.iloc[[0]].assign(year=2024, month=2)
    etl.etl_pipeline(FakeUpload("feb.xlsx", _workbook({"Prov_Jenis_Kelas": feb})))
    jan_n = _rows(etl, "SELECT COUNT(*) AS n FROM all_data WHERE year=2024 AND month=1").iloc[0].n
    feb_n = _rows(etl, "SELECT COUNT(*) AS n FROM all_data WHERE year=2024 AND month=2").iloc[0].n
    assert jan_n == 2, jan_n
    assert feb_n == 1, feb_n


if __name__ == '__main__':
    for name, fn in sorted(list(globals().items())):
        if name.startswith('test_'):
            fn()
            print(f'  ok  {name}')
    print('test_etl_prov_jenis_ref: all pass')
