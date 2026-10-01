"""
Isi template InDesign BRS (format .idml) dari hasil Laporan Komparatif Strategis.

Modul ini sengaja TIDAK mengimpor streamlit / report_page supaya bisa diuji
sendiri. Pemanggil (report_page.py) menyerahkan data yang sudah dihitung:

    result = fill_brs_template(
        template_bytes=<isi file .idml>,
        prov="Papua Tengah", thn="2026", bln="Juni",
        items=all_report_data,            # keluaran prepare_table_item(), + p1/p2 narasi
        kab_lookup=<fungsi nama lokasi -> nama kabupaten>,
        nomor_brs=None, tanggal_rilis=None,
    )
    result.idml_bytes  -> berkas .idml baru (buka di InDesign, lalu Save As .indd)
    result.warnings    -> daftar hal yang perlu dicek manual

Peta story (Story_uXXXX.xml) di bawah ini khusus untuk template di
templates/BRS_Transportasi_Template Folder/. Story ID berubah setiap kali
template di-export ulang dari InDesign, jadi peta WAJIB dicek ulang tiap
template diganti: structure.py-able lewat inspection manual, atau cari
errornya dari pesan guard "tidak cocok".
"""
import copy
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from lxml import etree

# ---------------------------------------------------------------------------
# Konstanta template
# ---------------------------------------------------------------------------
DEFAULT_TEMPLATE = (Path(__file__).resolve().parent.parent / "templates"
                    / "BRS_Transportasi_Template Folder" / "BRS_Transportasi_Template.idml")

TPL_CUR_FULL = "Agustus 2026"
TPL_PROV = "Papua Tengah"
# Nama provinsi yang tertulis di dalam teks template (footer, judul, narasi).
# Semua diganti ke provinsi tujuan, jadi template bisa dipakai untuk provinsi mana pun.
TPL_PROV_TOKENS = ("Papua Selatan", "Papua Tengah")

ABBR = {
    "Januari": "Jan", "Februari": "Feb", "Maret": "Mar", "April": "Apr",
    "Mei": "Mei", "Juni": "Jun", "Juli": "Jul", "Agustus": "Ags",
    "September": "Sep", "Oktober": "Okt", "November": "Nov", "Desember": "Des",
}

# story tabel -> (moda, kolom data di DB, judul tabel, satuan, desimal, label kolom-1)
TABLE_STORIES = {
    "ud48": ("laut",  "dn_penumpang_naik",     "Perkembangan Penumpang Berangkat Angkutan Laut Dalam Negeri", "Orang", 0),
    "ud5f": ("laut",  "dn_penumpang_turun",    "Perkembangan Penumpang Datang Angkutan Laut Dalam Negeri",    "Orang", 0),
    "udab": ("laut",  "dn_muat_barang_ton",    "Perkembangan Muat Barang Angkutan Laut Dalam Negeri",         "Ton",   2),
    "ud94": ("laut",  "dn_bongkar_barang_ton", "Perkembangan Bongkar Barang Angkutan Laut Dalam Negeri",      "Ton",   2),
    "ue65": ("udara", "penumpang_berangkat",   "Perkembangan Penumpang Berangkat Angkutan Udara Dalam Negeri", "Orang", 0),
    "ue84": ("udara", "penumpang_datang",      "Perkembangan Penumpang Datang Angkutan Udara Dalam Negeri",    "Orang", 0),
    "ue9b": ("udara", "barang_muat_kg",        "Perkembangan Muat Barang Angkutan Udara Dalam Negeri",         "Ton",   2),
    "ufa2": ("udara", "barang_bongkar_kg",     "Perkembangan Bongkar Barang Angkutan Udara Dalam Negeri",      "Ton",   2),
}

# Tabel udara dipakai sebagai sumber baris prototipe bila tabel lain kekurangan
# jenis baris (sub total / separator / total).
PROTO_TABLE_STORY = "ue65"

# Story yang TIDAK ikut diganti teks periodenya (gambar infografis & QR masih
# milik periode template, jadi keterangannya sengaja dibiarkan apa adanya).
SKIP_GLOBAL_REPLACE = {"uf3d"}

# Slot narasi: (story, indeks paragraf non-kosong dalam story) -> (moda, kolom, 'p1'/'p2')
# Indeks segmen dihitung per ParagraphStyleRange yang ditunjuk (lihat NARR_SLOTS).
# Kolom 'p' boleh tuple ("p1","p2") kalau template hanya menyediakan satu segmen
# untuk dua paragraf narasi -- keduanya lalu digabung jadi satu paragraf.
NARR_SLOTS = {
    # story: (indeks ParagraphStyleRange, [ (moda, kolom, p) , ... ] urut segmen non-kosong)
    "ud05": (1, [("laut", "dn_penumpang_naik", "p1"), ("laut", "dn_penumpang_naik", "p2"),
                 ("laut", "dn_penumpang_turun", "p1"), ("laut", "dn_penumpang_turun", "p2")]),
    "udc2": (1, [("laut", "dn_muat_barang_ton", "p1"), ("laut", "dn_muat_barang_ton", "p2")]),
    # ponytail: template ini cuma punya 1 segmen bongkar laut (bulanan); p2 (kumulatif)
    # digabung ke p1 daripada dibuang -- kalau BRS butuh 2 paragraf, split di InDesign.
    "udee": (0, [("laut", "dn_bongkar_barang_ton", ("p1", "p2"))]),
    "ue22": (1, [("udara", "penumpang_berangkat", "p1"), ("udara", "penumpang_berangkat", "p2"),
                 ("udara", "penumpang_datang", "p1"), ("udara", "penumpang_datang", "p2")]),
    "ue22#2": (3, [("udara", "barang_muat_kg", "p1"), ("udara", "barang_bongkar_kg", "p2")]),
    "uef4": (0, [("udara", "barang_muat_kg", "p2")]),
    "ufcf": (0, [("udara", "barang_bongkar_kg", "p1"), ("udara", "barang_bongkar_kg", "p2")]),
}

MODA_STORIES = {
    "laut": {"ud1b", "ud05", "udc2", "udee"},
    "udara": {"ue38", "ue22", "uef4", "ufcf"},
}

# Story tabel yang masih menyisakan paragraf narasi-contoh TELEPASAN setelah
# <Table> -- sisa template lama yang slot-nya sudah pindah ke NARR_SLOTS
# (bongkar laut p1+p2 sekarang digabung ke `udee`). Kalau tidak dikosongkan,
# narasi contoh Cetak Papua Selatan ikut tercetak sebagai milik provinsi tujuan.
# Paragraf dicari POSISI (sesudah PSR yg memuat <Table>), bukan indeks, karena
# indeks ikut geser setiap kali jumlah baris tabel berubah.
CLEAR_TAIL_NARRATIVE = {"ud94"}

# Story "Pointer Utama" per moda & story ringkasan cover.
POINTER_STORIES = {"laut": "ud1b", "udara": "ue38"}
COVER_STORY = "ucb1"
ITALIC_TERMS = re.compile(r"(Month-to-Month|Year-on-Year|Year-to-Date)")
DISPLAY_NAME_FIX = {"Illaga": "Ilaga"}  # ejaan di template
REVERSE_FIX = {v: k for k, v in DISPLAY_NAME_FIX.items()}  # untuk pencarian kabupaten


@dataclass
class ExportResult:
    idml_bytes: bytes
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilitas format
# ---------------------------------------------------------------------------
def fmt_id(x, decimals=2):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "Undefined"
    try:
        s = f"{float(x):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(x)
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def _prev_period(bln, thn):
    order = list(ABBR.keys())
    i = order.index(bln)
    return (order[i - 1], int(thn)) if i > 0 else (order[11], int(thn) - 1)


def _row_kind(label, other_vals_all_nan):
    l = str(label).strip().lower()
    if l in ("total", "total keseluruhan"):
        return "total"
    if l in ("sub total", "subtotal", "jumlah"):
        return "sub"
    if "lainnya" in l and other_vals_all_nan:
        return "sep"
    return "data"


def _direction(pct, up="naik", down="turun"):
    if pct is None or pd.isna(pct):
        return None
    return up if pct > 0 else (down if pct < 0 else "tidak berubah")


# ---------------------------------------------------------------------------
# XML helper
# ---------------------------------------------------------------------------
def _parse(b):
    return etree.fromstring(b)


def _dump(root):
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _content_texts(el):
    return [c for c in el.iter("Content")]


def _normalize_csr(psr):
    """Pecah setiap CharacterStyleRange menjadi satu anak per CSR (Content / Br)."""
    out = []
    for csr in list(psr.findall("CharacterStyleRange")):
        kids = [k for k in csr if k.tag in ("Content", "Br")]
        others = [k for k in csr if k.tag not in ("Content", "Br")]
        if others or len(kids) <= 1:
            out.append(csr)
            continue
        parent = csr.getparent()
        idx = parent.index(csr)
        parent.remove(csr)
        for j, k in enumerate(kids):
            new = etree.Element("CharacterStyleRange", attrib=dict(csr.attrib))
            new.append(k)
            parent.insert(idx + j, new)
            out.append(new)
    return out


def _segments(psr):
    """Kembalikan daftar segmen; tiap segmen = list CSR berisi Content berurutan (dipisah Br)."""
    csrs = _normalize_csr(psr)
    segs, cur = [], []
    for csr in psr.findall("CharacterStyleRange"):
        kid = [k for k in csr if k.tag in ("Content", "Br")]
        if not kid:
            continue
        if kid[0].tag == "Content":
            cur.append(csr)
        else:  # Br
            if cur:
                segs.append(cur)
                cur = []
    if cur:
        segs.append(cur)
    return segs


def _seg_text(seg):
    return "".join((c.find("Content").text or "") for c in seg)


def _make_runs(host, text):
    """Buat CSR (dengan cetak miring untuk istilah Inggris) menggantikan host."""
    runs = []
    for piece in ITALIC_TERMS.split(text):
        if piece == "":
            continue
        csr = etree.Element("CharacterStyleRange", attrib=dict(host.attrib))
        if "FontStyle" in csr.attrib:
            del csr.attrib["FontStyle"]
        if ITALIC_TERMS.fullmatch(piece):
            csr.set("FontStyle", "Italic")
        c = etree.SubElement(csr, "Content")
        c.text = piece
        runs.append(csr)
    return runs


def set_segments(psr, texts, warnings, where):
    """Ganti teks setiap segmen non-kosong pada sebuah ParagraphStyleRange (Br tetap)."""
    segs = [s for s in _segments(psr) if _seg_text(s).strip() not in ("",)]
    if len(segs) != len(texts):
        warnings.append(f"{where}: template punya {len(segs)} paragraf, terisi {len(texts)} — dilewati.")
        return
    for seg, text in zip(segs, texts):
        host = seg[0]
        parent = host.getparent()
        pos = parent.index(host)
        for extra in seg[1:]:
            parent.remove(extra)
        parent.remove(host)
        for k, run in enumerate(_make_runs(host, text)):
            parent.insert(pos + k, run)


def replace_period_text(root, mapping):
    """Ganti teks di setiap <Content> dengan satu kali lewat (aman dari saling timpa)."""
    if not mapping:
        return
    pat = re.compile("|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)))
    for c in root.iter("Content"):
        if c.text:
            c.text = pat.sub(lambda m: mapping[m.group(0)], c.text)


# ---------------------------------------------------------------------------
# Tabel
# ---------------------------------------------------------------------------
def _cells_by_row(tbl):
    rows = {}
    for c in tbl.findall("Cell"):
        x, y = map(int, c.get("Name").split(":"))
        rows.setdefault(y, {})[x] = c
    return rows


def _row_text(cells):
    return {x: "".join(t.text or "" for t in c.iter("Content")) for x, c in cells.items()}


def _collect_protos(tbl):
    """kind -> (Row element, {col: Cell element}) dari sebuah tabel template."""
    rows_el = {int(r.get("Name")): r for r in tbl.findall("Row")}
    cells = _cells_by_row(tbl)
    protos = {}
    for y in sorted(cells):
        if y < 3:
            continue
        txt = _row_text(cells[y])
        other_empty = all(not (txt.get(x) or "").strip() for x in range(1, 7))
        kind = _row_kind(txt.get(0, ""), other_empty)
        # 'data_first' = baris data pertama (garis atas khusus); 'data' = baris data kedua bila ada
        if kind == "data" and "data" not in protos:
            protos["data_first"] = (rows_el[y], cells[y])
        if kind == "data" and "data" in protos and "data2" not in protos:
            protos["data2"] = (rows_el[y], cells[y])
        protos.setdefault(kind, (rows_el[y], cells[y]))
    if "data2" in protos:
        protos["data"] = protos["data2"]
    return protos


def _set_cell_text(cell, text):
    contents = list(cell.iter("Content"))
    if not contents:
        # sel kosong: buat CSR + Content pada ParagraphStyleRange pertama
        psr = cell.find("ParagraphStyleRange")
        if psr is None:
            return
        csr = psr.find("CharacterStyleRange")
        if csr is None:
            csr = etree.SubElement(psr, "CharacterStyleRange",
                                   AppliedCharacterStyle="CharacterStyle/$ID/[No character style]")
        c = etree.Element("Content")
        c.text = text
        csr.insert(0, c)
        return
    contents[0].text = text
    for extra in contents[1:]:
        extra.text = ""


def _display_label(label, moda, is_kab, n):
    lab = DISPLAY_NAME_FIX.get(str(label).strip(), str(label).strip())
    if lab.isupper():
        lab = lab.title()
    if is_kab:
        return f"{n}. {lab}"
    return f"{'Bandara' if moda == 'udara' else 'Pelabuhan'} {lab}"


def fill_table(story_root, story_id, item, own_protos, fallback_protos, cols_txt, title, warnings):
    """Isi satu tabel. Mengembalikan selisih jumlah baris terhadap template."""
    moda, _, _, satuan, dec = TABLE_STORIES[story_id]
    story = story_root.find("Story")
    tbl = story.find(".//Table")
    tbl_self = tbl.get("Self")
    old_rows = [r for r in tbl.findall("Row") if int(r.get("Name")) >= 3]

    df = item["report_display_brs"]
    is_kab = moda == "laut" and item.get("row_col") == "nama_kabkota"

    # -- header: ganti token periode & label kolom pertama
    hdr = cols_txt
    pat = re.compile("|".join(re.escape(k) for k in sorted(hdr, key=len, reverse=True)))
    for cell in tbl.findall("Cell"):
        x, y = map(int, cell.get("Name").split(":"))
        if y < 3:
            for c in cell.iter("Content"):
                if c.text:
                    c.text = pat.sub(lambda m: hdr[m.group(0)], c.text)
    if moda == "laut":
        for cell in tbl.findall("Cell"):
            if cell.get("Name") == "0:0":
                _set_cell_text(cell, "Kabupaten" if is_kab else "Pelabuhan")

    # -- judul tabel (paragraf 'Judul Tabel BRS' pertama): satu Content saja
    for psr in story.findall("ParagraphStyleRange"):
        if psr.get("AppliedParagraphStyle", "").endswith("Judul Tabel BRS"):
            conts = list(psr.iter("Content"))
            if conts:
                conts[0].text = title
                for extra in conts[1:]:
                    holder = extra.getparent()
                    holder.remove(extra)
                    if len(holder) == 0:
                        holder.getparent().remove(holder)
            break

    # -- susun ulang baris badan
    kinds, labels = [], []
    n_data = 0
    for label, row in df.iterrows():
        vals = row.values
        other_nan = all(pd.isna(v) for v in vals)
        kind = _row_kind(label, other_nan)
        kinds.append(kind)
        if kind == "data":
            n_data += 1
            labels.append(_display_label(label, moda, is_kab, n_data))
        elif kind == "sep":
            labels.append(str(label).strip())
        else:
            labels.append(str(label).strip())

    for r in old_rows:
        tbl.remove(r)
    for cell in list(tbl.findall("Cell")):
        if int(cell.get("Name").split(":")[1]) >= 3:
            tbl.remove(cell)

    last_row = tbl.findall("Row")[-1]
    insert_at = tbl.index(last_row) + 1
    new_cells = []
    n_seen_data = 0
    for i, (kind, label) in enumerate(zip(kinds, labels)):
        lookup = "data_first" if (kind == "data" and n_seen_data == 0) else kind
        if kind == "data":
            n_seen_data += 1
        proto = (own_protos.get(lookup) or fallback_protos.get(lookup) or own_protos.get(kind)
                 or fallback_protos.get(kind) or own_protos.get("data") or fallback_protos["data"])
        prow, pcells = proto
        y = 3 + i
        row_el = copy.deepcopy(prow)
        row_el.set("Name", str(y))
        row_el.set("Self", f"{tbl_self}Row{y}")
        tbl.insert(insert_at + i, row_el)
        vals = list(df.iloc[i].values)
        for x in range(7):
            cell = copy.deepcopy(pcells[x])
            cell.set("Name", f"{x}:{y}")
            cell.set("Self", f"{tbl_self}x{y:03d}{x}")
            if x == 0:
                text = label
            elif kind == "sep":
                text = ""
            else:
                v = vals[x - 1]
                is_pct = x in (3, 6)
                text = fmt_id(v, 2 if is_pct else dec)
            _set_cell_text(cell, text)
            new_cells.append(cell)
    for c in new_cells:
        tbl.append(c)
    tbl.set("BodyRowCount", str(3 + len(kinds)))
    return len(kinds) - len(old_rows)


# ---------------------------------------------------------------------------
# Teks otomatis (ringkasan cover, poin utama, pengantar wilayah)
# ---------------------------------------------------------------------------
def _total_row(item):
    df = item["report_display_brs"]
    for lab in df.index:
        if str(lab).strip().lower() in ("total", "total keseluruhan"):
            return df.loc[lab]
    return df.iloc[-1]


def _join_id(parts):
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " dan " + parts[-1]


def _kab_short(name):
    n = str(name or "").upper().strip()
    n = re.sub(r"^(KABUPATEN|KOTA)\s+", "", n)
    return n.title()


def _names_from_display(item):
    df = item["report_display_brs"]
    utama, lain, seen_sep = [], [], False
    for lab in df.index:
        l = str(lab).strip()
        kind = _row_kind(l, all(pd.isna(v) for v in df.loc[lab].values))
        if kind == "sep":
            seen_sep = True
        elif kind == "data":
            (lain if seen_sep else utama).append(DISPLAY_NAME_FIX.get(l, l))
    return utama, lain


def region_intro(moda, prov, utama, lain, kab_lookup):
    """Kalimat pembuka bab: daftar pelabuhan/bandara + kabupatennya."""

    def kab_of(n):
        return _kab_short(kab_lookup(REVERSE_FIX.get(n, n)))

    def group(names, prefix):
        groups = []
        for n in names:
            k = kab_of(n)
            if groups and groups[-1][0] == k and k:
                groups[-1][1].append(n)
            else:
                groups.append([k, [n]])
        phr = []
        for k, ns in groups:
            ns = [x.title() if x.isupper() else x for x in ns]
            if moda == "laut":
                body = f"{prefix} " + _join_id(ns)
            else:
                body = ", ".join(f"{prefix} {x}" for x in ns)
            phr.append(body + (f" di {k}" if k else ""))
        return phr

    if moda == "laut":
        phr = group(utama + lain, "Pelabuhan")
        return f"Data transportasi laut terdiri dari data {_join_id(phr)} Provinsi {prov}."
    txt = f"Data transportasi udara terdiri dari data bandara utama yaitu {_join_id(group(utama, 'Bandara'))}"
    if lain:
        txt += f" dan bandara lainnya yaitu {_join_id(group(lain, 'Bandara'))}"
    return txt + f" Provinsi {prov}"


def highlight_texts(moda, prov, bln, thn, prev_bln, prev_thn, its, kab_lookup, laut_ports=None):
    cur, prev = f"{bln} {thn}", f"{prev_bln} {prev_thn}"
    kb = "angkutan laut" if moda == "laut" else "angkutan udara"
    key = {
        "laut": dict(ber="dn_penumpang_naik", dat="dn_penumpang_turun", mu="dn_muat_barang_ton", bo="dn_bongkar_barang_ton"),
        "udara": dict(ber="penumpang_berangkat", dat="penumpang_datang", mu="barang_muat_kg", bo="barang_bongkar_kg"),
    }[moda]

    def vals(k, dec):
        t = _total_row(its[k])
        df_cols = its[k]["report_display_brs"].columns
        return (t[df_cols[0]], t[df_cols[1]], t[df_cols[2]], dec)

    utama, lain = _names_from_display(its[key["ber"]])
    if moda == "laut" and laut_ports:
        utama, lain = list(laut_ports), []
    out = [region_intro(moda, prov, utama, lain, kab_lookup)]

    def penumpang(kind, k):
        p, c, pct, _ = vals(k, 0)
        verb = "yang berangkat menggunakan" if kind == "ber" else "yang datang dengan"
        d = _direction(pct, "kenaikan", "penurunan")
        if d is None or d == "tidak berubah":
            return (f"Jumlah penumpang {verb} {kb} pada {cur} tercatat sebanyak {fmt_id(c, 0)} orang "
                    f"(bulan {prev}: {fmt_id(p, 0)} orang).")
        return (f"Jumlah penumpang {verb} {kb} pada {cur} mengalami {d} sebesar "
                f"{fmt_id(abs(pct), 2)} persen, yaitu dari {fmt_id(p, 0)} orang pada {prev} menjadi {fmt_id(c, 0)} orang.")

    def barang(kind, k):
        p, c, pct, _ = vals(k, 2)
        verb = "dimuat" if kind == "mu" else "dibongkar"
        d = _direction(pct, "naik", "turun")
        base = f"Volume barang yang {verb} pada {cur} tercatat sebesar {fmt_id(c, 2)} ton"
        if d is None or d == "tidak berubah":
            return base + f" (bulan {prev}: {fmt_id(p, 2)} ton)."
        return (base + f" atau {d} sebesar {fmt_id(abs(pct), 2)} persen dibandingkan {prev} "
                f"yang sebesar {fmt_id(p, 2)} ton.")

    out += [penumpang("ber", key["ber"]), penumpang("dat", key["dat"]),
            barang("mu", key["mu"]), barang("bo", key["bo"])]
    return out


def cover_texts(bln, thn, its_laut, its_udara):
    cur = f"{bln} {thn}"
    out = []
    for moda, its, key in (("laut", its_laut, "dn_penumpang_naik"), ("udara", its_udara, "penumpang_berangkat")):
        if its and key in its:
            t = _total_row(its[key])
            v = t[its[key]["report_display_brs"].columns[1]]
            out.append(f"Jumlah penumpang berangkat dengan moda angkutan {moda} dalam negeri pada {cur} "
                       f"tercatat sebanyak {fmt_id(v, 0)} orang.")
    return out


# ---------------------------------------------------------------------------
# Fungsi utama
# ---------------------------------------------------------------------------
def _clean_narr(p):
    return re.sub(r"^\*\(.*?\)\*\n\n", "", p or "").strip()


def _embed_image(files, order, data, name, prov, thn, bln, token, label, warnings):
    """Taruh satu gambar ke dalam paket IDML dan arahkan <Link> ke file tersebut.

    Template mengaitkan QR dan Infografis ke path absolut 'file:D:/BPS/...' di luar
    paket, sehingga keduanya hilang/missing link saat .idml dibuka di mesin lain.
    Di sini filenya ditulis ke folder Links/ di dalam paket dan LinkResourceURI
    diganti ke path relatif. `token` = potongan nama file yang dicari di template
    ('qrcode' atau 'Infografis').
    """
    if not data:
        return False
    m = re.search(r"\.(png|jpe?g)$", name or "", re.I)
    ext = "." + m.group(1).lower() if m else ".png"
    slug = f"{token}-Transportasi-Bulan-{bln}-{thn}-Provinsi-{prov}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug).strip("_")
    link_path = f"Links/{slug}{ext}"
    files[link_path] = data
    if link_path not in order:
        order.append(link_path)

    # XML spread/master yang punya <Image> dengan <Link> ke file <token>*.
    # Prefix absolut "file:.../Links/" dibuang supaya tautan relatif ke file di paket.
    pat = re.compile(rb'<Link\b[^>]*?LinkResourceURI="file:[^"]*?/Links/'
                     + token.encode() + rb'[^"]*?"[^>]*?/>')
    patched = 0
    for nm in list(files):
        if not (nm.startswith(("Spreads/", "MasterSpreads/")) and nm.endswith(".xml")):
            continue
        blob = files[nm]
        if b"LinkResourceURI" not in blob or token.encode() not in blob:
            continue

        def repl(m):
            tag = m.group(0)
            tag = re.sub(rb'LinkResourceURI="[^"]*"',
                         b'LinkResourceURI="' + link_path.encode() + b'"', tag)
            if ext in (".jpg", ".jpeg"):
                tag = re.sub(rb'LinkResourceFormat="[^"]*"',
                             b'LinkResourceFormat="$ID/JPEG"', tag)
            else:
                tag = re.sub(rb'LinkResourceFormat="[^"]*"',
                             b'LinkResourceFormat="$ID/Portable Network Graphics (PNG)"', tag)
            return tag

        new = pat.sub(repl, blob)
        if new != blob:
            files[nm] = new
            patched += 1
    if patched:
        warnings.append(f"{label} ({name}) dimasukkan ke paket pada {link_path}.")
    else:
        warnings.append(f"{label} diunggah tapi <Link> {token}-* tidak ditemukan di template — "
                        f"ganti gambarnya secara manual di InDesign.")
    return patched > 0


def fill_brs_template(template_bytes, prov, thn, bln, items, kab_lookup,
                      nomor_brs=None, tanggal_rilis=None, laut_ports=None,
                      qr_bytes=None, qr_name=None,
                       infografis_bytes=None, infografis_name=None,
                       prev_bln=None, prev_thn=None):
    """
    items: list dict dari prepare_table_item(), masing-masing berisi juga
           'p1' & 'p2' (narasi) dan 'row_col'. Diindeks (moda, col_target).
    nomor_brs / tanggal_rilis: mis. "235/10/94/Th. XXIX" dan "1 Oktober 2026".
    qr_bytes / qr_name: PNG/JPG QR code untuk disisipkan ke template.
    """
    warnings = []
    zin = zipfile.ZipFile(io.BytesIO(template_bytes))
    files = {n: zin.read(n) for n in zin.namelist()}
    order = zin.namelist()

    # Periode pembanding harus sama dengan angka di dalam tabel. prepare_table_item()
    # sudah membawa prev_bln/prev_thn dari get_comparison_data() (yang tahu baris
    # periode mana yang benar-benar ada di DB), jadi dipakai dulu. _prev_period()
    # hanya tebakan "bulan sebelumnya" dari urutan kalender —WRONG kalau ada
    # bulan yang bolong di DB atau lompatan tahun.
    if not (prev_bln and prev_thn):
        _p = [(it.get("prev_bln"), it.get("prev_thn")) for it in items
              if it.get("prev_bln") and it.get("prev_thn")]
        if _p:
            prev_bln, prev_thn = _p[0]
        else:
            prev_bln, prev_thn = _prev_period(bln, thn)
    prev_thn = str(prev_thn)
    by_key = {}
    for it in items:
        m = "udara" if it["moda"] == "Transportasi Udara" else "laut"
        by_key[(m, it["col_target"])] = it
    have = {m: any(k[0] == m for k in by_key) for m in ("laut", "udara")}
    for m in ("laut", "udara"):
        if not have[m]:
            warnings.append(f"Tidak ada data moda {m} untuk {prov} {bln} {thn}: halaman/tabel moda {m} "
                            f"di template masih berisi teks contoh — hapus atau isi manual.")

    # token tabel
    a_cur, a_prev = ABBR[bln], ABBR[prev_bln]
    cols_txt = {
        f"Jan-{ABBR['Agustus']} 2025": f"Jan-{a_cur} {int(thn) - 1}",
        f"Jan-{ABBR['Agustus']} 2026": f"Jan-{a_cur} {thn}",
        "Jul 2026": f"{a_prev} {prev_thn}",
        f"{ABBR['Agustus']} 2026": f"{a_cur} {thn}",
    }
    # (token 'Jan-Ags 2026' harus ikut thn; template selalu 2026 sebagai tahun berjalan)
    global_map = {TPL_CUR_FULL: f"{bln} {thn}"}
    global_map.update({tok: prov for tok in TPL_PROV_TOKENS})

    # story
    def load(sid):
        return _parse(files[f"Stories/Story_{sid}.xml"])

    roots = {}
    for n in list(files):
        if n.startswith("Stories/Story_"):
            sid = n.split("Story_")[1][:-4]
            roots[sid] = _parse(files[n])

    # Template .idml memakai ID story InDesign yang di-hardcode di TABLE_STORIES /
    # NARR_SLOTS. ID itu hanya berlaku untuk template resmi; .idml hasil upload
    # user atau hasil ekspor ulang bisa punya ID lain, lalu setiap akses roots[sid]
    # di bawah jadi KeyError. Satu cek di titik choked ini lebih murah daripada
    # Menambal guard di tiap loop.
    required = (set(TABLE_STORIES)
                | {s.split("#")[0] for s in NARR_SLOTS}
                | set(POINTER_STORIES.values()) | {COVER_STORY, PROTO_TABLE_STORY})
    missing = sorted(required - set(roots))
    if missing:
        raise ValueError(
            "Template .idml tidak cocok dengan template BRS bawaan: story "
            + ", ".join(missing[:8])
            + (f" (+{len(missing) - 8} lainnya)" if len(missing) > 8 else "")
            + ". Gunakan template bawaan, atau pastikan ID story tidak berubah."
        )

    # 1) penggantian global periode/provinsi
    for sid, r in roots.items():
        if sid in SKIP_GLOBAL_REPLACE or sid in TABLE_STORIES:
            continue
        if any(sid in MODA_STORIES[m] and not have[m] for m in MODA_STORIES):
            continue
        replace_period_text(r, global_map)
    if nomor_brs or tanggal_rilis:
        pat = re.compile(r"No\.\s*235/10/94/Th\.\s*XXIX,\s*1 Oktober 2026")
        for sid, r in roots.items():
            for c in r.iter("Content"):
                if c.text and pat.search(c.text):
                    old = c.text
                    new_no = nomor_brs or "235/10/94/Th. XXIX"
                    new_tgl = tanggal_rilis or "1 Oktober 2026"
                    c.text = pat.sub(f"No. {new_no}, {new_tgl}", old)
    else:
        warnings.append("Nomor BRS & tanggal rilis (\"No. 235/10/94/Th. XXIX, 1 Oktober 2026\") belum diganti — isi "
                        "kolom Nomor BRS dan Tanggal rilis atau ubah manual di InDesign.")

    # 2) tabel
    ud_proto_src = _collect_protos(roots[PROTO_TABLE_STORY].find("Story").find(".//Table"))
    row_delta = {}
    for sid, (moda, col, title, satuan, dec) in TABLE_STORIES.items():
        it = by_key.get((moda, col))
        if it is None:
            continue
        tbl = roots[sid].find("Story").find(".//Table")
        own = _collect_protos(tbl)
        it["row_col"] = it.get("row_col") or ("nama_kabkota" if (moda == "laut" and prov == "Papua Tengah") else "")
        full_title = f"{title}\u2028Provinsi {prov}, {bln} {thn}"
        delta = fill_table(roots[sid], sid, it, own, ud_proto_src, cols_txt, full_title, warnings)
        row_delta[sid] = delta
        if delta:
            warnings.append(f"{title}: jumlah baris berbeda {delta:+d} dari template — "
                            f"cek tinggi frame teks tabel di InDesign (baris berlebih bisa tersembunyi/overset).")

    # 2b) kosongkan sisa narasi-contoh yang menempel setelah <Table>
    for sid in CLEAR_TAIL_NARRATIVE:
        kids = [c for c in roots[sid].find("Story")
                if etree.QName(c).localname == "ParagraphStyleRange"]
        tbl_at = next((i for i, c in enumerate(kids) if c.find(".//Table") is not None), None)
        if tbl_at is None:
            continue
        for c in kids[tbl_at + 1:]:
            for t in c.iter("Content"):
                t.text = ""

    # 3) narasi
    for slot_id, (psr_idx, slots) in NARR_SLOTS.items():
        sid = slot_id.split("#")[0]
        moda = slots[0][0]
        if not have[moda]:
            continue
        psrs = roots[sid].find("Story").findall("ParagraphStyleRange")
        if psr_idx >= len(psrs):
            warnings.append(f"{slot_id}: paragraf template tidak ditemukan.")
            continue
        texts = []
        for (m, col, p) in slots:
            it = by_key.get((m, col))
            if it is None:
                texts.append("")
                continue
            parts = (_clean_narr(it.get(k)) for k in ((p,) if isinstance(p, str) else p))
            texts.append(" ".join(t for t in parts if t))
        if any(not t for t in texts):
            warnings.append(f"{slot_id}: ada narasi kosong — slot dilewati.")
            continue
        set_segments(psrs[psr_idx], texts, warnings, slot_id)

    # 4) poin utama (Pointer Utama) & ringkasan cover
    its_by = {m: {k[1]: v for k, v in by_key.items() if k[0] == m} for m in ("laut", "udara")}
    for moda, sid in POINTER_STORIES.items():
        if have[moda]:
            texts = highlight_texts(moda, prov, bln, thn, prev_bln, prev_thn, its_by[moda], kab_lookup, laut_ports)
            psr = roots[sid].find("Story").findall("ParagraphStyleRange")[0]
            set_segments(psr, texts, warnings, sid)
    if have["laut"] and have["udara"]:
        psr = roots[COVER_STORY].find("Story").findall("ParagraphStyleRange")[0]
        set_segments(psr, cover_texts(bln, thn, its_by["laut"], its_by["udara"]), warnings, COVER_STORY)
    else:
        warnings.append("Ringkasan cover (2 poin: laut & udara) tidak diisi otomatis karena salah satu moda tidak ada data.")

    # 5) sisa kata template
    left = set()
    for sid, r in roots.items():
        if sid in SKIP_GLOBAL_REPLACE:
            continue
        for c in r.iter("Content"):
            t = c.text or ""
            if bln != "Agustus" and re.search(r"Agustus|\bAgs\b", t):
                left.add(sid)
            if any(tok != prov and tok in t for tok in TPL_PROV_TOKENS):
                left.add(sid)
    if left:
        warnings.append("Masih ada sisa teks periode/provinsi template di story: " + ", ".join(sorted(left)))
    if qr_bytes:
        _embed_image(files, order, qr_bytes, qr_name, prov, thn, bln, "qrcode", "QR code", warnings)
    if infografis_bytes:
        _embed_image(files, order, infografis_bytes, infografis_name, prov, thn, bln,
                     "Infografis", "Infografis", warnings)
    else:
        warnings.append("QR code belum diunggah — QR pada template masih milik Papua Tengah Agustus 2026; "
                        "unggah QR atau ganti manual di InDesign.")
    warnings.append("Gambar infografis (hlm. 11) masih milik Papua Tengah Agustus 2026 — "
                    "ganti file gambar & keterangan \"Gambar 1\" secara manual.")
    warnings.append("Setelah dibuka di InDesign, cek teks overset (tanda + merah) dan perataan halaman; "
                    "panjang narasi berbeda dari template.")

    # 6) tulis ulang zip
    for sid, r in roots.items():
        files[f"Stories/Story_{sid}.xml"] = _dump(r)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        # 'mimetype' wajib pertama & tidak dikompres
        zout.writestr(zipfile.ZipInfo("mimetype"), files["mimetype"], compress_type=zipfile.ZIP_STORED)
        for n in order:
            if n == "mimetype" or n.endswith("/"):
                continue
            zout.writestr(n, files[n], compress_type=zipfile.ZIP_DEFLATED)
    return ExportResult(idml_bytes=buf.getvalue(), warnings=warnings)
