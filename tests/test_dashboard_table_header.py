"""Regresi: nama baris & kolom tabel dashboard harus hitam di header terang."""
import pandas as pd

from modules.dashboard_page import style_growth_table


def test_header_text_is_black_on_light_background():
    df = pd.DataFrame({"PENUMPANG BERANGKAT": [1.0]}, index=["PAPUA"])
    styler = style_growth_table(df, "#FDE68A")

    th = [s for s in styler.table_styles if s["selector"] == "th"]
    assert th, "aturan header 'th' hilang"
    props = dict(th[0]["props"])

    assert props["color"] == "#0F172A", f"nama kolom harus hitam, dapat {props['color']}"


def test_header_background_is_light_enough_for_black_text():
    """Semua warna header yang dipakai harus >= 4.5:1 dengan teks hitam."""

    def lum(h):
        h = h.lstrip("#")
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]

    ink = lum("#0F172A")
    df = pd.DataFrame({"X": [1.0]}, index=["Y"])
    for bg in ("#FDE68A", "#FECACA"):
        lb = lum(bg)
        rasio = (max(ink, lb) + .05) / (min(ink, lb) + .05)
        assert rasio >= 4.5, f"{bg} hanya {rasio:.2f}:1 dengan teks hitam"
        assert style_growth_table(df, bg) is not None