"""Cek periode pembanding pada .idml mengikuti p_bln/p_thn dari database.

Bug: fill_brs_template() memanggil _prev_period() yang menebak "bulan sebelumnya"
dari tabel urutan bulan. Angka di dalam tabel & narasi diambil dari
prepare_table_item() yang sudah memakai p_bln/p_thn hasil hitungan database.
Kalau tebakan != database (mis. Juli tidak ada sehingga pembanding sebenarnya
Juni), Pointer Utama & ringkasan cover menyebut periode yang salah.

Run:  python tests/test_prev_period.py
"""
import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import pandas as pd  # noqa: E402

from modules.indesign_export import (  # noqa: E402
    COVER_STORY, POINTER_STORIES, _prev_period, fill_brs_template)
from modules.report_page import DEFAULT_IDML_TEMPLATE  # noqa: E402

PROV, THN, BLN = "Papua Tengah", "2026", "Agustus"
COLS = [f"c{i}" for i in range(6)]


def _item(moda, col, prev_bln, prev_thn):
    """Satu itemPrepare_table_item()-like, dengan periode pembanding dari DB."""
    rows = ["Bandara A", "Bandara B"]
    df = pd.DataFrame(
        [[i * 10 + j for j in range(6)] for i in range(len(rows))], columns=COLS,
        index=rows,
    )
    total = pd.DataFrame([list(range(6))], columns=COLS, index=["TOTAL"])
    flat = pd.concat([df, total])
    return {
        "moda": moda, "col_target": col, "table_no": 1, "label": col,
        "prov": PROV, "bln": BLN, "thn": THN,
        "prev_bln": prev_bln, "prev_thn": prev_thn,
        "report_display_brs": flat,
        "report_flat": flat,
        "p1": f"narasi {col}", "p2": f"narasi {col} (2)", "row_col": "",
    }


def _build(items, **kw):
    return fill_brs_template(DEFAULT_IDML_TEMPLATE.read_bytes(), PROV, THN, BLN,
                             items, lambda n: n, **kw)


def test_prev_period_falls_back_to_guess_when_not_supplied():
    """Perilaku lama harus tetap jadi fallback saat p_bln/p_thn tidak ada."""
    assert _prev_period("Agustus", "2026") == ("Juli", 2026)
    assert _prev_period("Januari", "2026") == ("Desember", 2025)
    res = _build([])
    assert res is not None


def test_authoritative_prev_period_overrides_guess():
    """p_bln/p_thn dari DB harus menang atas _prev_period().

    Ambil skenario nyata: periode tabel = Agustus 2026, DB tidak punya Juli2026
    sehingga get_comparison_data() melaporkan periode pembanding = Juni 2026.
    _prev_period() akan menebak Juli ->.Pointer Utama akan menyebut Juli, padahal
    angka di tabel berasal dari Juni.
    """
    p_bln, p_thn = "Juni", "2026"
    assert _prev_period(BLN, THN) != (p_bln, p_thn), (
        "skenario test tidak membedakan tebakan vs nilai DB"
    )

    items = [
        _item("Transportasi Udara", "penumpang_berangkat", p_bln, p_thn),
        _item("Transportasi Udara", "penumpang_datang", p_bln, p_thn),
        _item("Transportasi Udara", "barang_muat_kg", p_bln, p_thn),
        _item("Transportasi Udara", "barang_bongkar_kg", p_bln, p_thn),
    ]
    res = _build(items, prev_bln=p_bln, prev_thn=p_thn)
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    pointer = z.read(f"Stories/Story_{POINTER_STORIES['udara']}.xml").decode("utf-8", "ignore")

    assert "Juni 2026" in pointer, f"Pointer Utama tidak menyebut periode DB: {pointer[-800:]}"
    assert "bulan Juli" not in pointer and "dari Juli" not in pointer, \
        "Pointer Utama masih memakai tebakan _prev_period(), bukan nilai DB"
    print("  Pointer Utama:", "Juni 2026" in pointer and "OK" or "MISSING")


def test_cover_summary_uses_current_period():
    """Ringkasan cover hanya menyebut periode berjalan, jadi harus ikut bulan/tahun
    dari meta — bukan periode pembanding."""
    items = [
        _item("Transportasi Laut", "dn_penumpang_naik", "Juni", "2026"),
        _item("Transportasi Laut", "dn_penumpang_turun", "Juni", "2026"),
        _item("Transportasi Laut", "dn_muat_barang_ton", "Juni", "2026"),
        _item("Transportasi Laut", "dn_bongkar_barang_ton", "Juni", "2026"),
        _item("Transportasi Udara", "penumpang_berangkat", "Juni", "2026"),
        _item("Transportasi Udara", "penumpang_datang", "Juni", "2026"),
        _item("Transportasi Udara", "barang_muat_kg", "Juni", "2026"),
        _item("Transportasi Udara", "barang_bongkar_kg", "Juni", "2026"),
    ]
    res = _build(items, prev_bln="Juni", prev_thn="2026")
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    cover = z.read(f"Stories/Story_{COVER_STORY}.xml").decode("utf-8", "ignore")
    assert f"pada {BLN} {THN}" in cover, (
        f"ringkasan cover tidak menyebut periode berjalan {BLN} {THN}: {cover[-600:]}"
    )


def test_items_carry_prev_period_used_when_meta_lacks_it():
    """Bila fill_brs_template dipanggil tanpa prev_*, nilai harus diambil dari item."""
    p_bln, p_thn = "Juni", "2026"
    items = [
        _item("Transportasi Udara", "penumpang_berangkat", p_bln, p_thn),
        _item("Transportasi Udara", "penumpang_datang", p_bln, p_thn),
        _item("Transportasi Udara", "barang_muat_kg", p_bln, p_thn),
        _item("Transportasi Udara", "barang_bongkar_kg", p_bln, p_thn),
    ]
    res = _build(items)  # tanpa prev_bln/prev_thn eksplisit
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    pointer = z.read(f"Stories/Story_{POINTER_STORIES['udara']}.xml").decode("utf-8", "ignore")
    assert "Juni 2026" in pointer, (
        "prev_bln/prev_thn pada item harus dipakai sebagai sumber periode pembanding"
    )


if __name__ == "__main__":
    for fn in (test_prev_period_falls_back_to_guess_when_not_supplied,
               test_authoritative_prev_period_overrides_guess,
               test_cover_summary_uses_current_period,
               test_items_carry_prev_period_used_when_meta_lacks_it):
        fn()
        print(f"OK  {fn.__name__}")
    print("selesai")