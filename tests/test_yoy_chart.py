"""Regression: the YoY line chart must label the compared months and stay readable.

Two defects this guards against:

1. ``plotly_theme`` pins ``margin.l = 10``, so a long y-axis title such as
   ``RLMTGAB (Length of Stay) (malam)`` and its tick labels were rendered into a
   10px gutter and clipped. The chart now re-sets the margin and turns on
   ``automargin`` so Plotly sizes the gutter to the real label width.
2. The chart carried no data labels, so the year-over-year comparison could only
   be read by hovering, and the hover text leaked the raw column name (``tpk=``)
   instead of the human label. It now prints a value label on the selected month
   and on the same month one year earlier.

``render_yoy_chart`` binds ``import streamlit as st`` at import time, so the page
module is imported against a stub that captures the figure.

Run directly:  python tests/test_yoy_chart.py
Or with pytest: pytest tests/test_yoy_chart.py
"""
import importlib
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

CAPTURED = []
PROV, YEAR = "Papua", 2026
BINTANG, NON_BINTANG = "Hotel Bintang", "Hotel Non Bintang"


class Stub:
    """Captures the figure handed to st.plotly_chart."""

    def plotly_chart(self, fig, **kwargs):
        CAPTURED.append((fig, kwargs))

    def __getattr__(self, name):
        return lambda *a, **k: None


sys.modules["streamlit"] = Stub()
pages = importlib.import_module("pariwisata.pages")


def _trend():
    """Prior year has 12 months, current year stops at August, as in the live data."""
    rows = []
    for jenis, base in ((BINTANG, 30.0), (NON_BINTANG, 20.0)):
        for month in range(1, 13):
            rows.append({"jenis_akomodasi": jenis, "year": YEAR - 1, "month": month,
                         "tpk": base + month / 10, "rlmtgab": 1.5})
        for month in range(1, 9):
            rows.append({"jenis_akomodasi": jenis, "year": YEAR, "month": month,
                         "tpk": base + 5 + month / 10, "rlmtgab": 1.8})
    return pd.DataFrame(rows)


def _render(month=None, jenis=BINTANG, year=YEAR):
    CAPTURED.clear()
    pages.render_yoy_chart(_trend(), "tpk", jenis, PROV, year, month)
    assert len(CAPTURED) == 1, "expected exactly one chart"
    return CAPTURED[0][0]


def _by_year(fig):
    return {tr.name: tr for tr in fig.data}


def _labelled_indexes(trace):
    return [i for i, t in enumerate(trace.text) if str(t).strip()]


def _month_index(fig, name):
    return list(fig.data[0].x).index(name)


def test_labels_mark_selected_month_in_both_years():
    """June is labelled on 2026 and on the same month of 2025, nowhere else."""
    fig = _render(month=6)
    juni = _month_index(fig, "Juni")
    for year in (str(YEAR), str(YEAR - 1)):
        tr = _by_year(fig)[year]
        assert _labelled_indexes(tr) == [juni], (year, list(tr.text))
        assert tr.mode == "lines+markers+text"


def test_label_text_is_the_value():
    fig = _render(month=6)
    juni = _month_index(fig, "Juni")
    tr = _by_year(fig)[str(YEAR)]
    assert str(tr.text[juni]).replace(",", "") == f"{tr.y[juni]:.2f}"


def test_month_none_falls_back_to_latest_available_month():
    """No month selected -> label the newest month the selected year actually has."""
    fig = _render(month=None)
    agustus = list(fig.data[0].x).index("Agustus")
    for year in (str(YEAR), str(YEAR - 1)):
        assert _labelled_indexes(_by_year(fig)[year]) == [agustus]


def test_hover_uses_human_label_not_column_name():
    fig = _render(month=6)
    for tr in fig.data:
        assert "TPK (Occupancy Rate)" in tr.hovertemplate, tr.hovertemplate
        assert "tpk=" not in tr.hovertemplate, tr.hovertemplate


def test_no_undefined_anywhere_in_the_figure():
    assert "undefined" not in _render(month=6).to_json().lower()


def test_axis_titles_are_not_clipped():
    fig = _render(month=6)
    for axis in ("xaxis", "yaxis"):
        assert fig.layout[axis].automargin is True, axis
        assert fig.layout[axis].tickfont.size == 13, axis
    assert fig.layout.yaxis.title.standoff >= 10


def _relative_luminance(hex_colour):
    """WCAG 2.x relative luminance."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    channels = []
    for c in (r, g, b):
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(foreground, background="#FFFFFF"):
    """WCAG 2.x contrast ratio, 1.0 to 21.0."""
    a, b = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def test_axis_text_meets_wcag_aa():
    """Titles and tick labels must clear 4.5:1 on the white plot background.

    Regression: `axis.color` also paints the axis *title*, so setting it to
    INK_DIM pushed the title to 4.8:1 at 13px and it read as grey on grey. Each
    now carries its own font, and this asserts the measured ratios.
    """
    fig = _render(month=6)
    for axis in ("xaxis", "yaxis"):
        spec = fig.layout[axis]
        assert _contrast(spec.tickfont.color) >= 4.5, (axis, spec.tickfont.color)
        assert _contrast(spec.title.font.color) >= 4.5, (axis, spec.title.font.color)
    # and the title must be visually distinct from the ticks, not just legal
    assert fig.layout.yaxis.title.font.color != fig.layout.yaxis.tickfont.color
    assert fig.layout.yaxis.title.font.size >= 14


CUR_LINE = "#ED7D31"
PREV_LINE = "#70AD47"


def _trace_for(fig, year):
    return next(tr for tr in fig.data if tr.name == str(year))


def test_line_colors_are_the_requested_ones():
    """Current year orange #ED7D31, previous year green #70AD47."""
    fig = _render(month=6)
    assert _trace_for(fig, 2026).line.color == CUR_LINE
    assert _trace_for(fig, 2025).line.color == PREV_LINE
    assert _trace_for(fig, 2026).line.color != _trace_for(fig, 2025).line.color


def test_markers_contrast_against_their_own_line():
    """White fill inside a ring in the line colour.

    The ring guarantees separation from the stroke it sits on; the white centre
    keeps the point readable where two series cross.
    """
    fig = _render(month=6)
    for tr, line in ((_trace_for(fig, 2026), CUR_LINE), (_trace_for(fig, 2025), PREV_LINE)):
        assert tr.marker.color == "#FFFFFF", tr.name
        assert tr.marker.line.color == line, tr.name
        # the ring must be thick enough to actually read as a ring
        assert tr.marker.line.width >= 2, tr.name
        assert tr.marker.size >= 8, tr.name


def test_marker_ring_matches_its_series():
    """No copy-paste slip where both traces get the same marker colour."""
    fig = _render(month=6)
    cur, prev = _trace_for(fig, 2026), _trace_for(fig, 2025)
    assert cur.marker.line.color != prev.marker.line.color
    assert cur.line.color == cur.marker.line.color
    assert prev.line.color == prev.marker.line.color


def test_data_labels_meet_wcag_aa():
    """The two value labels sit on white too, so they get the same check."""
    fig = _render(month=6)
    for tr in fig.data:
        assert _contrast(tr.textfont.color) >= 4.5, (tr.name, tr.textfont.color)


def test_legend_text_meets_wcag_aa():
    fig = _render(month=6)
    assert _contrast(fig.layout.legend.font.color) >= 4.5
    assert _contrast(fig.layout.legend.title.font.color) >= 4.5


def test_only_the_requested_accommodation_type_is_plotted():
    """Both types arrive in one frame; the chart must not merge them into one series."""
    fig = _render(month=6)
    assert len(fig.data[0].x) == 12
    non_bintang = _render(month=6, jenis=NON_BINTANG)
    tpk_bintang = _by_year(fig)[str(YEAR)].y[0]
    tpk_non = _by_year(non_bintang)[str(YEAR)].y[0]
    assert abs(tpk_bintang - tpk_non) > 1


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if failures else 0)