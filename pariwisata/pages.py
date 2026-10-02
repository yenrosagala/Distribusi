import logging

import pandas as pd
import numpy as np
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from modules.database import is_local_dummy_db
from modules.etl_engine import MONTH_NAMES
from modules.indesign_export import paket_zip
from modules.indesign_pariwisata import (
    DEFAULT_TEMPLATE as DEFAULT_TEMPLATE_PARI,
    fill_brs_pariwisata_template,
    kumpulkan_kelas,
)
from pariwisata.ai import generate_akomodasi_tables

logger = logging.getLogger(__name__)

# ============================================================
# THEME TOKENS — kept in sync with style.css
# ============================================================
PRIMARY, PRIMARY_DARK, POSITIVE, NEGATIVE = "#F59E0B", "#D97706", "#10B981", "#EF4444"
INK, INK_DIM, LINE, MAP_BG = "#0F172A", "#64748B", "#E2E8F0", "#0B0F14"
# Axis chrome: tick text sits at #334155 and titles at INK so neither relies on
# INK_DIM, which is only 4.8:1 on white and reads as grey-on-grey at 13px.
TICK, AXIS_LINE = "#334155", "#CBD5E1"
# YoY series colours. Markers are white-filled with a ring in the line colour so
# each point reads against its own line regardless of how light the line is.
CUR_LINE, PREV_LINE = "#ED7D31", "#70AD47"
# Data labels are darkened shades of their line, so they stay >= 4.5:1 on white
# (the raw line colours are only ~2.5:1 and would be illegible as text).
CUR_LABEL, PREV_LABEL = "#92400E", "#166534"

TARGET_PROVINCES = ["Papua", "Papua Tengah", "Papua Pegunungan", "Papua Selatan"]
LEFT_PROVINCES = ["Papua Tengah", "Papua Selatan"]
RIGHT_PROVINCES = ["Papua", "Papua Pegunungan"]

JENIS_LABELS = {"Hotel Bintang": "Klasifikasi Bintang", "Hotel Non Bintang": "Klasifikasi NonBintang"}
INDICATOR_META = {
    "tpk": {"label": "TPK (Occupancy Rate)", "unit": "%"},
    "rlmtgab": {"label": "RLMTGAB (Length of Stay)", "unit": " malam"},
}


def plotly_theme(fig, height=440, dark=False):
    font_color = "#F1F5F9" if dark else INK
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color=font_color, size=13),
        title_font=dict(family="Inter, sans-serif", size=16, color=font_color),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=font_color)),
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.update_xaxes(gridcolor=LINE if not dark else "rgba(255,255,255,0.08)")
    fig.update_yaxes(gridcolor=LINE if not dark else "rgba(255,255,255,0.08)")
    return fig


def month_name(m):
    return pd.to_datetime(str(int(m)), format="%m").strftime("%B") if m else ""


# The infographic map keeps `month_name` (English) so its period labels and CSV
# export are left exactly as they were. Every other tab uses Indonesian.
def bulan(m):
    return MONTH_NAMES[int(m) - 1] if m and 1 <= int(m) <= 12 else month_name(m)


def period_label(year, month):
    return f"{bulan(month)} {int(year)}"


def prev_period(year, month):
    """The period immediately before (year, month)."""
    return (int(year), int(month) - 1) if int(month) > 1 else (int(year) - 1, 12)


def periods_in(df_info, province=None):
    """{(year, month)} present in the filter index already passed in, so the
    checks below cost no extra query. All provinces when omitted."""
    if df_info is None or df_info.empty:
        return set()
    if province is not None:
        df_info = df_info[df_info["kd_prov"].astype(str) == str(province)]
    return {(int(y), int(m)) for y, m in zip(df_info["year"], df_info["month"])}


def baseline_warning(df_info, province, year, month):
    """Message when the previous month is missing, else None — the comparison
    columns and the AI narrative both silently degrade without it."""
    py, pm = prev_period(year, month)
    if (py, pm) in periods_in(df_info, province):
        return None
    return (
        f"Data untuk bulan sebelumnya ({period_label(py, pm)}) belum tersedia di "
        f"{province}. Perbandingan bulan-ke-bulan tidak dapat dihitung untuk "
        f"{period_label(year, month)} — pilih periode yang lebih akhir."
    )


def card_open(title=None, tag=None):
    if title:
        tag_html = f"<span>{tag}</span>" if tag else ""
        st.markdown(
            f'<div class="dashboard-card"><div class="card-header"><h3>{title}</h3>{tag_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown('<div class="dashboard-card">', unsafe_allow_html=True)


def card_close():
    st.markdown("</div>", unsafe_allow_html=True)


def render_province_card(province, df_cur, df_prev):
    rows_html = ""
    for jenis, jenis_label in JENIS_LABELS.items():
        cur_val = df_cur[(df_cur["province"] == province) & (df_cur["jenis_akomodasi"] == jenis)]["val"]
        prev_val = df_prev[(df_prev["province"] == province) & (df_prev["jenis_akomodasi"] == jenis)]["val"]
        cur_val = cur_val.mean() if not cur_val.empty else np.nan
        prev_val = prev_val.mean() if not prev_val.empty else np.nan

        delta = (
            ((cur_val - prev_val) / prev_val * 100)
            if pd.notna(cur_val) and pd.notna(prev_val) and prev_val != 0
            else np.nan
        )
        value_display = f"{cur_val:.2f}%" if pd.notna(cur_val) else "—"

        if pd.isna(delta):
            delta_html = '<span class="stat-delta-na">‒ N/A</span>'
        else:
            arrow = "▲" if delta >= 0 else "▼"
            cls = "badge-up" if delta >= 0 else "badge-down"
            delta_html = f'<span class="{cls}">{arrow} {abs(delta):.1f}%</span>'

        rows_html += f"""
        <div class="stat-row">
            <div><span class="stat-label">{jenis_label}</span><span class="stat-value">{value_display}</span></div>
            {delta_html}
        </div>
        """

    st.markdown(
        f"""
        <div class="province-card">
            <div class="province-header">{province}</div>
            {rows_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data
def get_filter_options(_etl_engine):
    with _etl_engine.engine.connect() as conn:
        try:
            return pd.read_sql_query(
                text(f"SELECT DISTINCT kd_prov, jenis_akomodasi, year, month FROM {_etl_engine.general_table_name}"),
                conn,
            )
        except Exception:
            return pd.DataFrame()


# ============================================================
# PAGE — HOME DASHBOARD
# ============================================================
def render_home_dashboard(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="hero-title">👋 Selamat datang, ' + st.session_state["name"] + '</div>', unsafe_allow_html=True)

    if not df_info.empty:
        periods = sorted(periods_in(df_info))
        first_period, last_period = periods[0], periods[-1]
        latest_year, latest_month = last_period
        with etl_engine.engine.connect() as conn:
            df_latest = pd.read_sql_query(
                text(
                    f"SELECT AVG(tpk) as tpk, AVG(rlmtgab) as rlmtgab FROM {etl_engine.general_table_name} "
                    "WHERE year = :year AND month = :month"
                ),
                conn,
                params={"year": latest_year, "month": latest_month},
            )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Provinsi Tercakup", len(prov_list))
        m2.metric("Periode Terakhir", period_label(latest_year, latest_month))
        m3.metric("Rata-rata TPK (terakhir)", f"{df_latest['tpk'].iloc[0]:.1f}%" if pd.notna(df_latest['tpk'].iloc[0]) else "—")
        m4.metric(
            "Rata-rata RLMTGAB (terakhir)",
            f"{df_latest['rlmtgab'].iloc[0]:.1f} malam" if pd.notna(df_latest['rlmtgab'].iloc[0]) else "—",
        )

        with st.container(border=True):
            st.markdown("### Cakupan Data")
            st.write(
                f"Data mencakup **{period_label(*first_period)}** sampai "
                f"**{period_label(*last_period)}**, meliputi {len(prov_list)} provinsi "
                "dengan klasifikasi Hotel Bintang dan Non Bintang."
            )
            st.caption("Pindah tab di atas untuk melihat peta statistik, grafik tren, dan laporan narasi AI.")
    else:
        with st.container(border=True):
            st.info(
                "Belum ada data yang dimuat. Jika Anda admin, buka tab "
                "**Admin ETL Uploads** untuk memuat matriks Excel pertama."
            )


# ============================================================
# PAGE — INFOGRAPHIC STAT MAP
# ============================================================
def render_infographic_map(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="filter-pill">', unsafe_allow_html=True)
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        map_indicator = st.selectbox(
            "Select Indicator",
            options=[("tpk", "TPK (Occupancy Rate)"), ("rlmtgab", "RLMTGAB (Length of Stay)")],
            format_func=lambda x: x[1],
            label_visibility="collapsed",
        )[0]
    with f_col2:
        map_year = st.selectbox(
            "Select Year", options=year_list, index=len(year_list) - 1 if year_list else 0,
            label_visibility="collapsed",
        )
    with f_col3:
        map_month = st.selectbox(
            "Select Month", options=month_list, format_func=month_name, label_visibility="collapsed"
        )
    st.markdown("</div>", unsafe_allow_html=True)

    if map_indicator and map_year and map_month:
        prev_month = (map_month - 1) if map_month > 1 else 12
        prev_year = map_year if map_month > 1 else (map_year - 1)

        query = text(f"""
            SELECT kd_prov AS province, jenis_akomodasi, month, year, AVG({map_indicator}) as val
            FROM {etl_engine.general_table_name}
            WHERE year IN (:year, :prev_year) AND month IN (:month, :prev_month)
            GROUP BY kd_prov, jenis_akomodasi, month, year
        """)
        with etl_engine.engine.connect() as conn:
            df_infographic = pd.read_sql_query(
                query, conn,
                params={"year": map_year, "prev_year": prev_year, "month": map_month, "prev_month": prev_month},
            )

        if df_infographic.empty:
            with st.container(border=True):
                st.info(
                    "No records match this period yet. Try a different month/year, or ask an "
                    "admin to ingest data for this range in **Admin ETL Uploads**."
                )
        else:
            df_cur = df_infographic[(df_infographic["year"] == map_year) & (df_infographic["month"] == map_month)]
            df_prev = df_infographic[(df_infographic["year"] == prev_year) & (df_infographic["month"] == prev_month)]

            period_label = f"{month_name(map_month)} {map_year}"
            st.markdown(f'<div class="hero-title">Papua Regional Performance — {period_label}</div>', unsafe_allow_html=True)

            left_provs = LEFT_PROVINCES
            right_provs = RIGHT_PROVINCES

            col_left, col_map, col_right = st.columns([1.1, 2.2, 1.1])

            with col_left:
                for prov in left_provs:
                    render_province_card(prov, df_cur, df_prev)

            with col_map:
                if not gdf_provinces.empty:
                    merged_gdf = gdf_provinces.merge(
                        df_cur.groupby("province")["val"].mean().reset_index(),
                        left_on="PROVINSI", right_on="province", how="inner",
                    )
                    merged_gdf = merged_gdf[merged_gdf["PROVINSI"].isin(TARGET_PROVINCES)]

                    gdf_projected = merged_gdf.to_crs(epsg=32753)
                    wgs84_centroids = gdf_projected.geometry.centroid.to_crs(epsg=4326)
                    merged_gdf["lat"] = wgs84_centroids.y
                    merged_gdf["lon"] = wgs84_centroids.x

                    fig_map = px.choropleth(
                        merged_gdf, geojson=merged_gdf.geometry, locations=merged_gdf.index, color="val",
                        color_continuous_scale=[[0, "#3A2A0E"], [0.5, PRIMARY], [1, "#FDE68A"]],
                        hover_name="PROVINSI", hover_data={"val": ":.2f"},
                    )
                    fig_scatter = px.scatter_geo(merged_gdf, lat="lat", lon="lon", text="PROVINSI")
                    fig_scatter.update_traces(
                        marker=dict(size=11, color="#FDE68A", symbol="circle", line=dict(width=1.5, color=MAP_BG)),
                        textfont=dict(color="#F1F5F9", size=10),
                    )
                    for trace in fig_scatter.data:
                        fig_map.add_trace(trace)
                    fig_map.update_geos(fitbounds="locations", visible=False, bgcolor="rgba(0,0,0,0)")
                    fig_map.update_layout(showlegend=False, coloraxis_colorbar=dict(title="val", tickfont=dict(color="#F1F5F9")))
                    plotly_theme(fig_map, height=460, dark=True)

                    with st.container(border=True):
                        st.plotly_chart(fig_map, width='stretch')
                else:
                    with st.container(border=True):
                        st.warning("Map geometry file (papua_provinces.parquet) could not be loaded.")

                csv_data = df_cur.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "📥 Download Summary CSV", data=csv_data,
                    file_name=f"infographic_{map_indicator}_{map_year}_{map_month}.csv", mime="text/csv",
                    width='stretch',
                )

            with col_right:
                for prov in right_provs:
                    render_province_card(prov, df_cur, df_prev)


def render_yoy_chart(df_trend, indicator, jenis, province, year, month=None, height=380):
    """Month-by-month line for one indicator + accommodation type, current year vs the year before.

    Labelled points: the selected month and the same month one year earlier, so the
    year-over-year comparison is readable without hovering.
    """
    if df_trend is None or df_trend.empty:
        return
    if "jenis_akomodasi" in df_trend.columns:
        df_trend = df_trend[df_trend["jenis_akomodasi"] == jenis]
        if df_trend.empty:
            return
    meta = INDICATOR_META[indicator]
    unit = meta["unit"].strip()
    prev_year = int(year) - 1
    sub = df_trend.copy()
    sub["Bulan"] = sub["month"].apply(bulan)
    sub["Tahun"] = sub["year"].astype(int).astype(str)
    sub["_bln"] = sub["month"].astype(int)

    cur_months = sub.loc[sub["Tahun"] == str(year), "_bln"]
    target_month = int(month) if month else (int(cur_months.max()) if len(cur_months) else None)

    fig = px.line(
        sub, x="Bulan", y=indicator, color="Tahun", markers=True,
        custom_data=["_bln"],
        category_orders={
            "Bulan": [bulan(m) for m in sorted(sub["month"].unique())],
            "Tahun": [str(prev_year), str(year)],
        },
        color_discrete_map={str(prev_year): PREV_LINE, str(year): CUR_LINE},
    )
    fig.update_traces(line=dict(width=3))

    for tr in fig.data:
        is_cur = tr.name == str(year)
        line_color = CUR_LINE if is_cur else PREV_LINE
        # white fill + a ring in the line colour: the point reads as its own
        # series while staying clearly separated from the stroke it sits on
        tr.marker = dict(size=9, color="#FFFFFF", line=dict(width=2.5, color=line_color))
        # readable hover: use the human label, not the raw column name
        tr.hovertemplate = (
            f"<b>Tahun {tr.name}</b><br>"
            f"%{{x}}<br>{meta['label']}: %{{y:,.2f}} {unit}<extra></extra>"
        )
        if target_month is None:
            continue
        months = [c[0] for c in tr.customdata]
        tr.text = [
            f"{v:,.2f}" if int(m) == target_month and pd.notna(v) else ""
            for m, v in zip(months, tr.y)
        ]
        tr.mode = "lines+markers+text"
        # label away from each other so the pair stays legible when they overlap
        tr.textposition = "top center" if is_cur else "bottom center"
        tr.textfont = dict(size=12, color=CUR_LABEL if is_cur else PREV_LABEL)
        tr.cliponaxis = False

    fig.update_layout(
        xaxis_title="Bulan",
        yaxis_title=f"{meta['label']} ({unit})",
        font=dict(family="Inter, sans-serif", color=INK, size=13),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor=LINE),
        legend_title_text="Tahun",
    )
    plotly_theme(fig, height=height)
    # plotly_theme pins a 10px left margin, which clips the y-axis title and tick
    # labels; automargin lets plotly size the gutter to the real label width.
    #
    # Plotly's `axis.color` also paints the axis *title*, so leaving the title at
    # INK_DIM made it the same weight as the ticks and near-illegible at 13px.
    # Titles and ticks therefore get their own font, both well past WCAG AA.
    fig.update_layout(
        margin=dict(l=8, r=8, t=56, b=8),
        xaxis=dict(
            automargin=True, linecolor=AXIS_LINE, linewidth=1,
            tickfont=dict(size=13, color=TICK), title_standoff=14,
            title=dict(font=dict(size=14, color=INK)),
        ),
        yaxis=dict(
            automargin=True, linecolor=AXIS_LINE, linewidth=1,
            gridcolor=LINE, tickfont=dict(size=13, color=TICK), title_standoff=14,
            title=dict(font=dict(size=14, color=INK)),
        ),
        legend=dict(font=dict(size=13, color=INK), title_font=dict(size=13, color=INK)),
    )
    st.plotly_chart(fig, width='stretch')


## ============================================================
# PAGE — REPORT
# ============================================================
def render_report(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="hero-title">Laporan &amp; Narasi AI</div>', unsafe_allow_html=True)
    with st.container(border=True):
        r_col1, r_col2, r_col3 = st.columns(3)
        with r_col1:
            rep_prov = st.selectbox("Provinsi", options=prov_list, key="rep_prov")
        with r_col2:
            rep_year = st.selectbox("Tahun", options=year_list, key="rep_year")
        with r_col3:
            rep_month = st.selectbox(
                "Bulan", options=month_list, format_func=bulan, key="rep_month",
                index=max(len(month_list) - 1, 0),
            )

    warn = baseline_warning(df_info, rep_prov, rep_year, rep_month)
    if warn:
        st.warning(warn + " Analisis AI hanya akan menguraikan nilai periode terpilih.", icon="⚠️")

    if rep_prov and rep_year and rep_month:
        with st.container(border=True):
            generate_akomodasi_tables(etl_engine, rep_prov, rep_year, rep_month, render_yoy_chart)
        render_indesign_pariwisata(etl_engine, rep_prov, rep_year, rep_month)


def render_indesign_pariwisata(etl_engine, prov, thn, bln):
    """Isi template .idml BRS dari database, lalu unduh.

    Sengaja membaca ulang dari DB (bukan dari tabel di atas): template butuh tiga
    periode per kelas sekaligus, sedangkan tabel di layar hanya menampilkan satu
    periode berjalan.
    """
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

    bln_nama = bln if isinstance(bln, str) else bulan(int(bln))
    st.markdown("---")
    st.subheader("🎨 Isi Template InDesign (BRS)")
    st.caption(
        "Tabel TPK/RLMT, narasi, ringkasan, dan judul chart untuk periode di atas "
        "dimasukkan ke template .idml. Hasilnya dibuka di InDesign lalu disimpan "
        "sebagai .indd/PDF."
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
                    key="pari_info",
                    help="Gambar infografis untuk periode ini.",
                )

        with c_qr:
            with st.container(border=True):
                st.markdown("**2. Kode QR**")
                qr_up = st.file_uploader(
                    "Unggah Gambar Kode QR",
                    type=["png", "jpg", "jpeg"],
                    key="pari_qr",
                    help="Kode QR untuk periode ini.",
                )

        c_no, c_tgl = st.columns(2)
        with c_no:
            with st.container(border=True):
                st.markdown("**3. Nomor BRS**")
                nomor_brs = st.text_input(
                    "Nomor BRS",
                    key="pari_no",
                    placeholder="mis. 235/10/94/Th. XXIX",
                    label_visibility="collapsed"
                )

        with c_tgl:
            with st.container(border=True):
                st.markdown("**4. Tanggal Rilis**")
                tgl_rilis = st.text_input(
                    "Tanggal Rilis",
                    key="pari_tgl",
                    placeholder="mis. 1 Oktober 2026",
                    label_visibility="collapsed"
                )

        if DEFAULT_TEMPLATE_PARI.exists():
            st.caption(f"Template: **{DEFAULT_TEMPLATE_PARI.name}** (bawaan)")
        else:
            st.caption("⚠️ Template bawaan tidak ditemukan.")

    if st.button("🎨 Buat File InDesign (.idml)", width='stretch', key="btn_idml_pari"):
        if not DEFAULT_TEMPLATE_PARI.exists():
            st.error("Template bawaan tidak ditemukan.")
            return
        try:
            with st.spinner("Mengisi template…"):
                data = kumpulkan_kelas(etl_engine.engine, prov, thn, bln_nama)
                res = fill_brs_pariwisata_template(
                    DEFAULT_TEMPLATE_PARI.read_bytes(), prov, thn, bln_nama, data,
                    nomor_brs=nomor_brs.strip() or None,
                    tanggal_rilis=tgl_rilis.strip() or None,
                    qr_bytes=qr_up.getvalue() if qr_up is not None else None,
                    qr_name=qr_up.name if qr_up is not None else None,
                    infografis_bytes=info_up.getvalue() if info_up is not None else None,
                    infografis_name=info_up.name if info_up is not None else None,
                )
            st.session_state["idml_pari"] = (res, prov, thn, bln_nama)
        except Exception as e:  # noqa: BLE001
            logger.exception("Gagal mengisi template IDML pariwisata")
            st.error(f"Gagal mengisi template: {e}")

    stored = st.session_state.get("idml_pari")
    if not stored:
        return
    res, prov_s, thn_s, bln_s = stored
    nama_idml = f"BRS_Pariwisata_{prov_s.replace(' ', '_')}_{bln_s}_{thn_s}.idml"
    st.download_button(
        "📦 Download Paket (.zip: .idml + PNG)", data=paket_zip(res.idml_bytes, nama_idml),
        file_name=f"BRS_Pariwisata_{prov_s.replace(' ', '_')}_{bln_s}_{thn_s}.zip",
        mime="application/zip",
        width='stretch',
        key="dl_zip_pari",
    )
    warnings = list(res.warnings)
    if is_local_dummy_db():
        warnings.insert(0, "Sumber data adalah SQLite lokal `data/app_data.db` yang berisi "
                          "baris placeholder — angka di template ini BUKAN data BPS resmi.")
    with st.expander(f"⚠️ {len(warnings)} hal yang perlu dicek di InDesign"):
        for w in warnings:
            st.markdown(f"- {w}")
            
# ============================================================
# PAGE — ADMIN ETL UPLOADS
# ============================================================
def render_admin_etl(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="hero-title">Admin: Ingesti Data ETL</div>', unsafe_allow_html=True)
    card_open("Panel Admin", "Ingesti data ETL")
    st.markdown(
        f"<p style='color:{INK_DIM};'>Unggah matriks Excel sumber langsung ke basis data "
        "dan jalankan pemeliharaan sistem.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    uploaded_files = st.file_uploader("Unggah File Excel Sumber (.xlsx)", type=["xlsx"], accept_multiple_files=True)

    # Default to the newest period already in the system rather than a fixed
    # month, so a repeat ingest does not silently land in the wrong slot.
    default_year = int(year_list[-1]) if year_list else 2026
    default_month = int(month_list[-1]) if month_list else 1
    adm_col1, adm_col2 = st.columns(2)
    with adm_col1:
        target_year = st.number_input("Tahun Target", value=default_year, min_value=2015, max_value=2100, step=1)
    with adm_col2:
        target_month = st.selectbox(
            "Bulan Target", options=list(range(1, 13)), format_func=bulan, index=default_month - 1
        )

    if st.button("🚀 Proses & Masukkan Data", type="primary"):
        if uploaded_files:
            with st.spinner("Memproses file ke basis data…"):
                for uploaded_file in uploaded_files:
                    etl_engine.etl_pipeline(uploaded_file, year=int(target_year), month=int(target_month))
            st.success(f"{len(uploaded_files)} file berhasil dimasukkan ke basis data.")
            get_filter_options.clear()
        else:
            st.warning("Unggah setidaknya satu file Excel sebelum memproses.")
    card_close()
