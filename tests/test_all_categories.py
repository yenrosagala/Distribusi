"""Tabel harus menampilkan semua kategori, dan 0/0 -> string "undefined".

Dua permintaan:
  1. Semua kategori muncul di tabel walau nilainya 0. Sebelumnya indeks tabel
     dibangun dari `curr_grp.index` saja, jadi entitas yang hanya punya data di
     periode lama hilang dari tabel (walau nilainya sudah di-fill 0).
  2. Pembagian 0/0 tidak punya jawaban numerik, jadi ditampilkan sebagai
     string "undefined" — bukan sel kosong yang読者以为是 data yang hilang.

Run:  python tests/test_all_categories.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import pandas as pd  # noqa: E402

from modules.config import PEMETAAN_WILAYAH  # noqa: E402
from modules.report_page import (  # noqa: E402
    UNDEFINED, _union_index, build_brs_display_table, format_pct_number,
    prepare_table_item, table_formatters,
)

PROV = "Papua Tengah"
KAB = PEMETAAN_WILAYAH[PROV][:4]


def _frame(rows, values, target="Nilai"):
    """rows dan values harus panjang sama — jangan sampai salah pasang diam-diam."""
    assert len(rows) == len(values), f"{len(rows)} label vs {len(values)} value"
    return pd.DataFrame({"Kabupaten": rows, target: values})


def _item(curr, prev, cum_curr, cum_prev, moda="Transportasi Laut", target="Nilai"):
    return prepare_table_item(
        _frame(curr[0], curr[1], target),
        _frame(prev[0], prev[1], target),
        _frame(cum_curr[0], cum_curr[1], target),
        _frame(cum_prev[0], cum_prev[1], target),
        target, "Penumpang", "Kabupaten", "2026", "Juni", "Mei", "2026",
        table_no=1, prov=PROV, moda=moda,
    )


# --- 1. semua kategori selalu muncul -----------------------------------------

def test_prev_only_category_still_listed():
    """Kategori yang hanya ada di periode lalu harus tetap jadi baris."""
    it = _item(
        curr=([KAB[0]], [5]),
        prev=([KAB[0], KAB[1]], [10, 99]),
        cum_curr=([KAB[0]], [5]),
        cum_prev=([KAB[0], KAB[1]], [10, 99]),
    )
    rows = it["report_flat"].index.tolist()
    for kab in (KAB[0], KAB[1]):
        assert kab in rows, f"{kab} hilang dari tabel: {rows}"
    # nilainya tampil sebagai 0, bukan kosong
    gone = it["report_flat"].loc[KAB[1]]
    assert gone["Juni 2026"] == 0, f"periode kosong harus 0, dapat {gone['Juni 2026']}"


def test_cumulative_only_category_still_listed():
    """Kategori yang cuma muncul di data kumulatif juga ikut tampil."""
    it = _item(
        curr=([KAB[0]], [5]),
        prev=([KAB[0]], [10]),
        cum_curr=([KAB[0], KAB[2]], [5, 70]),
        cum_prev=([KAB[0], KAB[2]], [10, 70]),
    )
    rows = it["report_flat"].index.tolist()
    assert KAB[2] in rows, f"kategori kumulatif hilang: {rows}"


def test_zero_value_category_not_dropped():
    """Nilai 0 yang eksplisit di database harus tetap jadi baris."""
    it = _item(
        curr=(KAB[:3], [10, 0, 5]),
        prev=(KAB[:3], [8, 3, 4]),
        cum_curr=(KAB[:3], [15, 0, 9]),
        cum_prev=(KAB[:3], [12, 6, 7]),
    )
    rows = it["report_flat"].index.tolist()
    for kab in KAB[:3]:
        assert kab in rows, f"{kab} (nilai 0) hilang: {rows}"
    # baris kedua nilainya benar-benar 0 dari data, dan tetap tampil
    assert it["report_flat"].loc[KAB[1], "Juni 2026"] == 0


def test_row_order_unchanged_when_nothing_new():
    """Tabel yang sudah lengkap tidak boleh diurutkan ulang."""
    it = _item(
        curr=(KAB[:3], [10, 5, 3]),
        prev=(KAB[:3], [8, 4, 2]),
        cum_curr=(KAB[:3], [15, 9, 6]),
        cum_prev=(KAB[:3], [12, 7, 5]),
    )
    rows = [r for r in it["report_flat"].index.tolist() if r != "TOTAL"]
    assert rows == KAB[:3], f"urutan berubah: {rows} != {KAB[:3]}"


def test_union_index_dedupes_and_keeps_order():
    a = pd.Series([1, 2, 3], index=["x", "y", "z"])
    b = pd.Series([4, 5], index=["y", "w"])
    assert _union_index(a, b).tolist() == ["x", "y", "z", "w"]
    assert _union_index(a, a).tolist() == ["x", "y", "z"]
    assert _union_index(a, pd.Series(dtype=float)).tolist() == ["x", "y", "z"]
    assert len(_union_index(a, b)) == len(_union_index(a, b).unique())


# --- 2. 0/0 -> "undefined" ----------------------------------------------------

def test_zero_over_zero_row_shows_undefined():
    """Baris 0 -> 0: kolom (%) numeric NaN, tampil "undefined" di tabel."""
    it = _item(
        curr=(KAB[:2], [0, 10]),
        prev=(KAB[:2], [0, 8]),
        cum_curr=(KAB[:2], [0, 15]),
        cum_prev=(KAB[:2], [0, 12]),
    )
    flat = it["report_flat"]
    row = flat.loc[KAB[0]]
    assert pd.isna(row["M-to-M (%)"]), "harus NaN, bukan string (gradient butuh numeric)"
    assert pd.isna(row["Y-on-Y (%)"])
    assert format_pct_number(row["M-to-M (%)"]) == UNDEFINED
    assert format_pct_number(row["Y-on-Y (%)"]) == UNDEFINED


def test_growth_from_zero_shows_undefined():
    """0 -> 100 juga tidak terdefinisi (100/0), harus string sama."""
    it = _item(
        curr=(KAB[:2], [100, 10]),
        prev=(KAB[:2], [0, 8]),
        cum_curr=(KAB[:2], [100, 15]),
        cum_prev=(KAB[:2], [0, 12]),
    )
    flat = it["report_flat"]
    assert format_pct_number(flat.loc[KAB[0], "M-to-M (%)"]) == UNDEFINED


def test_total_row_zero_shows_undefined():
    """Baris TOTAL ikut aturan yang sama saat agregat pembandingnya 0."""
    it = _item(
        curr=([KAB[0]], [0]),
        prev=([KAB[0]], [0]),
        cum_curr=([KAB[0]], [0]),
        cum_prev=([KAB[0]], [0]),
    )
    total = it["report_flat"][it["report_flat"].index == "TOTAL"].iloc[0]
    assert format_pct_number(total["M-to-M (%)"]) == UNDEFINED
    assert format_pct_number(total["Y-on-Y (%)"]) == UNDEFINED


def test_styler_renders_with_undefined_present():
    """Regresi crash: kolom (%) berisi string bikin background_gradient gagal.

    Ini jalur yang dipakai halaman Report sungguhan. Kalau kolomnya jadi
    object-dtype, pandas melempar "could not convert string to float" dan tabel
    tidak muncul sama sekali — bukan cuma satu sel yang salah.
    """
    it = _item(
        curr=(KAB[:2], [0, 10]), prev=(KAB[:2], [0, 8]),
        cum_curr=(KAB[:2], [0, 15]), cum_prev=(KAB[:2], [0, 12]),
    )
    display = build_brs_display_table(it["report_flat"], PROV, "Transportasi Laut")
    pct_cols = [c for c in display.columns if "(%)" in c]
    assert pct_cols, "kolom persen tidak ditemukan"
    styler = display.style.format(table_formatters(display.columns, "Nilai"))
    styler = styler.background_gradient(subset=pct_cols, cmap="RdYlGn")
    html = styler.to_html()
    assert UNDEFINED in html, f"'undefined' tidak muncul di HTML: {html[:500]}"


def test_normal_percentage_still_numeric():
    """Perhitungan yang valid tetap angka, tidak jadi string."""
    it = _item(
        curr=(KAB[:2], [10, 5]), prev=(KAB[:2], [8, 4]),
        cum_curr=(KAB[:2], [15, 9]), cum_prev=(KAB[:2], [12, 7]),
    )
    flat = it["report_flat"]
    for idx in KAB[:2] + ["TOTAL"]:
        for col in ("M-to-M (%)", "Y-on-Y (%)"):
            v = flat.loc[idx, col]
            assert isinstance(v, (int, float)), f"{idx}/{col} jadi {type(v).__name__}: {v!r}"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("selesai")