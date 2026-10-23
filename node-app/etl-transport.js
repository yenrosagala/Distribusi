import * as xlsx from 'xlsx';
import * as cheerio from 'cheerio';
import { pool } from './db.js';

export const MONTH_MAP = {
  Januari: 1, Februari: 2, Maret: 3, April: 4, Mei: 5, Juni: 6,
  Juli: 7, Agustus: 8, September: 9, Oktober: 10, November: 11, Desember: 12,
};
export const MONTH_LIST = Object.keys(MONTH_MAP);

export const PEMETAAN_WILAYAH = {
  PAPUA: ['KABUPATEN BIAK NUMFOR', 'KABUPATEN JAYAPURA', 'KABUPATEN KEEROM', 'KABUPATEN KEPULAUAN YAPEN', 'KABUPATEN MAMBERAMO RAYA', 'KABUPATEN SARMI', 'KABUPATEN SUPIORI', 'KABUPATEN WAROPEN', 'KOTA JAYAPURA'],
  'PAPUA BARAT': ['KABUPATEN FAKFAK', 'KABUPATEN KAIMANA', 'KABUPATEN MANOKWARI', 'KABUPATEN MANOKWARI SELATAN', 'KABUPATEN PEGUNUNGAN ARFAK', 'KABUPATEN SORONG', 'KABUPATEN SORONG SELATAN', 'KABUPATEN TAMBRAUW', 'KABUPATEN TELUK BINTUNI', 'KABUPATEN TELUK WONDAMA', 'KOTA SORONG'],
  'PAPUA SELATAN': ['KABUPATEN ASMAT', 'KABUPATEN BOVEN DIGOEL', 'KABUPATEN MAPPI', 'KABUPATEN MERAUKE'],
  'PAPUA TENGAH': ['KABUPATEN DEIYAI', 'KABUPATEN DOGIYAI', 'KABUPATEN INTAN JAYA', 'KABUPATEN JAYAWIJAYA', 'KABUPATEN LANNY JAYA', 'KABUPATEN MIMIKA', 'KABUPATEN NABIRE', 'KABUPATEN PANIAI', 'KABUPATEN PUNCAK', 'KABUPATEN PUNCAK JAYA', 'KABUPATEN TOLIKARA', 'KABUPATEN YALIMO'],
  'PAPUA PEGUNUNGAN': ['KABUPATEN JAYAWIJAYA', 'KABUPATEN LANNY JAYA', 'KABUPATEN MAMBERAMO TENGAH', 'KABUPATEN NDUGA', 'KABUPATEN PEGUNUNGAN BINTANG', 'KABUPATEN TOLIKARA', 'KABUPATEN YAHUKIMO', 'KABUPATEN YALIMO'],
};

export function getProvinceByKabupaten(kabName) {
  if (!kabName) return null;
  const raw = String(kabName).toUpperCase().trim();
  const kabClean = raw.replace('KABUPATEN ', '').replace('KOTA ', '').trim();
  if (kabClean === 'JAYAPURA' && raw.includes('KOTA')) return 'PAPUA';
  for (const [prov, kabs] of Object.entries(PEMETAAN_WILAYAH)) {
    for (const k of kabs) {
      const kClean = k.toUpperCase().replace('KABUPATEN ', '').replace('KOTA ', '').trim();
      if (kabClean === kClean) return prov;
    }
  }
  return null;
}

function extractCodeAndName(text) {
  if (text == null) return ['', ''];
  const s = String(text).replace(/\u00a0/g, ' ').replace(/\r?\n/g, ' ').trim();
  const m = s.match(/\[(.*?)\]\s*(.*)/);
  if (m) return [m[1].trim(), m[2].trim()];
  return ['', s];
}

export function cleanNumber(val) {
  if (val == null) return 0;
  const s = String(val).trim().replace(/\./g, '').replace(',', '.');
  const n = Number(s);
  return Number.isNaN(n) ? 0 : n;
}

function workbookRows(fileBuf) {
  // BPS exports .xls files that are really HTML tables (pandas users read them
  // with read_html). Detect the HTML signature before handing bytes to xlsx.
  const head = fileBuf.subarray(0, 4096).toString('utf8').replace(/^\uFEFF/, '').trimStart();
  if (/^</.test(head) || /<table[\s>]/i.test(head)) return htmlRows(fileBuf.toString('utf8'));
  const wb = xlsx.read(fileBuf, { type: 'buffer' });
  const ws = wb.Sheets[wb.SheetNames[0]];
  if (!ws) throw new Error('File kosong');
  return xlsx.utils.sheet_to_json(ws, { header: 1, defval: '' });
}

function htmlRows(html) {
  const $ = cheerio.load(html);
  const table = $('table').first();
  if (!table.length) throw new Error('Tabel tidak ditemukan pada berkas.');
  const rows = [];
  table.find('tr').each((_, tr) => {
    const cells = [];
    $(tr).find('th,td').each((__, cell) => {
      cells.push($(cell).text().replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim());
    });
    rows.push(cells);
  });
  return rows;
}

export function detectFileMetadata(fileBuf) {
  try {
    const rows = workbookRows(fileBuf);
    const headerStr = rows.slice(0, 4).flat().join(' | ');
    let tableType = null;
    if (headerStr.includes('Transportasi Laut')) tableType = 'transportasi_laut';
    else if (headerStr.includes('Pesawat Terbang') || headerStr.includes('Bandara')) tableType = 'transportasi_udara';
    let tahun = null;
    let bulan = null;
    const m = headerStr.match(/Tahun\s+(\d{4})\s+Bulan\s+([A-Za-z]+)/i);
    if (m) {
      tahun = Number(m[1]);
      const b = m[2][0].toUpperCase() + m[2].slice(1).toLowerCase();
      if (MONTH_LIST.includes(b)) bulan = b;
    }
    return { tableType, tahun, bulan };
  } catch {
    return { tableType: null, tahun: null, bulan: null };
  }
}

function processLaut(rows, tahun, bulan) {
  const parsed = [];
  for (const row of rows) {
    const [provCode] = extractCodeAndName(row[0]);
    const [kabCode, kabName] = extractCodeAndName(row[1]);
    const [pelCode, pelName] = extractCodeAndName(row[2]);
    if (!provCode || !kabCode || !pelCode) continue;
    parsed.push({
      tahun: Number(tahun), bulan,
      kode_provinsi: Number(provCode),
      kode_kabkota: Number(kabCode), nama_kabkota: kabName,
      kode_pelabuhan: Number(pelCode), nama_pelabuhan: pelName,
      dn_penumpang_turun: Math.round(cleanNumber(row[4])),
      dn_penumpang_naik: Math.round(cleanNumber(row[5])),
      dn_bongkar_barang_ton: cleanNumber(row[6]),
      dn_muat_barang_ton: cleanNumber(row[7]),
    });
  }
  return parsed;
}

function processUdara(rows, tahun, bulan) {
  const parsed = [];
  for (const row of rows) {
    const [provCode] = extractCodeAndName(row[0]);
    const [kabCode, kabName] = extractCodeAndName(row[1]);
    const [banCode, banName] = extractCodeAndName(row[2]);
    if (!provCode || !kabCode || !banCode) continue;
    parsed.push({
      tahun: Number(tahun), bulan,
      kode_provinsi: Number(provCode),
      kode_kabkota: Number(kabCode), nama_kabkota: kabName,
      kode_bandara: Number(banCode), nama_bandara: banName,
      pesawat_berangkat: Math.round(cleanNumber(row[4])),
      pesawat_datang: Math.round(cleanNumber(row[5])),
      penumpang_berangkat: Math.round(cleanNumber(row[6])),
      penumpang_datang: Math.round(cleanNumber(row[7])),
      barang_muat_kg: cleanNumber(row[9]),
      barang_bongkar_kg: cleanNumber(row[10]),
    });
  }
  return parsed;
}

export function parseTransportFile(fileBuf, tahun, bulan) {
  const rows = workbookRows(fileBuf);
  if (rows.length === 0) throw new Error('File kosong');
  const headerStr = rows.slice(0, 4).flat().join(' | ');
  let tableType = null;
  if (headerStr.includes('Transportasi Laut')) tableType = 'transportasi_laut';
  else if (headerStr.includes('Pesawat Terbang') || headerStr.includes('Bandara')) tableType = 'transportasi_udara';
  else throw new Error('Format header tabel tidak dikenali!');

  const data = tableType === 'transportasi_laut'
    ? processLaut(rows, tahun, bulan)
    : processUdara(rows, tahun, bulan);
  if (!data.length) throw new Error('Tidak ada baris data yang bisa dibaca dari berkas ini.');

  const unknown = [];
  for (const row of data) {
    const prov = getProvinceByKabupaten(row.nama_kabkota);
    if (!prov) unknown.push(String(row.nama_kabkota));
    row.nama_provinsi = prov;
  }
  if (unknown.length) {
    throw new Error(`Kabupaten tidak ditemukan di PEMETAAN_WILAYAH: ${[...new Set(unknown)].sort().join(', ')}. Tambahkan ke modules/config.py lalu unggah ulang.`);
  }
  return { tableType, rows: data };
}

const INSERT_SQL = {
  transportasi_laut: 'INSERT INTO transportasi_laut (tahun,bulan,kode_provinsi,nama_provinsi,kode_kabkota,nama_kabkota,kode_pelabuhan,nama_pelabuhan,dn_penumpang_turun,dn_penumpang_naik,dn_bongkar_barang_ton,dn_muat_barang_ton) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)',
  transportasi_udara: 'INSERT INTO transportasi_udara (tahun,bulan,kode_provinsi,nama_provinsi,kode_kabkota,nama_kabkota,kode_bandara,nama_bandara,pesawat_berangkat,pesawat_datang,penumpang_berangkat,penumpang_datang,penumpang_transit,bagasi_muat_kg,bagasi_bongkar_kg,barang_muat_kg,barang_bongkar_kg,pos_muat_kg,pos_bongkar_kg) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)',
};

function insertValues(tableType, r) {
  if (tableType === 'transportasi_laut') {
    return [r.tahun, r.bulan, r.kode_provinsi, r.nama_provinsi, r.kode_kabkota, r.nama_kabkota,
      r.kode_pelabuhan, r.nama_pelabuhan, r.dn_penumpang_turun, r.dn_penumpang_naik,
      r.dn_bongkar_barang_ton, r.dn_muat_barang_ton];
  }
  return [r.tahun, r.bulan, r.kode_provinsi, r.nama_provinsi, r.kode_kabkota, r.nama_kabkota,
    r.kode_bandara, r.nama_bandara, r.pesawat_berangkat, r.pesawat_datang,
    r.penumpang_berangkat, r.penumpang_datang, r.penumpang_transit ?? 0,
    r.bagasi_muat_kg ?? 0, r.bagasi_bongkar_kg ?? 0, r.barang_muat_kg,
    r.barang_bongkar_kg, r.pos_muat_kg ?? 0, r.pos_bongkar_kg ?? 0];
}

export async function countExistingRows(tableType, tahun, bulan) {
  const r = await pool.query(
    `SELECT COUNT(*)::int AS n FROM ${tableType} WHERE CAST(tahun AS TEXT) = $1 AND bulan = $2`,
    [String(tahun), bulan],
  );
  return r.rows[0].n;
}

export async function insertRows(client, tableType, rows) {
  for (const row of rows) {
    await client.query(INSERT_SQL[tableType], insertValues(tableType, row));
  }
}

export async function savePeriod(tableType, tahun, bulan, rows, overwrite) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    if (overwrite) {
      await client.query(
        `DELETE FROM ${tableType} WHERE CAST(tahun AS TEXT) = $1 AND bulan = $2`,
        [String(tahun), bulan],
      );
    }
    await insertRows(client, tableType, rows);
    await client.query('COMMIT');
  } catch (err) {
    try { await client.query('ROLLBACK'); } catch { /* already aborted */ }
    throw err;
  } finally {
    client.release();
  }
}

export function computeUploadStats(tableType, rows) {
  const sum = (key) => rows.reduce((acc, r) => acc + (Number(r[key]) || 0), 0);
  if (tableType === 'transportasi_udara') {
    return {
      jumlah_baris: rows.length,
      total_penumpang: sum('penumpang_berangkat') + sum('penumpang_datang'),
      total_barang: sum('barang_muat_kg') + sum('barang_bongkar_kg'),
      satuan_barang: 'kg',
      label_lokasi: 'Bandara',
      jumlah_lokasi: new Set(rows.map((r) => r.nama_bandara)).size,
      jumlah_provinsi: new Set(rows.map((r) => r.nama_provinsi)).size,
    };
  }
  return {
    jumlah_baris: rows.length,
    total_penumpang: sum('dn_penumpang_naik') + sum('dn_penumpang_turun'),
    total_barang: sum('dn_muat_barang_ton') + sum('dn_bongkar_barang_ton'),
    satuan_barang: 'ton',
    label_lokasi: 'Pelabuhan',
    jumlah_lokasi: new Set(rows.map((r) => r.nama_pelabuhan)).size,
    jumlah_provinsi: new Set(rows.map((r) => r.nama_provinsi)).size,
  };
}

export async function deleteFiltered(tableType, filters) {
  const where = [];
  const params = [];
  if (filters.province) {
    params.push(String(filters.province).toUpperCase());
    where.push(`UPPER(nama_provinsi) = $${params.length}`);
  }
  if (filters.year) {
    params.push(String(filters.year));
    where.push(`CAST(tahun AS TEXT) = $${params.length}`);
  }
  if (filters.month) {
    params.push(filters.month);
    where.push(`bulan = $${params.length}`);
  }
  const clause = where.length ? where.join(' AND ') : '1=1';
  const count = await pool.query(
    `SELECT COUNT(*)::int AS n FROM ${tableType} WHERE ${clause}`,
    params,
  );
  return { clause, params, count: count.rows[0].n };
}
