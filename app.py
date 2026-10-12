import os
from pathlib import Path

import streamlit as st

import modules.admin_page as transportasi_admin
import modules.dashboard_page as transportasi_dashboard
from modules.config import load_project_env
from modules.database import get_engine, init_db, init_narrative_table
import modules.report_page as transportasi_report
import pariwisata.pages as pariwisata_pages
from pariwisata.etl_engine import ETLEngine

# ============================================================
# PAGE CONFIG  (UI/UX shell — from dash-pariwisata)
# ============================================================
st.set_page_config(
    page_title="Statistik Distribusi Papua",
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

# ============================================================
# AUTH STATE  (from dash-pariwisata — now guards both domains)
# ============================================================
if "authenticated" not in st.session_state:
  st.session_state["authenticated"] = False
  st.session_state["role"] = "user"
  st.session_state["name"] = "General Analyst"
if "section" not in st.session_state:
  st.session_state["section"] = "pariwisata"

# Login credentials. Values below are local-dev defaults only; override via
# LOGIN_ADMIN_PASSWORD / LOGIN_USER_PASSWORD in the project-root .env
# (gitignored) or the real environment.
load_project_env()
_ADMIN_PASSWORD = os.getenv("LOGIN_ADMIN_PASSWORD", "admin123")
_USER_PASSWORD = os.getenv("LOGIN_USER_PASSWORD", "user123")

USERS = {
    "admin": {
        "password": _ADMIN_PASSWORD,
        "role": "admin",
        "name": "Database Administrator",
    },
    "user": {"password": _USER_PASSWORD, "role": "user", "name": "General Analyst"},
}

# ============================================================
# LOGIN SCREEN
# ============================================================
if not st.session_state["authenticated"]:
    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
    _, col2, _ = st.columns([1, 1.15, 1])
    with col2:
        st.markdown(
            "<div style='text-align:center;'>"
            "<img src='https://raw.githubusercontent.com/yenrosagala/Distribusi/main/logo.png'"
            " width='54' alt='Logo BPS Papua'/>"
            "</div>",
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <h1 style='text-align:center; color:#0F172A; margin-top:8px; font-size:24px;'>Dashboard <br> Statistik Distribusi & Jasa <br> BPS Papua</h1>
            <p style='text-align:center; color:#64748B; font-size:13px; margin:4px 0 18px 0;'>
                Sign in to the Papua Pariwisata &amp; Transportasi intelligence platform
            </p>
            """,
            unsafe_allow_html=True,
        )

        with st.form("login_form"):
            username = st.text_input(
                "Nama Pengguna",
                placeholder="admin atau user",
            )
            password = st.text_input(
                "Kata Sandi",
                type="password",
                placeholder="Masukkan kata sandi",
            )
            submit = st.form_submit_button(
                "Masuk", type="primary", width='stretch'
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
            <p class="sidebar-title" style="margin:0;">Dashboard <br> Statistik Distribusi & Jasa <br> BPS Papua</p>
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
        width='stretch',
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

  ai_online = False
  try:
    from pariwisata.ai import get_gemini_client
    from modules.ai_backup import get_openrouter_key

    # Online bila salah satu provider tersambung; OpenRouter ada sebagai
    # cadangan, jadi status tidak boleh padam hanya karena Gemini mati.
    ai_online = get_gemini_client() is not None or bool(get_openrouter_key())
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
  if st.button("Keluar", width='stretch', help="Akhiri sesi dan kembali ke halaman masuk."):
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
    f'<div><h1 class="section-title">{active_meta["title"]}</h1>'
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
      "📋 Report",
  ]
  if st.session_state["role"] == "admin":
    tab_labels.append("🛠️ Admin ETL Uploads")
  tabs = st.tabs(tab_labels)

  with tabs[0]:
    pariwisata_pages.render_home_dashboard(
        etl_engine, df_info, prov_list, year_list, month_list
    )
  with tabs[1]:
    pariwisata_pages.render_infographic_map(
        etl_engine, df_info, prov_list, year_list, month_list
    )
  with tabs[2]:
    pariwisata_pages.render_report(
        etl_engine, df_info, prov_list, year_list, month_list
    )
  if st.session_state["role"] == "admin":
    with tabs[3]:
      pariwisata_pages.render_admin_etl(
          etl_engine, df_info, prov_list, year_list, month_list
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
    '<p class="app-footer">Dashboard Statistik Distribusi & Jasa BPS Papua · Pariwisata (BPS hotel occupancy matrices) '
    "&amp; Transportasi (BPS transport laut/udara) — data bersumber dari BPS Provinsi Papua</p>",
    unsafe_allow_html=True,
)
