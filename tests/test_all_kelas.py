"""Tabel akomodasi harus menampilkan semua kelas, termasuk yang tidak ada isinya.

Papua Pegunungan hanya punya Bintang 1 & 3 dan Kelas 1, 2 & 4, sedangkan
provinsi lain punya kelas 1-4. Sebelumnya tabel hanya memuat baris yang ada
di dataframe, jadi kelas yang kosong hilang tanpa jejak — pembaca mengira
kategori itu tidak berlaku, padahal "tidak ada hotel di kelas itu" adalah
informasi yang berbeda.

Run:  python tests/test_all_kelas.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import pandas as pd  # noqa: E402

# domain kelas di Papua Pegunungan, apa adanya (lihat tests/test_all_kelas.py
# docstring): bintang 1,3 dan non-bintang 1,2,4.
BINTANG = ["Hotel Bintang", 1, 3]
NON_BINTANG = ["Hotel Non Bintang", 1, 2, 4]
FULL = [1, 2, 3, 4]


def _kelas_domain(observed):
    """Replika domain query: kelas yang ada di SELURUH tabel."""
    domain = {"Hotel Bintang": [], "Hotel Non Bintang": []}
    for jenis, _ in observed:
        for j, k in observed:
            if j == jenis and k not in domain[j]:
                domain[j].append(k)
    return {j: sorted(v) for j, v in domain.items() if v}


def _reindex_classes(merged, jenis, domain):
    """Replika reindex di generate_akomodasi_tables."""
    merged = merged.dropna(subset=["kelas_akomodasi"]).copy()
    merged["kelas_akomodasi"] = pd.to_numeric(merged["kelas_akomodasi"], errors="coerce")
    all_kelas = sorted(set(domain.get(jenis, []))
                       | set(int(k) for k in merged["kelas_akomodasi"].dropna().unique()))
    if not all_kelas:
        return merged
    return (merged.set_index("kelas_akomodasi")
                  .reindex(all_kelas)
                  .rename_axis("kelas_akomodasi")
                  .reset_index())


def test_domain_covers_every_province():
    """Domain harus union kelas dari semua provinsi, bukan satu provinsi."""
    domain = _kelas_domain([
        ("Hotel Bintang", 1), ("Hotel Bintang", 3),      # Papua Pegunungan
        ("Hotel Bintang", 1), ("Hotel Bintang", 2),
        ("Hotel Bintang", 3), ("Hotel Bintang", 4),      # Papua Tengah
        ("Hotel Non Bintang", 1), ("Hotel Non Bintang", 2),
        ("Hotel Non Bintang", 4), ("Hotel Non Bintang", 3),
    ])
    assert domain["Hotel Bintang"] == FULL, domain
    assert domain["Hotel Non Bintang"] == FULL, domain


def test_missing_bintang_classes_are_listed():
    """Bintang 2 dan 4 harus jadi baris walau Papua Pegunungan tidak punya."""
    jenis, kelas = BINTANG[0], BINTANG[1:]
    domain = _kelas_domain([(jenis, 1), (jenis, 3), (jenis, 2), (jenis, 4)])
    merged = pd.DataFrame({"kelas_akomodasi": kelas,
                           "current": [35.21, 64.55],
                           "prev": [35.28, 67.42],
                           "last_year": [31.34, 58.19]})
    out = _reindex_classes(merged, jenis, domain)
    assert out["kelas_akomodasi"].tolist() == FULL, out["kelas_akomodasi"].tolist()
    # kelas kosong tetap kosong, bukan 0 (TPK = rata-rata, bukan jumlah)
    for k in (2, 4):
        assert pd.isna(out.loc[out["kelas_akomodasi"] == k, "current"].iloc[0]), \
            f"kelas {k} harus kosong, bukan 0"
    # kelas yang ada datanya tidak boleh disturbed
    assert out.loc[out["kelas_akomodasi"] == 1, "current"].iloc[0] == 35.21


def test_missing_non_bintang_classes_are_listed():
    """Kelas 3 harus jadi baris walau tidak ada di Papua Pegunungan."""
    jenis, kelas = NON_BINTANG[0], NON_BINTANG[1:]
    domain = _kelas_domain([(jenis, k) for k in kelas] + [(jenis, 3)])
    merged = pd.DataFrame({"kelas_akomodasi": kelas,
                           "current": [10.67, 13.42, 5.16],
                           "prev": [10.21, 12.75, 5.15],
                           "last_year": [9.61, 11.94, 4.95]})
    out = _reindex_classes(merged, jenis, domain)
    assert out["kelas_akomodasi"].tolist() == FULL, out["kelas_akomodasi"].tolist()
    assert pd.isna(out.loc[out["kelas_akomodasi"] == 3, "current"].iloc[0])


def test_no_duplicate_or_missing_rows():
    """Satu baris per kelas, urut, tidak ada kelas yang dobel."""
    jenis, kelas = BINTANG[0], BINTANG[1:]
    domain = _kelas_domain([(jenis, 1), (jenis, 3), (jenis, 2)])
    merged = pd.DataFrame({"kelas_akomodasi": kelas, "current": [1.0, 2.0]})
    out = _reindex_classes(merged, jenis, domain)
    vals = out["kelas_akomodasi"].tolist()
    assert vals == sorted(vals)
    assert len(vals) == len(set(vals))
    assert len(vals) == 3, vals
    assert vals == [1, 2, 3], vals


def test_class_present_in_only_one_period_still_shown():
    """Kelas yang cuma muncul di satu periode pun tetap jadi baris."""
    jenis = "Hotel Bintang"
    domain = _kelas_domain([(jenis, 1), (jenis, 3), (jenis, 4)])
    merged = pd.DataFrame({"kelas_akomodasi": [1, 3], "current": [1.0, 2.0]})
    out = _reindex_classes(merged, jenis, domain)
    assert out["kelas_akomodasi"].tolist() == [1, 3, 4]
    # kelas 4 ada di domain tapi tidak di periode ini -> tetap tampil, kosong
    assert pd.isna(out.loc[out["kelas_akomodasi"] == 4, "current"].iloc[0])


def test_empty_domain_still_lists_observed():
    """Domain gagal diambil (offline) -> jangan sampai tabel kosong."""
    merged = pd.DataFrame({"kelas_akomodasi": [1, 3], "current": [1.0, 2.0]})
    out = _reindex_classes(merged, "Hotel Bintang", {})
    assert out["kelas_akomodasi"].tolist() == [1, 3]


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {name}")
    print("selesai")