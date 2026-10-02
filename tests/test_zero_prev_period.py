"""Cek perhitungan persentase tidak crash saat nilai pembanding 0.

Bug: `np.where(prev == 0, np.nan, (curr - prev) / prev)` tetap mengevaluasi
kedua cabang. Kolom hasil agregasi pandas bisa bertipe object atau nullable
`Int64`; pada dtype itu `0 / 0` memanggil operator Python, yang melempar
ZeroDivisionError. `np.errstate` tidak bisa menutupnya karena hanya berlaku
untuk operasi numpy-native — jadi `np.where` tidak pernah sempat mengembalikan
NaN. (dtype int64/float64 asli aman; ini yang membuat bugnya lolos unnoticed.)

Akibatnya memilih periode yang baris pembandingnya kosong (mis. periode
sebelumnya belum terisi di database) membuat halaman error. Guard yang sama
sudah dipasang di 3 tempat: prepare_table_item() M-to-M & Y-on-Y, dan
build_brs_display_table() M-to-M.

Run:  python tests/test_zero_prev_period.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules.report_page import (  # noqa: E402
    UNDEFINED, build_brs_display_table, format_pct_number, safe_pct_change,
)


def _flat(prev, curr, cum_prev, cum_curr):
    """report_flat dengan kolom nilai + 2 kolom (%)."""
    idx = pd.Index(["Bandara X", "TOTAL"])
    return pd.DataFrame({
        "c_prev": [prev, prev], "c_curr": [curr, curr],
        "c_cp": [cum_prev, cum_prev], "c_cc": [cum_curr, cum_curr],
        "M-to-M (%)": [np.nan, np.nan], "Y-on-Y (%)": [np.nan, np.nan],
    }, index=idx)


def _pct_col(res):
    return res[[c for c in res.columns if "(%)" in c][0]]


def _numbers(col):
    """Hanya nilai numerik; string 'undefined' sengaja tidak ikut."""
    return pd.to_numeric(col, errors="coerce").dropna().tolist()


def test_both_zero_renders_undefined_string():
    """0 -> 0: kolom (%) tetap numeric NaN, tapi tampil "undefined"."""
    res = build_brs_display_table(_flat(0, 0, 0, 0), "Papua Tengah", "Transportasi Udara")
    for col in [c for c in res.columns if "(%)" in c]:
        assert res[col].isna().all(), f"{col} harus NaN agar gradient tidak error"
        assert format_pct_number(res[col].iloc[0]) == UNDEFINED


def test_zero_prev_with_nonzero_curr_renders_undefined():
    """0 -> 100: naik dari nol tidak terdefinisi; cukup string "undefined"."""
    res = build_brs_display_table(_flat(0, 100, 0, 100), "Papua Tengah", "Transportasi Udara")
    assert _numbers(_pct_col(res)) == []


def test_normal_computation_still_works():
    """Sanity: guard tidak merusak perhitungan yang normal."""
    res = build_brs_display_table(_flat(200, 250, 100, 400),
                                  "Papua Tengah", "Transportasi Udara")
    vals = _numbers(_pct_col(res))
    assert vals, f"tidak ada persentase yang terhitung: {_pct_col(res).tolist()}"
    for v in vals:
        assert abs(v - 25.0) < 1e-6, f"harusnya 25%, dapat {v}"


def test_pct_column_dtype_stays_numeric():
    """Regresi: string di dalam kolom (%) bikin background_gradient crash.

    Styling memakai `styler.background_gradient(subset=[...pct...])`. Kolom
    object-dtype berisi "undefined" membuat pandas melempar
    "could not convert string to float" dan seluruh tabel gagal dirender.
    Jadi string HARUS datang dari formatter, bukan dari datanya.
    """
    res = build_brs_display_table(_flat(0, 100, 0, 100), "Papua Tengah", "Transportasi Udara")
    for col in [c for c in res.columns if "(%)" in c]:
        assert not isinstance(res[col].iloc[0], str), f"{col} berisi string: {res[col].iloc[0]!r}"


def test_object_dtype_division_raises_why_guard_is_needed():
    """Akar masalah: dtype object/nullable, bukan integer.

    Kolom hasil agregasi pandas bisa bertipe object atau nullable Int64 ketika
    ada nilai kosong. Pada dtype itu `0 / 0` memanggil operator Python, yang
    melempar ZeroDivisionError — dan numpy tidak bisa menutupnya karena
    errstate hanya berlaku untuk operasi numpy-native. Karena itu
    safe_pct_change() mem-parsing lewat float() per-sel, bukan
    mengandalkan numpy broadcasting.
    """
    for a in (np.array([0, 10], dtype=object),
              pd.array([0, 10], dtype="Int64").to_numpy(dtype=object)):
        try:
            with np.errstate(divide="ignore", invalid="ignore"):
                np.where(a == 0, np.nan, (a - a) / a)
            raise AssertionError(f"dtypes {a.dtype} tidak melempar — guard mungkin tak perlu")
        except ZeroDivisionError:
            pass

    # dtype numerik asli aman; helper kita tetap menangani keduanya
    assert np.isnan(safe_pct_change(10, 0))
    assert np.isnan(safe_pct_change(0, 0))
    assert np.isnan(safe_pct_change(None, 5))
    assert np.isnan(safe_pct_change("abc", 5))
    for dt in ("int64", "float64"):
        a = np.array([0, 10], dtype=dt)
        assert np.isnan(safe_pct_change(a[1], a[0]))
        assert safe_pct_change(a[0], a[1]) == -100.0


def test_every_zero_division_path_renders_the_same_string():
    """Semua jalur 0/0 harus tampil "undefined" persis sama di web dan .idml.

    Kalau Styler, IDML export, atau narrative prompt memakai string berbeda,
    tabel web dan file .idml akan berbeda isi untuk kasus yang sama.
    """
    for curr, prev in [(0, 0), (0, None), (None, 0), (0, np.nan), (np.nan, 0),
                       (np.inf, 5), (5, np.inf), ("undefined", 5)]:
        got = format_pct_number(safe_pct_change(curr, prev))
        assert got == UNDEFINED, f"({curr!r}, {prev!r}) -> {got!r}"
    # kolom yang benar-benar punya angka tetap diformat sebagai persen
    assert format_pct_number(safe_pct_change(250, 200)) == "25,00"
    assert format_pct_number(safe_pct_change(150, 200)) == "-25,00"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("selesai")