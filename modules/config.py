import pandas as pd
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_env():
    """Load the project-root .env into os.environ.

    Idempotent and never overrides a variable that is already set in the real
    environment, so a real env var or a st.secrets entry always wins. Safe to
    call from any module; a missing .env is not an error.
    """
    from dotenv import load_dotenv

    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
    return env_path


def read_secret(key, default=None):
    """st.secrets.get(key) that returns `default` instead of raising.

    st.secrets raises StreamlitSecretNotFoundError when no secrets.toml exists
    at all, so an unguarded read anywhere takes down the whole page. Every
    caller that touches st.secrets must go through this.
    """
    try:
        import streamlit as st

        value = st.secrets.get(key)
    except Exception:
        return default
    return default if value in (None, "") else value


# 1. Pemetaan Provinsi & Kabupaten Terbaru
PEMETAAN_WILAYAH = {
    'Papua': ['KABUPATEN BIAK NUMFOR', 'KABUPATEN JAYAPURA', 'KABUPATEN KEEROM', 'KABUPATEN KEPULAUAN YAPEN', 'KABUPATEN MAMBERAMO RAYA', 'KABUPATEN SARMI', 'KABUPATEN SUPIORI', 'KABUPATEN WAROPEN', 'KOTA JAYAPURA'],
    'Papua Selatan': ['KABUPATEN ASMAT', 'KABUPATEN BOVEN DIGOEL', 'KABUPATEN MAPPI', 'KABUPATEN MERAUKE'],
    'Papua Tengah': ['KABUPATEN DEIYAI', 'KABUPATEN DOGIYAI', 'KABUPATEN INTAN JAYA', 'KABUPATEN MIMIKA', 'KABUPATEN NABIRE', 'KABUPATEN PANIAI', 'KABUPATEN PUNCAK', 'KABUPATEN PUNCAK JAYA'],
    'Papua Pegunungan': ['KABUPATEN JAYAWIJAYA', 'KABUPATEN LANNY JAYA', 'KABUPATEN MAMBERAMO TENGAH', 'KABUPATEN NDUGA', 'KABUPATEN PEGUNUNGAN BINTANG', 'KABUPATEN TOLIKARA', 'KABUPATEN YAHUKIMO', 'KABUPATEN YALIMO']
}

# 2. Pemetaan Detail Lokasi Komprehensif
MAPPING_LOKASI_KAB_PROV = {
    'MOPPAH': {'kab': 'KABUPATEN MERAUKE', 'prov': 'PAPUA SELATAN'},
    'OKABA': {'kab': 'KABUPATEN MERAUKE', 'prov': 'PAPUA SELATAN'},
    'WAMENA': {'kab': 'KABUPATEN JAYAWIJAYA', 'prov': 'PAPUA PEGUNUNGAN'},
    'SENTANI': {'kab': 'KABUPATEN JAYAPURA', 'prov': 'PAPUA'},
    'NABIRE': {'kab': 'KABUPATEN NABIRE', 'prov': 'PAPUA TENGAH'},
    'STEVANUS RUMBEWAS': {'kab': 'KABUPATEN KEPULAUAN YAPEN', 'prov': 'PAPUA'},
    'FRANS KAISIEPO': {'kab': 'KABUPATEN BIAK NUMFOR', 'prov': 'PAPUA'},
    'ENAROTALI': {'kab': 'KABUPATEN PANIAI', 'prov': 'PAPUA TENGAH'},
    'ZUGAPA BILORAI': {'kab': 'KABUPATEN INTAN JAYA', 'prov': 'PAPUA TENGAH'},
    'MULIA': {'kab': 'KABUPATEN PUNCAK JAYA', 'prov': 'PAPUA TENGAH'},
    'MOZES KILANGIN': {'kab': 'KABUPATEN MIMIKA', 'prov': 'PAPUA TENGAH'},
    'MINDIPTANAH': {'kab': 'KABUPATEN BOVEN DIGOEL', 'prov': 'PAPUA SELATAN'},
    'TANAH MERAH': {'kab': 'KABUPATEN BOVEN DIGOEL', 'prov': 'PAPUA SELATAN'},
    'BOMAKIA': {'kab': 'KABUPATEN BOVEN DIGOEL', 'prov': 'PAPUA SELATAN'},
    'KEPI': {'kab': 'KABUPATEN MAPPI', 'prov': 'PAPUA SELATAN'},
    'BADE': {'kab': 'KABUPATEN MAPPI', 'prov': 'PAPUA SELATAN'},
    'EWER': {'kab': 'KABUPATEN ASMAT', 'prov': 'PAPUA SELATAN'},
    'KAMUR': {'kab': 'KABUPATEN ASMAT', 'prov': 'PAPUA SELATAN'},
    'DEKAI': {'kab': 'KABUPATEN YAHUKIMO', 'prov': 'PAPUA PEGUNUNGAN'},
    'OKSIBIL': {'kab': 'KABUPATEN PEGUNUNGAN BINTANG', 'prov': 'PAPUA PEGUNUNGAN'},
    'BATOM': {'kab': 'KABUPATEN PEGUNUNGAN BINTANG', 'prov': 'PAPUA PEGUNUNGAN'},
    'KARUBAGA': {'kab': 'KABUPATEN TOLIKARA', 'prov': 'PAPUA PEGUNUNGAN'},
    'MARARENA': {'kab': 'KABUPATEN SARMI', 'prov': 'PAPUA'},
    'KASONAWEJA': {'kab': 'KABUPATEN MAMBERAMO RAYA', 'prov': 'PAPUA'},
    'ILLAGA': {'kab': 'KABUPATEN PUNCAK', 'prov': 'PAPUA TENGAH'},
    'SINAK': {'kab': 'KABUPATEN PUNCAK', 'prov': 'PAPUA TENGAH'},
    'BEOGA': {'kab': 'KABUPATEN PUNCAK', 'prov': 'PAPUA TENGAH'},
    'MOANAMANI': {'kab': 'KABUPATEN DOGIYAI', 'prov': 'PAPUA TENGAH'},
    'MERAUKE': {'kab': 'KABUPATEN MERAUKE', 'prov': 'PAPUA SELATAN'},
    'NABIRE / TELUK KINI': {'kab': 'KABUPATEN NABIRE', 'prov': 'PAPUA TENGAH'},
    'SERUI': {'kab': 'KABUPATEN KEPULAUAN YAPEN', 'prov': 'PAPUA'},
    'BIAK': {'kab': 'KABUPATEN BIAK NUMFOR', 'prov': 'PAPUA'},
    'AMAMAPARE': {'kab': 'KABUPATEN MIMIKA', 'prov': 'PAPUA TENGAH'},
    'POMAKO': {'kab': 'KABUPATEN MIMIKA', 'prov': 'PAPUA TENGAH'},
    'HABESILAM': {'kab': 'KABUPATEN MAPPI', 'prov': 'PAPUA SELATAN'},
    'AGATS': {'kab': 'KABUPATEN ASMAT', 'prov': 'PAPUA SELATAN'},
    'ATSY': {'kab': 'KABUPATEN ASMAT', 'prov': 'PAPUA SELATAN'},
    'SARMI': {'kab': 'KABUPATEN SARMI', 'prov': 'PAPUA'},
    'WAREN': {'kab': 'KABUPATEN WAROPEN', 'prov': 'PAPUA'},
    'JAYAPURA': {'kab': 'KOTA JAYAPURA', 'prov': 'PAPUA'}
}

def get_location_metadata(name):
    # Tangani jika data kosong (NaN/None)
    if pd.isna(name) or name is None:
        return {'kab': None, 'prov': 'PAPUA'}
        
    name_clean = str(name).upper().strip()
    
    # 1. Prioritas Pertama: Pencocokan Persis (Exact Match)
    # Ini lebih aman untuk mencegah salah deteksi nama yang mirip
    if name_clean in MAPPING_LOKASI_KAB_PROV:
        return MAPPING_LOKASI_KAB_PROV[name_clean]
        
    # 2. Prioritas Kedua: Pencocokan Sebagian (Partial Match)
    # Berguna jika format BPS menuliskan "BANDARA SENTANI" (sedangkan key hanya "SENTANI")
    # ponytail: iterate longest-key-first so the most specific key always wins.
    # Previously this depended on dict insertion order — e.g. 'BIAK' is a substring
    # of 'BIAK NUMFOR' — so a future key inserted above another could silently
    # change a mapping.
    for key in sorted(MAPPING_LOKASI_KAB_PROV, key=len, reverse=True):
        if key in name_clean:
            return MAPPING_LOKASI_KAB_PROV[key]
            
    # Fallback jika tidak ada lokasi yang dikenali sama sekali
    return {'kab': None, 'prov': 'PAPUA'}

def get_province_by_kabupaten(kab_name):
    # 1. Bersihkan input dari data excel (Hapus kata KABUPATEN/KOTA jika terbawa)
    kab_clean = str(kab_name).upper().strip()
    kab_clean = kab_clean.replace('KABUPATEN ', '').replace('KOTA ', '').strip()
    
    # Khusus untuk Jayapura, kita biarkan manual karena ada Kabupaten dan Kota
    # Namun BPS biasanya membedakan kodenya atau menulis 'KOTA JAYAPURA'
    if kab_clean == 'JAYAPURA' and 'KOTA' in str(kab_name).upper():
        return 'PAPUA' # Kota Jayapura tetap di Papua
        
    # 2. Cocokkan dengan dictionary
    for prov, kabs in PEMETAAN_WILAYAH.items():
        for k in kabs:
            # Bersihkan juga nama di dictionary saat mencocokkan
            k_clean = k.upper().replace('KABUPATEN ', '').replace('KOTA ', '').strip()
            
            # Jika nama kabupaten/kota cocok
            if kab_clean == k_clean:
                return prov.upper()
                
    # Fallback: None signals "unmapped". Callers (the ETL) must fail loudly on this
    # rather than silently attributing an unknown kabupaten to Papua's totals.
    return None

