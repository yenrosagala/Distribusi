"""Regresi pembuat IDML Braya.

Fokusnya bukan "kalimatnya enak dibaca" tapi "angka lama template tidak pernah
bocor ke publikasi" — kesalahan yang tidak terlihat sampai dokumen terbit.
Karena itu setiap uji membandingkan IDML hasil isi terhadap template asli.

Run:  python -m pytest tests/test_indesign_pariwisata.py -q
"""
import io
import math
import os
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

from modules.indesign_pariwisata import (  # noqa: E402
    DEFAULT_TEMPLATE, KELAS_TEMPLATE, LABEL_KOLOM, RINGKASAN, RINGKASAN_UTAMA,
    RASIO_GRAFIK, JUDUL_GRAFIK, TABEL_SLOTS, TOKEN_GRAFIK, TPL_PROV_TOKENS,
    deret_tabel, fill_brs_pariwisata_template, grafik_png, kumpulkan_kelas,
    total_kelas,
)

PROV = "Papua Pegunungan"


def _tren(*nilai):
    """Deret Jan-2025..Agustus-2026 -> [(tahun, bulan, nilai)].

    Nilai None = bulan tanpa data, harus jadi celah pada garis, bukan 0.
    """
    keluar, i = [], 0
    for thn in (2025, 2026):
        for bln in range(1, 13 if thn == 2025 else 9):
            keluar.append((thn, bln, nilai[i] if i < len(nilai) else None))
            i += 1
    return keluar


NAN = float("nan")

# Angka fixture sengaja tidak sama dengan angka template supaya "angka lama masih
# ada" di hasil uji berarti benar-benar slot yang terlewat.
DATA = {
    ("tpk", "Hotel Bintang"): {
        "rows": {1: (37.27, 41.63, 33.31), 3: (61.44, 68.72, 60.19),
                 4: (55.88, 54.07, 51.66)},
        "tren": _tren(41.0, 43.5, NAN, 52.0, 49.0, 55.5, 58.0, 47.5, 50.0, 53.5,
                      56.0, 54.0, 51.5, 49.0, 52.5, 48.0, 46.5, 50.0, 44.0, 42.5),
    },
    ("rlmtgab", "Hotel Bintang"): {
        "rows": {1: (1.73, 1.91, 1.28), 2: (2.44, 2.77, 1.65),
                 3: (2.19, 2.65, 2.08), 4: (2.31, 2.19, 1.94)},
        "tren": _tren(1.9, 2.0, NAN, 2.4, 2.2, 2.5, 2.3, 1.9, 2.1, 2.4, 2.2,
                      1.8, 1.9, 2.0, 2.2, 1.9, 1.7, 1.8, 1.4, 1.3),
    },
    ("tpk", "Hotel Non Bintang"): {
        "rows": {1: (12.94, 14.88, 10.37), 2: (15.76, 17.42, 13.11),
                 4: (23.18, 25.87, 22.46)},
        "tren": _tren(14.5, 15.0, 16.0, 14.0, 13.5, 15.5, 16.5, 12.0, 13.0, 14.5,
                      15.0, 13.5, 12.5, 11.5, 13.0, 12.0, 11.0, 12.5, 10.5, 10.0),
    },
    ("rlmtgab", "Hotel Non Bintang"): {
        "rows": {1: (5.71, 5.24, 5.09), 2: (4.38, 4.66, 3.97),
                 3: (3.85, 4.19, 3.62), 4: (6.24, 5.48, 5.93)},
        "tren": _tren(5.2, 5.0, NAN, 4.8, 5.1, 4.6, 4.9, 5.3, 5.0, 4.7, 4.9, 5.1,
                      4.8, 4.6, 5.0, 4.7, 4.5, 4.8, 5.2, 5.1),
    },
}

# Angka模板 asli per story, dipakai untuk mendeteksi slot yang tidak tertimpa.
TPL_NUMS = {
    "uc98": ["52,13", "49,76", "2,37"], "ud82": ["21,55", "23,34"],
    "ud2a": ["1,42", "1,63"], "ue1d": ["5,16", "5,77"],
    "uc40": ["2,37", "50,63"], "ub99": ["50,63", "52,13"],
    "ud40": ["15,21", "17,60"], "uddb": ["15,21", "17,60"],
}


def _isi(**kw):
    """Isi template sekali, dipakai banyak uji."""
    params = dict(template_bytes=DEFAULT_TEMPLATE.read_bytes(), prov=PROV, thn=2026,
                  bln="Agustus", data=DATA,
                  nomor_brs="218/09/94/Th. XXIX", tanggal_rilis="2 Maret 2026")
    params.update(kw)
    return fill_brs_pariwisata_template(**params)


def _story(r, sid):
    return "".join(ET.fromstring(
        zipfile.ZipFile(io.BytesIO(r.idml_bytes)).read(f"Stories/Story_{sid}.xml")
    ).itertext())


def _zip(r):
    return zipfile.ZipFile(io.BytesIO(r.idml_bytes))


# --- Angka & tabel -------------------------------------------------------

def test_kumpulkan_kelas_membaca_tiga_periode_dan_tidak_menenuhkan_nilai():
    """Kelas ada di DB tapi nilainya NULL, atau periodenya tidak ada sama sekali,
    harus jadi NaN. Kalau nol, pembaca akan menyimpulkan TPK kelas itu nol."""
    import pandas as pd
    from sqlalchemy import create_engine

    baris = [
        # kd_prov, jenis, kelas, tpk, rlmtgab, year, month
        ("93", "Hotel Bintang", 1, 37.27, 1.73, 2026, 8),   # bulan ini
        ("93", "Hotel Bintang", 1, 41.63, 1.91, 2026, 7),   # bulan lalu
        ("93", "Hotel Bintang", 1, 33.31, 1.28, 2025, 8),   # tahun lalu
        ("93", "Hotel Bintang", 1, None, None, 2026, 8),    # NULL di bulan ini -> NaN
        ("93", "Hotel Bintang", 3, 61.44, 2.19, 2026, 8),   # tanpa bulan lalu -> NaN
        ("94", "Hotel Bintang", 1, 99.99, 9.99, 2026, 8),   # provinsi lain -> diabaikan
    ]
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE all_data (kd_prov TEXT, jenis_akomodasi TEXT, "
            "kelas_akomodasi INT, tpk REAL, rlmtgab REAL, year INT, month INT)")
        c.exec_driver_sql(
            "INSERT INTO all_data VALUES (?,?,?,?,?,?,?)",
            baris,
        )

    out = kumpulkan_kelas(engine, "93", 2026, "Agustus")
    tpk = out[("tpk", "Hotel Bintang")]

    # kelas 2 dan 4 tidak ada di DB -> tidak ikut masuk; deret_tabel yang menambahkannya
    assert set(tpk["rows"]) == {1, 3}, set(tpk["rows"])
    assert tpk["rows"][3][1] != tpk["rows"][3][1], "kelas tanpa bulan lalu harus NaN"
    assert math.isnan(tpk["rows"][3][0]), "kelas tanpa tahun lalu harus NaN"
    assert tpk["rows"][3][2] == 61.44
    # provinsi lain tidak boleh bocor
    assert 99.99 not in {v for row in tpk["rows"].values() for v in row if v == v}


def test_periode_null_dan_tahun_lalu_terbaca_benar():
    import pandas as pd
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE all_data (kd_prov TEXT, jenis_akomodasi TEXT, "
            "kelas_akomodasi INT, tpk REAL, rlmtgab REAL, year INT, month INT)")
        c.exec_driver_sql("INSERT INTO all_data VALUES (?,?,?,?,?,?,?)",
                          [("93", "Hotel Bintang", 1, 10.0, 1.0, 2026, 1),
                           ("93", "Hotel Bintang", 1, 20.0, 2.0, 2025, 12),  # Desember 2025
                           ("93", "Hotel Bintang", 1, 30.0, 3.0, 2025, 1)])  # Jan 2025
    out = kumpulkan_kelas(engine, "93", 2026, "Januari")
    tpk = out[("tpk", "Hotel Bintang")]
    thn_lalu, bln_lalu, bln_ini = tpk["rows"][1]
    assert (thn_lalu, bln_lalu, bln_ini) == (30.0, 20.0, 10.0), \
        "urutan harus (tahun lalu, bulan lalu, bulan ini)"


def test_deret_tabel_menambah_kelas_yang_tidak_ada_di_db():
    """Lima baris (4 kelas + Total) walau DB hanya punya sebagian kelas. Kelas
    kosong harus NaN, bukan 0 — angka 0 akan terbaca sebagai "TPK kelas itu nol",
    bukan "kelas itu tidak ada hotel"."""
    blok = {"rows": {1: (37.27, 41.63, 33.31), 3: (61.44, 68.72, 60.19)}}
    deret = deret_tabel(blok, "Hotel Bintang")
    assert len(deret) == len(KELAS_TEMPLATE) + 1, "4 kelas + 1 Total"
    assert [r[0] for r in deret] == ["Bintang 1", "Bintang 2", "Bintang 3", "Bintang 4",
                                      "Total Bintang"]
    assert semua_nan(deret[1][1:]), f"kelas kosong harus NaN, dapat {deret[1][1:]}"
    assert not semua_nan(deret[0][1:]), "kelas yang punya data tidak boleh NaN"
    # Total hanya dari kelas yang ada isinya (2 dan 4 tidak boleh menurunkan rata-rata)
    assert math.isclose(deret[4][1], (37.27 + 61.44) / 2, rel_tol=1e-9)


def semua_nan(nilai):
    return all(v is None or (isinstance(v, float) and math.isnan(v)) for v in nilai)


def test_total_kelas_adalah_rata_tanpa_berat():
    """Total = rata-rata kelas yang punya data (all_data tidak menyimpan jumlah kamar)."""
    import math as _m
    rows = {1: (37.27, 41.63, 33.31), 3: (61.44, 68.72, 60.19)}
    total = total_kelas(rows)
    assert _m.isclose(total[0], (37.27 + 61.44) / 2, rel_tol=1e-9)


# --- Kemasan ZIP ---------------------------------------------------------

def test_kemasan_idml_valid():
    """mimetype harus entri pertama dan uncompressed; InDesign menolak selain itu."""
    z = _zip(_isi())
    assert z.testzip() is None, "zip korup"
    assert z.namelist()[0] == "mimetype"
    assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def test_semua_xml_lolos_parse():
    z = _zip(_isi())
    rusak = []
    for n in z.namelist():
        if n.endswith((".xml", ".dtd")):
            try:
                ET.fromstring(z.read(n))
            except Exception as e:  # noqa: BLE001
                rusak.append((n, str(e)[:60]))
    assert not rusak, f"XML tidak bisa di-parse: {rusak}"


# --- Sisa teks template --------------------------------------------------

def test_tidak_ada_angka_template_lama_yang_bocor():
    """Angka publikasi lama di story yang kita tulis = slot terlewat."""
    r = _isi()
    z = _zip(r)
    for sid, nums in TPL_NUMS.items():
        nama = f"Stories/Story_{sid}.xml"
        assert nama in z.namelist(), f"story {sid} hilang dari paket"
        teks = "".join(ET.fromstring(z.read(nama)).itertext())
        bocor = [n for n in nums if n in teks]
        assert not bocor, f"{sid} masih memuat angka template lama: {bocor}"


def test_tidak_ada_provinsi_asing_dan_salah_ketik():
    r = _isi()
    z = _zip(r)
    for n in z.namelist():
        if not n.startswith("Stories/Story_"):
            continue
        teks = "".join(ET.fromstring(z.read(n)).itertext())
        asing = [p for p in TPL_PROV_TOKENS if p != PROV and p in teks]
        assert not asing, f"{n} masih menyebut provinsi lain: {asing}"
        assert "Julii" not in teks, f"{n} masih punya salah ketik 'Julii'"


def test_tidak_ada_peringatan_kegagalan_slot():
    """Hanya peringatan manual (Total, RSE, cek InDesign) dan konfirmasi penempatan
    gambar yang boleh muncul; sisanya berarti ada slot yang gagal ditulis."""
    r = _isi()
    keras = [w for w in r.warnings
             if not w.startswith(("Baris Total", "Tanda (*)", "Setelah dibuka",
                                  "Grafik ", "QR code", "Infografis"))]
    assert not keras, f"peringatan tak terduga: {keras}"


# --- Periode & rollover --------------------------------------------------

def test_periode_januari_menggeser_bulan_lalu_ke_desember():
    """Target Januari harus membandingkan Desember tahun sebelumnya."""
    r = _isi(bln="Januari", thn=2027)
    teks = _story(r, "uc40")
    assert "Desember 2026" in teks, teks[-300:]
    assert "Januari 2027" in teks, teks[-300:]


def test_periode_dan_provinsi_tertulis_di_judul():
    r = _isi()
    assert f"{PROV} Agustus 2026" in _story(r, "ua33")
    assert "Perkembangan Pariwisata" in _story(r, "u111a")


# --- Struktur paragraf ----------------------------------------------------

def test_ringkasan_dua_segmen_per_paragraf():
    """ub99/uc40 menyimpan TPK di segmen 1 dan RLMT di segmen 2; kalauastisr hilang
    RLMT tidak akan pernah tampil."""
    r = _isi()
    for sid in (RINGKASAN, RINGKASAN_UTAMA):
        teks = _story(r, sid)
        assert "TPK" in teks and "RLMT" in teks, f"{sid} kehilangan salah satu indikator"


def test_glosarium_mempertahankan_teks_statis():
    """ufd7 (TPK) & uce8 (RLMT) memuat definisi tetap + angka periode; definisinya
    harus utuh. Menimpa segmen utuh tanpa menjaga jangkar akan ikut menghapus
    definisi, membuat footnote kehilangan makna."""
    r = _isi()
    assert "adalah perbandingan antara banyaknya malam kamar" in _story(r, "ufd7"), \
        "definisi TPK hilang"
    assert "adalah banyaknya malam tempat tidur" in _story(r, "uce8"), \
        "definisi RLMT hilang"
    # definisi tetap DAN angka periode harus ada di story yang sama
    assert "Agustus 2026" in _story(r, "ufd7"), "angka periode hilang di glosarium TPK"


def test_empat_tabel_terisi_penuh():
    """Setiap tabel harus punya 13 sel header + 5x6 sel isi; kalau tidak, InDesign
    akan menampilkan tabel kosong tanpa warning."""
    r = _isi()
    z = _zip(r)
    for sid in TABEL_SLOTS:
        root = ET.fromstring(z.read(f"Stories/Story_{sid}.xml"))
        sel = root.findall(".//Table/Cell")
        assert len(sel) == 13 + (len(KELAS_TEMPLATE) + 1) * 6, \
            f"{sid} punya {len(sel)} sel, tak sesuai kontrak tabel"


def test_kelas_kosong_tertulis_tanda_strip_di_tabel():
    """NaN harus sampai ke tabel sebagai '-'. Kalau sampai sebagai '0,00' pembaca
    mengira TPK kelas itu nol, bukan bahwa kelas itu tidak dilaporkan."""
    r = _isi()
    z = _zip(r)
    root = ET.fromstring(z.read("Stories/Story_uc98.xml"))
    sel = ["".join(c.itertext()).strip() for c in root.findall(".//Table/Cell")]
    # 13 sel header, lalu tiap baris 6 sel; baris ke-2 adalah "Bintang 2".
    mulai = 13 + 6
    baris_kosong = sel[mulai:mulai + 6]
    assert baris_kosong[0] == "Bintang 2", baris_kosong
    assert baris_kosong[1:] == ["-"] * 5, f"kelas kosong harus '-', dapat {baris_kosong[1:]}"


def test_daftar_dua_unsur_tanpa_koma_sebelum_dan():
    """Bahasa Indonesia: dua unsur ditulis 'A dan B'; koma sebelum 'dan' hanya
    untuk tiga unsur atau lebih. Kalimat ini masuk ke publikasi BPS."""
    from modules.indesign_pariwisata import _gabung, _sebut

    assert _gabung(["a", "b"]) == "a dan b"
    assert _gabung(["a", "b", "c"]) == "a, b, dan c"
    assert _gabung(["a"]) == "a"

    _, poin = _sebut([(1, -0.14), (4, -0.02)], "Hotel Non Bintang")
    assert poin == "0,14 dan 0,02", poin


def test_header_tabel_memuat_nama_indikator():
    """Regresi: nama indikator pernah ditulis terpisah lalu ditimpa string kosong
    oleh penulisan header, sehingga kolom "TPK (persen)" muncul kosong di publikasi."""
    r = _isi()
    z = _zip(r)
    for sid, (jenis, ind) in TABEL_SLOTS.items():
        root = ET.fromstring(z.read(f"Stories/Story_{sid}.xml"))
        sel = ["".join(c.itertext()).strip() for c in root.findall(".//Table/Cell")]
        assert sel[1], f"{sid} ({ind}) nama indikator kosong"
        assert sel[1] == LABEL_KOLOM[ind], f"{sid} nama indikator salah: {sel[1]!r}"


def test_sel_tidak_ada_yang_kosong():
    """Sel kosong berarti kelas tanpa data belum ditulis '-'."""
    r = _isi()
    z = _zip(r)
    for sid in TABEL_SLOTS:
        root = ET.fromstring(z.read(f"Stories/Story_{sid}.xml"))
        for i, cell in enumerate(root.findall(".//Table/Cell")):
            teks = "".join(cell.itertext()).strip()
            if i == 0:
                continue  # sel pertama adalah baris label kelas pertama
            assert teks, f"{sid} sel {i} kosong"


def _png_ukuran(blob):
    """(lebar, tinggi) dari header PNGIHDR — tanpa Pillow, tidak menambah dependensi."""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "bukan PNG"
    return struct.unpack(">II", blob[16:24])


def _link_uri(z):
    """Semua LinkResourceURI di Spreads/ yang sudah di-decode."""
    from urllib.parse import unquote
    uri = []
    for n in z.namelist():
        if not (n.startswith(("Spreads/", "MasterSpreads/")) and n.endswith(".xml")):
            continue
        root = ET.fromstring(z.read(n))
        for link in root.iter("Link"):
            u = link.get("LinkResourceURI")
            if u:
                uri.append(unquote(u))
    return uri


# --- Grafik ---------------------------------------------------------------
def test_empat_grafik_tertanam_di_paket():
    """Keempat frame grafik harus berisi gambar milik paket ini. Kalau <Link> masih
    menunjuk 'file:D:/BPS/...' gambar hilang (missing link) saat .idml dibuka.

    Nama file di Links/ WAJIB sama dengan nama yang dipakai META-INF/metadata.xml
    (<stRef:lastURL> berakhiran 'Links/TPK Series Hotel Bintang.png'). Kalau ekspor
    memberi nama sendiri, InDesign mencari nama yang tidak ada di paket dan link-nya
    hilang padahal filenya ada. Lihat test_nama_paket_sama_dengan_xmp.
    """
    z = _zip(_isi())
    di_paket = {n.rsplit("/", 1)[-1] for n in z.namelist() if n.startswith("Links/")}
    tertaut = {u.rsplit("/", 1)[-1] for u in _link_uri(z)}
    for token in TOKEN_GRAFIK.values():
        nama = f"{token}.png"
        assert nama in di_paket, f"grafik {token!r} tidak ada di folder Links/"
        assert nama in tertaut, f"<Link> {token!r} tidak diarahkan ke file dalam paket"
    # Foto dekoratif templat masih tertaut ke 'file:D:/BPS/...' — itu di luar
    # lingkup pekerjaan ini; yang wajib bersih adalah keempat frame grafik.
    for token in TOKEN_GRAFIK.values():
        assert not [u for u in _link_uri(z)
                    if u.lower().startswith("file:") and token.split()[0] in u], \
            f"grafik {token!r} masih tertaut ke luar paket"


def test_nama_paket_sama_dengan_xmp():
    """Nama aset di Links/ harus cocok persis dengan <stRef:lastURL> di
    META-INF/metadata.xml. Inilah yang membuat InDesign bisa menemukan gambarnya."""
    z = _zip(_isi())
    md = z.read("META-INF/metadata.xml").decode("utf-8", "replace")
    import re as _re
    from urllib.parse import unquote as _unquote
    dari_xmp = {_unquote(m) for m in _re.findall(r"<stRef:lastURL>([^<]+)</stRef:lastURL>", md)}
    dari_xmp = {u.rsplit("/", 1)[-1] for u in dari_xmp}
    di_paket = {n.rsplit("/", 1)[-1] for n in z.namelist() if n.startswith("Links/")}
    for token in TOKEN_GRAFIK.values():
        nama = f"{token}.png"
        assert nama in dari_xmp, f"template asli tak punya aset {nama!r}"
        assert nama in di_paket, f"{nama!r} di paket tapi nama XMP beda"


def test_rasio_gambar_sesuai_frame():
    """Frame templat lebar tetap / tinggi fleksibel: PNG dengan rasio lain akan
    mengubah tinggi frame dan menggeser isi halaman. Rasio wajib per token."""
    z = _zip(_isi())
    for n in z.namelist():
        if not n.startswith("Links/"):
            continue
        base = n.rsplit("/", 1)[-1]
        token = next((t for t in RASIO_GRAFIK
                      if base.startswith(t.replace(" ", "_"))), None)
        if not token:
            continue  # qrcode / infografis, bukan frame grafik
        w, h = _png_ukuran(z.read(n))
        assert abs(w / h - RASIO_GRAFIK[token]) < 0.02, \
            f"{token}: rasio {w}/{h} = {w / h:.3f}, harus ~{RASIO_GRAFIK[token]}"


def test_bulan_kosong_menjadi_celah_bukan_nol():
    """Bulan tanpa laporan harus jadi celah pada garis, bukan 0. Kalau jadi 0,
    pembaca menyimpulkan TPK bulan itu nol."""
    tren = DATA[("tpk", "Hotel Bintang")]["tren"]
    assert any(v is None or math.isnan(v) for _, _, v in tren), \
        "fixture harus punya bulan tanpa data"
    w, h = _png_ukuran(grafik_png(tren, "tpk", "TPK Series Hotel Bintang"))
    assert w > 2000, w
    assert grafik_png([(2026, m, None) for m in range(1, 9)], "tpk",
                      "TPK Series Hotel Bintang") is None, \
        "tren tanpa satu pun angka tidak boleh menghasilkan gambar"


def test_semua_judul_grafik_tertulis():
    """Regresi: 'uc6b' tidak ada di template, sehingga judul grafik TPK Hotel
    Bintang dilewati dan baru ketahuan saat publikasi terbit."""
    r = _isi()
    for sid in JUDUL_GRAFIK:
        assert _story(r, sid).strip(), f"{sid} kosong: judul grafik tidak tertulis"
    assert not [w for w in r.warnings if "tidak ditemukan" in w], r.warnings


def test_sumbu_x_mulai_dari_bulan_pertama_yang_ada_datanya():
    """all_data baru terisi beberapa bulan terakhir. Kalau sumbu X tetap dimulai
    Jan(tahun-1), garis terjepit di pojok kanan dan grafik terbaca kosong."""
    tren = [(2025, m, None) for m in range(1, 13)] + \
           [(2026, m, 40 + m) for m in (4, 5, 6)]
    assert grafik_png(tren, "tpk", "TPK Series Hotel Bintang") is not None

    # Zahl titik sumbu X = jumlah bulan sejak data pertama, bukan 18.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n_sumbu = {}
    asli = plt.Axes.set_xticks

    def sadap(self, ticks, *a, **k):
        n_sumbu["n"] = len(ticks)
        return asli(self, ticks, *a, **k)

    plt.Axes.set_xticks = sadap
    try:
        grafik_png(tren, "tpk", "TPK Series Hotel Bintang")
    finally:
        plt.Axes.set_xticks = asli
    assert n_sumbu["n"] == 3, f"sumbu X harus 3 bulan, dapat {n_sumbu['n']}"


def test_grafik_tanpa_data_memberi_peringatan_bukan_diam_diam():
    """Frame yang gagal diisi harus disebut. Diam-diam meninggalkan frame menunjuk
    gambar template membuat hasil ekspor terlihat sama saja dengan yang berhasil."""
    r = _isi(data={k: {"rows": v["rows"]} for k, v in DATA.items()})
    z = _zip(r)
    assert not [n for n in z.namelist() if n.startswith("Links/") and "Series" in n], \
        "tanpa data, tidak boleh ada PNG grafik di paket"
    pesan = [w for w in r.warnings if "Grafik tanpa data" in w]
    assert pesan, r.warnings
    for (_, jenis) in DATA:
        assert jenis in pesan[0], pesan[0]


def test_paket_zip_berisi_idml_dan_semua_png():
    """Tombol unduh harus memberi .zip: .idml + folder PNG/. Kalau PNG tidak ikut,
    pengguna tidak bisa placing manual saat InDesign gagal menautkan otomatis."""
    from modules.indesign_export import paket_zip
    r = _isi()
    z = _zip(r)
    png_di_idml = {n.rsplit("/", 1)[-1] for n in z.namelist()
                   if n.startswith("Links/") and n.lower().endswith(".png")}
    assert png_di_idml, "ekspor tidak menghasilkan PNG sama sekali"

    nama = "BRS_Pariwisata_Papua_Juni_2026.idml"
    zf = zipfile.ZipFile(io.BytesIO(paket_zip(r.idml_bytes, nama)))
    assert zf.testzip() is None
    isi = set(zf.namelist())
    assert nama in isi
    assert isi == {nama} | {f"PNG/{p}" for p in png_di_idml}, sorted(isi)
    for p in png_di_idml:
        assert zf.read(f"PNG/{p}") == z.read(f"Links/{p}"), f"isi PNG/{p} beda"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print(f"\n{sum(1 for n in globals() if n.startswith('test_'))} uji lolos")