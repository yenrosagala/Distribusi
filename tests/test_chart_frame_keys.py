"""Dua grafik tren berbeda pada satu halaman tidak boleh bentrok key.

Dulu key container-nya konstanta ("chartframe_yoy"), jadi halaman laporan yang
merender satu grafik per (indikator x jenis) langsung melempar
StreamlitDuplicateElementKey. Key sekarang memuat jenis, jadi unik.
"""
from streamlit.testing.v1 import AppTest

SCRIPT = """
import pandas as pd
from pariwisata.pages import render_yoy_chart

df = pd.DataFrame({
    "jenis_akomodasi": ["Hotel Bintang"] * 4 + ["Hotel Non Bintang"] * 4,
    "month": [1, 2, 1, 2] * 2,
    "year": [2026, 2026, 2025, 2025] * 2,
    "tpk": [40.0, 41.0, 38.0, 39.0, 30.0, 31.0, 28.0, 29.0],
})
render_yoy_chart(df, "tpk", "Hotel Bintang", "Papua", 2026, 2)
render_yoy_chart(df, "tpk", "Hotel Non Bintang", "Papua", 2026, 2)
"""


def test_dua_chart_beda_jenis_tidak_bentrok_key():
    at = AppTest.from_string(SCRIPT, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
