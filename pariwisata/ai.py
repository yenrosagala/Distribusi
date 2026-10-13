import logging
import os

import numpy as np
import pandas as pd
import streamlit as st
from google import genai
from sqlalchemy import text

from modules.ai_backup import get_openrouter_key, openrouter_generate, GEMINI_CANDIDATE_MODELS
from modules.config import read_secret
from modules.etl_engine import MONTH_NAMES
from modules.report_page import normalize_narrative, italicize_english
from pariwisata.etl_engine import PARIWISATA_REF_AVG

logger = logging.getLogger(__name__)


def _month_year(year, month):
    """Indonesian period label, e.g. (2026, 8) -> 'Agustus 2026'."""
    return f"{MONTH_NAMES[int(month) - 1]} {int(year)}"


def _load_ref_avg(etl_engine_instance, province):
    """Official TPK/RLMTGAB per jenis (uploaded Prov_Jenis sheet).

    Returns {(jenis_akomodasi, year, month): {'tpk', 'rlmtgab'}}. Missing table
    (older DB) or no rows -> {}, so the caller falls back to the computed mean.
    """
    try:
        with etl_engine_instance.engine.connect() as conn:
            df = pd.read_sql_query(
                text(f"SELECT jenis_akomodasi, year, month, tpk, rlmtgab FROM {PARIWISATA_REF_AVG} WHERE kd_prov = :prov"),
                conn, params={"prov": str(province).strip()},
            )
    except Exception:
        logger.warning("Gagal baca tabel referensi %s", PARIWISATA_REF_AVG, exc_info=True)
        return {}
    return {
        (r.jenis_akomodasi, int(r.year), int(r.month)): {"tpk": float(r.tpk), "rlmtgab": float(r.rlmtgab)}
        for r in df.itertuples()
    }


def _api_keys():
    """All configured Gemini keys: the secrets list, else a single env/secret."""
    raw = read_secret("GEMINI_API_KEYS")
    api_keys = [raw] if isinstance(raw, str) else list(raw or [])
    if not api_keys:
        env_key = os.getenv('GEMINI_API_KEY') or read_secret('GEMINI_API_KEY')
        if env_key:
            api_keys = [env_key]
    return [str(k).strip() for k in api_keys if k and str(k).strip()]


def get_gemini_client():
    # First key that constructs wins; _gemini_text cycles the rest on failure.
    for key in _api_keys():
        try:
            return genai.Client(api_key=key)
        except Exception:
            continue
    return None


def _gemini_text(prompt, client=None):
    """First non-empty reply from the injected client then every key x model.

    ``client`` is the one from :func:`get_gemini_client` (kept injectable for
    tests) and is tried first; every configured key follows so one exhausted
    key, or a retired model id, no longer takes the whole page's AI down.
    """
    clients = [client] if client is not None else []
    for key in _api_keys():
        try:
            clients.append(genai.Client(api_key=key))
        except Exception:
            continue
    for c in clients:
        for model_name in GEMINI_CANDIDATE_MODELS:
            try:
                text = c.models.generate_content(model=model_name, contents=prompt).text
            except Exception as exc:
                logger.warning("Gemini gagal (%s), coba model berikutnya: %s", model_name, exc)
                continue
            if text and str(text).strip():
                return str(text).strip()
    return None


def generate_akomodasi_tables(etl_engine_instance, province, year, month, trend_chart=None):
    client = get_gemini_client()

    prev_month = (month - 1) if month > 1 else 12
    prev_year = year if month > 1 else (year - 1)
    last_year = year - 1

    province_str = str(province).strip()

    # Define base_prompt_text AFTER its dependent variables are initialized
    base_prompt_text = (
            "Anda adalah Kepala Pusat Statistik / Penasihat Kebijakan Utama yang menyusun ringkasan eksekutif strategis berstandar tinggi bagi Dewan Pimpinan dan Pengambil Kebijakan.\n"
            f"Buatlah narasi Executive Summary tingkat tinggi yang padat dan tajam (tepat 2 paragraf) untuk indikator statistik Wilayah Provinsi {province} periode komparasi {year} {month} terhadap {prev_year} {prev_month}.\n\n"
            "Pedoman & Fokus Penulisan:\n"
            "- Paragraf 1: Analisis komprehensif kinerja bulanan (Month-to-Month/MTM), arah tren sektoral, serta kontribusi agregat dari wilayah-wilayah utama dalam hierarki BRS.\n"
            "- Paragraf 2: Analisis mendalam kinerja kumulatif (Year-to-Date / Year-on-Year), pembacaan deviasi pertumbuhan, serta signifikansi fluktuasi antarwilayah dalam kerangka ekonomi regional.\n"
            "- Gunakan diksi birokratik profesional, objektif, analitis, dengan standarisasi format angka Indonesia.\n"
            "- Jangan sertakan pengantar, sapaan, catatan kaki, ataupun penutup. Langsung berikan 2 paragraf teks yang dipisahkan oleh satu baris kosong (\\n\\n).\n\n"
            "Sumber Data Tabel:\n"
            f"{province_str}"
    )

    query = text(f"""
        SELECT * FROM {etl_engine_instance.general_table_name}
        WHERE TRIM(CAST(kd_prov AS TEXT)) = :province AND year IN (:year, :prev_year, :last_year) AND month IN (:month, :prev_month)
    """)

    with etl_engine_instance.engine.connect() as conn:
        df_all = pd.read_sql_query(
            query, conn,
            params={
                "province": province_str, "year": year, "prev_year": prev_year,
                "last_year": last_year, "month": month, "prev_month": prev_month,
            },
        )

    if df_all.empty:
        st.markdown('<div class="dashboard-card">', unsafe_allow_html=True)
        st.warning(f'No data found matching parameters for Province: {province}')
        st.markdown('</div>', unsafe_allow_html=True)
        return

    df_current = df_all[(df_all['year'] == year) & (df_all['month'] == month)]
    df_prev = df_all[(df_all['year'] == prev_year) & (df_all['month'] == prev_month)]
    df_last = df_all[(df_all['year'] == last_year) & (df_all['month'] == month)]

    if df_current.empty:
        st.markdown('<div class="dashboard-card">', unsafe_allow_html=True)
        st.warning(f'No current period data found for {province} on {month}/{year}.')
        st.markdown('</div>', unsafe_allow_html=True)
        return

    ref_avg = _load_ref_avg(etl_engine_instance, province_str)

    # Domain kelas per jenis, diambil dari SELURUH tabel — bukan hanya periode yang
    # sedang ditampilkan. Papua Pegununganmis. hanya punya Bintang 1 & 3 dan Kelas
    # 1, 2 & 4, sedangkan provinsi lain punya 1-4. Kalau tabel hanya memuat baris
    # yang ada isinya, kelas yang hilang "diam" dan pembaca menyimpulkan kategori itu
    # tidak berlaku — padahal kehabisan atau tidaknya hotel adalah informasi.
    domain_query = text(f"""
        SELECT jenis_akomodasi, kelas_akomodasi
        FROM {etl_engine_instance.general_table_name}
        WHERE kelas_akomodasi IS NOT NULL
        GROUP BY jenis_akomodasi, kelas_akomodasi
    """)
    try:
        with etl_engine_instance.engine.connect() as conn:
            df_domain = pd.read_sql_query(domain_query, conn)
        kelas_domain = {
            j: sorted(int(k) for k in g['kelas_akomodasi'].dropna().unique())
            for j, g in df_domain.groupby('jenis_akomodasi')
        }
    except Exception:
        logging.warning("Gagal ambil domain kelas, pakai kelas yang terlihat", exc_info=True)
        kelas_domain = {}

    st.markdown('<div class="dashboard-card">', unsafe_allow_html=True)
    st.markdown(f"<h3 style='margin-top: 0; color: #0f172a;'>📋 Executive Summary — {province} ({month}/{year})</h3>", unsafe_allow_html=True)
    st.markdown("<p style='color: #64748b; font-size: 14px;'>Analisis metrik akomodasi, tingkat penghunian kamar (TPK), dan lama menginap.</p>", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Whole-year trend for the optional per-indicator line chart below each table.
    # Only queried when a renderer is passed in, so AI-only callers pay nothing.
    df_trend = pd.DataFrame()
    if trend_chart is not None:
        trend_query = text(f"""
            SELECT jenis_akomodasi, year, month, AVG(tpk) as tpk, AVG(rlmtgab) as rlmtgab
            FROM {etl_engine_instance.general_table_name}
            WHERE TRIM(CAST(kd_prov AS TEXT)) = :province AND year IN (:year, :prev_year)
            GROUP BY jenis_akomodasi, year, month
            ORDER BY jenis_akomodasi, year, month
        """)
        with etl_engine_instance.engine.connect() as conn:
            df_trend = pd.read_sql_query(
                trend_query, conn,
                params={"province": province_str, "year": year, "prev_year": year - 1},
            )
        if not df_trend.empty and str(year - 1) not in set(df_trend["year"].astype(int).astype(str)):
            st.info(
                f"Belum ada data {year - 1} di {province}, jadi grafik hanya memuat seri {year}. "
                f"Seri {year - 1} muncul otomatis begitu datanya tersedia.",
                icon="ℹ️",
            )
        # Titik bulan yang punya rujukan resmi (sheet Prov_Jenis) dipakai apa
        # adanya, bukan rataan kelas.
        if not df_trend.empty:
            for rec in df_trend.itertuples():
                ref = ref_avg.get((rec.jenis_akomodasi, int(rec.year), int(rec.month)))
                if ref:
                    df_trend.loc[rec.Index, "tpk"] = ref["tpk"]
                    df_trend.loc[rec.Index, "rlmtgab"] = ref["rlmtgab"]

    indicators = ['tpk', 'rlmtgab']
    # Semua jenis ikut tampil, bahkan yang cuma ada di periode lalu — sama seperti
    # kelas di bawahnya. Duluan ini hanya melihat df_current.
    jenis_types = sorted(
        set(df_current['jenis_akomodasi'].dropna())
        | set(df_prev['jenis_akomodasi'].dropna())
        | set(df_last['jenis_akomodasi'].dropna())
        | set(kelas_domain)
    )

    for indicator in indicators:
        for jenis in jenis_types:
            st.markdown('<div class="dashboard-card">', unsafe_allow_html=True)
            st.markdown(f"<h4 style='color: #1e293b; margin-top: 0;'>Indicator: {indicator.upper()} — {jenis}</h4>", unsafe_allow_html=True)
            st.divider()

            cur_sub = df_current[df_current['jenis_akomodasi'] == jenis][['kelas_akomodasi', indicator]].rename(columns={indicator: 'current'})
            prev_sub = df_prev[df_prev['jenis_akomodasi'] == jenis][['kelas_akomodasi', indicator]].rename(columns={indicator: 'prev'})
            last_sub = df_last[df_last['jenis_akomodasi'] == jenis][['kelas_akomodasi', indicator]].rename(columns={indicator: 'last_year'})

            merged = cur_sub.merge(prev_sub, on='kelas_akomodasi', how='outer').merge(last_sub, on='kelas_akomodasi', how='outer')
            merged = merged.dropna(subset=['kelas_akomodasi'])

            # Reindex ke daftar kelas lengkap jenis ini. Kelas tanpa data di satu
            # periode tetap dapat baris (nilainya kosong, bukan 0 — TPK adalah
            # rata-rata, "tidak ada hotel" bukan "0% tingkat penghunian").
            merged['kelas_akomodasi'] = pd.to_numeric(merged['kelas_akomodasi'], errors='coerce')
            all_kelas = sorted(
                set(kelas_domain.get(jenis, []))
                | set(int(k) for k in merged['kelas_akomodasi'].dropna().unique())
            )
            if all_kelas:
                merged = (merged.set_index('kelas_akomodasi')
                                .reindex(all_kelas)
                                .rename_axis('kelas_akomodasi')
                                .reset_index())

            merged['change_prev'] = np.where(merged['prev'].notna(), merged['current'] - merged['prev'], np.nan)
            merged['change_last'] = np.where(merged['last_year'].notna(), merged['current'] - merged['last_year'], np.nan)

            def format_kelas(val):
                if pd.isna(val):
                    return 'Undefined Class'
                try:
                    int_val = int(val)
                except (ValueError, TypeError):
                    return str(val)

                if jenis == 'Hotel Bintang':
                    return f"Bintang {int_val}"
                elif jenis == 'Hotel Non Bintang':
                    return f"Kelas {int_val}"
                else:
                    return str(int_val)

            merged['nama_kelas_akomodasi'] = merged['kelas_akomodasi'].apply(format_kelas)
            display_df = merged[['nama_kelas_akomodasi', 'last_year', 'prev', 'current', 'change_prev', 'change_last']].set_index('nama_kelas_akomodasi').round(2)

            avg_row = pd.DataFrame({
                'last_year': [display_df['last_year'].mean()],
                'prev': [display_df['prev'].mean()],
                'current': [display_df['current'].mean()],
                'change_prev': [display_df['change_prev'].mean()],
                'change_last': [display_df['change_last'].mean()]
            }, index=['Total']).round(2)

            # Rujukan resmi (sheet Prov_Jenis) menggantikan rataan pada periode
            # yang file-nya diunggah; sel lain tetap rataan kelas.
            for cell, (yr, mo) in (("last_year", (last_year, month)), ("prev", (prev_year, prev_month)), ("current", (year, month))):
                ref = ref_avg.get((jenis, yr, mo))
                if ref:
                    avg_row.loc["Total", cell] = round(ref[indicator], 2)
            avg_row["change_prev"] = (avg_row["current"] - avg_row["prev"]).round(2)
            avg_row["change_last"] = (avg_row["current"] - avg_row["last_year"]).round(2)

            final_table = pd.concat([display_df, avg_row])
            for col in ['change_prev', 'change_last']:
                final_table[col] = final_table[col].apply(lambda x: f'{x:+.2f} pts' if pd.notna(x) else '-')

            # Header kolom = periode yang diwakilinya (bukan "prev"/"last_year").
            final_table = final_table.rename(columns={
                'last_year': _month_year(last_year, month),
                'prev': _month_year(prev_year, prev_month),
                'current': _month_year(year, month),
                'change_prev': f"Δ vs {_month_year(prev_year, prev_month)}",
                'change_last': f"Δ vs {_month_year(last_year, month)}",
            })

            # Narratives are served from the database first. Only an admin ever calls the
            # model, so an ordinary page view never spends an API call.
            is_admin = st.session_state.get("role") == "admin"
            regen_key = f"regen_{province}_{year}_{month}_{jenis}_{indicator}"
            gen_key = f"gen_{province}_{year}_{month}_{jenis}_{indicator}"

            narrative_params = {
                "province": province, "year": year, "month": month,
                "jenis_akomodasi": jenis, "indicator": indicator,
            }

            def _delete_narrative():
                with etl_engine_instance.engine.begin() as conn:
                    conn.execute(
                        text(
                            "DELETE FROM pariwisata_ai_narratives WHERE province = :province AND year = :year "
                            "AND month = :month AND jenis_akomodasi = :jenis_akomodasi AND indicator = :indicator"
                        ),
                        narrative_params,
                    )

            def _generate_narrative():
                # Tanpa Gemini pun OpenRouter masih bisa memenuhi permintaan.
                if not client and not get_openrouter_key():
                    st.error("AI tidak terkonfigurasi (Gemini & OpenRouter), narasi tidak dapat dibuat.")
                    return None
                prompt = f"Table summary for {indicator.upper()} ({jenis}) in {province}:\n" + final_table.to_string() + "\n" + base_prompt_text
                try:
                    with st.spinner(f'Membuat narasi AI untuk {jenis} {indicator.upper()}...'):
                        narrative_text = _gemini_text(prompt, client)
                        if not narrative_text or not str(narrative_text).strip():
                            narrative_text = openrouter_generate(prompt, temperature=0.3)
                    if not narrative_text or not str(narrative_text).strip():
                        st.error('AI tidak merespons (Gemini dan OpenRouter 모두 gagal).')
                        return None
                    _delete_narrative()
                    with etl_engine_instance.engine.begin() as conn:
                        conn.execute(
                            text(
                                "INSERT INTO pariwisata_ai_narratives (province, year, month, jenis_akomodasi, indicator, narrative) "
                                "VALUES (:province, :year, :month, :jenis_akomodasi, :indicator, :narrative)"
                            ),
                            {**narrative_params, "narrative": narrative_text},
                        )
                    return narrative_text
                except Exception as e:
                    st.error(f'AI error: {e}')
                    return None

            cached_narrative = None
            with etl_engine_instance.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT narrative FROM pariwisata_ai_narratives WHERE province = :province AND year = :year "
                        "AND month = :month AND jenis_akomodasi = :jenis_akomodasi AND indicator = :indicator"
                    ),
                    narrative_params,
                ).fetchone()
                if row:
                    cached_narrative = normalize_narrative(row[0])

            if cached_narrative:
                st.markdown(f'<div style="background: #f8fafc; padding: 16px; border-radius: 10px; border-left: 4px solid #f59e0b; margin-bottom: 16px;"><strong>🤖 AI Narrative (from database):</strong><br>{italicize_english(cached_narrative, html=True)}</div>', unsafe_allow_html=True)
                if is_admin and st.button(f"🔄 Regenerate AI Narrative ({jenis} - {indicator.upper()})", key=regen_key):
                    _delete_narrative()
                    st.rerun()
            elif is_admin:
                if st.button(f"🤖 Generate AI Narrative ({jenis} - {indicator.upper()})", key=gen_key):
                    narrative_text = _generate_narrative()
                    if narrative_text:
                        narrative_text = normalize_narrative(narrative_text)
                        st.markdown(f'<div style="background: #f8fafc; padding: 16px; border-radius: 10px; border-left: 4px solid #f59e0b; margin-bottom: 16px;"><strong>🤖 AI Narrative (freshly generated):</strong><br>{italicize_english(narrative_text, html=True)}</div>', unsafe_allow_html=True)
                else:
                    st.info("Narasi AI belum dibuat untuk indikator ini.")
            else:
                st.info("Narasi AI belum tersedia untuk indikator ini. Hubungi admin untuk membuatnya.")

            st.dataframe(final_table, width='stretch')

            if trend_chart is not None and not df_trend.empty:
                trend_chart(
                    df_trend[df_trend["jenis_akomodasi"] == jenis],
                    indicator, jenis, province, year, month,
                )

            st.markdown('</div>', unsafe_allow_html=True)
