"""Regression: the Infographic map loads its geometry without geopandas.

``load_geodata()`` was born as ``gpd.read_parquet(...)``; it now reads a plain
GeoJSON and derives label centroids from vertex averages. The PROVINSI property
is asserted so a re-exported ``.json`` that renames the key fails loudly here
instead of KeyError-ing the whole tab at runtime.

Run directly:  python tests/test_infographic_map.py
Or with pytest: pytest tests/test_infographic_map.py
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("STREAMLIT_SUPPRESS_WARNINGS", "1")


class Stub:
    """Minimal Streamlit surface: cache decorators are pass-through."""

    def cache_data(self, *a, **k):
        if len(a) == 1 and not k and callable(a[0]):
            return a[0]
        return lambda fn: fn

    cache_resource = cache_data

    def __getattr__(self, name):
        return lambda *a, **k: None


sys.modules["streamlit"] = Stub()
pages = importlib.import_module("pariwisata.pages")
importlib.reload(pages)  # a prior test may have imported it against a different stub


def test_geodata_covers_all_target_provinces():
    geo = pages.load_geodata()
    assert set(geo) == set(pages.TARGET_PROVINCES)
    for name, g in geo.items():
        assert -10 < g["lat"] < 0, name
        assert 130 < g["lon"] < 145, name
        assert g["geometry"]["type"] in ("Polygon", "MultiPolygon")


if __name__ == "__main__":
    test_geodata_covers_all_target_provinces()
    print("OK: geodata covers all target provinces")