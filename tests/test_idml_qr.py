"""Regression: QR code must travel INSIDE the .idml, and nomor BRS must replace.

Bug: the template's QR <Link> pointed at an absolute path outside the package
("file:D:/BPS/.../Links/qrcode-*.png"), so the QR rendered as a missing link on
any machine but the original author's. The uploaded QR is now written into the
package's Links/ folder and the LinkResourceURI is made relative.

Run directly:  python tests/test_idml_qr.py
Or with pytest: pytest tests/test_idml_qr.py
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

# smallest valid 1x1 PNG
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
       b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
PROV, THN, BLN = "Papua Tengah", "2026", "Agustus"


def _build(tpl, **kw):
    return fill_brs_template(tpl, PROV, THN, BLN, [], {}, **kw)


def _links(z):
    """(xml_file, uri) for every LinkResourceURI in spread/master XML."""
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


def test_qr_packsaged_and_link_is_relative():
    res = _build(DEFAULT_IDML_TEMPLATE.read_bytes(), qr_bytes=PNG, qr_name="my qr.png")
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    names = z.namelist()

    qr = [n for n in names if "qrcode" in n]
    assert len(qr) == 1, f"harus 1 file QR di paket, dapat {qr}"
    assert z.read(qr[0]) == PNG, "isi file QR di paket beda dari yang diunggah"
    assert z.testzip() is None, "zip hasil rusak"

    # tautan harus relatif DAN resolve di dalam paket (tidak ada file: absolut)
    rel = [(n, u) for n, u in _links(z) if "qrcode" in u]
    assert rel, "tidak ada Link qrcode di spread"
    for n, u in rel:
        assert not u.startswith("file:"), f"{n}: link masih absolut -> {u}"
        assert u in names, f"{n}: link {u} tidak ada di paket"

    # gambar/logo lain tetap absolut -> tidak ikut tertimpa
    other = [u for _, u in _links(z) if "qrcode" not in u]
    assert other and all(u.startswith("file:") for u in other), "link non-QR ikut berubah"

    # nama file mengikuti pola template, bukan nama unggahan mentah
    assert re.search(r"qrcode-Transportasi-Bulan-Agustus-2026-Provinsi-Papua_Tengah\.png$", qr[0]), \
        f"nama QR tak sesuai pola: {qr[0]}"


def test_nomor_brs_dan_tgl_rilis_terganti():
    res = _build(DEFAULT_IDML_TEMPLATE.read_bytes(),
                 nomor_brs="999/10/94/Th. XXX", tanggal_rilis="9 November 2026")
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    story = b"".join(z.read(n) for n in z.namelist() if n.startswith("Stories/"))
    assert b"BRS No. 999/10/94/Th. XXX, 9 November 2026" in story, "nomor BRS tidak terganti"
    assert b"235/10/94/Th. XXIX" not in story, "nomor BRS lama masih ada"


def test_qr_format_bersesuaian_dengan_jpg():
    res = _build(DEFAULT_IDML_TEMPLATE.read_bytes(),
                 qr_bytes=b"\xff\xd8\xff\xe0" + b"\x00" * 40, qr_name="q.jpg")
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    jpg = [n for n in z.namelist() if "qrcode" in n]
    assert jpg and jpg[0].endswith(".jpg"), f"ekstensi JPG salah: {jpg}"
    qr_spread = _spread_with(DEFAULT_IDML_TEMPLATE.read_bytes(), b"qrcode")
    assert 'LinkResourceFormat="$ID/JPEG"' in z.read(qr_spread).decode(), \
        f"format link JPG tidak di-set di {qr_spread}"


def test_tanpa_qr_tetap_valid():
    """Unggahan opsional: tanpa QR output tetap sah dan link lama tak berubah."""
    res = _build(DEFAULT_IDML_TEMPLATE.read_bytes())
    z = zipfile.ZipFile(io.BytesIO(res.idml_bytes))
    assert z.testzip() is None
    assert not any("qrcode" in n for n in z.namelist()), "file QR muncul tanpa diunggah"
    assert any("file:" in u for _, u in _links(z)), "link absolut hilang tanpa sebab"
    assert any("QR" in w for w in res.warnings), "tak ada peringatan QR belum diunggah"


def test_template_tanpa_link_qrcode_tidak_crash():
    """Layout lain tanpa <Link> qrcode -> warning, bukan crash."""
    zin = zipfile.ZipFile(DEFAULT_IDML_TEMPLATE)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zo:
        for n in zin.namelist():
            d = zin.read(n)
            if n.startswith(("Spreads/", "MasterSpreads/")) and b"qrcode" in d:
                d = d.replace(b"qrcode", b"zzz")
            zo.writestr(n, d)
    res = _build(buf.getvalue(), qr_bytes=PNG, qr_name="q.png")
    assert any("tidak ditemukan" in w for w in res.warnings), \
        "tak ada warning saat link qrcode hilang"


if __name__ == "__main__":
    for fn in [test_qr_packsaged_and_link_is_relative,
               test_nomor_brs_dan_tgl_rilis_terganti,
               test_qr_format_bersesuaian_dengan_jpg,
               test_tanpa_qr_tetap_valid,
               test_template_tanpa_link_qrcode_tidak_crash]:
        fn()
        print(f"OK  {fn.__name__}")
    print("\nOK - 5 pemeriksaan lolos")