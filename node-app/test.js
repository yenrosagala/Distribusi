import test from 'node:test';
import assert from 'node:assert/strict';
import {
  aggByProvinsi, computeStats, fallbackNarrative, formatIdNumber, growthRows,
  pctChange, prevPeriod, reportTable, sortPeriods, yoySeries,
} from './lib.js';
import {
  fallbackTransportNarrative, normalisasiEntitas, normalizeNarrative, transportReport,
} from './lib-transport.js';
import { cleanNumber, computeUploadStats, parseTransportFile } from './etl-transport.js';
import { exportFileName } from './export.js';

test('prevPeriod rolls back over the year boundary', () => {
  assert.deepEqual(prevPeriod(2026, 'September'), { thn: 2026, bln: 'Agustus' });
  assert.deepEqual(prevPeriod(2026, 'Januari'), { thn: 2025, bln: 'Desember' });
});

test('pctChange guards zero and missing baselines', () => {
  assert.equal(pctChange(120, 100), 20);
  assert.equal(pctChange(80, 100), -20);
  assert.equal(pctChange(5, 0), null);
  assert.equal(pctChange(null, 10), null);
});

test('aggByProvinsi sums matching provinces and ignores the rest', () => {
  const rows = [
    { nama_provinsi: 'PAPUA SELATAN', a: '10', b: 1 },
    { nama_provinsi: 'Papua Selatan', a: 5, b: 2 },
    { nama_provinsi: 'Jawa Barat', a: 999, b: 999 },
  ];
  const agg = aggByProvinsi(rows, ['a', 'b']);
  assert.deepEqual(agg['Papua Selatan'], { a: 15, b: 3 });
  assert.equal(agg['Jawa Barat'], undefined);
});

test('computeStats produces month-to-month and year-to-date deltas', () => {
  const agg = (v) => aggByProvinsi([{ nama_provinsi: 'Papua', v }], ['v']);
  const stats = computeStats(agg(150), agg(100), agg(300), agg(240), ['v']);
  assert.equal(stats.curr, 150);
  assert.equal(stats.mtm, 50);
  assert.equal(stats.yoy, 25);
});

test('growthRows keeps PROVINSI_ORDER and nulls missing baselines', () => {
  const rows = growthRows(
    aggByProvinsi([{ nama_provinsi: 'Papua', v: 110 }], ['v']),
    aggByProvinsi([], ['v']),
    ['v'],
    ['VOLUME'],
  );
  assert.equal(rows.length, 4);
  assert.equal(rows[0].provinsi, 'Papua');
  assert.equal(rows[0].values[0], null);
});

test('formatIdNumber uses Indonesian separators', () => {
  assert.equal(formatIdNumber(1234567.891, 2), '1.234.567,89');
  assert.equal(formatIdNumber(0, 0), '0');
  assert.equal(formatIdNumber(null), 'Undefined');
});

test('fallbackNarrative names the direction of the change', () => {
  const stats = { curr: 1200, prev: 1000, mtm: 20, cumCurr: 5000, cumPrev: 5000, yoy: 0 };
  const [first] = fallbackNarrative('Laut', 'September', 2026, 'Agustus', 2026, stats, stats, 'ton');
  assert.match(first, /mengalami lonjakan/);
  assert.match(first, /1\.200 orang/);
});

test('sortPeriods dedupes and orders by year then month', () => {
  const rows = [
    { tahun: '2026', bulan: 'Januari' },
    { tahun: 2025, bulan: 'Desember' },
    { tahun: 2026, bulan: 'Januari' },
  ];
  assert.deepEqual(sortPeriods(rows), [
    { thn: 2025, bln: 'Desember' },
    { thn: 2026, bln: 'Januari' },
  ]);
});

test('reportTable keeps every kelas, stars high RSE and lets Prov_Jenis win the Total', () => {
  const rows = [
    { jenis_akomodasi: 'Hotel Bintang', kelas_akomodasi: 1, year: 2026, month: 8, tpk: 40, tpk_rse: 0.1 },
    { jenis_akomodasi: 'Hotel Bintang', kelas_akomodasi: 2, year: 2026, month: 8, tpk: 30, tpk_rse: 0.4 },
    { jenis_akomodasi: 'Hotel Bintang', kelas_akomodasi: 1, year: 2026, month: 7, tpk: 36 },
    { jenis_akomodasi: 'Hotel Bintang', kelas_akomodasi: 1, year: 2025, month: 8, tpk: 32 },
  ];
  const ref = new Map([['Hotel Bintang|2026|8', { tpk: 41.5 }]]);
  const table = reportTable(rows, {
    jenis: 'Hotel Bintang', indicator: 'tpk', year: 2026, month: 8,
    prevYear: 2026, prevMonth: 7, lastYear: 2025, domain: [1, 2, 3], ref,
  });
  assert.deepEqual(table.body.map((r) => r.label), ['Bintang 1', 'Bintang 2', 'Bintang 3']);
  assert.equal(table.body[1].current, '30.00*');
  assert.equal(table.body[0].dPrev, '+4.00 pts');
  assert.equal(table.body[2].prev, '-');
  assert.equal(table.total.current, '41.50');
  assert.equal(table.total.dPrev, '+5.50 pts');
});

test('yoySeries stops the current year at the selected month and prefers ref values', () => {
  const rows = [
    { jenis_akomodasi: 'Hotel Bintang', year: 2026, month: 7, tpk: 20 },
    { jenis_akomodasi: 'Hotel Bintang', year: 2026, month: 8, tpk: 30 },
    { jenis_akomodasi: 'Hotel Bintang', year: 2025, month: 12, tpk: 10 },
  ];
  const series = yoySeries(rows, {
    jenis: 'Hotel Bintang', indicator: 'tpk', year: 2026, month: 8,
    ref: new Map([['Hotel Bintang|2026|8', { tpk: 33 }]]),
  });
  assert.equal(series.prevYear, 2025);
  assert.equal(series.cur[7], 33);
  assert.equal(series.cur[8], null);
  assert.equal(series.prev[11], 10);
  assert.equal(series.labels[7], 'Agu');
});

test('normalisasiEntitas strips modal prefixes and maps Nabire to Douw Aturure', () => {
  assert.equal(normalisasiEntitas('Bandara Sentani', 'Transportasi Udara'), 'Sentani');
  assert.equal(normalisasiEntitas('Bandara Nabire', 'Transportasi Udara'), 'Douw Aturure');
  assert.equal(normalisasiEntitas('Pelabuhan Jayapura', 'Transportasi Laut'), 'Jayapura');
});

test('transportReport lays out the BRS hierarchy, converts kg to ton and recomputes pct', () => {
  const mk = (tahun, bulan, bandara, kg) => ({
    tahun, bulan, nama_bandara: bandara, barang_muat_kg: kg, nama_provinsi: 'PAPUA',
  });
  const rows = [
    mk(2026, 'Agustus', 'Bandara Sentani', 1000),
    mk(2026, 'Agustus', 'Bandara Frans Kaisiepo', 500),
    mk(2026, 'Juli', 'Bandara Sentani', 500),
    mk(2025, 'Agustus', 'Bandara Sentani', 400),
    mk(2026, 'Januari', 'Bandara Sentani', 1000),
  ];
  const table = transportReport(rows, {
    col: 'barang_muat_kg', label: 'Barang Muat (Ton)', rowCol: 'nama_bandara',
    moda: 'Transportasi Udara', prov: 'Papua',
    year: 2026, monthName: 'Agustus', prevYear: 2026, prevMonthName: 'Juli',
  });
  assert.equal(table.columns.curr, 'Agustus 2026');
  assert.equal(table.columns.cumPrev, 'Jan-Agustus 2025');
  const labels = table.rows.map((r) => r.label);
  // separator row is emitted whenever the province has a "lainnya" block, even
  // when no other bandara reports in the period (matches build_brs_display_table)
  assert.deepEqual(labels, [
    'Sentani', 'Frans Kaisiepo', 'Subtotal', 'Bandara Lainnya', 'Total',
  ]);
  const sentani = table.rows[0];
  assert.equal(sentani.curr, '1,00');
  assert.equal(sentani.prev, '0,50');
  assert.equal(sentani.mtm, '100,00');
  assert.equal(sentani.cumPrev, '0,40');
  // Jan + Jul + Aug 2026 (all inside the cumulative window) = 2,5 ton
  assert.equal(sentani.cumCurr, '2,50');
  assert.equal(sentani.yoy, '525,00');
  const total = table.rows.at(-1);
  assert.equal(total.prev, '0,50');
  assert.equal(total.curr, '1,50');
  assert.equal(total.mtm, '200,00');
  assert.equal(table.rows[2].kind, 'subtotal');
  assert.equal(table.rows[3].kind, 'other');
  assert.equal(table.rows[3].mtm, 'undefined');
  assert.equal(table.rows[3].curr, '');
});

test('fallbackTransportNarrative is deterministic and normalizes Indonesian numbers', () => {
  const meta = { subject: 'Jumlah penumpang yang datang', satuan: 'orang', penumpang: true };
  const total = { prev: 1000, curr: 1250, cumPrev: 8000, cumCurr: 9000, mtm: 25, yoy: 12.5 };
  const one = fallbackTransportNarrative({
    total, meta, moda: 'Transportasi Udara', prov: 'Papua', bln: 'Agustus', thn: 2026,
    prevBln: 'Juli', prevThn: 2026,
  });
  const two = fallbackTransportNarrative({
    total, meta, moda: 'Transportasi Udara', prov: 'Papua', bln: 'Agustus', thn: 2026,
    prevBln: 'Juli', prevThn: 2026,
  });
  assert.deepEqual(one, two);
  assert.equal(one.length, 2);
  assert.match(one[0], /1\.250 orang/);
  assert.match(one.join(' '), /lonjakan/);
  assert.equal(normalizeNarrative('naik 10,5% dari 7.778,45 ton', 'dn_muat_barang_ton'), 'naik 10,50 persen dari 7.778,45 ton');
});

const BPS_UDARA_HTML = `<html><body><table>
<tr><th colspan="11">PROVINSI PAPUA Tahun 2026 Bulan Agustus</th></tr>
<tr><th>Provinsi</th><th>Kabupaten</th><th>Bandara</th><th>Pesawat Terbang</th></tr>
<tr><td>[94] PAPUA</td><td>[9403] KABUPATEN JAYAPURA</td><td>[940301] SENTANI</td><td>-</td>
  <td>1.000</td><td>1.100</td><td>57.941</td><td>54.760</td><td>0</td><td>1,5</td><td>656,549</td></tr>
<tr><td>JUMLAH</td><td></td><td></td><td></td>
  <td>1.000</td><td>1.100</td><td>57.941</td><td>54.760</td><td>0</td><td>1,5</td><td>656,549</td></tr>
</table></body></html>`;

test('cleanNumber mirrors the Python dot-thousands / comma-decimal rule', () => {
  assert.equal(cleanNumber('10.000'), 10000);
  assert.equal(cleanNumber('2,5'), 2.5);
  assert.equal(cleanNumber('7.778,45'), 7778.45);
  assert.equal(cleanNumber('-'), 0);
  assert.equal(cleanNumber(''), 0);
});

test('parseTransportFile reads a BPS HTML table and attributes the province', () => {
  const { tableType, rows } = parseTransportFile(Buffer.from(BPS_UDARA_HTML, 'utf8'), 2026, 'Agustus');
  assert.equal(tableType, 'transportasi_udara');
  assert.equal(rows.length, 1, 'JUMLAH row is skipped');
  const [row] = rows;
  assert.equal(row.nama_bandara, 'SENTANI');
  assert.equal(row.nama_provinsi, 'PAPUA');
  assert.equal(row.kode_provinsi, 94);
  assert.equal(row.penumpang_datang, 54760);
  assert.equal(row.barang_bongkar_kg, 656.549);

  const stats = computeUploadStats(tableType, rows);
  assert.equal(stats.jumlah_baris, 1);
  assert.equal(stats.total_penumpang, 54760 + 57941);
  assert.equal(stats.satuan_barang, 'kg');
});

test('parseTransportFile rejects files whose kabupaten is unmapped', () => {
  const html = BPS_UDARA_HTML.replace('KABUPATEN JAYAPURA', 'KABUPATEN ANTARTIKA');
  assert.throws(
    () => parseTransportFile(Buffer.from(html, 'utf8'), 2026, 'Agustus'),
    /PEMETAAN_WILAYAH/,
  );
});

test('exportFileName keeps province and period in every download name', () => {
  const payload = { prov: 'Papua Selatan', thn: 2026, bln: 'September' };
  assert.equal(exportFileName('pptx', payload), 'dashboard_transportasi_September_2026.pptx');
  assert.equal(exportFileName('docx', payload), 'Master_Laporan_Transportasi_Papua_Selatan_September_2026.docx');
  assert.equal(exportFileName('idml-transport', payload), 'BRS_Transportasi_Papua_Selatan_September_2026.zip');
  assert.equal(exportFileName('idml-pariwisata', payload), 'BRS_Pariwisata_Papua_Selatan_September_2026.zip');
});

