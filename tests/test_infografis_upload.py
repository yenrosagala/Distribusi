"""Cek Infografis bisa diunggah & dipaketkan seperti QR.

Template mengaitkan Infografis ke path absolut 'file:D:/BPS/.../Links/Infografis*.jpg'
(sama seperti QR) -- jadi hilang/missing link di mesin lain. Fungsi
_embed_image() sekarang menulis kedua gambar ke Links/ dan membuat tautannya relatif.

Run langsung:  python tests/test_infografis_upload.py
"""
import io
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

from modules.indesign_export import fill_brs_template  # noqa: E402
from modules.report_page import DEFAULT_IDML_TEMPLATE  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
       b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
JPG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"qr" * 8
TPL = DEFAULT_IDML_TEMPLATE.read_bytes()
PROV, THN, BLN = "Papua Tengah", "2026", "Agustus"


def _build(**kw):
    return fill_brs_template(TPL, PROV, THN, BLN, [], {}, **kw)


def _links(z):
    out = []
    for n in z.namelist():
        if n.startswith(("Spreads/", "MasterSpreads/")) and n.endswith(".xml"):
            for m in re.finditer(rb'LinkResourceURI="([^"]*)"', z.read(n)):
                out.append((n, m.group(1).decode()))
    return out


def _spread_with(tpl_bytes, needle: bytes) -> str:
    """Nama spread di template yang memuat link `needle` -- ID spread berubah
    tiap template di-export ulang, jadi jangan di-hardcode."""
    with zipfile.ZipFile(io.BytesIO(tpl_bytes)) as z:
        for n in z.namelist():
            if n.startswith(("Spreads/", "MasterSpreads/")) and needle in z.read(n):
                return n
    raise AssertionError(f"tidak ada spread dengan {needle!r} di template")


INFO_SPREAD = _spread_with(TPL, b"Infografis")


def test_infografis_packsaged_and_link_is_relative():
    res = _build(infografis_bytes=PNG, infografis_name="my info.png")
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    names = z.namelist()

    info = [n for n in names if n.startswith("Links/Infografis")]
    assert len(info) == 1, f"harus 1 file Infografis di paket, dapat {info}"
    assert z.read(info[0]) == PNG, "isi file Infografis di paket beda dari yang diunggah"
    assert z.testzip() is None, "zip hasil rusak"

    rel = [(n, u) for n, u in _links(z) if "Infografis" in u]
    assert rel, "tidak ada Link Infografis di spread"
    for n, u in rel:
        assert not u.startswith("file:"), f"{n}: link Infografis masih absolut -> {u}"
        assert u in names, f"{n}: link {u} tidak ada di paket"
    assert any(n == INFO_SPREAD for n, _ in rel), \
        f"link Infografis tidak di spread yang benar: {[n for n, _ in rel]}"
    assert not [n for n in names if "qrcode" in n], "QR ikut ter-embed tanpa diunggah"


def test_qr_and_infografis_together():
    res = _build(qr_bytes=JPG, qr_name="qr.jpg",
                 infografis_bytes=PNG, infografis_name="info.png")
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    names = z.namelist()

    assert [n for n in names if n.startswith("Links/qrcode")], "QR tidak masuk paket"
    assert [n for n in names if n.startswith("Links/Infografis")], "Infografis tidak masuk paket"
    assert z.testzip() is None
    # JPEG -> format JPEG, PNG -> format PNG
    uri = [u for _, u in _links(z) if "Infografis" in u][0]
    assert uri.endswith(".png"), uri
    uri = [u for _, u in _links(z) if "qrcode" in u][0]
    assert uri.endswith(".jpg"), uri


def test_no_upload_adds_nothing():
    res = _build()
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    assert [n for n in z.namelist() if n.startswith("Links/")] == [], \
        "tanpa upload tidak boleh ada file Links/ baru"
    assert z.testzip() is None


def _template_without_infografis_link():
    """Buka paket, ganti nama 'Infografis' di spread-nya, lalu rakit ulang.

    .idml itu ZIP, jadi replace di byte mentah tidak kena (XML-nya terkompresi).
    """
    src = zipfile.ZipFile(io.BytesIO(TPL))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            blob = src.read(info.filename)
            if info.filename == INFO_SPREAD:
                assert b"Infografis" in blob, f"fixture: {INFO_SPREAD} tidak punya link Infografis"
                blob = blob.replace(b"Infografis", b"x-tidak-ada-link-x")
            out.writestr(info, blob)
    return buf.getvalue()


def test_infografis_without_matching_link_warns_not_crashes():
    """Template tanpa <Link> Infografis harus memberi peringatan, bukan exception."""
    stripped = _template_without_infografis_link()
    res = fill_brs_template(stripped, PROV, THN, BLN, [], {}, infografis_bytes=PNG,
                            infografis_name="info.png")
    w = " ".join(res.warnings)
    assert "Infografis" in w and "manual" in w, res.warnings


if __name__ == "__main__":
    for fn in (test_infografis_packsaged_and_link_is_relative,
               test_qr_and_infografis_together,
               test_no_upload_adds_nothing,
               test_infografis_without_matching_link_warns_not_crashes):
        fn()
        print(f"OK  {fn.__name__}")
    print("selesai")