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

from modules.report_page import build_brs_display_table  # noqa: E402


def _flat(prev, curr, cum_prev, cum_curr):
    """report_flat dengan kolom nilai + 2 kolom (%)."""
    idx = pd.Index(["Bandara X", "TOTAL"])
    return pd.DataFrame({
        "c_prev": [prev, prev], "c_curr": [curr, curr],
        "c_cp": [cum_prev, cum_prev], "c_cc": [cum_curr, cum_curr],
        "M-to-M (%)": [np.nan, np.nan], "Y-on-Y (%)": [np.nan, np.nan],
    }, index=idx)


def test_both_zero_does_not_raise():
    """0 -> 0: harus NaN, bukan crash."""
    res = build_brs_display_table(_flat(0, 0, 0, 0), "Papua Tengah", "Transportasi Udara")
    pct = res[[c for c in res.columns if "(%)" in c][0]]
    assert pct.isna().all(), f"0 -> 0 harus NaN, dapat: {pct.tolist()}"


def test_zero_prev_with_nonzero_curr_does_not_raise():
    """0 -> 100: naik dari nol, tidak terdefinisi; cukup NaN."""
    build_brs_display_table(_flat(0, 100, 0, 100), "Papua Tengah", "Transportasi Udara")


def test_normal_computation_still_works():
    """Sanity: guard tidak merusak perhitungan yang normal."""
    res = build_brs_display_table(_flat(200, 250, 100, 400),
                                  "Papua Tengah", "Transportasi Udara")
    pct = res[[c for c in res.columns if "(%)" in c][0]]
    vals = pct.dropna().tolist()
    assert vals, f"tidak ada persentase yang terhitung: {pct.tolist()}"
    for v in vals:
        assert abs(v - 25.0) < 1e-6, f"harusnya 25%, dapat {v}"


def test_object_dtype_division_raises_why_guard_is_needed():
    """Akar masalah: dtype object/nullable, bukan integer.

    Kolom hasil agregasi pandas bisa bertipe object atau nullable Int64 ketika
    ada nilai kosong. Pada dtype itu `0 / 0` memanggil operator Python, yang
    melempar ZeroDivisionError — dan numpy tidak bisa menutupnya karena
    errstate hanya berlaku untuk operasi numpy-native.
    """
    for a in (np.array([0, 10], dtype=object),
              pd.array([0, 10], dtype="Int64").to_numpy(dtype=object)):
        try:
            with np.errstate(divide="ignore", invalid="ignore"):
                np.where(a == 0, np.nan, (a - a) / a)
            raise AssertionError(f"dtypes {a.dtype} tidak melempar — guard mungkin tak perlu")
        except ZeroDivisionError:
            pass

    # dtype numerik asli aman; guard .astype(float) tidak merusak apa pun
    for dt in ("int64", "float64"):
        a = np.array([0, 10], dtype=dt)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(a == 0, np.nan, (a.astype(float) - a) / a)
        assert np.isnan(r[0]) and r[1] == 0.0


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("selesai")