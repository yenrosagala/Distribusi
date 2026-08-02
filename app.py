from pathlib import Path
import geopandas as gpd
import pandas as pd
import streamlit as st

from modules.admin_page import show_series_admin_page
import modules.admin_page as transportasi_admin
import modules.dashboard_page as transportasi_dashboard
from modules.database import get_engine, init_db, init_narrative_table
import modules.report_page as transportasi_report
import pariwisata.pages as pariwisata_pages
from pariwisata.etl_engine import ETLEngine

# ============================================================
# PAGE CONFIG  (UI/UX shell — from dash-pariwisata)
# ============================================================
st.set_page_config(
    page_title="Dashboard Integrasi Papua — Pariwisata & Transportasi",
    page_icon="https://raw.githubusercontent.com/yenrosagala/Distribusi/main/logo.png",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).parent

# ============================================================
# STYLE INJECTION
# ============================================================
def load_css():
  css_path = BASE_DIR / "style.css"
  if css_path.exists():
    st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)

load_css()

# ============================================================
# SHARED DATABASE — single engine for both domains (StaTransportasi's layer)
# ============================================================
@st.cache_resource
def get_shared_engine():
  engine = get_engine()
  init_db()  # transportasi tables (wilayah, transportasi_laut, transportasi_udara)
  init_narrative_table()  # transportasi AI narrative cache
  return engine

engine = get_shared_engine()

@st.cache_resource
def get_etl():
  return ETLEngine(engine)

etl_engine = get_etl()

@st.cache_data
def load_geodata():
  return gpd.read_parquet(BASE_DIR / "papua_provinces.parquet")

try:
  gdf_provinces = load_geodata()
except Exception:
  gdf_provinces = pd.DataFrame()

# ============================================================
# AUTH STATE  (from dash-pariwisata — now guards both domains)
# ============================================================
if "authenticated" not in st.session_state:
  st.session_state["authenticated"] = False
  st.session_state["role"] = "user"
  st.session_state["name"] = "General Analyst"
if "section" not in st.session_state:
  st.session_state["section"] = "pariwisata"

USERS = {
    "admin": {
        "password": "admin123",
        "role": "admin",
        "name": "Database Administrator",
    },
    "user": {"password": "user123", "role": "user", "name": "General Analyst"},
}

# ============================================================
# LOGIN SCREEN
# ============================================================
if not st.session_state["authenticated"]:
  st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
  _, col2, _ = st.columns([1, 1.15, 1])
  with col2:
    # Logo & Header Title
    l_col1, l_col2, l_col3 = st.columns([1, 1, 1])
    with l_col2:
      st.image(
          "https://raw.githubusercontent.com/yenrosagala/Distribusi/main/logo.png",
          width=54,
      )

    st.markdown(
        """
        <h2 style='text-align:center; color:#0F172A; margin-top:8px;'>Dashboard Integrasi Papua</h2>
        <p style='text-align:center; color:#64748B; font-size:13px; margin:4px 0 18px 0;'>
            Sign in to the Papua Pariwisata &amp; Transportasi intelligence platform
        </p>
        """,
        unsafe_allow_html=True,
    )
    
    # Form login otomatis menjadi card container yang membungkus input & tombol secara rapi
    with st.form("login_form"):
      username = st.text_input("Username", placeholder="admin or user")
      password = st.text_input(
          "Password", type="password", placeholder="Enter password"
      )
      submit = st.form_submit_button(
          "Sign in", type="primary", use_container_width=True
      )
      if submit:
        if (
            username in USERS
            and USERS[username]["password"] == password
        ):
          st.session_state["authenticated"] = True
          st.session_state["role"] = USERS[username]["role"]
          st.session_state["name"] = USERS[username]["name"]
          st.rerun()
        else:
          st.error(
              "Username or password is incorrect. Please try again."
          )
          
  st.markdown("</div>", unsafe_allow_html=True)
  st.stop()
# ============================================================
# NAVIGATION — two top-level sections only. Every sub-page that used
# to be a separate sidebar button now lives inside a tab within its
# section, so the sidebar stays short and each section owns its own
# in-page navigation.
# ============================================================
SECTIONS = [
    {
        "id": "pariwisata",
        "title": "Pariwisata",
        "icon": "🏝️",
        "subtitle": "Hotel occupancy & length-of-stay",
    },
    {
        "id": "transportasi",
        "title": "Transportasi",
        "icon": "🚌",
        "subtitle": "Sea & air transport statistics",
    },
]

# ============================================================
# SIDEBAR — brand, two-section navigation, session
# ============================================================
with st.sidebar:
  col_brand1, col_brand2 = st.columns([1, 3])
  with col_brand1:
    st.image(
        "https://raw.githubusercontent.com/yenrosagala/Distribusi/main/logo.png",
        width=36,
    )
  with col_brand2:
    st.markdown(
        """
            <p class="sidebar-title" style="margin:0;">Dashboard Integrasi Papua</p>
            <p class="sidebar-subtitle" style="margin:0;">Pariwisata &amp; Transportasi</p>
            """,
        unsafe_allow_html=True,
    )

  st.markdown("<br>", unsafe_allow_html=True)
  st.caption("MAIN MENU")
  for sec in SECTIONS:
    active = st.session_state["section"] == sec["id"]
    if st.button(
        f"{sec['icon']}  {sec['title']}",
        use_container_width=True,
        key=f"nav_{sec['id']}",
        type="primary" if active else "secondary",
    ):
      st.session_state["section"] = sec["id"]
      st.rerun()
    st.markdown(
        f'<p class="nav-subtitle">{sec["subtitle"]}</p>',
        unsafe_allow_html=True,
    )

  st.markdown("<br>", unsafe_allow_html=True)

  ai_online = None
  try:
    from pariwisata.ai import get_gemini_client

    ai_online = get_gemini_client() is not None
  except Exception:
    ai_online = False
  ai_status = "AI Engine Online" if ai_online else "AI Engine Offline"
  status_class = "status-dot" if ai_online else "status-dot status-dot-off"
  st.markdown(
      f"""
        <div class="user-card">
            <b>USER:</b> {st.session_state['name']}<br>
            <b>ROLE:</b> {st.session_state['role'].upper()}<br>
            <span class="{status_class}"></span>{ai_status}
        </div>
        """,
      unsafe_allow_html=True,
  )
  st.markdown("<br>", unsafe_allow_html=True)
  if st.button("Log out", use_container_width=True):
    st.session_state["authenticated"] = False
    st.rerun()

section = st.session_state["section"]

# ============================================================
# PARIWISATA DATA CONTEXT (shared across its tabs)
# ============================================================
df_info = pariwisata_pages.get_filter_options(etl_engine)
prov_list = (
    sorted(df_info["kd_prov"].dropna().astype(str).unique().tolist())
    if not df_info.empty
    else []
)
year_list = (
    sorted(df_info["year"].dropna().astype(int).unique().tolist())
    if not df_info.empty
    else []
)
month_list = (
    sorted(df_info["month"].dropna().astype(int).unique().tolist())
    if not df_info.empty
    else []
)

# ============================================================
# SECTION HEADER
# ============================================================
active_meta = next(s for s in SECTIONS if s["id"] == section)
st.markdown(
    f'<div class="section-header"><span class="section-icon">{active_meta["icon"]}</span>'
    f'<div><div class="section-title">{active_meta["title"]}</div>'
    f'<div class="section-subtitle">{active_meta["subtitle"]}</div></div></div>',
    unsafe_allow_html=True,
)

# ============================================================
# PAGE ROUTING — every function that used to be its own sidebar page
# now renders inside a tab of its section.
# ============================================================
if section == "pariwisata":
  tab_labels = [
      "🏠 Home Dashboard",
      "🗺️ Infographic Stat Map",
      "📈 Trends Visualizations",
      "📋 Report",
  ]
  if st.session_state["role"] == "admin":
    tab_labels.append("🛠️ Admin ETL Uploads")
  tabs = st.tabs(tab_labels)

  with tabs[0]:
    pariwisata_pages.render_home_dashboard(
        etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces
    )
  with tabs[1]:
    pariwisata_pages.render_infographic_map(
        etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces
    )
  with tabs[2]:
    pariwisata_pages.render_trends(
        etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces
    )
  with tabs[3]:
    pariwisata_pages.render_report(
        etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces
    )
  if st.session_state["role"] == "admin":
    with tabs[4]:
      pariwisata_pages.render_admin_etl(
          etl_engine, df_info, prov_list, year_list, month_list, gdf_provinces
      )

elif section == "transportasi":
  tab_labels = ["📊 Dashboard Statistik", "📄 Laporan Komparatif"]
  if st.session_state["role"] == "admin":
    tab_labels.append("🔐 Admin & Analisis Series")
  tabs = st.tabs(tab_labels)

  with tabs[0]:
    transportasi_dashboard.show_dashboard_page()
  with tabs[1]:
    transportasi_report.show_report_page()
  if st.session_state["role"] == "admin":
    with tabs[2]:
      transportasi_admin.show_series_admin_page()

# ============================================================
# FOOTER
# ============================================================
st.markdown(
    '<p class="app-footer">Dashboard Integrasi Papua · Pariwisata (BPS hotel occupancy matrices) '
    "&amp; Transportasi (BPS transport laut/udara) — data bersumber dari BPS Provinsi Papua</p>",
    unsafe_allow_html=True,
)
