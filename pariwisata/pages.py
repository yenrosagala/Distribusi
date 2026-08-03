import pandas as pd
import numpy as np
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from pariwisata.ai import generate_akomodasi_tables

# ============================================================
# THEME TOKENS — kept in sync with style.css
# ============================================================
PRIMARY, PRIMARY_DARK, POSITIVE, NEGATIVE = "#F59E0B", "#D97706", "#10B981", "#EF4444"
INK, INK_DIM, LINE, MAP_BG = "#0F172A", "#64748B", "#E2E8F0", "#0B0F14"

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
    st.markdown('<div class="hero-title">👋 Welcome back, ' + st.session_state["name"] + '</div>', unsafe_allow_html=True)

    if not df_info.empty:
        latest_year, latest_month = year_list[-1], month_list[-1]
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
        m1.metric("Provinces Tracked", len(TARGET_PROVINCES))
        m2.metric("Latest Period", f"{month_name(latest_month)} {latest_year}")
        m3.metric("Avg. TPK (latest)", f"{df_latest['tpk'].iloc[0]:.1f}%" if pd.notna(df_latest['tpk'].iloc[0]) else "—")
        m4.metric(
            "Avg. RLMTGAB (latest)",
            f"{df_latest['rlmtgab'].iloc[0]:.1f} malam" if pd.notna(df_latest['rlmtgab'].iloc[0]) else "—",
        )

        with st.container(border=True):
            st.markdown("### Data Coverage")
            st.write(f"Records span **{month_name(month_list[0])} {year_list[0]}** through "
                     f"**{month_name(month_list[-1])} {year_list[-1]}**, covering "
                     f"{len(prov_list)} province(s) and Hotel Bintang / Non Bintang classifications.")
            st.caption("Use the sidebar to jump to the Infographic map, trend charts, or the AI-narrated report.")
    else:
        with st.container(border=True):
            st.info(
                "No data has been ingested yet. If you're an admin, head to **Admin ETL Uploads** "
                "in the sidebar to load the first Excel matrix."
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
                        st.plotly_chart(fig_map, use_container_width=True)
                else:
                    with st.container(border=True):
                        st.warning("Map geometry file (papua_provinces.parquet) could not be loaded.")

                csv_data = df_cur.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "📥 Download Summary CSV", data=csv_data,
                    file_name=f"infographic_{map_indicator}_{map_year}_{map_month}.csv", mime="text/csv",
                    use_container_width=True,
                )

            with col_right:
                for prov in right_provs:
                    render_province_card(prov, df_cur, df_prev)


# ============================================================
# PAGE — TRENDS VISUALIZATIONS
# ============================================================
def render_trends(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="hero-title">Trends Visualizations</div>', unsafe_allow_html=True)
    st.markdown('<div class="filter-container">', unsafe_allow_html=True)
    v_col1, v_col2, v_col3 = st.columns(3)
    with v_col1:
        viz_prov = st.selectbox("Select Province", options=prov_list, key="v_prov")
    with v_col2:
        viz_year = st.selectbox("Select Year", options=year_list, key="v_year")
    with v_col3:
        viz_month = st.selectbox(
            "Select Month for Comparison", options=month_list, format_func=month_name, key="v_month"
        )
    st.markdown("</div>", unsafe_allow_html=True)

    if viz_prov and viz_year and viz_month:
        # 1. Fetch trend data across the whole year for line charts
        trend_query = text(f"""
            SELECT jenis_akomodasi, month, AVG(tpk) as tpk, AVG(rlmtgab) as rlmtgab
            FROM {etl_engine.general_table_name}
            WHERE kd_prov = :prov AND year = :year
            GROUP BY jenis_akomodasi, month
            ORDER BY month
        """)
        with etl_engine.engine.connect() as conn:
            df_agg = pd.read_sql_query(trend_query, conn, params={"prov": viz_prov, "year": viz_year})

        # 2. Determine previous month and year for comparison
        prev_month = viz_month - 1
        prev_year = viz_year
        if prev_month < 1:
            prev_month = 12
            prev_year = viz_year - 1

        comp_query = text(f"""
            SELECT jenis_akomodasi, month, year, AVG(tpk) as tpk, AVG(rlmtgab) as rlmtgab
            FROM {etl_engine.general_table_name}
            WHERE kd_prov = :prov AND ((year = :year AND month = :month) OR (year = :prev_year AND month = :prev_month))
            GROUP BY jenis_akomodasi, month, year
            ORDER BY year, month
        """)
        with etl_engine.engine.connect() as conn:
            df_comp = pd.read_sql_query(
                comp_query, 
                conn, 
                params={"prov": viz_prov, "year": viz_year, "month": viz_month, "prev_year": prev_year, "prev_month": prev_month}
            )

        if not df_agg.empty:
            for jenis in df_agg["jenis_akomodasi"].unique():
                sub_df = df_agg[df_agg["jenis_akomodasi"] == jenis].copy()
                
                # Convert numerical month (e.g., 4, 5, 6) to readable month names so X-axis avoids decimals
                sub_df["month_name"] = sub_df["month"].apply(month_name)
                
                df_melted = sub_df.melt(
                    id_vars=["month_name"], value_vars=["tpk", "rlmtgab"], var_name="Indicator", value_name="Value"
                )
                df_melted["Indicator"] = df_melted["Indicator"].replace(
                    {"tpk": "TPK (Occupancy Rate)", "rlmtgab": "RLMTGAB (Length of Stay)"}
                )
                
                fig_line = px.line(
                    df_melted, x="month_name", y="Value", color="Indicator", markers=True,
                    color_discrete_map={"TPK (Occupancy Rate)": PRIMARY, "RLMTGAB (Length of Stay)": "#334155"},
                    title=None  # Explicitly prevents the "undefined" title block
                )
                fig_line.update_traces(line=dict(width=3), marker=dict(size=8))
                
                # Fix axis labels readability & styling override
                fig_line.update_layout(
                    xaxis_title="Month",
                    yaxis_title="Metric Value",
                    font=dict(family="Inter, sans-serif", color="#0F172A"),
                    xaxis=dict(showgrid=False, color="#64748B"),
                    yaxis=dict(showgrid=True, gridcolor="#E2E8F0", color="#64748B")
                )
                
                plotly_theme(fig_line, height=380)

                with st.container(border=True):
                    st.markdown(f"### Monthly Performance — {jenis}")
                    st.caption(f"{viz_prov} · {viz_year}")
                    st.plotly_chart(fig_line, width='stretch')
                    
                # Bar chart for current month vs previous month comparison
                sub_comp = df_comp[df_comp["jenis_akomodasi"] == jenis]
                if not sub_comp.empty:
                    sub_comp["Period Label"] = sub_comp.apply(lambda row: f"{month_name(int(row['month']))} {int(row['year'])}", axis=1)
                    df_bar_melted = sub_comp.melt(
                        id_vars=["Period Label"], value_vars=["tpk", "rlmtgab"], var_name="Indicator", value_name="Value"
                    )
                    df_bar_melted["Indicator"] = df_bar_melted["Indicator"].replace(
                        {"tpk": "TPK (Occupancy Rate)", "rlmtgab": "RLMTGAB (Length of Stay)"}
                    )
                    fig_bar = px.bar(
                        df_bar_melted, x="Indicator", y="Value", color="Period Label", barmode="group",
                        color_discrete_sequence=[PRIMARY, "#334155"],
                        title=f"Comparison: {month_name(viz_month)} {viz_year} vs Previous Month"
                    )
                    plotly_theme(fig_bar, height=340)

                    with st.container(border=True):
                        st.caption(f"{viz_prov} · {jenis}")
                        st.plotly_chart(fig_bar, width='stretch')
        else:
            with st.container(border=True):
                st.info("No trend data found for this province and year yet.")
    else:
        with st.container(border=True):
            st.info("Please select a province, year, and month to view trends and comparisons.")

## ============================================================
# PAGE — REPORT
# ============================================================
def render_report(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="hero-title">Report &amp; AI Narratives</div>', unsafe_allow_html=True)
    st.markdown('<div class="filter-container">', unsafe_allow_html=True)
    r_col1, r_col2, r_col3 = st.columns(3)
    with r_col1:
        rep_prov = st.selectbox("Province", options=prov_list, key="rep_prov")
    with r_col2:
        rep_year = st.selectbox("Year", options=year_list, key="rep_year")
    with r_col3:
        rep_month = st.selectbox("Month", options=month_list, format_func=month_name, key="rep_month")
    st.markdown("</div>", unsafe_allow_html=True)

    if rep_prov and rep_year and rep_month:
        with st.container(border=True):
            generate_akomodasi_tables(etl_engine, rep_prov, rep_year, rep_month)
            
# ============================================================
# PAGE — ADMIN ETL UPLOADS
# ============================================================
def render_admin_etl(etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces):
    st.markdown('<div class="hero-title">Admin: ETL Data Ingestion</div>', unsafe_allow_html=True)
    card_open("Admin Control Panel", "ETL data ingestion")
    st.markdown(
        f"<p style='color:{INK_DIM};'>Upload source Excel matrices directly into the database "
        "and run system maintenance.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    uploaded_files = st.file_uploader("Upload Excel Source Files (.xlsx)", type=["xlsx"], accept_multiple_files=True)

    adm_col1, adm_col2 = st.columns(2)
    with adm_col1:
        target_year = st.number_input("Target Year", value=2026)
    with adm_col2:
        target_month = st.selectbox("Target Month", options=list(range(1, 13)), format_func=month_name)

    if st.button("🚀 Process & Ingest Files", type="primary"):
        if uploaded_files:
            with st.spinner("Ingesting files into the database…"):
                for uploaded_file in uploaded_files:
                    etl_engine.etl_pipeline(uploaded_file, year=int(target_year), month=int(target_month))
            st.success(f"{len(uploaded_files)} file(s) successfully ingested into the database.")
            get_filter_options.clear()
        else:
            st.warning("Please upload at least one Excel file before processing.")
    card_close()
