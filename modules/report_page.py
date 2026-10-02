import streamlit as st
import pandas as pd
import numpy as np
import random
import io
import docx
import re
import time
import logging
from google import genai
from sqlalchemy import text
from modules.database import get_engine, get_db_narrative, save_db_narrative, delete_db_narrative
from modules.ai_backup import get_openrouter_key, openrouter_generate
from modules.config import PEMETAAN_WILAYAH, get_location_metadata
from modules.indesign_export import (
    fill_brs_template,
    paket_zip,
    DEFAULT_TEMPLATE as DEFAULT_IDML_TEMPLATE,
)
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

MONTH_MAP = {'Januari':1, 'Februari':2, 'Maret':3, 'April':4, 'Mei':5, 'Juni':6,
             'Juli':7, 'Agustus':8, 'September':9, 'Oktober':10, 'November':11, 'Desember':12}
INV_MONTH_MAP = {v: k for k, v in MONTH_MAP.items()}

# ==============================================================================
# KONFIGURASI HIERARKI BRS & NORMALISASI
# ==============================================================================
HIERARKI_BRS = {
    "Papua Tengah": {
        "Transportasi Udara": {
            "utama": ['Douw Aturure', 'Mozes Kilangin'],
            "lainnya": ['Enarotali', 'Zugapa Bilorai', 'Moanamani', 'Sinak', 'Illaga', 'Beoga', 'Mulia'],
            "label_subtotal": "Sub Total",
            "label_total": "Total",
            "teks_separator": "Bandara Lainnya"
        },
        "Transportasi Laut": {
            "utama": ['Mimika', 'Nabire'],
            "lainnya": [],
            "label_subtotal": "", 
            "label_total": "Total",
            "teks_separator": ""
        }
    },
    "Papua": {
        "Transportasi Laut": {
            "utama": ['Jayapura', 'Biak'],
            "lainnya": ['Sarmi', 'Serui', 'Waren', 'Kasonaweja'],
            "label_subtotal": "Subtotal",
            "label_total": "Total",
            "teks_separator": "Pelabuhan Lainnya"
        },
        "Transportasi Udara": {
            "utama": ['Sentani', 'Frans Kaisiepo'],
            "lainnya": ['Mararena', 'Stevanus Rumbewas', 'Kasonaweja'],
            "label_subtotal": "Subtotal",
            "label_total": "Total",
            "teks_separator": "Bandara Lainnya"
        }
    },
    "Papua Pegunungan": {
        "Transportasi Udara": {
            "utama": ['Wamena', 'Dekai', 'Batom'],
            "lainnya": ['Oksibil', 'Karubaga'],
            "label_subtotal": "Total",
            "label_total": "Total Keseluruhan",
            "teks_separator": "Bandara Lainnya"
        }
    },
    "Papua Selatan": {
        "Transportasi Laut": {
            "utama": ['Merauke'],
            "lainnya": ['Bade', 'Habesilam', 'Agats', 'Atsy'],
            "label_subtotal": "Jumlah",
            "label_total": "Total",
            "teks_separator": "Pelabuhan lainnya"
        },
        "Transportasi Udara": {
            "utama": ['Moppah'],
            "lainnya": [
                'Okaba', 'Tanah Merah', 'Bomakia', 
                'Mindiptanah', 'Kepi', 'Bade', 
                'Ewer', 'Kamur'
            ],
            "label_subtotal": "Jumlah",
            "label_total": "Total",
            "teks_separator": "Bandara lainnya"
        }
    }
}

def normalisasi_entitas(nama, moda):
    if not isinstance(nama, str):
        return str(nama)
    clean_name = nama.strip()
    if moda == "Transportasi Udara":
        if clean_name.lower().startswith("bandara "):
            clean_name = clean_name[8:].strip()
        mapping_khusus = {"Nabire": "Douw Aturure"}
        return mapping_khusus.get(clean_name, clean_name)
    else:
        if clean_name.lower().startswith("pelabuhan "):
            clean_name = clean_name[10:].strip()
        return clean_name

def build_brs_display_table(report_flat, prov, moda):
    df = report_flat.copy()
    if 'TOTAL' in df.index:
        total_row = df.loc[['TOTAL']]
        df = df.drop(index='TOTAL')
    else:
        total_row = pd.DataFrame()
        
    df = df.reset_index()
    row_col = df.columns[0]
    
    df[row_col] = df[row_col].apply(lambda x: normalisasi_entitas(x, moda))
    df = df.groupby(row_col).sum(min_count=1).reset_index() 
    
    raw_cols = [c for c in df.columns if '(%)' not in c and c != row_col]
    potongan = []
    
    if prov in HIERARKI_BRS and moda in HIERARKI_BRS[prov]:
        config = HIERARKI_BRS[prov][moda]
        df['match_key'] = df[row_col].astype(str).str.lower()
        
        for entitas_utama in config.get("utama", []):
            match_row = df[df['match_key'] == str(entitas_utama).lower()]
            if not match_row.empty:
                potongan.append(match_row.drop(columns=['match_key']))
                
        if len(config.get("lainnya", [])) > 0 and potongan:
            df_utama_gabung = pd.concat(potongan, ignore_index=True)
            sub_utama = pd.DataFrame(df_utama_gabung[raw_cols].sum()).T
            sub_utama[row_col] = config["label_subtotal"]
            potongan.append(sub_utama)
            
        if len(config.get("lainnya", [])) > 0:
            separator = pd.DataFrame([{row_col: config["teks_separator"]}])
            for c in df.columns: 
                if c != row_col and c != 'match_key': 
                    separator[c] = np.nan
            potongan.append(separator)
            
            for entitas_lain in config.get("lainnya", []):
                match_row_lain = df[df['match_key'] == str(entitas_lain).lower()]
                if not match_row_lain.empty:
                    potongan.append(match_row_lain.drop(columns=['match_key']))
            
            lainnya_lower = [str(x).lower() for x in config["lainnya"]]
            df_lain_gabung = df[df['match_key'].isin(lainnya_lower)]
            if not df_lain_gabung.empty:
                sub_lain = pd.DataFrame(df_lain_gabung[raw_cols].sum()).T
                sub_lain[row_col] = config["label_subtotal"] + " " 
                potongan.append(sub_lain)
        
        terdaftar = [str(x).lower() for x in config.get("utama", []) + config.get("lainnya", [])]
        df_sisa = df[~df['match_key'].isin(terdaftar)].copy()
        if not df_sisa.empty:
            potongan.append(df_sisa.drop(columns=['match_key']))
            
        if not potongan and not df.empty:
            if 'match_key' in df.columns: df = df.drop(columns=['match_key'])
            potongan.append(df)
    else:
        if 'match_key' in df.columns: df = df.drop(columns=['match_key'])
        potongan.append(df)
        
    if not total_row.empty:
        t_label = HIERARKI_BRS.get(prov, {}).get(moda, {}).get("label_total", "TOTAL")
        total_row = total_row.reset_index()
        total_row.rename(columns={'index': row_col}, inplace=True)
        total_row[row_col] = t_label
        potongan.append(total_row)
        
    res = pd.concat(potongan, ignore_index=True).set_index(row_col) if potongan else df.set_index(row_col)
    
    col_prev, col_curr = raw_cols[0], raw_cols[1]
    col_cum_prev, col_cum_curr = raw_cols[2], raw_cols[3]
    
    # percent change via helper: 0/0 -> NaN (numeric, supaya background_gradient
    # tidak gagal), lalu teks "undefined" muncul saat diformat di tabel.
    prev_vals_res = res[col_prev].values.astype(float)
    curr_vals_res = res[col_curr].values.astype(float)
    res['M-to-M (%)'] = [safe_pct_change(c, p) for c, p in zip(curr_vals_res, prev_vals_res)]
    
    cum_prev_vals_res = res[col_cum_prev].values.astype(float)
    cum_curr_vals_res = res[col_cum_curr].values.astype(float)
    res['Y-on-Y (%)'] = [safe_pct_change(c, p) for c, p in zip(cum_curr_vals_res, cum_prev_vals_res)]
    
    return res[[col_prev, col_curr, 'M-to-M (%)', col_cum_prev, col_cum_curr, 'Y-on-Y (%)']]

def get_comparison_data(prov, thn, bln, moda):
    table = "transportasi_udara" if moda == "Transportasi Udara" else "transportasi_laut"
    bln_num = MONTH_MAP[bln]
    thn_int = int(thn)
    engine = get_engine()

    where_clause = "WHERE UPPER(nama_provinsi) = :prov"
    q_curr = f"SELECT * FROM {table} {where_clause} AND CAST(tahun AS TEXT) = :thn AND bulan = :bln"
    df_curr = pd.read_sql(text(q_curr), engine, params={"prov": prov.upper(), "thn": str(thn), "bln": bln})

    prev_bln_num = bln_num - 1 if bln_num > 1 else 12
    prev_thn = thn_int if bln_num > 1 else thn_int - 1
    prev_bln_name = INV_MONTH_MAP[prev_bln_num]
    q_prev = f"SELECT * FROM {table} {where_clause} AND CAST(tahun AS TEXT) = :prev_thn AND bulan = :prev_bln"
    df_prev = pd.read_sql(text(q_prev), engine, params={"prov": prov.upper(), "prev_thn": str(prev_thn), "prev_bln": prev_bln_name})

    months_cum = [INV_MONTH_MAP[i] for i in range(1, bln_num + 1)]
    month_params = {f"m{i}": m for i, m in enumerate(months_cum)}
    months_placeholder = "(" + ", ".join(f":{k}" for k in month_params) + ")"
    q_cum_curr = f"SELECT * FROM {table} {where_clause} AND CAST(tahun AS TEXT) = :thn AND bulan IN {months_placeholder}"
    df_cum_curr = pd.read_sql(text(q_cum_curr), engine, params={"prov": prov.upper(), "thn": str(thn), **month_params})

    q_cum_prev = f"SELECT * FROM {table} {where_clause} AND CAST(tahun AS TEXT) = :prev_thn_full AND bulan IN {months_placeholder}"
    df_cum_prev = pd.read_sql(text(q_cum_prev), engine, params={"prov": prov.upper(), "prev_thn_full": str(thn_int - 1), **month_params})

    return df_curr, df_prev, df_cum_curr, df_cum_prev, prev_bln_name, prev_thn

# Divide-by-zero has no numeric answer; every consumer renders this one string.
UNDEFINED = "undefined"


def _union_index(*series):
    """Ordered union of several pandas Series indexes.

    Keeps first-seen order (current period first) and de-dupes, so a category
    present in any period yields exactly one row. Plain `Index.union` would sort,
    which would reshuffle every table the moment one extra row appears.
    """
    seen, out = set(), []
    for s in series:
        for key in s.index:
            if key not in seen:
                seen.add(key)
                out.append(key)
    return pd.Index(out)


def safe_pct_change(curr, prev):
    """Percentage change as float, or NaN when it is undefined (0/0, 0->x).

    Returns NaN rather than a string on purpose. The percent columns feed
    `styler.background_gradient`, which raises
    "could not convert string to float" on an object-dtype column, so writing
    the literal "undefined" into the cell breaks the whole table. The string is
    added at the display layer by `format_pct_number` instead.

    float() per cell, not numpy broadcasting: an aggregated column can be
    object/nullable dtype, where 0 / 0 calls Python's operator and raises
    ZeroDivisionError — which np.errstate cannot suppress.
    """
    try:
        c, p = float(curr), float(prev)
    except (TypeError, ValueError):
        return np.nan
    if p == 0 or np.isnan(p) or np.isnan(c) or np.isinf(c):
        return np.nan
    result = (c - p) / p * 100
    return np.nan if np.isinf(result) else result


def format_pct_number(x, decimals=2):
    """Display value for a percent cell: `undefined` when there is no number.

    A 0/0 change is not "missing data" — saying so with an empty cell makes a
    real growth-from-zero look like a reporting gap. Render the word instead.
    """
    if x is None or (isinstance(x, str) and x.strip().lower() == 'undefined'):
        return UNDEFINED
    try:
        val = float(x)
    except (TypeError, ValueError):
        return UNDEFINED
    if np.isnan(val) or np.isinf(val):
        return UNDEFINED
    return format_id_number(val, decimals)


def format_id_number(x, decimals=2):
    if pd.isna(x) or str(x).lower() in ['nan', 'inf', '-inf', 'undefined']:
        return "Undefined"
    try:
        val = float(x)
        if np.isinf(val) or np.isnan(val): return "Undefined"
        s = f"{val:,.{decimals}f}"
    except (ValueError, TypeError):
        return str(x)
    return s.replace(",", "§").replace(".", ",").replace("§", ".")

NARRATIVE_META = {
    'penumpang_datang':      {'subject': 'Jumlah penumpang yang datang', 'satuan': 'orang', 'is_penumpang': True},
    'penumpang_berangkat':   {'subject': 'Jumlah penumpang yang berangkat', 'satuan': 'orang', 'is_penumpang': True},
    'barang_bongkar_kg':     {'subject': 'Volume barang yang dibongkar', 'satuan': 'ton', 'is_penumpang': False},
    'barang_muat_kg':        {'subject': 'Volume barang yang dimuat', 'satuan': 'ton', 'is_penumpang': False},
    'barang_bongkar_ton':    {'subject': 'Volume barang yang dibongkar', 'satuan': 'ton', 'is_penumpang': False},
    'barang_muat_ton':       {'subject': 'Volume barang yang dimuat', 'satuan': 'ton', 'is_penumpang': False},
    'dn_penumpang_turun':    {'subject': 'Jumlah penumpang yang datang', 'satuan': 'orang', 'is_penumpang': True},
    'dn_penumpang_naik':     {'subject': 'Jumlah penumpang yang berangkat', 'satuan': 'orang', 'is_penumpang': True},
    'dn_bongkar_barang_ton': {'subject': 'Volume barang yang dibongkar', 'satuan': 'ton', 'is_penumpang': False},
    'dn_muat_barang_ton':    {'subject': 'Volume barang yang dimuat', 'satuan': 'ton', 'is_penumpang': False},
}

# Desimal mengikuti satuan, sama seperti yang dipakai fill_brs_template()
# (TABLE_STORIES dec=0 untuk kolom Orang, dec=2 untuk kolom ton).
_SATUAN_DESIMAL = {'orang': 0, 'ton': 2, 'persen': 2}

def table_formatters(columns, col_target):
    """{kolom: formatter} untuk Styler.

    Sebelumnya satu `styler.format(format_id_number)` applies ke semua kolom,
    jadi kolom penumpang di web tampil "47.526,00" sementara .idml "47.526".
    Kolom (%) selalu 2 desimal, apa pun satuannya.
    """
    satuan = NARRATIVE_META.get(col_target, {}).get('satuan', 'ton')

    # factory, bukan lambda di dalam dict comprehension: comprehension cuma punya
    # satu sel untuk `c`, jadi semua lambda-nya lihat kolom terakhir dan tiap
    # kolom dapat desimal yang sama.
    def make(col):
        is_pct = '(%)' in str(col)
        dec = 2 if is_pct else _SATUAN_DESIMAL.get(satuan, 2)
        return (lambda x: format_pct_number(x, dec)) if is_pct else (lambda x: format_id_number(x, dec))

    return {c: make(c) for c in columns}

# Angka gaya Indonesia ("7.778,45") atau gaya Inggris ("5.5"). Lookaround-nya
# wajib: tanpa itu "3286.296" akan kena mulai digit ketiga -> "286.296".
_NUM_TOK = r"(?<![\d.,])(?:-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[.,]\d+)?)(?![\d])"
_NUM_UNIT = re.compile(
    r"(" + _NUM_TOK + r")\s*(%|(?:orang|penumpang|ton|persen)\b)", re.IGNORECASE
)
# Angka bertanda desimal yang tidak menempel kata satuan ("dari 4.318,20 menjadi").
# Lookahead 'persen' supaya hasil pass pertama tidak jadi "10 persen" di laporan
# penumpang.
_NUM_LOOSE = re.compile(r"(?<![\d.,])(-?\d{1,3}(?:\.\d{3})*,\d+)(?![\d])(?!\s*persen\b)")

def _to_float_id(tok):
    """'7.778,45' -> 7778.45 (Indonesia) ; '47526.0' -> 47526.0 (Inggris).

    Titik hanya pemisah ribuan kalau HOL nya 3 digit dan tidak ada koma. Tanpa
    aturan ini "47526.0" jadi 475260 karena titiknya dihapus sebagai ribuan.
    """
    if ',' in tok:
        return float(tok.replace('.', '').replace(',', '.'))
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', tok):
        return float(tok.replace('.', ''))
    return float(tok)

def normalize_narrative(text, col_target=None):
    """Bikin angka narasi sama persis formatnya dengan tabel.

    Narasi dari Gemini lalu di-cache di DB, jadi jumlah desimalnya bebas: model
    menulis "65.352,1 ton" sementara tabel "65.352,10", dan menulis "10,27%"
    sementara narasi fallback memakai kata "persen". Runs sekali sebelum narasi
    masuk ke preview web maupun .idml, jadi keduanya tidak bisa berbeda lagi.

    Angka bulat tanpa kata satuan sengaja dibiarkan: "3 maskapai" atau
    "2 Simulator" tidak boleh jadi "3,00". Kalau model menulis desimal di
    sana ("4.318,20"), desimalnya ikut satuan tabel karena saat itu sudah
    jelas bukan bilangan bulat.
    """
    if not text:
        return text

    default_dec = _SATUAN_DESIMAL.get(NARRATIVE_META.get(col_target, {}).get('satuan', 'ton'), 2)

    def repl(m):
        val = _to_float_id(m.group(1))
        if val is None:
            return m.group(0)
        unit = m.group(2)
        dec = 2 if unit == '%' else _SATUAN_DESIMAL.get(unit.lower(), default_dec)
        label = 'persen' if unit == '%' else m.group(2)
        return f"{format_id_number(val, dec)} {label}"

    out = _NUM_UNIT.sub(repl, text)
    return _NUM_LOOSE.sub(
        lambda m: format_id_number(_to_float_id(m.group(1)), default_dec), out
    )

def _arah_dinamis(pct):
    # ponytail: the direction of a number is a fact, not a style choice. This used
    # random.choice, so the same +0.3% could be printed as "mengalami lonjakan" on
    # one run and "naik" on the next. Threshold is the only tunable here.
    if pd.isna(pct): return "tercatat"
    if pct >= 10: return "mengalami lonjakan"
    if pct > 0: return "meningkat"
    if pct <= -10: return "mengalami penurunan tajam"
    if pct < 0: return "menurun"
    return "stabil"

def get_gemini_api_keys():
    keys = []
    def add_value(v):
        if not v: return
        if isinstance(v, str):
            v = v.strip()
            if v: keys.append(v)
        elif isinstance(v, (list, tuple)):
            for x in v: add_value(x)
        else:
            s = str(v).strip()
            if s: keys.append(s)

    try:
        add_value(st.secrets.get("GEMINI_API_KEYS"))
        add_value(st.secrets.get("GEMINI_API_KEY"))
        add_value(st.secrets.get("GOOGLE_API_KEY"))
        add_value(st.secrets.get("API_GEMINI_KEYS"))
        add_value(st.secrets.get("API_GEMINI_KEY"))
        add_value(st.secrets.get("API-GEMINI-KEYS"))
    except Exception as e:
        logger.exception("Gagal membaca Streamlit secrets: %s", e)

    seen = set()
    unique_keys = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique_keys.append(k)
    return unique_keys

def parse_two_paragraphs(text):
    if not text or not str(text).strip(): return None, None
    parts = [p.strip() for p in str(text).strip().split("\n\n") if p.strip()]
    if len(parts) >= 2: return parts[0], "\n\n".join(parts[1:])
    if len(parts) == 1: return parts[0], ""
    return None, None


def generate_single_narrative_ai(df_flat, label, prov, moda, bln, thn, prev_bln, prev_thn):
    report_type = f"report_{moda}_{label}"
    period_key = f"{prov}|{bln}|{thn}"
    
    # 1. Cek database terlebih dahulu (Retrieve)
    db_text = get_db_narrative(report_type, period_key)
    if db_text:
        return db_text, "Database (Cached)"

    api_keys = get_gemini_api_keys()
    if not api_keys and not get_openrouter_key():
        return None, "No API Key"
    # api_keys boleh kosong: loop Gemini di bawah lalu tidak jalan dan langsung
    # jatuh ke cadangan OpenRouter. Jangan return di sini.

    candidate_models = [
        "gemini-2.5-flash",
        "gemini-2.6-flash-lite",
        "gemini-3-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite"
    ]

    if "gemini_key_index" not in st.session_state:
        st.session_state["gemini_key_index"] = 0

    num_keys = len(api_keys)
    data_str = df_flat.to_markdown(index=False)
    
    prompt = (
        "Anda adalah Kepala Pusat Statistik / Penasihat Kebijakan Utama yang menyusun ringkasan eksekutif strategis berstandar tinggi bagi Dewan Pimpinan dan Pengambil Kebijakan.\n"
        f"Buatlah narasi Executive Summary tingkat tinggi yang padat dan tajam (tepat 2 paragraf) untuk indikator statistik \"{label}\" pada moda {moda} Wilayah Provinsi {prov} periode komparasi {bln} {thn} terhadap {prev_bln} {prev_thn}.\n\n"
        "Pedoman & Fokus Penulisan:\n"
        "- Paragraf 1: Analisis komprehensif kinerja bulanan (Month-to-Month/MTM), arah tren sektoral, serta kontribusi agregat dari wilayah-wilayah utama dalam hierarki BRS.\n"
        "- Paragraf 2: Analisis mendalam kinerja kumulatif (Year-to-Date / Year-on-Year), pembacaan deviasi pertumbuhan, serta signifikansi fluktuasi antarwilayah dalam kerangka ekonomi regional.\n"
        "- Gunakan diksi birokratik profesional, objektif, analitis, dengan standarisasi format angka Indonesia.\n"
        "- Jangan sertakan pengantar, sapaan, catatan kaki, ataupun penutup. Langsung berikan 2 paragraf teks yang dipisahkan oleh satu baris kosong (\\n\\n).\n\n"
        "Sumber Data Tabel:\n"
        f"{data_str}"
    )

    for attempt in range(num_keys):
        current_idx = (st.session_state["gemini_key_index"] + attempt) % num_keys
        key = api_keys[current_idx]
        
        for model_name in candidate_models:
            try:
                client = genai.Client(api_key=key.strip())
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(temperature=0.2)
                )
                raw_text = getattr(response, "text", None)
                if raw_text and str(raw_text).strip():
                    text_clean = str(raw_text).strip()
                    # Simpan otomatis ke database saat pertama kali di-generate
                    save_db_narrative(report_type, period_key, text_clean)
                    st.session_state["gemini_key_index"] = (current_idx + 1) % num_keys
                    return text_clean, f"Gemini AI ({model_name})"
            except Exception as e:
                logger.warning("Gagal pada Report dengan Key ke-%d menggunakan model %s: %s...", current_idx + 1, model_name, e)
                continue

    # Semua Gemini key gagal -> coba OpenRouter sebagai cadangan.
    backup = openrouter_generate(prompt, temperature=0.2)
    if backup:
        save_db_narrative(report_type, period_key, backup)
        return backup, "OpenRouter (Cadangan)"

    return None, "Failed"

def generate_narrative_fallback(report_flat, col_target, moda, region_label, bln, thn, prev_bln, prev_thn,
                               col_prev, col_curr, col_cum_prev, col_cum_curr, prov=""):
    meta = NARRATIVE_META.get(col_target, {'subject': 'Total volume', 'satuan': '', 'is_penumpang': False})
    angkutan_kecil = 'udara' if moda == 'Transportasi Udara' else 'laut'
    val_decimals = 0 if meta['satuan'] == 'orang' else 2
    fmt = lambda v: format_id_number(v, decimals=val_decimals)
    fmt_pct = lambda v: format_id_number(v, decimals=2)

    subject = meta['subject']
    if meta['is_penumpang']: 
        subject += f" angkutan {angkutan_kecil} domestik"

    total = report_flat.loc['TOTAL']
    total_curr, total_prev, total_mtm = total[col_curr], total[col_prev], total['M-to-M (%)']
    total_cum_curr, total_cum_prev, total_yoy = total[col_cum_curr], total[col_cum_prev], total['Y-on-Y (%)']

    abs_mtm = fmt_pct(abs(total_mtm) if pd.notna(total_mtm) else np.nan)
    abs_yoy = fmt_pct(abs(total_yoy) if pd.notna(total_yoy) else np.nan)

    p1_options = [
        (
            f"Berdasarkan hasil pencatatan data makro sektoral, {subject.lower()} di Provinsi {prov} pada periode {bln} {thn} "
            f"membukukan realisasi agregat sebesar {fmt(total_curr)} {meta['satuan']}. "
            f"Kinerja bulanan tersebut menunjukkan pergerakan yang {_arah_dinamis(total_mtm)} dengan tingkat fluktuasi "
            f"sebesar {abs_mtm} persen apabila dikomparasikan terhadap baseline operasional bulan {prev_bln} {prev_thn} "
            f"yang berada di angka {fmt(total_prev)} {meta['satuan']}."
        ),
        (
            f"Dinamika sektor transportasi mencatat bahwa {subject.lower()} di wilayah Provinsi {prov} "
            f"mencapai volume total {fmt(total_curr)} {meta['satuan']} selama bulan {bln} {thn}. "
            f"Realisasi ini {_arah_dinamis(total_mtm)} sebesar {abs_mtm} persen secara month-to-month (M-to-M) "
            f"dibandingkan kondisi bulan {prev_bln} {prev_thn} yang mencatatkan angka {fmt(total_prev)} {meta['satuan']}."
        )
    ]
    p2_options = [
        (
            f"Secara kumulatif (Year-to-Date hingga {bln} {thn}), akumulasi realisasi {subject.lower()} "
            f"telah menyentuh angka {fmt(total_cum_curr)} {meta['satuan']}. "
            f"Capaian ini mencatatkan tren pertumbuhan yang {_arah_dinamis(total_yoy)} sebesar {abs_yoy} persen "
            f"secara Year-on-Year (Y-on-Y) jika dibandingkan dengan akumulasi periode yang sama pada tahun sebelumnya "
            f"({fmt(total_cum_prev)} {meta['satuan']}), merefleksikan stabilitas aktivitas ekonomi regional."
        ),
        (
            f"Meninjau kinerja tahun berjalan hingga bulan {bln} {thn}, total realisasi kumulatif tercatat sebesar {fmt(total_cum_curr)} "
            f"{meta['satuan']}. Dibandingkan dengan capaian kumulatif Januari–{bln} tahun sebelumnya ({fmt(total_cum_prev)} {meta['satuan']}), "
            f"indikator ini {_arah_dinamis(total_yoy)} di level {abs_yoy} persen secara Y-on-Y."
        )
    ]
    # Seeded per report so narrative variety still exists across different
    # provinces/periods, but the same report always renders the same wording —
    # regenerating or diffing a report must not change the prose.
    rng = random.Random(f"{prov}|{thn}|{bln}|{subject}")
    return rng.choice(p1_options), rng.choice(p2_options)

def create_complete_master_word_report(prov, thn, bln, all_report_data):
    doc = docx.Document()
    doc.add_heading(f"Laporan Komprehensif Perkembangan Transportasi Provinsi {prov} - {bln} {thn}", level=1)
    doc.add_paragraph(f"Dokumen ini memuat seluruh tabel hierarki BRS dan narasi strategis moda Transportasi Udara dan Transportasi Laut.")
    doc.add_paragraph()

    for item in all_report_data:
        table_no = item['table_no']
        label = item['label']
        moda_name = item['moda']
        p1 = item.get('p1', '')
        p2 = item.get('p2', '')
        df_display = item['df_display']

        angkutan = "Angkutan Udara" if moda_name == "Transportasi Udara" else "Angkutan Laut"
        full_title = f"Tabel {table_no} Perkembangan {label} {angkutan} Dalam Negeri Provinsi {prov}, {bln} {thn}"

        doc.add_heading(full_title, level=2)
        if p1:
            clean_p1 = re.sub(r'^\*\(.*?\)\*\n\n', '', p1)
            doc.add_paragraph(clean_p1)
            doc.add_paragraph()
        
        df_to_export = df_display.reset_index()
        total_rows = len(df_to_export) + 2
        total_cols = len(df_to_export.columns)
        
        table = doc.add_table(rows=total_rows, cols=total_cols)
        table.style = 'Table Grid'
        
        hdr_row_0 = table.rows[0]
        hdr_row_1 = table.rows[1]
        
        for j, col_tuple in enumerate(df_to_export.columns):
            if isinstance(col_tuple, tuple):
                lvl_0 = str(col_tuple[0]).strip()
                lvl_1 = str(col_tuple[1]).strip()
                if j == 0:
                    hdr_row_0.cells[j].text = "Wilayah / Entitas"
                    hdr_row_1.cells[j].text = ""
                else:
                    hdr_row_0.cells[j].text = lvl_0
                    hdr_row_1.cells[j].text = lvl_1 if lvl_1 and lvl_1 != lvl_0 else ""
            else:
                hdr_row_0.cells[j].text = str(col_tuple).strip()
                hdr_row_1.cells[j].text = ""

        try:
            hdr_row_0.cells[0].merge(hdr_row_1.cells[0])
            if total_cols >= 4:
                hdr_row_0.cells[1].merge(hdr_row_0.cells[3])
                if total_cols >= 7:
                    hdr_row_0.cells[4].merge(hdr_row_0.cells[6])
        except Exception:
            pass

        for i, row_data in enumerate(df_to_export.values):
            row_cells = table.rows[i + 2].cells
            first_col_val = str(row_data[0]) if not pd.isna(row_data[0]) else ""
            is_separator_row = "lainnya" in first_col_val.lower()

            for j, val in enumerate(row_data):
                if j == 0:
                    row_cells[j].text = str(val) if not pd.isna(val) else ""
                elif is_separator_row or val == "":
                    row_cells[j].text = ""
                else:
                    # pct kolom ikut "undefined" supaya .idml sama dengan tabel web
                    is_pct = '(%)' in str(df_to_export.columns[j]).upper()
                    fmt = format_pct_number if is_pct else format_id_number
                    row_cells[j].text = fmt(val, decimals=2)
                         
        doc.add_paragraph()
        if p2:
            clean_p2 = re.sub(r'^\*\(.*?\)\*\n\n', '', p2)
            doc.add_paragraph(clean_p2)
        doc.add_paragraph("---")

    file_stream = io.BytesIO()
    doc.save(file_stream)
    file_stream.seek(0)
    return file_stream

def prepare_table_item(df_curr, df_prev, df_cum_curr, df_cum_prev, col_target, label, row_col, thn, bln, prev_bln, prev_thn, table_no=None, prov=None, moda=None):
    divisor = 1.0
    if moda == "Transportasi Udara" and "kg" in col_target.lower():
        divisor = 1000.0  # kg -> ton (semua provinsi)

    curr_grp = df_curr.groupby(row_col)[col_target].sum() / divisor
    prev_grp = df_prev.groupby(row_col)[col_target].sum() / divisor
    cum_curr_grp = df_cum_curr.groupby(row_col)[col_target].sum() / divisor
    cum_prev_grp = df_cum_prev.groupby(row_col)[col_target].sum() / divisor

    # Every category must appear even when its value is 0. Indexing on the union
    # of all four series (instead of curr_grp alone) keeps an entity that only
    # has data in an earlier period on the table, where it reads as 0 rather than
    # vanishing. Current-period order first, then anything new, so the visible
    # ordering is unchanged for tables that were already complete.
    report = pd.DataFrame(index=_union_index(curr_grp, prev_grp, cum_curr_grp, cum_prev_grp))
    col_curr, col_prev = f"{bln} {thn}", f"{prev_bln} {prev_thn}"
    col_cum_curr, col_cum_prev = f"Jan-{bln} {thn}", f"Jan-{bln} {int(thn)-1}"

    report[col_prev] = prev_grp.reindex(report.index).fillna(0)
    report[col_curr] = curr_grp.reindex(report.index).fillna(0)
    
    # float cast: kolom hasil agregasi bisa bertipe object/nullable, dan pada dtype
    # itu 0 / 0 memanggil operator Python -> ZeroDivisionError yang tidak bisa
    # ditekan np.errstate (hanya berlaku untuk operasi numpy-native).
    prev_vals = report[col_prev].values.astype(float)
    curr_vals = report[col_curr].values.astype(float)
    report['M-to-M (%)'] = [safe_pct_change(c, p) for c, p in zip(curr_vals, prev_vals)]

    report[col_cum_prev] = cum_prev_grp.reindex(report.index).fillna(0)
    report[col_cum_curr] = cum_curr_grp.reindex(report.index).fillna(0)
    
    cum_prev_vals = report[col_cum_prev].values.astype(float)
    cum_curr_vals = report[col_cum_curr].values.astype(float)
    report['Y-on-Y (%)'] = [safe_pct_change(c, p) for c, p in zip(cum_curr_vals, cum_prev_vals)]

    # ponytail: totals come from the full grouped series, not report[...] — the
    # union index exists so a prev-only entity still shows as a row, but summing
    # the groups is what guarantees the total matches the source data.
    sum_prev = prev_grp.sum()
    sum_curr = curr_grp.sum()
    sum_cum_prev = cum_prev_grp.sum()
    sum_cum_curr = cum_curr_grp.sum()

    total_mtm = safe_pct_change(sum_curr, sum_prev)
    total_yoy = safe_pct_change(sum_cum_curr, sum_cum_prev)

    total_row = pd.DataFrame([{
        col_prev: sum_prev, col_curr: sum_curr, 'M-to-M (%)': total_mtm,
        col_cum_prev: sum_cum_prev, col_cum_curr: sum_cum_curr, 'Y-on-Y (%)': total_yoy
    }], index=['TOTAL'])[report.columns]

    report_flat = pd.concat([report, total_row])
    report_display_brs = build_brs_display_table(report_flat, prov, moda)

    cum_label = f"Kumulatif {label}"
    report_display = report_display_brs.copy()
    report_display.columns = pd.MultiIndex.from_tuples([
        (label, col_prev), (label, col_curr), (label, 'M-to-M (%)'),
        (cum_label, col_cum_prev), (cum_label, col_cum_curr), (cum_label, 'Y-on-Y (%)')
    ])
    pct_cols = [(label, 'M-to-M (%)'), (cum_label, 'Y-on-Y (%)')]

    def style_brs_hierarchy(styler):
        def highlight_rows(row):
            styles = [''] * len(row)
            val = str(row.name).strip().lower()
            if val in ['subtotal', 'sub total', 'jumlah']:
                styles = ['font-weight: bold; background-color: #f39c12; color: #ffffff;'] * len(row)
            elif val in ['total', 'total keseluruhan']:
                styles = ['font-weight: bold; background-color: #d35400; color: #ffffff;'] * len(row)
            elif 'lainnya' in val:
                styles = ['font-style: italic; background-color: #e8eaed; color: #000000;'] * len(row)
            return styles
            
        styler = styler.format(table_formatters(report_display.columns, col_target)).background_gradient(subset=pct_cols, cmap='RdYlGn')
        styler = styler.apply(highlight_rows, axis=1)
        return styler

    styled_df = style_brs_hierarchy(report_display.style)

    return {
        'moda': moda, 'table_no': table_no, 'label': label, 'col_target': col_target,
        'prov': prov, 'bln': bln, 'thn': thn, 'prev_bln': prev_bln, 'prev_thn': prev_thn,
        'col_prev': col_prev, 'col_curr': col_curr, 'col_cum_prev': col_cum_prev, 'col_cum_curr': col_cum_curr,
        'report_flat': report_flat, 'report_display_brs': report_display_brs,
        'df_display': report_display, 'styled_df': styled_df,
    }

def render_tables_and_narratives(all_collected_data):
    if not all_collected_data:
        return

    current_moda = None
    for item in all_collected_data:
        with st.container(border=True):
            if current_moda != item['moda']:
                current_moda = item['moda']
                icon = "✈️" if current_moda == "Transportasi Udara" else "🚢"
                st.subheader(f"{icon} Moda: {current_moda}")

            angkutan = "Angkutan Udara" if current_moda == "Transportasi Udara" else "Angkutan Laut"
            judul = f"Tabel {item['table_no']} Perkembangan {item['label']} {angkutan} Dalam Negeri Provinsi {item['prov']}, {item['bln']} {item['thn']}"
            st.markdown(f"**{judul}**")
            
            # Perbaikan agar dataframe ter-render sempurna di dalam card border
            st.dataframe(item['styled_df'], width='stretch')

            region_label = "Bandara" if current_moda == "Transportasi Udara" else "Pelabuhan/Kabupaten"

            h1, h2 = st.columns([5, 1])
            with h1:
                st.markdown(f"**📝 Executive Summary — {item['label']}**")
            with h2:
                if st.session_state.get("role") == "admin":
                    if st.button("🔄 Regenerasi", key=f"regen_report_{item['table_no']}", width='stretch'):
                        report_type = f"report_{current_moda}_{item['label']}"
                        period_key = f"{item['prov']}|{item['bln']}|{item['thn']}"
                        delete_db_narrative(report_type, period_key)
                        st.rerun()

            with st.spinner(f"Menyusun Executive Summary untuk {item['label']}..."):
                text_final, source = generate_single_narrative_ai(
                    item['report_display_brs'].reset_index(), 
                    item['label'], 
                    item['prov'], 
                    current_moda, 
                    item['bln'], 
                    item['thn'], 
                    item['prev_bln'], 
                    item['prev_thn']
                )

            if text_final:
                p1, p2 = parse_two_paragraphs(text_final)
                p1_text = f"*(Executive Summary - [{source}])*\n\n{p1}" if p1 else ""
                p2_text = p2 if p2 else ""
            else:
                p1, p2 = generate_narrative_fallback(
                    report_flat=item['report_flat'], col_target=item['col_target'], moda=current_moda,
                    region_label=region_label, bln=item['bln'], thn=item['thn'], prev_bln=item['prev_bln'],
                    prev_thn=item['prev_thn'], col_prev=item['col_prev'], col_curr=item['col_curr'],
                    col_cum_prev=item['col_cum_prev'], col_cum_curr=item['col_cum_curr'], prov=item['prov']
                )
                p1_text = f"*(Executive Summary - Sistem Fallback)*\n\n{p1}"
                p2_text = p2

            if p1_text: st.markdown(p1_text)
            if p2_text: st.markdown(p2_text)
            
            # satu titik untuk web + Word + .idml: narasi tidak boleh punya
            # format angka sendiri yang beda dari tabel.
            item['p1'] = normalize_narrative(p1_text, item['col_target'])
            item['p2'] = normalize_narrative(p2_text, item['col_target'])

# ==============================================================================
# EXPORT KE TEMPLATE INDESIGN (.idml)
# ==============================================================================
# DEFAULT_IDML_TEMPLATE ikut diimpor dari modules.indesign_export (lihat baris import)

def _kab_lookup_brs(name):
    if str(name).strip().upper() == "DOUW ATURURE":
        return "KABUPATEN NABIRE"
    return get_location_metadata(name)["kab"]

def render_indesign_export(meta, all_collected_data):
    # CSS injection to make dark button text clearly visible (white)
    # Hanya tombol berlatar gelap/amber yang jadi putih. Tombol putih (sidebar,
    # secondary, unduh) harus tetap tinta gelap — putih di atas putih hilang.
    st.markdown("""
        <style>
        div[data-testid="stMain"] .stButton>button[kind="primary"],
        div[data-testid="stMain"] .stButton>button[kind="primary"] * {
            color: #FFFFFF !important;
        }
        div[data-testid="stDownloadButton"] button,
        div[data-testid="stDownloadButton"] button * {
            color: #0F172A !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("🎨 Isi Template InDesign (BRS)")
    st.caption(
        "Tabel, narasi, poin utama, dan ringkasan cover dari laporan di atas dimasukkan ke template "
        ".idml. Hasilnya dibuka di InDesign lalu disimpan sebagai .indd/PDF."
    )
    
    with st.container(border=True):
        st.markdown("### 📋 Pengaturan & Berkas BRS")
        c_info, c_qr = st.columns(2)
        
        with c_info:
            with st.container(border=True):
                st.markdown("**1. Infografis**")
                info_up = st.file_uploader(
                    "Unggah Gambar Infografis",
                    type=["png", "jpg", "jpeg"],
                    key="idml_info",
                    help="Gambar infografis untuk periode ini.",
                )
                
        with c_qr:
            with st.container(border=True):
                st.markdown("**2. Kode QR**")
                qr_up = st.file_uploader(
                    "Unggah Gambar Kode QR",
                    type=["png", "jpg", "jpeg"],
                    key="idml_qr",
                    help="Kode QR untuk periode ini.",
                )
                
        c_no, c_tgl = st.columns(2)
        with c_no:
            with st.container(border=True):
                st.markdown("**3. Nomor BRS**")
                nomor_brs = st.text_input(
                    "Nomor BRS",
                    key="idml_no",
                    placeholder="mis. 235/10/94/Th. XXIX",
                    label_visibility="collapsed"
                )
                
        with c_tgl:
            with st.container(border=True):
                st.markdown("**4. Tanggal Rilis**")
                tgl_rilis = st.text_input(
                    "Tanggal Rilis",
                    key="idml_tgl",
                    placeholder="mis. 1 Oktober 2026",
                    label_visibility="collapsed"
                )
                
        if DEFAULT_IDML_TEMPLATE.exists():
            st.caption(f"Template: **{DEFAULT_IDML_TEMPLATE.name}** (bawaan)")
        else:
            st.caption("⚠️ Template bawaan tidak ditemukan.")

    if st.button("🎨 Buat File InDesign (.idml)", width='stretch', key="btn_idml"):
        if not DEFAULT_IDML_TEMPLATE.exists():
            st.error("Template bawaan tidak ditemukan.")
            return
        tpl_bytes = DEFAULT_IDML_TEMPLATE.read_bytes()
        laut_ports = None
        try:
            df_laut = get_comparison_data(meta['prov'], meta['thn'], meta['bln'], "Transportasi Laut")[0]
            if not df_laut.empty:
                laut_ports = [re.split(r"\s*/\s*", str(x))[0] for x in df_laut['nama_pelabuhan'].drop_duplicates()]
        except Exception as e:
            logger.warning("Gagal mengambil daftar pelabuhan: %s", e)
        try:
            res = fill_brs_template(
                tpl_bytes, meta['prov'], meta['thn'], meta['bln'], all_collected_data, _kab_lookup_brs,
                nomor_brs=nomor_brs.strip() or None, tanggal_rilis=tgl_rilis.strip() or None,
                laut_ports=laut_ports,
                qr_bytes=qr_up.getvalue() if qr_up is not None else None,
                qr_name=qr_up.name if qr_up is not None else None,
                infografis_bytes=info_up.getvalue() if info_up is not None else None,
                infografis_name=info_up.name if info_up is not None else None,
            )
            st.session_state['idml_result'] = (res, meta)
        except Exception as e:
            logger.exception("Gagal mengisi template IDML")
            st.error(f"Gagal mengisi template: {e}")

    stored = st.session_state.get('idml_result')
    if stored:
        res, m = stored
        nama_idml = f"BRS_Transportasi_{m['prov'].replace(' ', '_')}_{m['bln']}_{m['thn']}.idml"
        st.download_button(
            "📦 Download Paket (.zip: .idml + PNG)", data=paket_zip(res.idml_bytes, nama_idml),
            file_name=f"BRS_Transportasi_{m['prov'].replace(' ', '_')}_{m['bln']}_{m['thn']}.zip",
            mime="application/zip",
            key="dl_zip_trans",
        )
        with st.expander(f"⚠️ {len(res.warnings)} hal yang perlu dicek di InDesign"):
            for w in res.warnings:
                st.markdown(f"- {w}")

def _available_years():
    """Years that actually have data, ascending. Mirrors the admin page's
    SELECT DISTINCT tahun pattern; both modalities are unioned because the report
    renders udara and laut for the same period."""
    engine = get_engine()
    years = set()
    for table in ("transportasi_udara", "transportasi_laut"):
        try:
            rows = pd.read_sql(text(f"SELECT DISTINCT tahun FROM {table}"), engine)
            years.update(str(y) for y in rows["tahun"].dropna())
        except Exception:
            continue
    return sorted(years, key=int) or [str(datetime.now().year)]


def collect_report_tables(prov, thn, bln):
    """Kumpulkan 8 item tabel untuk satu (prov, thn, bln) dari database.

    Dipakai oleh tombol Generate (Admin), tombol Show Report, dan sinkronisasi
    ulang saat filter berubah — supaya ketiga jalur itu tidak bisa berbeda.
    """
    out = []
    n = 1
    for moda, targets in (
        ("Transportasi Udara", [
            ('penumpang_datang', 'Penumpang Datang'), ('penumpang_berangkat', 'Penumpang Berangkat'),
            ('barang_bongkar_kg', 'Barang Bongkar (Ton)'),
            ('barang_muat_kg', 'Barang Muat (Ton)')]),
        ("Transportasi Laut", [
            ('dn_penumpang_turun', 'Penumpang Turun'), ('dn_penumpang_naik', 'Penumpang Naik'),
            ('dn_bongkar_barang_ton', 'Barang Bongkar (Ton)'), ('dn_muat_barang_ton', 'Barang Muat (Ton)')]),
    ):
        cu, pr, cc, cp, p_bln, p_thn = get_comparison_data(prov, thn, bln, moda)
        if cu.empty:
            continue
        if moda == "Transportasi Udara":
            row_col = 'nama_bandara'
        else:
            row_col = 'nama_kabkota' if prov == "Papua Tengah" else 'nama_pelabuhan'
        for col, label in targets:
            out.append(prepare_table_item(
                cu, pr, cc, cp, col, label, row_col, thn, bln, p_bln, p_thn,
                table_no=n, prov=prov, moda=moda))
            n += 1
    return out


def meta_matches_filter(meta, now):
    """True kalau meta yang tersimpan masih sama dengan filter yang aktif.

    thn bisa str atau int tergantung sumbernya, jadi dibandingkan sebagai str.
    Key yang hilang dianggap tidak cocok supaya data stale tidak ikut diekspor.
    """
    return all(str(meta.get(k)) == str(v) for k, v in now.items())


def show_report_page():
    st.title("📋 Laporan Komparatif Strategis")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        prov = st.selectbox(
            "Provinsi", list(PEMETAAN_WILAYAH.keys()),
            help="Wilayah yang dilaporkan.",
        )
    with c2:
        year_options = _available_years()
        thn = st.selectbox(
            "Tahun", year_options, index=len(year_options) - 1,
            help="Tahun periode laporan.",
        )
    with c3:
        bln = st.selectbox(
            "Bulan", list(MONTH_MAP.keys()),
            help="Bulan periode laporan.",
        )

    # Cek hak akses role pengguna
    is_admin = st.session_state.get("role") == "admin"

    col_btn1, col_btn2 = st.columns(2)
    
    with col_btn1:
        # Tombol khusus Admin untuk Generate/Regenerate Laporan Baru
        if is_admin:
            if st.button("⚙️ Generate Semua Laporan (Admin)", width='stretch'):
                all_collected_data = collect_report_tables(prov, thn, bln)
                if all_collected_data:
                    st.session_state['report_all_data'] = all_collected_data
                    st.session_state['report_meta'] = {'prov': prov, 'thn': thn, 'bln': bln}
                    st.success("Laporan berhasil digenerate dan disimpan ke database!")
        else:
            st.markdown("") # Kosongkan placeholder kolom untuk user non-admin

    with col_btn2:
        # Tombol untuk User Umum / Semua User untuk melakukan Show Report (Retrieve dari DB)
        if st.button("📊 Show Report", width='stretch'):
            all_collected_data = collect_report_tables(prov, thn, bln)
            if all_collected_data:
                st.session_state['report_all_data'] = all_collected_data
                st.session_state['report_meta'] = {'prov': prov, 'thn': thn, 'bln': bln}
            else:
                st.warning("Data untuk periode atau provinsi tersebut tidak ditemukan.")

    if st.session_state.get('report_all_data'):
        all_collected_data = st.session_state['report_all_data']
        meta = st.session_state.get('report_meta', {'prov': prov, 'thn': thn, 'bln': bln})

        # Filter di atas adalah widget hidup: begitu diubah, Streamlit rerun tapi
        # report_all_data masih berisi periode lama. Kalau export boleh tetap jalan,
        # judul/header/nama file akan menyebut periode yang berbeda dari yang
        # difilter — persis masalah "konteks tidak cocok". Jadi data diminta ulang
        # untuk periode yang sedang dipilih.
        _now = {'prov': prov, 'thn': thn, 'bln': bln}
        if not meta_matches_filter(meta, _now):
            _fresh = collect_report_tables(prov, thn, bln)
            if _fresh:
                all_collected_data = _fresh
                meta = _now
                st.session_state['report_all_data'] = _fresh
                st.session_state['report_meta'] = _now
                st.info(
                    f"Filter diubah ke **{prov} · {bln} {thn}** — laporan dimuat ulang "
                    "untuk periode tersebut."
                )
            else:
                st.session_state.pop('report_all_data', None)
                st.session_state.pop('report_meta', None)
                all_collected_data = []
                st.warning(
                    f"Tidak ada data untuk **{prov} · {bln} {thn}**. "
                    "Laporan yang tampil sudah dikosongkan."
                )

        # Saat fungsi render dipanggil, teks narasi otomatis di-retrieve dari database melalui fungsi get_db_narrative()
        render_tables_and_narratives(all_collected_data)

        if all_collected_data:
            master_word_file = create_complete_master_word_report(
                meta['prov'], meta['thn'], meta['bln'], all_collected_data
            )
            st.success("Laporan berhasil dimuat!")
            st.download_button(
                label="📥 Download Master Dokumen Word (Semua 8 Tabel & Narasi)",
                data=master_word_file,
                file_name=f"Master_Laporan_Transportasi_{meta['prov']}_{meta['bln']}_{meta['thn']}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            render_indesign_export(meta, all_collected_data)
