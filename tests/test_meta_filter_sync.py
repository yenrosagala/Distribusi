"""Cek meta yang dipakai export selalu sama dengan filter yang sedang dipilih.

Bug: report_meta disimpan ke session_state saat tombol ditekan, lalu dibaca lagi
pada run berikutnya. Selectbox-nya adalah widget hidup — mengubah filter memicu
rerun, jadi judul/header/nama file .idml bisa menyebut periode yang berbeda dari
yang difilter.

Fungsi ini menguji helper sinkronisasi meta secara murni (tanpa Streamlit).
Run:  python tests/test_meta_filter_sync.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

from modules.report_page import meta_matches_filter  # noqa: E402


def test_same_filter_needs_no_reload():
    now = {"prov": "Papua Tengah", "thn": "2026", "bln": "Agustus"}
    assert meta_matches_filter(now, now), "meta identik seharusnya tidak memicu reload"


def test_changed_month_is_detected():
    old = {"prov": "Papua Tengah", "thn": "2026", "bln": "Agustus"}
    now = {"prov": "Papua Tengah", "thn": "2026", "bln": "September"}
    assert not meta_matches_filter(old, now), "perubahan bulan harus terdeteksi"


def test_changed_year_is_detected():
    old = {"prov": "Papua Tengah", "thn": "2026", "bln": "Agustus"}
    now = {"prov": "Papua Tengah", "thn": "2025", "bln": "Agustus"}
    assert not meta_matches_filter(old, now), "perubahan tahun harus terdeteksi"


def test_changed_province_is_detected():
    old = {"prov": "Papua Tengah", "thn": "2026", "bln": "Agustus"}
    now = {"prov": "Papua Barat", "thn": "2026", "bln": "Agustus"}
    assert not meta_matches_filter(old, now), "perubahan provinsi harus terdeteksi"


def test_missing_meta_key_counts_as_mismatch():
    now = {"prov": "Papua Tengah", "thn": "2026", "bln": "Agustus"}
    assert not meta_matches_filter({"prov": "Papua Tengah"}, now), (
        "meta yang tidak lengkap harus dianggap tidak cocok"
    )


def test_types_are_compared_as_string():
    """thn dari widget bisa str atau int depending on source; harus tetap cocok."""
    old = {"prov": "Papua Tengah", "thn": 2026, "bln": "Agustus"}
    now = {"prov": "Papua Tengah", "thn": "2026", "bln": "Agustus"}
    assert meta_matches_filter(old, now), "2026 (int) dan '2026' (str) dianggap sama"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("selesai")