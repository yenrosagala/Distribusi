"""Regression: location matching, year options, and narrative determinism.

Three bugs guarded here:

1. get_location_metadata partial-matched by iterating the dict in insertion
   order, so a key that is a substring of another key could win depending on
   where it sat in the literal. Now iterates longest-key-first, which makes the
   result independent of dict order.
2. The report page's year dropdown was hardcoded to ['2024','2025','2026'], so it
   silently offered years with no data and hid years that had data. Now derived
   from the database.
3. _arah_dinamis used random.choice, so the same percentage could be narrated as
   "mengalami lonjakan" on one run and "meningkat" on the next. Direction of a
   number is a fact, so it is now magnitude-based and deterministic.

Run directly:  python tests/test_location_years_narrative.py
Or with pytest: pytest tests/test_location_years_narrative.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")

from modules.config import (  # noqa: E402
    MAPPING_LOKASI_KAB_PROV,
    get_location_metadata,
    get_province_by_kabupaten,
)
from modules.report_page import _arah_dinamis, _available_years  # noqa: E402


def test_partial_match_prefers_longest_key():
    for key in MAPPING_LOKASI_KAB_PROV:
        assert get_location_metadata(key) == MAPPING_LOKASI_KAB_PROV[key], key

    pairs = [
        (a, b)
        for a in MAPPING_LOKASI_KAB_PROV
        for b in MAPPING_LOKASI_KAB_PROV
        if a != b and a in b
    ]
    for _short, long in pairs:
        assert get_location_metadata(long) == MAPPING_LOKASI_KAB_PROV[long], long


def test_unknown_kabupaten_is_not_silently_papua():
    assert get_province_by_kabupaten("KABUPATEN TIDAK ADA") is None
    assert get_province_by_kabupaten("KOTA JAYAPURA") == "PAPUA"


def test_year_options_come_from_data():
    years = _available_years()
    assert years, "must not be empty"
    assert years == sorted(years, key=int), "must be ascending"
    assert all(y.isdigit() and len(y) == 4 for y in years), years


def test_direction_wording_is_deterministic_and_scaled():
    cases = {
        12: "mengalami lonjakan",
        10: "mengalami lonjakan",
        0.3: "meningkat",
        0: "stabil",
        -0.4: "menurun",
        -10: "mengalami penurunan tajam",
        -15: "mengalami penurunan tajam",
    }
    for pct, want in cases.items():
        assert _arah_dinamis(pct) == want, pct

    assert _arah_dinamis(float("nan")) == "tercatat"
    assert len({_arah_dinamis(5.0) for _ in range(50)}) == 1, "must be stable"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("test_location_years_narrative: all pass")
