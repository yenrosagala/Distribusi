"""Regression: report TOTAL rows must reflect the true per-period sum.

Bug: prepare_table_item built the report frame indexed by the CURRENT period
only, so any entity with data last period but zero this period was dropped
before the totals were computed. The previous-period TOTAL (and the M-to-M /
Y-on-Y derived from it) silently undercounted, corrupting the AI narrative
built from those numbers.

Run directly:  python tests/test_report_totals.py
Or with pytest: pytest tests/test_report_totals.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

import pandas as pd  # noqa: E402
from modules.config import PEMETAAN_WILAYAH  # noqa: E402
from modules.report_page import prepare_table_item  # noqa: E402

PROV = "Papua Tengah"
KAB = PEMETAAN_WILAYAH[PROV][:2]


def _item(curr, prev, cum_curr, cum_prev, moda="Transportasi Laut", target="Nilai"):
    f = lambda rows: pd.DataFrame({"Kabupaten": rows[0], target: rows[1]})  # noqa: E731
    return prepare_table_item(
        f(curr), f(prev), f(cum_curr), f(cum_prev),
        target, "Penumpang",
        "Kabupaten", "2026", "Juni", "Mei", "2026",
        table_no=1, prov=PROV, moda=moda,
    )


def _total(item):
    flat = item["report_flat"]
    return flat[flat.index == "TOTAL"].iloc[0]


def test_prev_only_entity_counted_in_total():
    """Entity with data last month but none this month must still count."""
    it = _item(
        curr=([KAB[0]], [5]),                 # this month: only A
        prev=([KAB[0], KAB[1]], [10, 99]),    # last month: A + GONE
        cum_curr=([KAB[0]], [5]),
        cum_prev=([KAB[0], KAB[1]], [10, 99]),
    )
    tot = _total(it)
    assert tot["Mei 2026"] == 109, f"prev total {tot['Mei 2026']} != 109"
    assert tot["Juni 2026"] == 5
    expected_mtm = (5 - 109) / 109 * 100
    assert abs(tot["M-to-M (%)"] - expected_mtm) < 1e-6


def test_normal_totals_unchanged():
    it = _item(
        curr=(KAB, [10, 5]), prev=(KAB, [8, 4]),
        cum_curr=(KAB, [15, 9]), cum_prev=(KAB, [12, 7]),
    )
    tot = _total(it)
    assert tot["Mei 2026"] == 12 and tot["Juni 2026"] == 15
    assert abs(tot["M-to-M (%)"] - 25.0) < 1e-9
    assert abs(tot["Jan-Juni 2025"] - 19) < 1e-9
    assert abs(tot["Jan-Juni 2026"] - 24) < 1e-9
    assert abs(tot["Y-on-Y (%)"] - (24 - 19) / 19 * 100) < 1e-9


def test_udara_kg_converted_to_ton_in_totals():
    it = _item(
        curr=(KAB, [10000, 5000]), prev=(KAB, [8000, 4000]),
        cum_curr=(KAB, [20000, 10000]), cum_prev=(KAB, [18000, 9000]),
        moda="Transportasi Udara", target="kg",
    )
    tot = _total(it)
    assert tot["Mei 2026"] == 12 and tot["Juni 2026"] == 15  # kg -> ton


if __name__ == "__main__":
    test_prev_only_entity_counted_in_total()
    test_normal_totals_unchanged()
    test_udara_kg_converted_to_ton_in_totals()
    print("test_report_totals: all pass")
