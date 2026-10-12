import json
import logging
from pathlib import Path

import pandas as pd
import numpy as np
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from modules.database import is_local_dummy_db, get_engine
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


def _ring_centroid_lonlat(ring):
    """Average of the outer ring's vertices. Good enough as a label anchor."""
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _feature_centroid_lonlat(geom):
    """GeoJSON Polygon/MultiPolygon -> (lon, lat) of the largest polygon's outer ring."""
    rings = (
        geom["coordinates"]
        if geom["type"] == "Polygon"
        else [r for poly in geom["coordinates"] for r in poly]
    )
    outer = max(
        rings, key=lambda r: abs(
            sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1))
        )
    )
    return _ring_centroid_lonlat(outer)


@st.cache_data
def load_geodata():
    """Province map geometry without geopandas: plain GeoJSON + vertex centroids.

    Replaces a 70KB parquet read that pulled in geopandas/shapely/pyarrow into
    an already-heavy cold start. The geometry was stored as WGS84 lon/lat, so a
    vertex average is a fine centroid for scatter labels over the projected
    round-trip the old code did purely for sub-pixel label precision.
    """
    geo_path = Path(__file__).parent.parent / "papua_provinces.json"
    with open(geo_path, encoding="utf-8") as f:
        fc = json.load(f)
    geo = {}
    for feat in fc["features"]:
        name = feat["properties"]["PROVINSI"]
        lon, lat = _feature_centroid_lonlat(feat["geometry"])
        geo[name] = {"geometry": feat["geometry"], "lon": lon, "lat": lat}
    return geo

JENIS_LABELS = {"Hotel Bintang": "Klasifikasi Bintang", "Hotel Non Bintang": "Klasifikasi NonBintang"}
INDICATOR_META = {
    "tpk": {"label": "Tingkat Penghunian Kamar (TPK)", "unit": "%"},
    "rlmtgab": {"label": "Rata-rata Lama Menginap (RLMT)", "unit": " malam"},
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


@st.cache_data(ttl=300)
def _latest_metrics(_etl_engine, year, month):
    with _etl_engine.engine.connect() as conn:
        return pd.read_sql_query(
            text(
                f"SELECT AVG(tpk) as tpk, AVG(rlmtgab) as rlmtgab FROM {_etl_engine.general_table_name} "
                "WHERE year = :year AND month = :month"
            ),
            conn,
            params={"year": year, "month": month},
        )


@st.cache_data(ttl=300)
def _infographic_data(_etl_engine, indicator, year, month, prev_year, prev_month):
    with _etl_engine.engine.connect() as conn:
        return pd.read_sql_query(
            text(f"""
                SELECT kd_prov AS province, jenis_akomodasi, month, year, AVG({indicator}) as val
                FROM {_etl_engine.general_table_name}
                WHERE year IN (:year, :prev_year) AND month IN (:month, :prev_month)
                GROUP BY kd_prov, jenis_akomodasi, month, year
            """),
            conn,
            params={"year": year, "prev_year": prev_year, "month": month, "prev_month": prev_month},
        )


# ============================================================
# PAGE — HOME DASHBOARD
# ============================================================
def render_home_dashboard(etl_engine, df_info, prov_list, year_list, month_list):
    st.markdown('<h2 class="hero-title">👋 Selamat datang, ' + st.session_state["name"] + '</h2>', unsafe_allow_html=True)

    if not df_info.empty:
        periods = sorted(periods_in(df_info))
        first_period, last_period = periods[0], periods[-1]
        latest_year, latest_month = last_period
        df_latest = _latest_metrics(etl_engine, latest_year, latest_month)
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
def render_infographic_map(etl_engine, df_info, prov_list, year_list, month_list):
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        map_indicator = st.selectbox(
            "Indikator",
            options=[("tpk", "TPK (Occupancy Rate)"), ("rlmtgab", "RLMTGAB (Length of Stay)")],
            format_func=lambda x: x[1],
        )[0]
    with f_col2:
        map_year = st.selectbox(
            "Tahun", options=year_list, index=len(year_list) - 1 if year_list else 0,
        )
    with f_col3:
        map_month = st.selectbox(
            "Bulan", options=month_list, format_func=month_name
        )

    if map_indicator and map_year and map_month:
        prev_month = (map_month - 1) if map_month > 1 else 12
        prev_year = map_year if map_month > 1 else (map_year - 1)

        df_infographic = _infographic_data(
            etl_engine, map_indicator, map_year, map_month, prev_year, prev_month
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
            st.markdown(f'<h2 class="hero-title">Papua Regional Performance — {period_label}</h2>', unsafe_allow_html=True)

            left_provs = LEFT_PROVINCES
            right_provs = RIGHT_PROVINCES

            col_left, col_map, col_right = st.columns([1.1, 2.2, 1.1])

            with col_left:
                for prov in left_provs:
                    render_province_card(prov, df_cur, df_prev)

            with col_map:
                geo = load_geodata() or {}
                vals = df_cur.groupby("province")["val"].mean()

                features, rows = [], []
                for prov in TARGET_PROVINCES:
                    g = geo.get(prov)
                    if g is None or prov not in vals.index:
                        continue
                    rows.append({
                        "PROVINSI": prov, "val": vals[prov],
                        "lat": g["lat"], "lon": g["lon"],
                    })
                    features.append({
                        "type": "Feature", "id": prov,
                        "properties": {"name": prov}, "geometry": g["geometry"],
                    })
                merged_gdf = pd.DataFrame(rows)

                if not merged_gdf.empty:
                    fig_map = px.choropleth(
                        merged_gdf,
                        geojson={"type": "FeatureCollection", "features": features},
                        locations="PROVINSI", featureidkey="properties.name", color="val",
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
                        st.warning("Map geometry file (papua_provinces.json) could not be loaded.")

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
    short = lambda m: bulan(m)[:3]  # "Januari" -> "Jan", "Agustus" -> "Agu"
    sub["Bulan"] = sub["month"].apply(short)
    sub["Tahun"] = sub["year"].astype(int).astype(str)
    sub["_bln"] = sub["month"].astype(int)

    cur_months = sub.loc[sub["Tahun"] == str(year), "_bln"]
    target_month = int(month) if month else (int(cur_months.max()) if len(cur_months) else None)
    # Stop the series at the selected month: no line into months after it.
    if target_month is not None:
        sub = sub[sub["_bln"] <= target_month]

    fig = px.line(
        sub, x="Bulan", y=indicator, color="Tahun", markers=True,
        custom_data=["_bln"],
        category_orders={
            "Bulan": [short(m) for m in sorted(sub["month"].unique())],
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
def render_report(etl_engine, df_info, prov_list, year_list, month_list):
    st.markdown('<h2 class="hero-title">Laporan &amp; Narasi AI</h2>', unsafe_allow_html=True)
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
def _pari_delete_filter(prov, year, month):
    """(SQL WHERE fragment, params) for the pariwisata delete panel.

    'SEMUA' on any axis drops that condition; all-'SEMUA' falls back to 1=1,
    which the caller guards behind a warning + confirm checkbox.
    """
    conditions, params = [], {}
    if prov != "SEMUA":
        conditions.append("kd_prov = :prov")
        params["prov"] = prov
    if year != "SEMUA":
        conditions.append("CAST(year AS TEXT) = :year")
        params["year"] = str(year)
    if month != "SEMUA":
        conditions.append("month = :month")
        params["month"] = int(month)
    return (" AND ".join(conditions) if conditions else "1=1"), params


MONTH_MAP = {name: i for i, name in enumerate(MONTH_NAMES, start=1)}


def _get_kabupaten_list(prov=None):
    try:
        engine = get_engine()
        query = f"SELECT DISTINCT kd_kab FROM {ETL_ALL_DATA}"
        params = {}
        if prov and prov != "SEMUA":
            query += " WHERE kd_prov = :prov"
            params["prov"] = prov
        df = pd.read_sql(text(query), engine, params=params)
        kabs = df["kd_kab"].dropna().astype(str).unique().tolist()
        return sorted(kabs)
    except Exception:
        return []


ETL_ALL_DATA = "all_data"  # shared table


@st.cache_data
def _kabupaten_list_cached(prov=None):
    """Distinct kabupaten, optionally for one province. Cached because the
    kabupaten set changes rarely (ETL re-ingests are rare)."""
    return _get_kabupaten_list(prov)


PARI_VAR_OPTIONS = [
    "mktj",
    "mkts",
    "mtgab",
    "tpk",
    "rlmtgab",
]


def render_admin_etl(etl_engine, df_info, prov_list, year_list, month_list):
    st.markdown('<h2 class="hero-title">Admin: Ingesti Data ETL</h2>', unsafe_allow_html=True)
    # Pariwisata lives in one table (all_data) with kd_prov / year / month as the
    # natural key, so all three panels filter on those same three columns.
    table = etl_engine.general_table_name
    default_year = int(year_list[-1]) if year_list else 2026
    default_month = int(month_list[-1]) if month_list else 1

    tab_upload, tab_correct, tab_delete, tab_series = st.tabs(
        ["📥 Ingesti Data (Upload)", "🛠️ Koreksi Manual", "⚠️ Hapus Data", "📊 Data Series Graph"]
    )

    # --- TAB 1: UPLOAD ---
    with tab_upload:
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
                st.cache_data.clear()  # rows changed; every cached read is now stale
            else:
                st.warning("Unggah setidaknya satu file Excel sebelum memproses.")
        card_close()

    # --- TAB 2: MANUAL CORRECTION ---
    with tab_correct:
        st.markdown("### Koreksi Data Tabel Langsung")
        c1, c2, c3 = st.columns(3)
        with c1:
            prov_edit = st.selectbox("Provinsi", TARGET_PROVINCES, key="pari_edit_prov")
        with c2:
            year_edit = st.number_input(
                "Tahun Periode", value=default_year, min_value=2015, max_value=2100, step=1, key="pari_edit_year"
            )
        with c3:
            month_edit = st.selectbox(
                "Bulan Periode", options=list(range(1, 13)), format_func=bulan,
                index=default_month - 1, key="pari_edit_month",
            )

        if st.button("🔍 Muat Data untuk Diedit", key="pari_btn_load_edit", width="stretch"):
            df_to_edit = pd.read_sql(
                text(f"SELECT * FROM {table} WHERE kd_prov = :prov AND year = :year AND month = :month"),
                etl_engine.engine,
                params={"prov": prov_edit, "year": int(year_edit), "month": int(month_edit)},
            )
            if df_to_edit.empty:
                st.warning("Data tidak ditemukan untuk periode tersebut.")
            else:
                st.session_state["pari_edit_df"] = df_to_edit

        if "pari_edit_df" in st.session_state:
            edited_df = st.data_editor(st.session_state["pari_edit_df"], width="stretch", num_rows="dynamic")
            if st.button("💾 Simpan Perubahan Baris", type="primary", width="stretch", key="pari_btn_save_edit"):
                try:
                    with etl_engine.engine.begin() as conn:
                        conn.execute(
                            text(f"DELETE FROM {table} WHERE kd_prov = :prov AND year = :year AND month = :month"),
                            {"prov": prov_edit, "year": int(year_edit), "month": int(month_edit)},
                        )
                        edited_df.to_sql(table, conn, if_exists="append", index=False)
                    st.success("✅ Perubahan berhasil disimpan ke database!")
                    st.cache_data.clear()  # rows changed; every cached read is now stale
                    del st.session_state["pari_edit_df"]
                except Exception as e:
                    st.error(f"Gagal menyimpan data: {e}")

    # --- TAB 3: DELETE ---
    with tab_delete:
        st.markdown("### Zona Penghapusan Data")
        st.markdown(
            "Hapus data berdasarkan kriteria filter tertentu untuk membersihkan"
            " entri yang salah atau duplikat."
        )
        d1, d2, d3 = st.columns(3)
        with d1:
            prov_del = st.selectbox("Provinsi", ["SEMUA"] + TARGET_PROVINCES, key="pari_del_prov")
        with d2:
            try:
                years_df = pd.read_sql(text(f"SELECT DISTINCT year FROM {table}"), etl_engine.engine)
                year_options = ["SEMUA"] + sorted(years_df["year"].astype(str).unique().tolist())
            except Exception:
                year_options = ["SEMUA"]
            year_del = st.selectbox("Tahun", year_options, key="pari_del_year")
        with d3:
            month_del = st.selectbox(
                "Bulan", ["SEMUA"] + list(range(1, 13)), key="pari_del_month",
                format_func=lambda m: m if m == "SEMUA" else bulan(m),
            )

        where_clause, params_del = _pari_delete_filter(prov_del, year_del, month_del)
        if not where_clause == "1=1":
            pass
        try:
            count_result = pd.read_sql(
                text(f"SELECT COUNT(*) as n FROM {table} WHERE {where_clause}"),
                etl_engine.engine, params=params_del,
            )
            jumlah_del = int(count_result["n"].iloc[0])
        except Exception as e:
            st.error(f"Gagal membaca data: {e}")
            jumlah_del = 0

        if jumlah_del == 0:
            st.info("Tidak ada data yang cocok dengan kriteria filter.")
        else:
            if where_clause == "1=1":
                st.error(
                    f"🚨 PERINGATAN: Tanpa filter aktif, **SEMUA {jumlah_del} baris**"
                    f" pada `{table}` akan dihapus!"
                )
            else:
                st.warning(
                    f"⚠️ **{jumlah_del} baris data** pada `{table}` akan dihapus"
                    " permanen berdasarkan filter."
                )
            confirm_del = st.checkbox(
                "Saya paham tindakan ini permanen dan ingin melanjutkan.",
                key="pari_confirm_delete",
            )
            if st.button(
                "🗑️ Hapus Data Sesuai Filter",
                disabled=not confirm_del,
                key="pari_btn_delete",
                type="primary",
                width="stretch",
            ):
                try:
                    with etl_engine.engine.begin() as conn:
                        conn.execute(text(f"DELETE FROM {table} WHERE {where_clause}"), params_del)
                    st.success(f"✅ Berhasil menghapus {jumlah_del} baris dari `{table}`.")
                    st.cache_data.clear()  # rows removed; every cached read is now stale
                    st.session_state.pop("pari_confirm_delete", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menghapus data: {e}")

    # --- TAB 4: SERIES GRAPH (TREND ANALYSIS) ---
    with tab_series:
        st.markdown("### Data Series Graph (Analisis Tren)")

        s1, s2 = st.columns(2)
        with s1:
            prov_series = st.selectbox("Provinsi", ["SEMUA"] + TARGET_PROVINCES, key="pari_series_prov")
        with s2:
            kab_options = ["SEMUA"] + _kabupaten_list_cached(prov_series)
            # Reset kabupaten when the province changes so a now-invalid choice
            # from the previous province's list cannot stick.
            if st.session_state.get("pari_series_kab") not in kab_options:
                st.session_state["pari_series_kab"] = "SEMUA"
            kab_series = st.selectbox("Kabupaten/Kota", kab_options, key="pari_series_kab")

        var_series = st.multiselect(
            "Indikator Variabel",
            PARI_VAR_OPTIONS,
            default=[PARI_VAR_OPTIONS[0]],
            key="pari_series_vars",
            help="Pilih satu atau lebih variabel untuk diplot pada grafik.",
        )

        if st.button("📈 Buat Visualisasi Grafik", key="pari_series_gen", type="primary", width="stretch"):
            if not var_series:
                st.warning("⚠️ Pilih minimal satu variabel indikator.")
                return

            # ponytail: push aggregation to SQL (sum per period) to cut row count/IO;
            # add cache key by filter+vars so identical renders avoid re-query.
            # Upgrade path: if per-series grouping by entity needed later, extend SELECT/GROUP BY.
            @st.cache_data
            def _load_series(table_name, prov_series, kab_series, var_list):
                cols_needed = ["year", "month", "kd_prov", "kd_kab"] + list(var_list)
                select_cols = ", ".join(f'"{c}"' if c in ("year", "month", "kd_prov", "kd_kab") else c for c in cols_needed)
                query = f"SELECT {select_cols} FROM {table_name}"
                conditions, params = [], {}
                if prov_series != "SEMUA":
                    conditions.append("kd_prov = :prov")
                    params["prov"] = prov_series
                if kab_series != "SEMUA":
                    conditions.append("kd_kab = :kab")
                    params["kab"] = kab_series
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                df = pd.read_sql(text(query), etl_engine.engine, params=params)
                return df

            try:
                df = _load_series(table, prov_series, kab_series, tuple(var_series))
            except Exception as e:
                st.error(f"Gagal membaca data: {e}")
                return

            if df.empty:
                st.warning("⚠️ Tidak ditemukan data yang cocok dengan kombinasi filter Anda.")
                return

            df = df.copy()
            df["tahun_int"] = df["year"].astype(int)
            df["month_num"] = df["month"].astype(int)
            df["periode"] = df["month_num"].apply(lambda m: bulan(m)) + " " + df["tahun_int"].astype(str)

            group_cols = ["tahun_int", "month_num", "periode"]
            frames = []
            for var in var_series:
                var_title = var.replace("_", " ").title()
                g = df.groupby(group_cols)[var].sum().reset_index()
                g["seri"] = var_title
                g = g.rename(columns={var: "nilai"})
                frames.append(g)

            df_long = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=group_cols + ["seri", "nilai"])

            periode_order = (
                df_long[["tahun_int", "month_num", "periode"]]
                .drop_duplicates()
                .sort_values(["tahun_int", "month_num"])["periode"]
                .tolist()
            )

            wilayah_label = "Seluruh Wilayah"
            if prov_series != "SEMUA" and kab_series != "SEMUA":
                wilayah_label = f"{kab_series}, {prov_series}"
            elif prov_series != "SEMUA":
                wilayah_label = prov_series
            elif kab_series != "SEMUA":
                wilayah_label = kab_series

            judul = ", ".join(v.replace("_", " ").title() for v in var_series)

            try:
                fig = px.line(
                    df_long,
                    x="periode",
                    y="nilai",
                    color="seri",
                    category_orders={"periode": periode_order},
                    title=f"Tren {judul} — {wilayah_label}",
                    markers=True,
                    template="plotly_white",
                )
                fig.update_layout(xaxis_title="Periode Waktu", yaxis_title="Nilai", legend_title="Indikator")
                st.plotly_chart(fig, width="stretch")
            except Exception as e:
                st.error(f"Gagal membuat grafik: {e}")

            with st.expander("📋 Lihat Tabel Data Mentah di Balik Grafik"):
                display_df = df_long.drop(columns=["tahun_int", "month_num"]).rename(
                    columns={"periode": "Periode", "seri": "Seri", "nilai": "Nilai"}
                )
                st.dataframe(display_df, width="stretch")
