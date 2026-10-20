"""Parser transportasi harus mengirim tahun/kode_* sebagai integer, bukan string.

Skema transportasi_laut/transportasi_udara memakai bigint. process_laut/
process_udara dulu mengirim 'tahun': str(tahun) dan kode hasil regex sebagai
string, sehingga pandas to_sql mengikatnya sebagai ::VARCHAR dan Postgres
menolak INSERT dengan DatatypeMismatch ("column tahun is of type bigint but
expression is of type character varying"). SQLite lolos (afinitas INTEGER
mengonversi sendiri), jadi bug ini hanya muncul di production.

Run:  python tests/test_transport_dtypes.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import pandas as pd  # noqa: E402

from modules.etl_engine import parse_transport_file  # noqa: E402

LAUT_HTML = """
<table>
<tr>
  <th>Transportasi Laut - PROVINSI PAPUA Tahun 2026 Bulan September</th>
  <th>Kabupaten/Kota</th><th>Pelabuhan</th><th>Satuan</th>
  <th>Darat ke Pelabuhan</th><th>Pelabuhan ke Darat</th>
  <th>Barang Bongkar</th><th>Barang Muat</th>
</tr>
<tr>
  <td>[94] PAPUA</td><td>[940101] KABUPATEN MERAUKE</td>
  <td>[9401011001] MERAUKE</td><td>-</td>
  <td>1.969</td><td>1.563</td><td>49.501,0</td><td>9.605,0</td>
</tr>
<tr>
  <td>JUMLAH</td><td></td><td></td><td></td>
  <td>1.969</td><td>1.563</td><td>49.501,0</td><td>9.605,0</td>
</tr>
</table>
"""

UDARA_HTML = """
<table>
<tr>
  <th>Pesawat Terbang - PROVINSI PAPUA Tahun 2026 Bulan September</th>
  <th>Kabupaten/Kota</th><th>Bandara</th><th>Satuan</th>
  <th>Berangkat</th><th>Datang</th><th>Penumpang Berangkat</th>
  <th>Penumpang Datang</th><th>Transit</th><th>Barang Muat</th>
  <th>Barang Bongkar</th>
</tr>
<tr>
  <td>[94] PAPUA</td><td>[940101] KABUPATEN MERAUKE</td>
  <td>[9401011001] MOPAH</td><td>-</td>
  <td>10</td><td>12</td><td>100</td><td>120</td><td>0</td>
  <td>1.000,5</td><td>2.000,5</td>
</tr>
</table>
"""

BIGINT_COLS = {
    "transportasi_laut": [
        "tahun", "kode_provinsi", "kode_kabkota", "kode_pelabuhan"],
    "transportasi_udara": [
        "tahun", "kode_provinsi", "kode_kabkota", "kode_bandara"],
}


def _check(html, tahun, bulan, table_type):
  _, df = parse_transport_file(html.encode("utf-8"), tahun, bulan)
  for col in BIGINT_COLS[table_type]:
    assert pd.api.types.is_integer_dtype(df[col]), (
        f"{table_type}.{col} harus integer, dapat {df[col].dtype}")
  return df


def test_laut_bigint_dtypes():
  df = _check(LAUT_HTML, 2026, "September", "transportasi_laut")
  assert df.loc[0, "tahun"] == 2026
  # kode adalah identitas numerik; nol di depan tidak bermakna
  assert df.loc[0, "kode_kabkota"] == 940101
  assert df.loc[0, "dn_bongkar_barang_ton"] == 49501.0
  assert len(df) == 1, "baris JUMLAH tanpa kode harus terfilter"


def test_udara_bigint_dtypes():
  df = _check(UDARA_HTML, 2026, "September", "transportasi_udara")
  assert df.loc[0, "kode_bandara"] == 9401011001
  assert df.loc[0, "barang_muat_kg"] == 1000.5


if __name__ == "__main__":
  test_laut_bigint_dtypes()
  test_udara_bigint_dtypes()
  print("OK")
