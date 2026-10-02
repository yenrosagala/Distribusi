"""Isi template IDML BRS Pariwisata.

Mesin XML/zip (parse, tulis sel, tulis run, pasang gambar) sudah dimiliki
``modules.indesign_export``; modul ini tidak mengulang itu, hanya memetakan slot
pariwisata: 4 tabel kelas hotel (2 indikator x 2 jenis), 4 judul grafik,
paragraf narasi BPS, judul halaman, dan nomor BRS.

Sumber angka adalah tabel ``all_data`` — satu baris per (provinsi, jenis, kelas,
tahun, bulan), sudah teragregasi di tingkat provinsi, jadi tidak perlu SUM.

Struktur paragraf template (hasil inspeksi, bukan asumsi):
  * judul tabel/heading -> satu ``ParagraphStyleRange`` gaya ``Sub Bab BRS``
  * paragraf narasi     -> ``Body Text BRS`` diawali tab, satu segmen
  * ringkasan           -> ``ub99`` 2 segmen (TPK, RLMT) dalam satu paragraf
  * ringkasan utama     -> ``uc40`` 2 segmen (TPK, RLMT) dalam satu paragraf
  * ``ufd7``/``uce8``    -> definisi statis + narasi menyatu dalam satu segmen,
    jadi ditulis dari teks jangkar agar definisinya tetap utuh.
"""
import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from modules.indesign_export import (
    ABBR,
    ExportResult,
    _dump,
    _embed_image,
    _parse,
    _prev_period,
    _segments,
    _seg_text,
    _set_cell_text,
    fmt_id,
    replace_period_text,
    set_segments,
)

DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "BRS PARIWISATA PAPUA Template.idml"
)

BULAN = list(ABBR.keys())
BULAN_N = {n: i + 1 for i, n in enumerate(BULAN)}

TPL_PROV_TOKENS = ("Papua Selatan", "Papua Tengah")

JENIS = ("Hotel Bintang", "Hotel Non Bintang")
INDIKATOR = ("tpk", "rlmtgab")
SATUAN = {"tpk": "persen", "rlmtgab": "hari"}
SINGKATAN = {"tpk": "TPK", "rlmtgab": "RLMT"}
SINGKATAN_PANJANG = {
    "tpk": "Tingkat Penghunian Kamar (TPK)",
    "rlmtgab": "Rata-rata Lama Menginap Tamu (RLMT)",
}
LABEL_KOLOM = {"tpk": "TPK (persen)", "rlmtgab": "Rata-rata Lama Menginap Tamu  (Hari)"}
KELAS_TEMPLATE = (1, 2, 3, 4)
ANGKA_KELAS = {1: "satu", 2: "dua", 3: "tiga", 4: "empat"}
# BPS menyebut kelas hotel bintang sebagai "kelas bintang satu", dll.
BATASAN_KELAS = {"Hotel Bintang": "kelas bintang", "Hotel Non Bintang": "kelas"}
SEL_TIDAK_ADA = "-"  # BPS menulis tanda hubung, bukan "Undefined", untuk data kosong

TABEL_SLOTS = {
    "uc98": ("Hotel Bintang", "tpk"),
    "ud2a": ("Hotel Bintang", "rlmtgab"),
    "ud82": ("Hotel Non Bintang", "tpk"),
    "ue1d": ("Hotel Non Bintang", "rlmtgab"),
}
JUDUL_GRAFIK = {
    # uc6c, bukan uc6b: story itu tidak ada di template sehingga judulnya
    # sebelumnya dilewati diam-diam.
    "uc6c": ("Hotel Bintang", "tpk"),
    "ucfe": ("Hotel Bintang", "rlmtgab"),
    "ud56": ("Hotel Non Bintang", "tpk"),
    "udf1": ("Hotel Non Bintang", "rlmtgab"),
}
# Nama file gambar yang di-<Link> di template, dipisah dari JUDUL_GRAFIK karena
# yang ini harus persis sama dengan nama di Links/ (lolos URL-encode).
TOKEN_GRAFIK = {
    ("tpk", "Hotel Bintang"): "TPK Series Hotel Bintang",
    ("rlmtgab", "Hotel Bintang"): "RLMT Series Hotel Bintang",
    ("tpk", "Hotel Non Bintang"): "TPK Series Hotel Non Bintang",
    ("rlmtgab", "Hotel Non Bintang"): "RLMT Series Hotel Non Bintang",
}
# Rasio bidang gambar (Lebar/Tinggi) dari GraphicBounds tiap frame. Penting:
# frame-nya "FixedDimension" pada lebar dan "FlexibleDimension" pada tinggi, jadi
# PNG dengan rasio berbeda akan mengubah tinggi frame dan menggeser isi halaman.
RASIO_GRAFIK = {
    "TPK Series Hotel Bintang": 2.229,
    "RLMT Series Hotel Bintang": 2.150,
    "TPK Series Hotel Non Bintang": 2.189,
    "RLMT Series Hotel Non Bintang": 2.041,
}
# Narasi pembanding bulan sebelumnya. ``jangkar`` dipakai kalau paragrafnya
# menyatu definisi statis dengan angka (see GLOSARIUM_TPK).
NARASI_BULAN_INI = {
    "ud40": ("Hotel Non Bintang", "tpk", None),
    "uddb": ("Hotel Non Bintang", "rlmtgab", None),
    "uce8": ("Hotel Bintang", "rlmtgab", "RLMT Hotel Bintang pada bulan"),
}
# Narasi pembanding bulan sama tahun sebelumnya.
NARASI_TAHUN_LALU = {
    "uc82": ("Hotel Bintang", "tpk"),
    "ud14": ("Hotel Bintang", "rlmtgab"),
    "ud6c": ("Hotel Non Bintang", "tpk"),
    "ue07": ("Hotel Non Bintang", "rlmtgab"),
}
RINGKASAN = "ub99"
RINGKASAN_UTAMA = "uc40"
KETERANGAN_GRAFIK = "udc5"
JUDUL_HALAMAN = "u111a"
GLOSARIUM_TPK = "ufd7"


# ---------------------------------------------------------------------------
# Angka
# ---------------------------------------------------------------------------
def angka(v, desimal=2):
    """Angka gaya BPS: 21,55 / 1.234,56, dan '-' untuk data tidak tersedia."""
    if v is None or pd.isna(v):
        return SEL_TIDAK_ADA
    return fmt_id(v, decimals=desimal)


def arah(v, cap=False):
    """'naik' / 'turun' / 'tidak berubah'; None bila pembanding tidak ada."""
    if v is None or pd.isna(v):
        return None
    kata = "naik" if v > 0 else ("turun" if v < 0 else "tidak berubah")
    return kata.capitalize() if cap else kata


def _ada(a, b):
    return not (pd.isna(a) or pd.isna(b))


def label_kelas(jenis):
    return "Bintang" if jenis == "Hotel Bintang" else "Kelas"


def total_kelas(rows):
    """Rata-rata tidak berbobot dari kelas yang tersedia pada tiap periode.

    Total resmi BPS dibobot jumlah kamar; jumlah kamar tidak ada di all_data,
    jadi angka ini perkiraan dan harus diverifikasi manual di InDesign.
    """
    def mean(vals):
        vals = [v for v in vals if not pd.isna(v)]
        return float(np.mean(vals)) if vals else np.nan

    ly = mean([r[0] for r in rows.values()])
    prev = mean([r[1] for r in rows.values()])
    cur = mean([r[2] for r in rows.values()])
    return ly, prev, cur, (cur - prev if _ada(cur, prev) else np.nan), (cur - ly if _ada(cur, ly) else np.nan)


def grafik_png(tren, ind, token, lebar_px=2130, dpi=200):
    """PNG garis tren untuk satu frame grafik di template.

    Tanpa judul: judul grafik sudah menjadi frame teks tersendiri di template
    (JUDUL_GRAFIK), jadi mengulanginya di dalam gambar akan tercetak dua kali.
    Sumbu Y memakai satuan BPS, dan bulan tanpa data memutus garis (NaN), bukan 0.

    Sumbu X dipangkas mulai bulan pertama yang punya data. all_data belum terisi
    penuh (hanya beberapa bulan terakhir), jadi sumbu Jan(tahun-1)..bulan berjalan
    akan membuat garis terjepit di pojok kanan dan grafik terbaca kosong.
    Celah di tengah rentang yang terpangkas tetap ditampilkan sebagai jeda.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tren = list(tren or [])
    isi = [i for i, (_, _, v) in enumerate(tren) if not pd.isna(v)]
    if not isi:
        return None
    tren = tren[isi[0]:]
    # Periode kosong di tengah tetap jadi titik sumbu X supaya celah data terlihat
    # sebagai jeda waktu dan bukan bulan yang hilang.
    label = [f"{ABBR[BULAN[m - 1]]} {t % 100}" for t, m, _ in tren]
    nilai = [v for _, _, v in tren]
    x = list(range(len(tren)))
    akhir = max(i for i, v in enumerate(nilai) if not pd.isna(v))

    tinggi = lebar_px / RASIO_GRAFIK[token]
    fig, ax = plt.subplots(figsize=(lebar_px / dpi, tinggi / dpi), dpi=dpi)
    ax.plot(x, nilai, color="#1F4E79", linewidth=2.0, marker="o", markersize=4.5,
            markerfacecolor="white", markeredgewidth=1.6, zorder=3)
    # bulan berjalan ditebalkan: titik terakhir adalah angka yang dipublikasikan
    ax.plot([x[akhir]], [nilai[akhir]], color="#C00000", marker="o", markersize=8,
            zorder=4)
    ax.annotate(angka(nilai[akhir]), (x[akhir], nilai[akhir]),
                textcoords="offset points", xytext=(-6, 10), ha="right",
                fontsize=11, color="#C00000", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(label, fontsize=10, color="#333333")
    ax.set_ylabel(f"{SINGKATAN[ind]} ({SATUAN[ind]})", fontsize=11, color="#333333")
    ax.yaxis.grid(True, color="#D9D9D9", linewidth=0.8)
    ax.set_axisbelow(True)
    for sisi in ("top", "right"):
        ax.spines[sisi].set_visible(False)
    for sisi in ("left", "bottom"):
        ax.spines[sisi].set_color("#808080")
    ax.margins(x=0.01, y=0.16)
    fig.tight_layout(pad=0.6)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def deret_tabel(blok, jenis):
    """Baris tabel: (label, thn_lalu, bln_lalu, skrg, d_bln_lalu, d_thn_lalu)."""
    label = label_kelas(jenis)
    rows = blok["rows"]
    keluar = []
    for k in KELAS_TEMPLATE:
        ly, prev, cur = rows.get(k, (np.nan, np.nan, np.nan))
        keluar.append((
            f"{label} {k}", ly, prev, cur,
            cur - prev if _ada(cur, prev) else np.nan,
            cur - ly if _ada(cur, ly) else np.nan,
        ))
    keluar.append((f"Total {jenis.replace('Hotel ', '')}", *total_kelas(rows)))
    return keluar


# ---------------------------------------------------------------------------
# Pengambilan data
# ---------------------------------------------------------------------------
def kumpulkan_kelas(engine, prov, thn, bln, tabel="all_data"):
    """Nilai tiap kelas hotel untuk 3 periode, per indikator.

    Mengembalikan ``{(indikator, jenis): {'rows': {kelas: (thn_lalu, bln_lalu, skrg)}}}``.
    Nilai yang tidak ada pada periode tertentu tetap ``None`` — bukan 0, karena
    TPK/RLMT adalah rata-rata dan "tidak ada hotel" bukan "0% tingkat penghunian".
    """
    prev_bln, prev_thn = _prev_period(bln, int(thn))
    bln_m = BULAN_N[bln]
    q = text(f"""
        SELECT jenis_akomodasi, kelas_akomodasi, tpk, rlmtgab, year, month
        FROM {tabel}
        WHERE TRIM(CAST(kd_prov AS TEXT)) = :prov
          AND (  (year = :thn      AND month = :bln)
                OR (year = :prev_thn AND month = :prev_bln)
                OR (year = :last_thn AND month = :bln)
                -- rentang Jan(thn-1)..bulan berjalan untuk garis grafik
                OR (year = :thn      AND month <= :bln_m)
                OR (year = :last_thn AND month <= :bln_m) )
    """)
    df = pd.read_sql_query(
        q, engine,
        params={
            "prov": str(prov).strip(), "thn": int(thn), "bln": bln_m,
            "prev_thn": int(prev_thn), "prev_bln": BULAN_N[prev_bln],
            "last_thn": int(thn) - 1, "bln_m": bln_m,
        },
    )
    keluar = {}
    if df.empty:
        return keluar

    for ind in INDIKATOR:
        for jenis in JENIS:
            sub = df[df["jenis_akomodasi"] == jenis]
            if sub.empty:
                continue
            kelas = sorted(
                {int(k) for k in pd.to_numeric(sub["kelas_akomodasi"], errors="coerce").dropna()}
            )
            keluar[(ind, jenis)] = {
                "rows": {
                    k: (
                        _nilai(sub, k, ind, int(thn) - 1, bln_m),
                        _nilai(sub, k, ind, int(prev_thn), BULAN_N[prev_bln]),
                        _nilai(sub, k, ind, int(thn), bln_m),
                    ) for k in kelas
                },
                "tren": _tren(sub, kelas, ind, int(thn), bln_m),
            }
    return keluar


def _tren(sub, kelas, kolom, thn, bln_m):
    """Deret bulanan Jan(thn-1)..bulan berjalan, rata-rata tidak berbobot kelas.

    Basisnya sama dengan baris Total di tabel (lihat ``total_kelas``) supaya angka
    pada grafik sama dengan tabel. Bulan tanpa data bernilai NaN, bukan 0 — garis
    putus di bulan itu, bukan menyentuh sumbu nol.
    """
    keluar = []
    for thn_ in (thn - 1, thn):
        akhir = bln_m if thn_ == thn else 12
        for m in range(1, akhir + 1):
            vals = [_nilai(sub, k, kolom, thn_, m) for k in kelas]
            vals = [v for v in vals if not pd.isna(v)]
            keluar.append((thn_, m, float(np.mean(vals)) if vals else np.nan))
    return keluar


def _nilai(sub, kelas, kolom, thn, bln):
    f = sub[(sub.year == thn) & (sub.month == bln)]
    if f.empty:
        return np.nan
    f = f[pd.to_numeric(f["kelas_akomodasi"], errors="coerce") == kelas]
    seri = pd.to_numeric(f[kolom], errors="coerce")
    return float(seri.iloc[0]) if len(seri) else np.nan


# ---------------------------------------------------------------------------
# Narasi gaya BPS
# ---------------------------------------------------------------------------
def _pisah(daftar):
    return [x for x in daftar if x[1] > 0], [x for x in daftar if x[1] < 0]


def _ubah(rows, ke):
    """[(kelas, selisih poin)] kelas yang punya nilai pada periode pembanding."""
    out = []
    for k, nilai in sorted(rows.items()):
        if _ada(nilai[2], nilai[ke]):
            out.append((k, nilai[2] - nilai[ke]))
    return out


def _gabung(items):
    """Daftar bahasa Indonesia: dua unsur 'A dan B', tiga unsur atau lebih
    'A, B, dan C' — koma sebelum 'dan' tidak dipakai untuk dua unsur."""
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return " dan ".join(items)
    return ", ".join(items[:-1]) + f", dan {items[-1]}"


def _sebut(daftar, jenis):
    """'kelas satu, kelas dua, dan kelas tiga' + '0,14 poin, 0,43 poin, dan 0,44 poin'."""
    batas = BATASAN_KELAS[jenis]
    vals = [(k, v) for k, v in daftar if not pd.isna(v)]
    kata = [f"{batas} {ANGKA_KELAS.get(k, k)}" for k, _ in vals]
    poin = [angka(abs(v)) for _, v in vals]
    if not kata:
        return None, None
    return _gabung(kata), _gabung(poin)


def kalimat_kelas(rows, jenis, ind, ke):
    """'Penurunan TPK terjadi pada kelas satu dan kelas dua sebesar 1,89 poin dan 3,44 poin.'"""
    naik, turun = _pisah(_ubah(rows, ke))
    sing = SINGKATAN[ind]
    kalimat = []
    for daftar, kata in ((turun, "Penurunan"), (naik, "Peningkatan")):
        if not daftar:
            continue
        sebut, poin = _sebut(daftar, jenis)
        kalimat.append(
            f"{kata} {sing} terjadi pada {sebut} "
            f"{'yaitu masing-masing ' if len(daftar) > 1 else ''}sebesar {poin} poin."
        )
    if len(kalimat) == 2:
        return " ".join(kalimat[:1]) + f" Sementara {kalimat[1][0].lower()}{kalimat[1][1:]}"
    if kalimat:
        return kalimat[0]
    batas = BATASAN_KELAS[jenis]
    semua = _gabung([f"{batas} {ANGKA_KELAS[k]}" for k in KELAS_TEMPLATE])
    return f"Sementara pada {semua} tidak terjadi perubahan."


def kalimat_bln_ini(rows, jenis, ind, prov, bln, thn, prev_bln, prev_thn):
    """'… pada Juli 2026 mencapai 21,55 persen atau turun sebesar 1,79 poin dibanding Juni 2026 yang sebesar 23,34 persen.'"""
    satuan = SATUAN[ind]
    _, prev, cur, d_prev, _ = total_kelas(rows)
    if not _ada(cur, prev):
        return (f"{SINGKATAN_PANJANG[ind]} {jenis} di {prov} pada periode {bln} {thn} "
                "tidak tersedia.")
    return (
        f"{SINGKATAN_PANJANG[ind]} {jenis} di {prov} pada periode {bln} {thn} mencapai "
        f"{angka(cur)} {satuan} atau {arah(d_prev)} sebesar {angka(abs(d_prev))} poin "
        f"dibanding bulan {prev_bln} {prev_thn} yang sebesar {angka(prev)} {satuan}. "
        + kalimat_kelas(rows, jenis, ind, 1)
    )


def kalimat_bln_lalu(rows, jenis, ind, bln, thn):
    """'Jika dibandingkan bulan yang sama pada tahun sebelumnya yang selama 2,22 hari, maka RLMT Juli 2026 turun sebesar 0,59 poin menjadi 1,63 hari.'"""
    satuan = SATUAN[ind]
    ly, _, cur, _, d_ly = total_kelas(rows)
    if not _ada(cur, ly):
        return (f"Jika dibandingkan dengan bulan yang sama pada tahun sebelumnya, "
                f"{SINGKATAN[ind]} {jenis} pada {bln} {thn} tidak tersedia.")
    return (
        f"Jika dibandingkan dengan bulan yang sama pada tahun sebelumnya yang "
        f"{'selama' if satuan == 'hari' else 'sebesar'} {angka(ly)} {satuan}, maka "
        f"{SINGKATAN[ind]} {bln} {thn} {arah(d_ly)} sebesar {angka(abs(d_ly))} poin "
        f"menjadi {angka(cur)} {satuan}. " + kalimat_kelas(rows, jenis, ind, 0)
    )


# ---------------------------------------------------------------------------
# Penulisan XML
# ---------------------------------------------------------------------------
def _psr_akar(root, gaya=None):
    """``ParagraphStyleRange`` pertama yang gayanya cocok (atau apa pun)."""
    if root is None:
        return None
    for psr in root.find("Story").findall("ParagraphStyleRange"):
        if gaya is None or (psr.get("AppliedParagraphStyle") or "").endswith(gaya):
            return psr
    return None


def _tulis(psr, texts, warnings, sid, tab=False, tertulis=None):
    """Tulis satu teks per segmen paragraf.

    Memakai ``set_segments`` (bukan tulis ke satu ``Content``) karena satu segmen
    bisa berpecah jadi banyak ``Content`` ber-CharacterStyleRange — menulis satu
    elemen saja meninggalkan sisa teks template lama di sibling-nya. Jumlah teks
    harus persis sama dengan jumlah segmen; kalau tidak, slot dilewati dan
    dilaporkan, bukan dipaksakan.
    """
    if psr is None:
        warnings.append(f"{sid}: paragraf dengan gaya yang diminta tidak ditemukan — dilewati.")
        return
    segs = [s for s in _segments(psr) if _seg_text(s).strip()]
    if len(segs) != len(texts):
        warnings.append(f"{sid}: jumlah segmen tidak cocok (butuh {len(texts)}, "
                        f"template punya {len(segs)}) — teks template dibiarkan utuh.")
        return
    set_segments(psr, [("\t" + t if tab and t else t) for t in texts], warnings, sid)
    if tertulis is not None:
        tertulis.add(sid)


def _isi_tabel(tbl, deret, bln, thn, prev_bln, prev_thn, ind):
    """Tabel BPS: 13 sel header, lalu tiap baris 6 sel (label + 5 angka).

    Header ditulis lengkap di sini — termasuk nama indikator di sel 1. Kalau
    sel 1 diisi terpisah di luar fungsi ini, penulisan header berikut akan
    menimpanya dengan string kosong dan kolom "TPK (Persen)" jadi kosong.
    """
    cells = tbl.findall("Cell")
    if len(cells) != 13 + 6 * len(deret):
        return False
    header = [
        "Kelas Hotel", LABEL_KOLOM[ind],
        f"Perubahan {bln} {thn} thd {prev_bln} {prev_thn} (poin)",
        f"Perubahan {bln} {thn} thd {bln} {int(thn) - 1} (poin)",
        f"{bln} {int(thn) - 1}", f"{prev_bln} {prev_thn}", f"{bln} {thn}",
        "(1)", "(2)", "(3)", "(4)", "(5)", "(6)",
    ]
    for c, t in zip(cells[:13], header):
        _set_cell_text(c, t)
    for i, baris in enumerate(deret):
        off = 13 + i * 6
        label, *vals = baris
        _set_cell_text(cells[off], label)
        for j, v in enumerate(vals):
            _set_cell_text(cells[off + 1 + j], angka(v))
    return True


def _tulis_setelah_jangkar(psr, anchor, teks_baru, warnings, sid, tertulis=None):
    """Tulis ulang segmen dari `anchor`, pertahankan teks statis di depannya.

    Dipakai untuk ``ufd7``/``uce8``: paragraf glosarium menyatukan definisi statis
    dengan angka periode, jadi definisinya harus tetap utuh sementara bagian
    angkanya diganti. Satu segmen ditulis utuh via ``set_segments`` supaya run
    bersisa yang masih memegang "Juli 2026" ikut hilang.
    """
    if psr is None:
        warnings.append(f"{sid}: paragraf glosarium tidak ditemukan — dilewati.")
        return
    segs = [s for s in _segments(psr) if _seg_text(s).strip()]
    if len(segs) != 1:
        warnings.append(f"{sid}:(glosarium) punya {len(segs)} segmen, deklaratif 1 — dilewati.")
        return
    teks = _seg_text(segs[0])
    i = teks.find(anchor)
    if i < 0:
        warnings.append(f"{sid}: teks jangkar '{anchor}' tidak ditemukan — dibiarkan.")
        return
    set_segments(psr, [teks[:i] + teks_baru], warnings, sid)
    if tertulis is not None:
        tertulis.add(sid)


def fill_brs_pariwisata_template(template_bytes, prov, thn, bln, data,
                                 nomor_brs=None, tanggal_rilis=None,
                                 qr_bytes=None, qr_name=None,
                                 infografis_bytes=None, infografis_name=None):
    """Isi template BRS Pariwisata dengan data dari :func:`kumpulkan_kelas`."""
    prev_bln, prev_thn = _prev_period(bln, int(thn))
    with zipfile.ZipFile(io.BytesIO(template_bytes)) as zf:
        order = zf.namelist()
        files = {n: zf.read(n) for n in order}

    warnings = []
    stories = {n.split("/")[-1][len("Story_"):-len(".xml")]: n
               for n in order if n.startswith("Stories/Story_") and n.endswith(".xml")}
    roots = {sid: _parse(files[n]) for sid, n in stories.items()}

    # 0) Periode, provinsi, nomor BRS — sapu menyeluruh DULUAN, sebelum slot
    #    dinamis ditulis. Urutan ini penting: kalau sapu dijalankan belakangan,
    #    ia akan mengganti "Juli 2026" yang barusan kita tulis menjadi bulan
    #    target. Sapu dulu aman karena slot dinamis menimpanya sepenuhnya.
    peta = {
        "Juli 2026": f"{bln} {thn}", "Julii 2026": f"{bln} {thn}",
        "Juni 2026": f"{prev_bln} {prev_thn}", "Juli 2025": f"{bln} {int(thn) - 1}",
        "Januari 2025": f"Januari {int(thn) - 1}",
    }
    peta.update({tok: prov for tok in TPL_PROV_TOKENS})
    if nomor_brs:
        peta["218/09/94/Th. XXIX"] = nomor_brs
        peta["69/03/94/Th. XXIX"] = nomor_brs
    if tanggal_rilis:
        peta[", 1  September 2026"] = f", {tanggal_rilis}"
        peta[", 1 September 2026"] = f", {tanggal_rilis}"
        peta[", 2 Maret 2026"] = f", {tanggal_rilis}"
        peta["1 September 2026"] = tanggal_rilis
    for root in roots.values():
        replace_period_text(root, peta)

    # Sidik jari: story mana yang memuat angka gaya BPS (2 desimal), yaitu story
    # yang WAJIB tertulisi. Dipakai sebagai cakupan, bukan pembanding nilai —
    # membandingkan angka lama vs baru tidak berguna karena nilai baru bisa saja
    # kebetulan sama dengan nilai template.
    bearer = {sid for sid, root in roots.items()
              if re.search(r"\d+,\d{2}", "".join(root.itertext()))}
    tertulis = set()

    # 1) Tabel + judul tabel
    terisi = 0
    for sid, (jenis, ind) in TABEL_SLOTS.items():
        root, blok = roots.get(sid), data.get((ind, jenis))
        if root is None or not blok:
            warnings.append(f"Tabel {sid} ({ind} {jenis}) dilewati — data atau story tidak ada.")
            continue
        tbl = root.find("Story").find(".//Table")
        if tbl is None:
            warnings.append(f"{sid}: tidak ada <Table> — dilewati.")
            continue
        _tulis(_psr_akar(root, "Judul Tabel BRS"),
               [f"{SINGKATAN_PANJANG[ind]} {jenis} di {prov}, {bln} {thn}"],
               warnings, sid, tertulis=tertulis)
        if _isi_tabel(tbl, deret_tabel(blok, jenis), bln, thn, prev_bln, prev_thn, ind):
            terisi += 1
            tertulis.add(sid)
        else:
            warnings.append(f"{sid}: jumlah sel template tidak cocok dengan "
                            f"{len(KELAS_TEMPLATE)} kelas — tabel tak terisi.")

    # 2) Judul grafik + keterangan infografis
    for sid, (jenis, ind) in JUDUL_GRAFIK.items():
        _tulis(_psr_akar(roots.get(sid), "Judul Gambar BRS"), [
            f"Perkembangan {SINGKATAN_PANJANG[ind].split(' (')[0]} {jenis} di {prov}, "
            f"Januari {int(thn) - 1} - {bln} {thn}"
        ], warnings, sid, tertulis=tertulis)
    _tulis(_psr_akar(roots.get(KETERANGAN_GRAFIK), "Judul Gambar BRS"),
           [f"Infografis TPK dan RLMT {prov} {bln} {thn}"], warnings, KETERANGAN_GRAFIK,
           tertulis=tertulis)

    # 3) Narasi pembanding bulan sebelumnya
    for sid, (jenis, ind, jangkar) in NARASI_BULAN_INI.items():
        root, blok = roots.get(sid), data.get((ind, jenis))
        if root is None or not blok:
            continue
        kalimat = kalimat_bln_ini(blok["rows"], jenis, ind, prov, bln, thn, prev_bln, prev_thn)
        if jangkar:
            _tulis_setelah_jangkar(_psr_akar(root, "Body Text BRS"), jangkar, kalimat,
                                   warnings, sid, tertulis=tertulis)
        else:
            _tulis(_psr_akar(root, "Body Text BRS"), [kalimat], warnings, sid,
                   tab=True, tertulis=tertulis)

    # 4) Narasi pembanding bulan sama tahun sebelumnya
    for sid, (jenis, ind) in NARASI_TAHUN_LALU.items():
        root, blok = roots.get(sid), data.get((ind, jenis))
        if root is None or not blok:
            continue
        _tulis(_psr_akar(root, "Body Text BRS"),
               [kalimat_bln_lalu(blok["rows"], jenis, ind, bln, thn)], warnings, sid,
               tab=True, tertulis=tertulis)

    # 5) Ringkasan (2 segmen per paragraf)
    _tulis(_psr_akar(roots.get(RINGKASAN)), _ringkasan(data, bln, thn), warnings, RINGKASAN,
           tertulis=tertulis)
    _tulis(_psr_akar(roots.get(RINGKASAN_UTAMA)),
           _ringkasan_utama(data, bln, thn, prev_bln, prev_thn), warnings, RINGKASAN_UTAMA,
           tertulis=tertulis)

    # 6) Judul halaman: "Perkembangan Pariwisata " + "Provinsi X, Bln Thn"
    _tulis(_psr_akar(roots.get(JUDUL_HALAMAN), "Judul BRS"),
           ["Perkembangan Pariwisata ", f"Provinsi {prov}, {bln} {thn}"],
           warnings, JUDUL_HALAMAN, tertulis=tertulis)

    # 7) Narasi TPK bintang yang menyatu dengan glosarium
    root, blok = roots.get(GLOSARIUM_TPK), data.get(("tpk", "Hotel Bintang"))
    if root is not None and blok:
        _tulis_setelah_jangkar(
            _psr_akar(root), "TPK Hotel Bintang",
            kalimat_bln_ini(blok["rows"], "Hotel Bintang", "tpk", prov, bln, thn,
                            prev_bln, prev_thn), warnings, GLOSARIUM_TPK, tertulis=tertulis)

    # 8) Cakupan slot — jangan diamkan. Setiap story yang memuat angka gaya BPS
    #    (dua desimal) WAJIB sudah tertulisi; kalau ada yang tidak, story itu
    #    masih menampilkan angka publikasi lama. Memperbandingkan angka lama vs
    #    baru tidak berguna karena nilai baru bisa kebetulan sama dengan nilai
    #    template, jadi yang dicek adalah "apakah slot-nya sempat ditulis".
    belum = sorted(bearer - tertulis)
    if belum:
        warnings.append(f"{len(belum)} story memuat angka BPS tapi slot-nya tidak "
                        f"tertulis (masih angka publikasi lama): {', '.join(belum)}")

    # 9) Nama provinsi template & salah ketik footer tidak boleh tersisa.
    for sid, root in roots.items():
        teks = "".join(root.itertext())
        sisa_prov = [t for t in TPL_PROV_TOKENS if t != prov and t in teks]
        if sisa_prov:
            warnings.append(f"{sid}: nama provinsi template belum terganti "
                            f"({', '.join(sisa_prov)}).")
        if "Julii" in teks:
            warnings.append(f"{sid}: salah ketik 'Julii' belum terganti.")

    if terisi < len(TABEL_SLOTS):
        warnings.append(f"Hanya {terisi}/{len(TABEL_SLOTS)} tabel yang terisi — periksa "
                        "ketersediaan data kelas hotel untuk indikator/jenis ini.")
    warnings.append("Baris Total dihitung sebagai rata-rata tidak berbobot antar kelas. Total "
                    "resmi BPS dibobot jumlah kamar, yang tidak ada di all_data — "
                    "verifikasi angka Total di InDesign.")
    warnings.append("Tanda (*) RSE >25% tidak dihitung karena all_data tidak menyimpan RSE — "
                    "baris kaki tabel dibiarkan, tapi nilai tidak diberi bintang.")
    kosong, tipis = [], []
    for (ind, jenis), token in TOKEN_GRAFIK.items():
        blok = data.get((ind, jenis))
        tren = (blok or {}).get("tren") or []
        n = sum(1 for _, _, v in tren if not pd.isna(v))
        png = grafik_png(tren, ind, token)
        if png:
            _embed_image(files, order, png, f"{token}.png", prov, thn, bln,
                         token, f"Grafik {SINGKATAN[ind]} {jenis}", warnings,
                         nama_paket=f"{token}.png")
        else:
            kosong.append(f"{SINGKATAN[ind]} {jenis}")
        if 0 < n < 6:
            tipis.append(f"{SINGKATAN[ind]} {jenis} ({n} bulan)")
    if kosong:
        warnings.append(
            "Grafik tanpa data bulanan, frame dibiarkan menunjuk gambar template: "
            + ", ".join(kosong) + ".")
    if tipis:
        warnings.append(
            "Grafik hanya punya sedikit bulan data sehingga garisnya pendek: "
            + ", ".join(tipis) + ". Data bulanan di all_data belum lengkap.")
    if qr_bytes:
        _embed_image(files, order, qr_bytes, qr_name, prov, thn, bln, "qrcode", "QR code", warnings)
    if infografis_bytes:
        _embed_image(files, order, infografis_bytes, infografis_name, prov, thn, bln,
                     "Infografis", "Infografis", warnings)
    warnings.append("Setelah dibuka di InDesign, cek teks overset (tanda + merah) dan perataan "
                    "halaman; panjang narasi berbeda dari template.")

    for sid, root in roots.items():
        files[stories[sid]] = _dump(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), files["mimetype"],
                      compress_type=zipfile.ZIP_STORED)
        for n in order:
            if n == "mimetype" or n.endswith("/"):
                continue
            zout.writestr(n, files[n], compress_type=zipfile.ZIP_DEFLATED)
    return ExportResult(idml_bytes=buf.getvalue(), warnings=warnings)


# ---------------------------------------------------------------------------
# Ringkasan gabungan
# ---------------------------------------------------------------------------
def _ringkasan(data, bln, thn):
    """ub99: tepat 2 segmen — [TPK kedua jenis, RLMT kedua jenis]."""
    out = []
    for ind in INDIKATOR:
        nilai = []
        for jenis in JENIS:
            blok = data.get((ind, jenis))
            if blok:
                _, _, cur, _, _ = total_kelas(blok["rows"])
                nilai.append((jenis.replace("Hotel ", ""), angka(cur)))
        satuan = SATUAN[ind]
        if not nilai:
            out.append("")
        else:
            awal, *sisa = nilai
            out.append(
                f"{SINGKATAN[ind]} Hotel {awal[0]} selama {bln} {thn} mencapai "
                f"{awal[1]} {satuan}"
                + "".join(f" dan Hotel {n} mencapai {v} {satuan}" for n, v in sisa)
                + "."
            )
    return out


def _ringkasan_utama(data, bln, thn, prev_bln, prev_thn):
    """uc40: [TPK bintang+non+YoY, RLMT bintang+non]."""
    tpk = [data.get(("tpk", j)) for j in JENIS]
    rlmt = [data.get(("rlmtgab", j)) for j in JENIS]
    if not all(tpk + rlmt):
        return ["", ""]

    def tpk_segmen():
        _, pb, cb, db, dly_b = total_kelas(tpk[0]["rows"])
        _, pn, cn, dn, dly_n = total_kelas(tpk[1]["rows"])
        return (
            f"{SINGKATAN_PANJANG['tpk']} Hotel Bintang selama {bln} {thn} mencapai {angka(cb)} "
            f"persen atau {arah(db)} sebesar {angka(abs(db))} poin dibanding {prev_bln} "
            f"{prev_thn} yang sebesar {angka(pb)} persen. Sementara TPK Hotel Non Bintang "
            f"mencapai {angka(cn)} persen atau {arah(dn)} sebesar {angka(abs(dn))} poin "
            f"dibanding {prev_bln} {prev_thn} yang sebesar {angka(pn)} persen. Apabila "
            f"dibandingkan dengan bulan yang sama pada tahun sebelumnya, TPK Hotel Bintang "
            f"{arah(dly_b)} sebesar {angka(abs(dly_b))} poin dan TPK Hotel Non Bintang "
            f"{arah(dly_n)} sebesar {angka(abs(dly_n))} poin."
        )

    def rlmt_segmen():
        _, prb, crb, drb, _ = total_kelas(rlmt[0]["rows"])
        _, prn, crn, drn, _ = total_kelas(rlmt[1]["rows"])
        return (
            f"{SINGKATAN_PANJANG['rlmtgab']} Hotel Bintang pada {bln} {thn} mencapai "
            f"{angka(crb)} hari atau {arah(drb)} sebesar {angka(abs(drb))} poin dibanding "
            f"bulan {prev_bln} {prev_thn} yang selama {angka(prb)} hari. Sementara RLMT "
            f"Hotel Non Bintang pada {bln} {thn} mencapai {angka(crn)} hari atau "
            f"{arah(drn)} sebesar {angka(abs(drn))} poin dibandingkan {prev_bln} {prev_thn} "
            f"yang sebesar {angka(prn)} hari."
        )

    return [tpk_segmen(), rlmt_segmen()]
