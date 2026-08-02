import pandas as pd
import io
import re
from modules.config import get_province_by_kabupaten

MONTH_NAMES = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
               'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']


def clean_number(val):
    if pd.isna(val):
        return 0.0
    val_str = str(val).strip()
    val_clean = val_str.replace('.', '').replace(',', '.')
    try:
        return float(val_clean)
    except ValueError:
        return 0.0

def extract_code_and_name(text):
    match = re.search(r'\[(.*?)\]\s*(.*)', str(text))
    if match:
        return match.group(1), match.group(2)
    return '', str(text)


def detect_file_metadata(file_content):
    """Deteksi otomatis moda transportasi, tahun, dan bulan dari header tabel BPS
    (mis. "PROVINSI PAPUA Tahun 2025 Bulan April"), tanpa perlu input manual admin."""
    try:
        df_raw = pd.read_html(io.BytesIO(file_content))[0]
    except Exception:
        return {'table_type': None, 'tahun': None, 'bulan': None}

    # Gabungkan seluruh level header (bukan str(columns) yang terpotong "...")
    # menjadi satu teks pencarian yang utuh.
    header_parts = []
    for col_tuple in df_raw.columns:
        if isinstance(col_tuple, tuple):
            header_parts.extend(str(x) for x in col_tuple)
        else:
            header_parts.append(str(col_tuple))
    header_str = " | ".join(header_parts)

    if 'Transportasi Laut' in header_str:
        table_type = 'transportasi_laut'
    elif 'Pesawat Terbang' in header_str or 'Bandara' in header_str:
        table_type = 'transportasi_udara'
    else:
        table_type = None

    tahun, bulan = None, None
    match = re.search(r'Tahun\s+(\d{4})\s+Bulan\s+([A-Za-z]+)', header_str)
    if match:
        tahun = int(match.group(1))
        bulan_raw = match.group(2).strip()
        for m in MONTH_NAMES:
            if m.lower() == bulan_raw.lower():
                bulan = m
                break

    return {'table_type': table_type, 'tahun': tahun, 'bulan': bulan}

def parse_transport_file(file_content, tahun, bulan):
    # Pass 1: quick read just to discover the column count.
    ncols = pd.read_html(io.BytesIO(file_content))[0].shape[1]

    # Pass 2: re-read forcing every column to raw string, and disable
    # pandas' default thousands=',' behavior. Without this, pandas'
    # automatic numeric type-inference (or its comma-as-thousands default)
    # silently corrupts BPS-formatted numbers such as "10.000" (=10000)
    # or "2,5" (=2.5) before clean_number() ever sees them.
    #
    # Some BPS files have trailing fully-empty columns (e.g. unused
    # "Unnamed" columns from merged/blank header cells). When `converters`
    # is supplied, pandas' HTML parser doesn't pad rows for those trailing
    # empty columns the way it does without converters, causing an
    # IndexError. We retry with a progressively smaller converter range
    # until it succeeds — any trimmed trailing columns are unused by
    # process_laut/process_udara anyway (they only read up to column 10).
    df_raw = None
    for n in range(ncols, 0, -1):
        try:
            dfs = pd.read_html(
                io.BytesIO(file_content),
                converters={i: str for i in range(n)},
                thousands=None,
            )
            df_raw = dfs[0]
            break
        except IndexError:
            continue

    if df_raw is None:
        raise ValueError('Gagal membaca struktur tabel pada file ini.')

    header_str = str(df_raw.columns)

    if 'Transportasi Laut' in header_str:
        table_type = 'transportasi_laut'
        df = process_laut(df_raw, tahun, bulan)
    elif 'Pesawat Terbang' in header_str or 'Bandara' in header_str:
        table_type = 'transportasi_udara'
        df = process_udara(df_raw, tahun, bulan)
    else:
        raise ValueError('Format header tabel tidak dikenali!')

    df['nama_provinsi'] = df['nama_kabkota'].apply(get_province_by_kabupaten)
    return table_type, df

def process_laut(df_raw, tahun, bulan):
    data_rows = df_raw.iloc[0:].values
    parsed_data = []
    for row in data_rows:
        prov_code, _ = extract_code_and_name(row[0])
        kab_code, kab_name = extract_code_and_name(row[1])
        pel_code, pel_name = extract_code_and_name(row[2])
        
        # 🚨 FILTER BARU: Lewati baris "JUMLAH" atau data yang tidak memiliki kode valid
        if not prov_code or not kab_code or not pel_code:
            continue
            
        parsed_data.append({
            'tahun': str(tahun), 'bulan': bulan, 'kode_provinsi': prov_code,
            'kode_kabkota': kab_code, 'nama_kabkota': kab_name, 
            'kode_pelabuhan': pel_code, 'nama_pelabuhan': pel_name,
            'dn_penumpang_turun': int(clean_number(row[4])), 
            'dn_penumpang_naik': int(clean_number(row[5])),
            'dn_bongkar_barang_ton': clean_number(row[6]), 
            'dn_muat_barang_ton': clean_number(row[7])
        })
    return pd.DataFrame(parsed_data)

def process_udara(df_raw, tahun, bulan):
    data_rows = df_raw.iloc[0:].values
    parsed_data = []
    for row in data_rows:
        prov_code, _ = extract_code_and_name(row[0])
        kab_code, kab_name = extract_code_and_name(row[1])
        ban_code, ban_name = extract_code_and_name(row[2])
        
        # 🚨 FILTER BARU: Lewati baris "JUMLAH" atau data yang tidak memiliki kode valid
        if not prov_code or not kab_code or not ban_code:
            continue
            
        parsed_data.append({
            'tahun': str(tahun), 'bulan': bulan, 'kode_provinsi': prov_code,
            'kode_kabkota': kab_code, 'nama_kabkota': kab_name, 
            'kode_bandara': ban_code, 'nama_bandara': ban_name,
            'pesawat_berangkat': int(clean_number(row[4])), 
            'pesawat_datang': int(clean_number(row[5])),
            'penumpang_berangkat': int(clean_number(row[6])), 
            'penumpang_datang': int(clean_number(row[7])),
            'barang_muat_kg': clean_number(row[9]), 
            'barang_bongkar_kg': clean_number(row[10])
        })
    return pd.DataFrame(parsed_data)
