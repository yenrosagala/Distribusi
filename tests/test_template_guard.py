"""Cek validasi template: template resmi lolos, template salah dapat pesan jelas."""
import io
import sys
import zipfile

import pandas as pd
from lxml import etree

sys.path.insert(0, r"D:\New Project\Distribusi-main (1)\Distribusi-main")

from modules.indesign_export import (CLEAR_TAIL_NARRATIVE, DEFAULT_TEMPLATE,
                                     PROTO_TABLE_STORY, TABLE_STORIES, fill_brs_template)

TPL = str(DEFAULT_TEMPLATE)
good = open(TPL, "rb").read()

# template "asing": satu story tabel dihapus (meniru .idml hasil ekspor ulang
# yang ID story-nya berubah). roots di-key dari nama file, jadi cukup dihapus.
GONE = PROTO_TABLE_STORY
buf = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(good)) as zin, zipfile.ZipFile(buf, "w") as zout:
    for n in zin.namelist():
        if n == f"Stories/Story_{GONE}.xml":
            continue
        zout.writestr(n, zin.read(n))
bad = buf.getvalue()

item = {
    "moda": "Transportasi Udara",
    "col_target": "penumpang_berangkat",
    "report_display_brs": pd.DataFrame(
        {"nama_bandara": ["Sentani"], "Agustus 2026": ["1"], "Juli 2026": ["2"]}
    ),
    "rows": [],
    "p1": "a",
    "p2": "b",
    "row_col": "",
}


def run(tpl, label):
    """Cuma cek guard template. Error lain (isi tabel) diabaikan di sini --
    pengiriman tabel sudah diuji test_idml_qr.py."""
    try:
        fill_brs_template(tpl, "Papua Tengah", "2026", "Agustus", [item], {})
        return "lolos validasi"
    except ValueError as e:
        if "tidak cocok" in str(e):
            return f"ditolak: {e}"
        return f"lolos validasi (ValueError lain: {e})"
    except Exception as e:  # isi tabel gagal karena dummy di atas, bukan guard
        return f"lolos validasi (error isi tabel: {type(e).__name__})"


print("template resmi  ->", run(good, "ok"))
print("template salah  ->", run(bad, "bad"))

# template salah harus ditolak dengan pesan yang menyebut story hilang
try:
    fill_brs_template(bad, "Papua Tengah", "2026", "Agustus", [item], {})
    raise AssertionError("template salah tidak ditolak")
except ValueError as e:
    assert "tidak cocok" in str(e), f"pesan salah: {e}"
    assert GONE in str(e), f"pesan tidak menyebut {GONE}: {e}"
    print(f"ditolak + sebut {GONE} : OK")

# template resmi harus lolos cek ini
try:
    fill_brs_template(good, "Papua Tengah", "2026", "Agustus", [item], {})
except ValueError as e:
    raise AssertionError(f"template resmi ditolak: {e}")
except Exception:
    pass  # error isi tabel karena dummy, bukan masalah guard
print("template resmi lolos : OK")
print("selesai")


def _items():
    """Satu item per kolom tabel, untuk kedua moda -- stories tiap moda hanya
    diganti periodenya kalau modanya memang ada datanya."""
    rows = ["Lokasi A", "TOTAL"]
    flat = pd.DataFrame([[i * 10 + j for j in range(6)] for i in range(len(rows))],
                        columns=[f"c{i}" for i in range(6)], index=rows)
    return [{"moda": "Transportasi Udara" if moda == "udara" else "Transportasi Laut",
             "col_target": col, "report_display_brs": flat, "report_flat": flat,
             "p1": "narasi satu", "p2": "narasi dua",
             "prev_bln": "Juli", "prev_thn": "2026", "row_col": ""}
            for _sid, (moda, col, _t, _s, _d) in TABLE_STORIES.items()]


def _export():
    return fill_brs_template(good, "Papua Tengah", "2026", "Agustus", _items(), lambda n: n)


def test_nama_provinsi_template_terganti_semua():
    """Template baru ditulis 'Papua Selatan'; hasil untuk Papua Tengah harus bersih."""
    z = zipfile.ZipFile(io.BytesIO(_export().idml_bytes))
    story = b"".join(z.read(n) for n in z.namelist()
                     if n.startswith("Stories/")).decode("utf-8", "ignore")
    assert "Papua Selatan" not in story, "ada sisa 'Papua Selatan' di story hasil"


def test_sisa_narasi_contoh_setelah_tabel_dikosongkan():
    """Paragraf narasi lama yang menempel setelah <Table> harus dibuang, jangan
    ikut tercetak sebagai milik provinsi tujuan."""
    z = zipfile.ZipFile(io.BytesIO(_export().idml_bytes))
    for sid in CLEAR_TAIL_NARRATIVE:
        root = etree.fromstring(z.read(f"Stories/Story_{sid}.xml"))
        kids = [c for c in root.find("Story")
                if etree.QName(c).localname == "ParagraphStyleRange"]
        tbl_at = next(i for i, c in enumerate(kids) if c.find(".//Table") is not None)
        for c in kids[tbl_at + 1:]:
            txt = "".join(t.text or "" for t in c.iter("Content"))
            assert not txt.strip(), f"{sid}: sisa narasi setelah tabel: {txt[:60]!r}"