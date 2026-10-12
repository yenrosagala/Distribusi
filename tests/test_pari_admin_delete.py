"""Runtable check for the pariwisata admin delete filter builder.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def test_pari_delete_filter_all():
    from pariwisata import pages as pari_pages
    where, params = pari_pages._pari_delete_filter('SEMUA', 'SEMUA', 'SEMUA')
    assert where == '1=1'
    assert params == {}

def test_pari_delete_filter_prov():
    from pariwisata import pages as pari_pages
    where, params = pari_pages._pari_delete_filter('Papua', 'SEMUA', 'SEMUA')
    assert where.strip() == 'kd_prov = :prov'
    assert params == {'prov': 'Papua'}

def test_pari_delete_filter_year_month():
    from pariwisata import pages as pari_pages
    where, params = pari_pages._pari_delete_filter('SEMUA', '2026', 5)
    assert 'CAST(year AS TEXT) = :year' in where
    assert 'month = :month' in where
    assert params == {'year': '2026', 'month': 5}

def test_pari_delete_filter_full():
    from pariwisata import pages as pari_pages
    where, params = pari_pages._pari_delete_filter('Papua', '2026', 6)
    assert 'kd_prov = :prov' in where
    assert 'CAST(year AS TEXT) = :year' in where
    assert 'month = :month' in where
    assert params == {'prov': 'Papua', 'year': '2026', 'month': 6}

if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
            print(f'  ok  {name}')
    print('test_pari_admin_delete: all pass')
