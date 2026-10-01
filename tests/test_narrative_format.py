"""Cek format angka & persen konsisten antara tabel dan narasi.

Tiga keputusan format yang dulu terpisah-pisah sehingga tidak sinkron:

1. Tabel web memakai `styler.format(format_id_number)` dengan decimals=2 untuk
   SEMUA kolom, termasuk penumpang. Tabel .idml memakai dec=0 untuk kolom Orang.
   -> web menampilkan "47.526,00", idml "47.526".

2. Narasi diambil dari Gemini (lalu di-cache di DB), jadi jumlah desimalnya
   bebas: model menulis "65.352,1 ton" sementara tabel menulis "65.352,10".

3. Model menulis "10,27%" sementara narasi fallback memakai kata "persen".

`normalize_narrative()` yang dipakai di sini menyatukan ketiganya: bentuk
angka jadi gaya Indonesia, desimal mengikuti satuan (orang/penumpang = 0,
ton/persen = 2), dan "%" diganti " persen".

Run:  python tests/test_narrative_format.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import pandas as pd  # noqa: E402

from modules.report_page import (  # noqa: E402
    NARRATIVE_META,
    format_id_number,
    normalize_narrative,
    table_formatters,
)


# ---------------------------------------------------------------------------
# 1. Tabel: desimal per kolom mengikuti satuan, bukan default 2
# ---------------------------------------------------------------------------
def test_passenger_columns_have_no_decimals():
    cols = ["Juli 2026", "Agustus 2026", "M-to-M (%)",
            "Jan-Agustus 2025", "Jan-Agustus 2026", "Y-on-Y (%)"]
    fmts = table_formatters(pd.Index(cols), "penumpang_berangkat")
    for c in ["Juli 2026", "Agustus 2026", "Jan-Agustus 2025", "Jan-Agustus 2026"]:
        assert fmts[c](47526.0) == "47.526", f"{c}: {fmts[c](47526.0)!r}"
        assert "," not in fmts[c](47526.0).replace(".", ""), (
            f"{c} masih punya desimal: {fmts[c](47526.0)!r}"
        )


def test_tonnage_columns_keep_two_decimals():
    cols = ["Juli 2026", "Agustus 2026", "M-to-M (%)",
            "Jan-Agustus 2025", "Jan-Agustus 2026", "Y-on-Y (%)"]
    fmts = table_formatters(pd.Index(cols), "barang_muat_kg")
    assert fmts["Agustus 2026"](3286.296) == "3.286,30"
    assert fmts["Juli 2026"](2754.442) == "2.754,44"


def test_pct_columns_always_two_decimals():
    """Kolom (%) selalu 2 desimal, termasuk untuk tabel barang."""
    for ct in ("penumpang_berangkat", "barang_muat_kg"):
        fmts = table_formatters(pd.Index(["M-to-M (%)", "Y-on-Y (%)"]), ct)
        assert fmts["M-to-M (%)"](-5.6799) == "-5,68"
        assert fmts["Y-on-Y (%)"](7.1683) == "7,17"


def test_formatters_cover_every_column():
    cols = pd.Index(["Juli 2026", "Agustus 2026", "M-to-M (%)",
                     "Jan-Agustus 2025", "Jan-Agustus 2026", "Y-on-Y (%)"])
    for ct in ("penumpang_datang", "penumpang_berangkat", "dn_penumpang_naik",
               "dn_penumpang_turun", "barang_muat_kg", "barang_bongkar_kg",
               "dn_muat_barang_ton", "dn_bongkar_barang_ton"):
        fmts = table_formatters(cols, ct)
        assert set(fmts) == set(cols), f"{ct}: kolom tidak lengkap"


def test_unknown_col_target_still_works():
    """col_target di luar NARRATIVE_META tidak boleh membuat KeyError."""
    fmts = table_formatters(pd.Index(["Agustus 2026"]), "entitas_tidak_dikenal")
    assert fmts["Agustus 2026"](1.5) == format_id_number(1.5)


# ---------------------------------------------------------------------------
# 2 & 3. Narasi: desimal ikut satuan, "%" jadi "persen"
# ---------------------------------------------------------------------------
def test_percent_sign_becomes_persen():
    txt = "kontraksi signifikan sebesar 10,27% dibandingkan Juli 2026."
    out = normalize_narrative(txt)
    assert "%" not in out, f"masih ada tanda %: {out!r}"
    assert "10,27 persen" in out, out
    assert "sebesar 10,27 persen dibandingkan" in out, out


def test_percent_with_dot_decimal_becomes_persen():
    out = normalize_narrative("tumbuh 5.5% menjadi 7.2%.")
    assert "%" not in out, out
    assert "5,50 persen" in out and "7,20 persen" in out, out


def test_passenger_number_loses_decimals():
    txt = "tercatat sebanyak 7.778,45 orang pada Agustus 2026."
    out = normalize_narrative(txt)
    assert "7.778 orang" in out, out
    assert "7.778,45" not in out, out


def test_passenger_number_with_word_penumpang_loses_decimals():
    out = normalize_narrative(
        "naik dari 4.318,20 menjadi 5.693,80 penumpang.", "penumpang_berangkat"
    )
    assert "5.694 penumpang" in out, out
    # angka desimal tanpa kata satuan ikut satuan tabel, karena satuan laporan ini
    # sudah pasti orang (satu narasi = satu tabel).
    assert "4.318" in out, out
    assert "4.318,20" not in out, out


def test_bare_integers_without_unit_left_alone():
    """'3 maskapai' tidak boleh jadi '3,00' -- satuan-nya tidak diketahui."""
    out = normalize_narrative(
        "dilayani 3 maskapai dengan 2 Simulator.", "penumpang_berangkat"
    )
    assert "3 maskapai" in out, out
    assert "2 Simulator" in out, out
    assert ",00" not in out, out


def test_percent_not_ruined_by_loose_decimal_pass():
    """Persen harus tetap 2 desimal walau satuan laporan ini orang (0 desimal)."""
    out = normalize_narrative("turun 10,27% menjadi 9,97%.", "penumpang_berangkat")
    assert "10,27 persen" in out, out
    assert "9,97 persen" in out, out
    assert "10 persen" not in out, out


def test_tonnage_loose_decimal_keeps_two():
    out = normalize_narrative("naik dari 253.610,5 menjadi 288.400,25 ton.", "barang_muat_kg")
    assert "253.610,50" in out, out


def test_tonnage_keeps_two_decimals():
    out = normalize_narrative("mencapai 65352,1 ton pada Agustus 2026.")
    assert "65.352,10 ton" in out, out


def test_table_number_in_narrative_matches_table_format():
    """Angka yang sama harus tampil sama persis di tabel dan narasi."""
    raw = 3286.296
    fmts = table_formatters(pd.Index(["Agustus 2026"]), "barang_muat_kg")
    in_table = fmts["Agustus 2026"](raw)
    in_narrative = normalize_narrative(f"mencapai {raw} ton.", "barang_muat_kg")
    assert in_table in in_narrative, (
        f"tabel {in_table!r} != narasi {in_narrative!r}"
    )


def test_passenger_table_matches_narrative():
    raw = 47526.0
    fmts = table_formatters(pd.Index(["Agustus 2026"]), "penumpang_berangkat")
    in_table = fmts["Agustus 2026"](raw)
    in_narrative = normalize_narrative(
        f"sebanyak {raw} orang tercatat.", "penumpang_berangkat"
    )
    assert in_table in in_narrative, f"tabel {in_table!r} != narasi {in_narrative!r}"


def test_month_year_tokens_untouched():
    """'Agustus 2026' dan 'Jan-Agustus 2026' bukan angka yang diformat ulang."""
    out = normalize_narrative("pada Agustus 2026 secara kumulatif Jan-Agustus 2026.")
    assert "Agustus 2026" in out, out
    assert "Jan-Agustus 2026" in out, out


def test_paragraph_break_preserved():
    out = normalize_narrative("*(Executive Summary - Database (Cached))*\n\nNilai 10,50% naik.")
    assert "\n\n" in out, f"baris paragraf hilang: {out!r}"
    assert "Executive Summary" in out, out


def test_empty_and_none_safe():
    assert normalize_narrative("") == ""
    assert normalize_narrative(None) is None


def test_undefined_not_mangled():
    out = normalize_narrative("terjadi Undefined pada periode tersebut.")
    assert "Undefined" in out, out


def test_existing_persen_not_duplicated():
    out = normalize_narrative("kenaikan 3,45 persen dari 1.000 orang.")
    assert "persen persen" not in out, out
    assert "3,45 persen" in out, out
    assert "1.000 orang" in out, out


def test_satuan_lookup_covers_all_targets():
    """Semua col_target yang dipakai harus punya aturan desimal."""
    for ct in NARRATIVE_META:
        assert NARRATIVE_META[ct]["satuan"] in ("orang", "ton"), (
            f"{ct} satuan tidak dikenal: {NARRATIVE_META[ct]['satuan']!r}"
        )


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("selesai")