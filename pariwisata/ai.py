import os

import numpy as np
import pandas as pd
import streamlit as st
from google import genai
from sqlalchemy import text


def get_gemini_client():
    # 1. Try fetching from Streamlit secrets list first
    api_keys = []
    try:
        if "GEMINI_API_KEYS" in st.secrets:
            api_keys = list(st.secrets["GEMINI_API_KEYS"])
    except Exception:
        pass

    # 2. Fallback to single environment variable or secrets if list is empty
    if not api_keys:
        env_key = os.getenv('GEMINI_API_KEY') or st.secrets.get('GEMINI_API_KEY')
        if env_key:
            api_keys = [env_key]

    if not api_keys:
        return None

    # Try initializing the client with the keys in sequence
    for key in api_keys:
        if key and str(key).strip():
            try:
                return genai.Client(api_key=str(key).strip())
            except Exception:
                continue

    return None


def generate_akomodasi_tables(etl_engine_instance, province, year, month):
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

    st.markdown('<div class="dashboard-card">', unsafe_allow_html=True)
    st.markdown(f"<h3 style='margin-top: 0; color: #0f172a;'>📋 Executive Summary — {province} ({month}/{year})</h3>", unsafe_allow_html=True)
    st.markdown("<p style='color: #64748b; font-size: 14px;'>Analisis metrik akomodasi, tingkat penghunian kamar (TPK), dan lama menginap.</p>", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    indicators = ['tpk', 'rlmtgab']
    jenis_types = sorted(df_current['jenis_akomodasi'].dropna().unique().tolist())

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
            }, index=['Average']).round(2)

            final_table = pd.concat([display_df, avg_row])
            for col in ['change_prev', 'change_last']:
                final_table[col] = final_table[col].apply(lambda x: f'{x:+.2f} pts' if pd.notna(x) else '-')

            # Check if admin wants to force regeneration via button click
            regen_key = f"regen_{province}_{year}_{month}_{jenis}_{indicator}"
            is_admin = st.session_state.get("role") == "admin"

            narrative_params = {
                "province": province, "year": year, "month": month,
                "jenis_akomodasi": jenis, "indicator": indicator,
            }

            if is_admin:
                if st.button(f"🔄 Regenerate AI Narrative ({jenis} - {indicator.upper()})", key=regen_key):
                    with etl_engine_instance.engine.begin() as conn:
                        conn.execute(
                            text(
                                "DELETE FROM ai_narratives WHERE province = :province AND year = :year "
                                "AND month = :month AND jenis_akomodasi = :jenis_akomodasi AND indicator = :indicator"
                            ),
                            narrative_params,
                        )
                    st.rerun()

            cached_narrative = None
            with etl_engine_instance.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT narrative FROM ai_narratives WHERE province = :province AND year = :year "
                        "AND month = :month AND jenis_akomodasi = :jenis_akomodasi AND indicator = :indicator"
                    ),
                    narrative_params,
                ).fetchone()
                if row:
                    cached_narrative = row[0]

            if cached_narrative:
                st.markdown(f'<div style="background: #f8fafc; padding: 16px; border-radius: 10px; border-left: 4px solid #f59e0b; margin-bottom: 16px;"><strong>🤖 AI Narrative (Retrieved from Database):</strong><br>{cached_narrative}</div>', unsafe_allow_html=True)
            else:
                if client:
                    prompt = f"Table summary for {indicator.upper()} ({jenis}) in {province}:\n" + final_table.to_markdown() + "\n" + base_prompt_text
                    try:
                        with st.spinner(f'Generating AI narrative for {jenis} {indicator.upper()}...'):
                            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                            narrative_text = response.text

                            with etl_engine_instance.engine.begin() as conn:
                                # Portable upsert: delete then insert (works on both SQLite and Postgres)
                                conn.execute(
                                    text(
                                        "DELETE FROM ai_narratives WHERE province = :province AND year = :year "
                                        "AND month = :month AND jenis_akomodasi = :jenis_akomodasi AND indicator = :indicator"
                                    ),
                                    narrative_params,
                                )
                                conn.execute(
                                    text(
                                        "INSERT INTO ai_narratives (province, year, month, jenis_akomodasi, indicator, narrative) "
                                        "VALUES (:province, :year, :month, :jenis_akomodasi, :indicator, :narrative)"
                                    ),
                                    {**narrative_params, "narrative": narrative_text},
                                )

                            st.markdown(f'<div style="background: #f8fafc; padding: 16px; border-radius: 10px; border-left: 4px solid #f59e0b; margin-bottom: 16px;"><strong>🤖 AI Narrative (Freshly Generated):</strong><br>{narrative_text}</div>', unsafe_allow_html=True)
                    except Exception as e:
                        st.error(f'AI error: {e}')
                else:
                    st.info('AI narrative skipped (Gemini client unconfigured).')

            st.dataframe(final_table, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
