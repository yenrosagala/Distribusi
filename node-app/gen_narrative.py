#!/usr/bin/env python3
import json
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlalchemy import text
from modules.database import get_engine
from modules.ai_backup import generate_text, get_gemini_client, get_openrouter_key
from modules.report_page import normalize_narrative
import pandas as pd


def main():
    try:
        payload = json.loads(sys.stdin.read())
        kind = payload.get("kind")  # pariwisata|transportasi
        prov = payload.get("provinsi") or payload.get("province")
        tahun = int(payload.get("tahun") or payload.get("year"))
        bulan = int(payload.get("bulan") or payload.get("month"))
        jenis = payload.get("jenis_akomodasi") or payload.get("jenis")
        indikator = payload.get("indikator") or payload.get("indicator") or payload.get("label")
        moda = payload.get("moda")
        table_no = payload.get("table_no")
        action = payload.get("action") or "generate"  # generate|regenerate

        eng = get_engine()
        client = get_gemini_client()

        if kind == "pariwisata":
            if not all([prov, tahun, bulan, jenis, indikator]):
                print(json.dumps({"error": "missing params"}))
                sys.exit(1)
            params = {"province": str(prov).strip(), "year": tahun, "month": bulan, "jenis_akomodasi": jenis, "indicator": indikator}
            # build minimal table text like Streamlit
            q = text("SELECT * FROM pariwisata_general_data WHERE TRIM(CAST(kd_prov AS TEXT))=:p AND year IN (:y,:py,:ly) AND month IN (:m,:pm)")
            prev_month = bulan - 1 if bulan > 1 else 12
            prev_year = tahun if bulan > 1 else tahun - 1
            last_year = tahun - 1
            with eng.connect() as c:
                df = pd.read_sql_query(q, c, params={"p": str(prov).strip(), "y": tahun, "py": prev_year, "ly": last_year, "m": bulan, "pm": prev_month})
            df_cur = df[(df["year"] == tahun) & (df["month"] == bulan) & (df["jenis_akomodasi"] == jenis)]
            if df_cur.empty:
                print(json.dumps({"error": "no data"}))
                sys.exit(1)
            # ponytail: simple table string is enough; full formatting not needed for prompt
            table_str = df_cur[["kelas_akomodasi", indikator]].to_string(index=False)
            prompt = f"Table summary for {str(indikator).upper()} ({jenis}) in {prov}:\n{table_str}\n\nSusun narasi ringkas, padat, objektif untuk publikasi BPS sesuai periode {bulan}/{tahun}."
            txt, src = generate_text(prompt, temperature=0.3, client=client)
            if not txt:
                print(json.dumps({"error": "ai failed"}))
                sys.exit(1)
            txt = normalize_narrative(txt)
            if action == "regenerate":
                with eng.begin() as c:
                    c.execute(text("DELETE FROM pariwisata_ai_narratives WHERE province=:province AND year=:year AND month=:month AND jenis_akomodasi=:jenis_akomodasi AND indicator=:indicator"), params)
            with eng.begin() as c:
                c.execute(text("INSERT INTO pariwisata_ai_narratives (province,year,month,jenis_akomodasi,indicator,narrative) VALUES (:province,:year,:month,:jenis_akomodasi,:indicator,:narrative)"), {**params, "narrative": txt})
            print(json.dumps({"ok": True, "text": txt}))
            return

        if kind == "transportasi":
            label_t = payload.get("label") or indikator or payload.get("indikator")
            if not all([prov, tahun, bulan, moda, label_t]):
                print(json.dumps({"error": "missing params"}))
                sys.exit(1)
            report_type = f"report_{moda}_{label_t}"
            period_key = f"{prov}|{bulan}|{tahun}"
            prev_month = bulan - 1 if bulan > 1 else 12
            prev_year = tahun if bulan > 1 else tahun - 1
            params_r = {"report_type": report_type, "period_key": period_key}
            try:
                from modules.report_page import get_comparison_data, prepare_table_item, collect_report_tables
            except Exception as e:
                print(json.dumps({"error": f"import report_page failed: {e}"}))
                sys.exit(1)
            # try to build real item for this label
            items = collect_report_tables(str(prov), int(tahun), bulan)
            item = None
            for it in items:
                if it.get("moda") == str(moda) and str(it.get("label")) == str(label_t):
                    item = it
                    break
            if item is None and moda and label_t:
                # ponytail: fallback minimal
                cu, pr, cc, cp, p_bln, p_thn = get_comparison_data(str(prov), int(tahun), bulan, str(moda))
                if cu.empty:
                    print(json.dumps({"error": "no data"}))
                    sys.exit(1)
                if str(moda) == "Transportasi Udara":
                    row_col = "nama_bandara"
                else:
                    row_col = "nama_kabkota" if str(prov) == "Papua Tengah" else "nama_pelabuhan"
                targets = {
                    "Penumpang Datang": ("penumpang_datang",),
                    "Penumpang Berangkat": ("penumpang_berangkat",),
                    "Barang Bongkar (Ton)": ("barang_bongkar_kg",),
                    "Barang Muat (Ton)": ("barang_muat_kg",),
                    "Penumpang Turun": ("dn_penumpang_turun",),
                    "Penumpang Naik": ("dn_penumpang_naik",),
                    "Barang Bongkar (Ton)": ("dn_bongkar_barang_ton",),
                    "Barang Muat (Ton)": ("dn_muat_barang_ton",),
                }
                # find col
                m = {
                    "Penumpang Datang": "penumpang_datang",
                    "Penumpang Berangkat": "penumpang_berangkat",
                    "Barang Bongkar (Ton)": "barang_bongkar_kg",
                    "Barang Muat (Ton)": "barang_muat_kg",
                    "Penumpang Turun": "dn_penumpang_turun",
                    "Penumpang Naik": "dn_penumpang_naik",
                }
                # laut ton cols may differ
                if str(moda) != "Transportasi Udara":
                    if label_t == "Barang Bongkar (Ton)": m_c = "dn_bongkar_barang_ton"
                    elif label_t == "Barang Muat (Ton)": m_c = "dn_muat_barang_ton"
                    else: m_c = m.get(label_t)
                else:
                    if label_t == "Barang Bongkar (Ton)": m_c = "barang_bongkar_kg"
                    elif label_t == "Barang Muat (Ton)": m_c = "barang_muat_kg"
                    else: m_c = m.get(label_t)
                try:
                    item = prepare_table_item(cu, pr, cc, cp, m_c, label_t, row_col, int(tahun), bulan, p_bln, p_thn, prov=str(prov), moda=str(moda))
                except Exception as e:
                    print(json.dumps({"error": f"prepare failed: {e}"}))
                    sys.exit(1)
            try:
                from modules.report_page import generate_single_narrative_ai
                txt, src = generate_single_narrative_ai(item['report_display_brs'].reset_index(), item['label'], str(prov), str(moda), item['bln'], item['thn'], item['prev_bln'], item['prev_thn'])
            except Exception as e:
                print(json.dumps({"error": f"gen failed: {e}"}))
                sys.exit(1)
            if not txt:
                print(json.dumps({"error": "ai failed"}))
                sys.exit(1)
            txt = normalize_narrative(txt)
            if action == "regenerate":
                try:
                    from modules.database import delete_db_narrative
                    delete_db_narrative(report_type, period_key)
                except Exception:
                    with eng.begin() as c:
                        c.execute(text("DELETE FROM ai_narratives_cache WHERE report_type=:report_type AND period_key=:period_key"), params_r)
            try:
                from modules.database import save_db_narrative
                save_db_narrative(report_type, period_key, txt)
            except Exception:
                with eng.begin() as c:
                    c.execute(text("INSERT INTO ai_narratives_cache (report_type,period_key,narrative_text,updated_at) VALUES (:report_type,:period_key,:narrative,now()) ON CONFLICT DO NOTHING"), {**params_r, "narrative": txt})
            print(json.dumps({"ok": True, "text": txt}))
            return

        print(json.dumps({"error": "bad kind"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
