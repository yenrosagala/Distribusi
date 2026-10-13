import logging
import io
import streamlit as st
from pptx import Presentation
from pptx.util import Inches, Pt
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from google import genai
from modules.database import get_engine, get_db_narrative, save_db_narrative, delete_db_narrative
from modules.ai_backup import openrouter_generate
from modules.report_page import (format_id_number, _arah_dinamis, get_gemini_api_keys,
                                 normalize_narrative, italicize_english, MONTH_MAP, INV_MONTH_MAP)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


# ==============================================================================
# HELPER MANDIRI
# ==============================================================================
MONTH_ABBR = {'Januari': 'Jan', 'Februari': 'Feb', 'Maret': 'Mar', 'April': 'Apr', 'Mei': 'Mei', 'Juni': 'Jun',
              'Juli': 'Jul', 'Agustus': 'Agt', 'September': 'Sep', 'Oktober': 'Okt', 'November': 'Nov', 'Desember': 'Des'}

PROVINSI_ORDER = ['Papua', 'Papua Selatan', 'Papua Tengah', 'Papua Pegunungan']


# ==============================================================================
# HELPERS DATA
# ==============================================================================
@st.cache_data(ttl=300)
def get_available_periods(_engine):
    periods = set()
    for table in ['transportasi_udara', 'transportasi_laut']:
        try:
            df = pd.read_sql(text(f"SELECT DISTINCT tahun, bulan FROM {table}"), _engine)
            for _, r in df.iterrows():
                periods.add((int(r['tahun']), r['bulan']))
        except Exception:
            continue
    return sorted(periods, key=lambda x: (x[0], MONTH_MAP.get(x[1], 0)))


def get_prev_period(thn, bln):
    m = MONTH_MAP[bln]
    if m > 1:
        return thn, INV_MONTH_MAP[m - 1]
    return thn - 1, 'Desember'


@st.cache_data(ttl=300)
def load_period_data(_engine, table, thn, bln):
    query = f"SELECT * FROM {table} WHERE CAST(tahun AS TEXT) = :thn AND bulan = :bln"
    return pd.read_sql(text(query), _engine, params={"thn": str(thn), "bln": bln})


def agg_by_provinsi(df, cols):
    base = pd.DataFrame(0.0, index=PROVINSI_ORDER, columns=cols)
    if df is None or df.empty:
        return base
    tmp = df.copy()
    tmp['nama_provinsi'] = tmp['nama_provinsi'].astype(str).str.strip().str.title()
    g = tmp.groupby('nama_provinsi')[cols].sum()
    for p in PROVINSI_ORDER:
        if p in g.index:
            base.loc[p] = g.loc[p]
    return base


def pct_change(curr, prev):
    if prev == 0 or pd.isna(prev) or pd.isna(curr):
        return None
    return (curr - prev) / prev * 100


@st.cache_data(ttl=300)
def load_cumulative_data(_engine, table, thn, bln):
    bln_num = MONTH_MAP[bln]
    months = [INV_MONTH_MAP[i] for i in range(1, bln_num + 1)]
    params = {f"m{i}": m for i, m in enumerate(months)}
    placeholders = ", ".join(f":{k}" for k in params)
    query = f"SELECT * FROM {table} WHERE CAST(tahun AS TEXT) = :thn AND bulan IN ({placeholders})"
    params["thn"] = str(thn)
    return pd.read_sql(text(query), _engine, params=params)


def build_stacked_bar(df_curr, col_bottom, col_top, label_bottom, label_top,
                       color_bottom, color_top, title):
    fig = go.Figure()
    fig.add_bar(
        name=label_bottom, x=df_curr.index, y=df_curr[col_bottom],
        marker_color=color_bottom,
        text=[f"{v:,.0f}".replace(",", ".") for v in df_curr[col_bottom]],
        textposition='inside', textfont=dict(color='white', size=12)
    )
    fig.add_bar(
        name=label_top, x=df_curr.index, y=df_curr[col_top],
        marker_color=color_top,
        text=[f"{v:,.0f}".replace(",", ".") for v in df_curr[col_top]],
        textposition='inside', textfont=dict(color='white', size=12)
    )
    fig.update_layout(
        barmode='stack', title=dict(text=title, font=dict(size=14)),
        template='plotly_white', legend=dict(orientation='h', y=-0.15, x=0.5, xanchor='center'),
        margin=dict(t=50, b=10, l=10, r=10), height=380
    )
    return fig


def build_growth_table(df_curr, df_prev, cols, labels, periode_label):
    rows = []
    for prov in PROVINSI_ORDER:
        row = {'PROVINSI': prov}
        for c, l in zip(cols, labels):
            curr_v = df_curr.loc[prov, c] if prov in df_curr.index else 0
            prev_v = df_prev.loc[prov, c] if prov in df_prev.index else 0
            row[f"PERKEMBANGAN {l} {periode_label} (%)"] = pct_change(curr_v, prev_v)
        rows.append(row)
    return pd.DataFrame(rows).set_index('PROVINSI')


def style_growth_table(df, header_color):
    def fmt(v):
        return "" if pd.isna(v) else format_id_number(v, decimals=2)

    styler = df.style.format(fmt).background_gradient(cmap='RdYlGn', axis=None, vmin=-30, vmax=30)
    # 'th' mencakup header kolom DAN nama baris (row_heading) -> keduanya hitam.
    # Header wajib background terang: hitam di atas emas tua hanya 5.5:1, di atas
    # merah tua hanya 1.8:1, sedangkan putih di atas emas hanya 2.4:1 (gagal AA).
    styler = styler.set_table_styles([
        {'selector': 'th', 'props': [('background-color', header_color), ('color', '#0F172A'),
                                      ('font-weight', 'bold'), ('text-align', 'center')]},
        {'selector': 'td', 'props': [('text-align', 'right')]}
    ])
    return styler


# ==============================================================================
# NARASI PER SECTION (Executive Summary)
# ==============================================================================
def compute_indicator_stats(df_curr, df_prev, df_cum_curr, df_cum_prev, cols):
    combined_curr = df_curr[cols].sum(axis=1)
    combined_prev = df_prev[cols].sum(axis=1)
    combined_cum_curr = df_cum_curr[cols].sum(axis=1)
    combined_cum_prev = df_cum_prev[cols].sum(axis=1)

    mtm_per_prov = pd.Series({p: pct_change(combined_curr.get(p, 0), combined_prev.get(p, 0)) for p in PROVINSI_ORDER})
    yoy_per_prov = pd.Series({p: pct_change(combined_cum_curr.get(p, 0), combined_cum_prev.get(p, 0)) for p in PROVINSI_ORDER})

    total_curr, total_prev = combined_curr.sum(), combined_prev.sum()
    total_cum_curr, total_cum_prev = combined_cum_curr.sum(), combined_cum_prev.sum()

    return {
        'curr': total_curr, 'prev': total_prev, 'mtm': pct_change(total_curr, total_prev),
        'cum_curr': total_cum_curr, 'cum_prev': total_cum_prev, 'yoy': pct_change(total_cum_curr, total_cum_prev),
        'mtm_per_prov': mtm_per_prov, 'yoy_per_prov': yoy_per_prov,
    }


def generate_section_narrative_ai(moda_nama, bln, thn, prev_bln, prev_thn,
                                   penumpang_stats, barang_stats, satuan_barang):
    api_keys = get_gemini_api_keys()
    if not api_keys:
        return None

    candidate_models = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-flash-lite"
    ]

    if "gemini_key_index" not in st.session_state:
        st.session_state["gemini_key_index"] = 0

    num_keys = len(api_keys)

    prompt = (
        "Anda adalah Analis Kebijakan Utama dan Ahli Statistik Senior pada Direktorat Statistik Transportasi Badan Pusat Statistik (BPS).\n"
        f"Susunlah sebuah Executive Summary resmi yang objektif, analitis, dan berwibawa tepat dalam 2 (dua) paragraf untuk publikasi kinerja Transportasi {moda_nama} "
        f"di regional Papua (Provinsi Papua, Papua Selatan, Papua Tengah, dan Papua Pegunungan) periode {bln} {thn}.\n\n"
        "Standar Penulisan & Kaidah Kebahasaan:\n"
        "- Gunakan ragam bahasa resmi instansi pemerintah (bahasa baku, objektif, berorientasi data).\n"
        "- Hindari pengulangan kata yang monoton; gunakan diksi analitis.\n\n"
        "Struktur & Substansi Wajib:\n"
        "1. Paragraf Pertama (Analisis Bulanan / Month-to-Month):\n"
        f"   - Evaluasi komparatif volume agregat penumpang saat ini ({format_id_number(penumpang_stats['curr'], 0)} orang) terhadap baseline {prev_bln} {prev_thn} ({format_id_number(penumpang_stats['prev'], 0)} orang).\n"
        f"   - Analisis dinamika volume barang: realisasi {format_id_number(barang_stats['curr'], 2)} {satuan_barang} berbanding periode sebelumnya {format_id_number(barang_stats['prev'], 2)} {satuan_barang}.\n\n"
        "2. Paragraf Kedua (Analisis Kumulatif / Year-to-Date & Year-on-Year):\n"
        f"   - Bedah performa makro kumulatif Januari–{bln} {thn} untuk penumpang dan barang.\n\n"
        "Aturan Mutlak:\n"
        "- Keluarkan HANYA teks dua paragraf tanpa pengantar, tanpa judul, dan tanpa penutup.\n"
        "- Format angka mutlak menerapkan standar penulisan Indonesia."
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
                    config=genai.types.GenerateContentConfig(temperature=0.3)
                )
                raw_text = getattr(response, "text", None)
                if raw_text and str(raw_text).strip():
                    st.session_state["gemini_key_index"] = (current_idx + 1) % num_keys
                    return str(raw_text).strip()
            except Exception as e:
                logger.warning("Gagal dengan Key ke-%d menggunakan model %s: %s", current_idx + 1, model_name, e)
                continue

    # Semua Gemini key gagal -> coba OpenRouter sebagai cadangan.
    backup = openrouter_generate(prompt, temperature=0.3)
    if backup:
        return backup

    return None                                       


def generate_section_narrative_fallback(moda_nama, bln, thn, prev_bln, prev_thn,
                                         penumpang_stats, barang_stats, satuan_barang):
    fmt0 = lambda v: format_id_number(v, decimals=0)
    fmt2 = lambda v: format_id_number(v, decimals=2)
    fmt_pct = lambda v: fmt2(abs(v)) if v is not None else "Undefined"

    para1 = (
        f"Total pergerakan penumpang angkutan {moda_nama.lower()} dalam negeri pada bulan {bln} {thn} "
        f"mencapai {fmt0(penumpang_stats['curr'])} orang, {_arah_dinamis(penumpang_stats['mtm'])} sebesar "
        f"{fmt_pct(penumpang_stats['mtm'])} persen dibandingkan {prev_bln} {prev_thn} ({fmt0(penumpang_stats['prev'])} orang). "
        f"Volume barang turut {_arah_dinamis(barang_stats['mtm'])} sebesar {fmt_pct(barang_stats['mtm'])} persen, "
        f"dari {fmt2(barang_stats['prev'])} {satuan_barang} menjadi {fmt2(barang_stats['curr'])} {satuan_barang}."
    )
    para2 = (
        f"Secara kumulatif periode Januari-{bln} {thn}, total penumpang mencapai {fmt0(penumpang_stats['cum_curr'])} orang, "
        f"{_arah_dinamis(penumpang_stats['yoy'])} {fmt_pct(penumpang_stats['yoy'])} persen dibandingkan periode yang sama tahun lalu. "
        f"Total barang kumulatif mencapai {fmt2(barang_stats['cum_curr'])} {satuan_barang}."
    )
    return para1, para2


def render_section_narrative(moda_nama, table, engine, cols_penumpang, cols_barang, satuan_barang,
                            df_curr, df_prev, thn, bln, prev_thn, prev_bln):
    report_type = f"dashboard_{table}"
    period_key = f"{bln}|{thn}"

    # 1. Cek database terlebih dahulu (Retrieve)
    db_text = get_db_narrative(report_type, period_key)
    if db_text:
        parts = [p.strip() for p in db_text.split("\n\n") if p.strip()]
        para1 = parts[0] if parts else ""
        para2 = "\n\n".join(parts[1:]) if len(parts) > 1 else ""
        source = "Database (Cached)"
    else:
        # Jika belum ada di DB, generate via AI / Fallback lalu simpan otomatis ke DB
        df_cum_curr_raw = load_cumulative_data(engine, table, thn, bln)
        df_cum_prev_raw = load_cumulative_data(engine, table, thn - 1, bln)
        all_cols = cols_penumpang + cols_barang
        df_cum_curr = agg_by_provinsi(df_cum_curr_raw, all_cols)
        df_cum_prev = agg_by_provinsi(df_cum_prev_raw, all_cols)

        penumpang_stats = compute_indicator_stats(df_curr, df_prev, df_cum_curr, df_cum_prev, cols_penumpang)
        barang_stats = compute_indicator_stats(df_curr, df_prev, df_cum_curr, df_cum_prev, cols_barang)

        with st.spinner(f"Menyusun ringkasan naratif Transportasi {moda_nama}..."):
            text_ai = generate_section_narrative_ai(
                moda_nama, bln, thn, prev_bln, prev_thn, penumpang_stats, barang_stats, satuan_barang
            )
            
        if text_ai:
            full_text = text_ai
            parts = [p.strip() for p in text_ai.split("\n\n") if p.strip()]
            para1 = parts[0] if parts else ""
            para2 = "\n\n".join(parts[1:]) if len(parts) > 1 else ""
            source = "Gemini AI"
        else:
            para1, para2 = generate_section_narrative_fallback(
                moda_nama, bln, thn, prev_bln, prev_thn, penumpang_stats, barang_stats, satuan_barang
            )
            full_text = f"{para1}\n\n{para2}"
            source = "Sistem Fallback"
            
        save_db_narrative(report_type, period_key, full_text)

    h1, h2 = st.columns([5, 1])
    with h1:
        st.markdown(f"**📝 Ringkasan Naratif** *(Sumber: {source})*")
    with h2:
        # Tombol Regenerasi hanya di-enable jika admin sudah login
        if st.session_state.get('admin_logged_in', False):
            if st.button("🔄 Regenerasi", key=f"regen_{table}_{thn}_{bln}", width='stretch'):
                delete_db_narrative(report_type, period_key)
                st.rerun()

    para1 = normalize_narrative(para1)
    if para2:
        para2 = normalize_narrative(para2)

    st.markdown(italicize_english(para1))
    if para2:
        st.markdown(italicize_english(para2))

    return para1, para2


# ==============================================================================
# SECTION RENDERER
# ==============================================================================
def show_section(engine, table, moda_title, icon, header_color,
                  cols_penumpang, labels_penumpang, colors_penumpang,
                  cols_barang, labels_barang, colors_barang,
                  thn, bln, prev_thn, prev_bln):
    st.markdown(f"## {icon} PERKEMBANGAN TRANSPORTASI {moda_title.upper()}")
    st.caption(f"Kondisi {bln} {thn}")

    df_curr_raw = load_period_data(engine, table, thn, bln)
    df_prev_raw = load_period_data(engine, table, prev_thn, prev_bln)

    if df_curr_raw.empty:
        st.warning(f"⚠️ Belum ada data Transportasi {moda_title} untuk periode {bln} {thn}.")
        return None

    all_cols = cols_penumpang + cols_barang
    df_curr = agg_by_provinsi(df_curr_raw, all_cols)
    df_prev = agg_by_provinsi(df_prev_raw, all_cols)

    c1, c2 = st.columns(2)
    with c1:
        fig1 = build_stacked_bar(
            df_curr, cols_penumpang[0], cols_penumpang[1],
            labels_penumpang[0], labels_penumpang[1],
            colors_penumpang[0], colors_penumpang[1],
            "PENUMPANG DATANG DAN BERANGKAT"
        )
        st.plotly_chart(fig1, width='stretch')
    with c2:
        fig2 = build_stacked_bar(
            df_curr, cols_barang[0], cols_barang[1],
            labels_barang[0], labels_barang[1],
            colors_barang[0], colors_barang[1],
            "MUAT DAN BONGKAR BARANG"
        )
        st.plotly_chart(fig2, width='stretch')

    periode_label = f"{MONTH_ABBR[prev_bln]}-{MONTH_ABBR[bln]} {thn}"
    t1, t2 = st.columns(2)
    with t1:
        tbl1 = build_growth_table(df_curr, df_prev, cols_penumpang, labels_penumpang, periode_label)
        st.dataframe(style_growth_table(tbl1, header_color), width='stretch')
    with t2:
        tbl2 = build_growth_table(df_curr, df_prev, cols_barang, labels_barang, periode_label)
        st.dataframe(style_growth_table(tbl2, "#FECACA"), width='stretch')

    satuan_barang = "ton" if moda_title == "Laut" else "kg"
    para1, para2 = render_section_narrative(
        moda_title, table, engine, cols_penumpang, cols_barang, satuan_barang,
        df_curr, df_prev, thn, bln, prev_thn, prev_bln
    )
    return {
        "moda": moda_title, "bln": bln, "thn": thn,
        "figs": [fig1, fig2], "tables": [tbl1, tbl2],
        "narration": (para1, para2),
    }


def _slide_title(slide, text):
    tb = slide.shapes.add_textbox(Inches(0.4), Inches(0.2), Inches(12.5), Inches(0.7))
    tf = tb.text_frame
    tf.text = text
    run = tf.paragraphs[0].runs[0]
    run.font.size = Pt(24)
    run.font.bold = True


def _add_pptx_table(slide, df, left, top, width, height):
    """Tabel dengan format sel sama persis dengan gaya web growth table."""
    def fmt(v):
        return "" if pd.isna(v) else format_id_number(v, decimals=2)

    rows, cols = df.shape
    gf = slide.shapes.add_table(rows + 1, cols + 1, left, top, width, height)
    tbl = gf.table
    for j, h in enumerate(["PROVINSI"] + list(df.columns)):
        tbl.cell(0, j).text = str(h)
    for i, (idx, row) in enumerate(df.iterrows(), start=1):
        tbl.cell(i, 0).text = str(idx)
        for j, v in enumerate(row, start=1):
            tbl.cell(i, j).text = fmt(v)
    for r_i, row in enumerate(tbl.rows):
        row.height = Inches(0.3)
        for cell in row.cells:
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9)
                    if r_i == 0:
                        run.font.bold = True


def _add_pptx_narration(slide, para1, para2, left, top, width, height):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.text = para1
    for p in tf.paragraphs:
        for run in p.runs:
            run.font.size = Pt(11)
    if para2:
        p2 = tf.add_paragraph()
        p2.text = para2
        for run in p2.runs:
            run.font.size = Pt(11)


def _fig_to_png(fig, width=1100, height=400):
    """Render a plotly figure to PNG bytes via matplotlib — tidak butuh Chrome.

    ponytail: hanya menangani bar trace bertumpuk (satu-satunya jenis yang dipakai
    dashboard). Kalau deck nanti memuat line/scatter, tambahkan cabangnya di sini.
    Angka di dalam batang sengaja dilewatkan — nilainya sudah ada di slide tabel.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    traces = list(fig.data)
    xs = list(traces[0].x)
    x = list(range(len(xs)))

    mfig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    bottom = [0.0] * len(xs)
    for tr in traces:
        y = [0.0 if v is None else float(v) for v in tr.y]
        ax.bar(x, y, bottom=bottom, width=0.62,
               color=getattr(tr.marker, "color", None) or "#1F4E79",
               label=tr.name)
        bottom = [b + v for b, v in zip(bottom, y)]

    ax.set_xticks(x)
    ax.set_xticklabels(xs, fontsize=10, color="#333333")
    title = fig.layout.title.text if fig.layout.title else None
    if title:
        ax.set_title(title, fontsize=13, color="#0F172A")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08),
              ncol=len(traces), frameon=False)
    ax.yaxis.grid(True, color="#D9D9D9", linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    mfig.tight_layout(pad=0.6)

    buf = io.BytesIO()
    mfig.savefig(buf, format="png", facecolor="white")
    plt.close(mfig)
    return buf.getvalue()


def _build_pptx(sections):
    """1 deck: tiap moda 2 slide — (1) grafik PNG, (2) tabel pertumbuhan + narasi."""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    for sec in sections:
        moda, bln, thn = sec["moda"], sec["bln"], sec["thn"]

        s1 = prs.slides.add_slide(prs.slide_layouts[6])
        _slide_title(s1, f"Transportasi {moda} — {bln} {thn}: Grafik")
        x = Inches(0.4)
        for fig in sec["figs"]:
            img = _fig_to_png(fig, width=1100, height=400)
            s1.shapes.add_picture(io.BytesIO(img), x, Inches(1.1), width=Inches(6.15))
            x += Inches(6.15) + Inches(0.25)

        s2 = prs.slides.add_slide(prs.slide_layouts[6])
        _slide_title(s2, f"Transportasi {moda} — {bln} {thn}: Tabel & Narasi")
        _add_pptx_table(s2, sec["tables"][0], Inches(0.4), Inches(1.1), Inches(6.15), Inches(2.2))
        _add_pptx_table(s2, sec["tables"][1], Inches(6.8), Inches(1.1), Inches(6.15), Inches(2.2))
        para1, para2 = sec["narration"]
        _add_pptx_narration(s2, para1, para2, Inches(0.4), Inches(3.6), Inches(12.5), Inches(3.6))
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _download_pptx(sections):
    bln, thn = sections[0]["bln"], sections[0]["thn"]
    st.download_button(
        "⬇️ Unduh Deck PowerPoint (chart, tabel & narasi seluruh moda)",
        data=_build_pptx(sections),
        file_name=f"dashboard_transportasi_{thn}_{bln}.pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        key="dl_pptx_all",
    )


# ==============================================================================
# MAIN PAGE
# ==============================================================================
def show_dashboard_page():
    st.header("📊 Dashboard Statistik Perkembangan Transportasi")

    engine = get_engine()
    periods = get_available_periods(engine)

    if not periods:
        st.warning("⚠️ Belum ada data tersedia di database.")
        return

    period_labels = [f"{b} {t}" for t, b in periods]

    with st.expander("⚙️ Filter Periode", expanded=True):
        selected = st.selectbox(
        "Pilih Periode (Bulan & Tahun)",
        period_labels,
        index=len(period_labels) - 1,
        help="Periode data yang ditampilkan pada tabel dan grafik.",
    )

    sel_bln = selected.split(" ")[0]
    sel_thn = int(selected.split(" ")[1])
    prev_thn, prev_bln = get_prev_period(sel_thn, sel_bln)

    st.markdown("---")

    laut = show_section(
        engine, 'transportasi_laut', "Laut", "🚢", "#FDE68A",
        cols_penumpang=['dn_penumpang_naik', 'dn_penumpang_turun'],
        labels_penumpang=['PENUMPANG BERANGKAT', 'PENUMPANG DATANG'],
        colors_penumpang=['#B8860B', '#FFC72C'],
        cols_barang=['dn_muat_barang_ton', 'dn_bongkar_barang_ton'],
        labels_barang=['MUAT BARANG', 'BONGKAR BARANG'],
        colors_barang=['#E74C3C', '#E8720C'],
        thn=sel_thn, bln=sel_bln, prev_thn=prev_thn, prev_bln=prev_bln
    )

    st.markdown("---")

    udara = show_section(
        engine, 'transportasi_udara', "Udara", "✈️", "#FDE68A",
        cols_penumpang=['penumpang_berangkat', 'penumpang_datang'],
        labels_penumpang=['PENUMPANG BERANGKAT', 'PENUMPANG DATANG'],
        colors_penumpang=['#FFC72C', '#FFE699'],
        cols_barang=['barang_muat_kg', 'barang_bongkar_kg'],
        labels_barang=['MUAT BARANG', 'BONGKAR BARANG'],
        colors_barang=['#F4A460', '#E8720C'],
        thn=sel_thn, bln=sel_bln, prev_thn=prev_thn, prev_bln=prev_bln
    )

    st.markdown("---")

    sections = [s for s in (laut, udara) if s]
    if sections:
        _download_pptx(sections)

    with st.expander("📋 Lihat Data Detail Mentah", expanded=False):
        moda_detail = st.radio(
        "Moda",
        ["Transportasi Laut", "Transportasi Udara"],
        horizontal=True,
        key="detail_moda",
        help="Pilih moda yang ingin dilihat rinciannya.",
    )
        table_detail = "transportasi_laut" if moda_detail == "Transportasi Laut" else "transportasi_udara"
        df_detail = load_period_data(engine, table_detail, sel_thn, sel_bln)
        st.dataframe(df_detail, width='stretch')
