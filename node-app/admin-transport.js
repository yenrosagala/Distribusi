import { pool } from './db.js';
import { MONTH_MAP, PEMETAAN_WILAYAH, insertRows } from './etl-transport.js';

export const ADMIN_TABLES = {
  'Transportasi Udara': {
    table: 'transportasi_udara',
    entityCol: 'nama_bandara',
    entityLabel: 'Bandara',
    vars: ['penumpang_berangkat', 'penumpang_datang', 'penumpang_transit',
      'barang_muat_kg', 'barang_bongkar_kg', 'bagasi_muat_kg', 'bagasi_bongkar_kg',
      'pos_muat_kg', 'pos_bongkar_kg', 'pesawat_berangkat', 'pesawat_datang'],
  },
  'Transportasi Laut': {
    table: 'transportasi_laut',
    entityCol: 'nama_pelabuhan',
    entityLabel: 'Pelabuhan',
    vars: ['dn_penumpang_turun', 'dn_penumpang_naik', 'dn_bongkar_barang_ton',
      'dn_muat_barang_ton', 'ln_penumpang_turun', 'ln_penumpang_naik',
      'ln_bongkar_barang_ton', 'ln_muat_barang_ton'],
  },
};

export const COLUMNS = {
  transportasi_udara: ['tahun', 'bulan', 'kode_provinsi', 'nama_provinsi', 'kode_kabkota',
    'nama_kabkota', 'kode_bandara', 'nama_bandara', 'pesawat_berangkat', 'pesawat_datang',
    'penumpang_berangkat', 'penumpang_datang', 'penumpang_transit', 'bagasi_muat_kg',
    'bagasi_bongkar_kg', 'barang_muat_kg', 'barang_bongkar_kg', 'pos_muat_kg', 'pos_bongkar_kg'],
  transportasi_laut: ['tahun', 'bulan', 'kode_provinsi', 'nama_provinsi', 'kode_kabkota',
    'nama_kabkota', 'kode_pelabuhan', 'nama_pelabuhan', 'dn_penumpang_turun',
    'dn_penumpang_naik', 'dn_bongkar_barang_ton', 'dn_muat_barang_ton', 'ln_penumpang_turun',
    'ln_penumpang_naik', 'ln_bongkar_barang_ton', 'ln_muat_barang_ton'],
};

const TEXT_COLUMNS = new Set(['bulan', 'nama_provinsi', 'nama_kabkota', 'nama_bandara', 'nama_pelabuhan']);

export function modaOf(label) {
  return ADMIN_TABLES[label] ? label : 'Transportasi Udara';
}

export function kabupatenOptions() {
  return [...new Set(Object.values(PEMETAAN_WILAYAH).flat())].sort();
}

function cleanKabupaten(kabupaten) {
  return kabupaten.replace('KABUPATEN ', '').replace('KOTA ', '').trim();
}

export async function entityOptions(modaLabel, kabupaten) {
  const meta = ADMIN_TABLES[modaLabel];
  const params = [];
  let sql = `SELECT DISTINCT ${meta.entityCol} AS nama FROM ${meta.table} WHERE ${meta.entityCol} IS NOT NULL`;
  if (kabupaten && kabupaten !== 'SEMUA') {
    params.push(kabupaten.toUpperCase(), cleanKabupaten(kabupaten).toUpperCase());
    sql += ' AND (UPPER(nama_kabkota) = $1 OR UPPER(nama_kabkota) = $2)';
  }
  sql += ' ORDER BY 1';
  const { rows } = await pool.query(sql, params);
  return rows.map((r) => r.nama);
}

export async function distinctYears(modaLabel) {
  const meta = ADMIN_TABLES[modaLabel];
  const { rows } = await pool.query(`SELECT DISTINCT CAST(tahun AS TEXT) AS y FROM ${meta.table}`);
  return rows.map((r) => r.y).sort();
}

export function buildSeriesQuery(modaLabel, { kabupaten, entities }) {
  const meta = ADMIN_TABLES[modaLabel];
  const conditions = [];
  const params = [];
  if (kabupaten && kabupaten !== 'SEMUA') {
    params.push(kabupaten.toUpperCase(), cleanKabupaten(kabupaten).toUpperCase());
    conditions.push(`(UPPER(nama_kabkota) = $${params.length - 1} OR UPPER(nama_kabkota) = $${params.length})`);
  }
  if (entities && entities.length) {
    params.push(entities);
    conditions.push(`${meta.entityCol} = ANY($${params.length})`);
  }
  const where = conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
  return { meta, sql: `SELECT * FROM ${meta.table}${where}`, params };
}

export async function seriesData(modaLabel, filters) {
  const variabel = filters.variabel.length
    ? filters.variabel.filter((v) => ADMIN_TABLES[modaLabel].vars.includes(v))
    : [ADMIN_TABLES[modaLabel].vars[0]];
  if (!variabel.length) throw new Error('Pilih minimal satu variabel indikator.');
  const { meta, sql, params } = buildSeriesQuery(modaLabel, filters);
  const { rows } = await pool.query(sql, params);
  if (!rows.length) {
    return { empty: true, title: '', periods: [], series: [], rows: [] };
  }

  const buckets = new Map();
  const names = [];
  const seriesName = (row, v) => (filters.entities?.length
    ? `${row[meta.entityCol]} – ${titleCase(v)}`
    : titleCase(v));
  for (const row of rows) {
    const key = `${Number(row.tahun)}|${MONTH_MAP[row.bulan]}`;
    if (!buckets.has(key)) {
      buckets.set(key, {
        period: `${row.bulan} ${Number(row.tahun)}`,
        year: Number(row.tahun),
        month: MONTH_MAP[row.bulan],
        values: new Map(),
      });
    }
    const bucket = buckets.get(key);
    for (const v of variabel) {
      const name = seriesName(row, v);
      if (!names.includes(name)) names.push(name);
      bucket.values.set(name, (bucket.values.get(name) || 0) + (Number(row[v]) || 0));
    }
  }

  const ordered = [...buckets.values()]
    .sort((a, b) => (a.year - b.year) || (a.month - b.month));

  const series = names.map((name) => ({
    name,
    data: ordered.map((b) => b.values.get(name) ?? 0),
  }));

  const wilayah = filters.kabupaten === 'SEMUA' || !filters.kabupaten ? 'Seluruh Wilayah' : filters.kabupaten;
  return {
    empty: false,
    title: `Tren ${variabel.map(titleCase).join(', ')} — ${wilayah} (${modaLabel})`,
    periods: ordered.map((b) => b.period),
    series,
    variabel,
    rows: ordered.flatMap((b) => names.map((name) => ({
      Periode: b.period, Seri: name, Nilai: b.values.get(name) ?? 0,
    }))),
  };
}

function titleCase(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export async function periodRows(table, tahun, bulan) {
  const { rows } = await pool.query(
    `SELECT * FROM ${table} WHERE CAST(tahun AS TEXT) = $1 AND bulan = $2 ORDER BY 1`,
    [String(tahun), bulan],
  );
  return rows;
}

export function rowsToTsv(table, rows) {
  const cols = COLUMNS[table];
  const lines = [cols.join('\t')];
  for (const row of rows) lines.push(cols.map((c) => (row[c] == null ? '' : row[c])).join('\t'));
  return lines.join('\n');
}

export function tsvToRows(table, tsv) {
  const cols = COLUMNS[table];
  const lines = tsv.split(/\r?\n/).filter((l) => l.trim() !== '');
  if (!lines.length) return [];
  const header = lines[0].split('\t').map((h) => h.trim());
  if (header.join('|') !== cols.join('|')) {
    throw new Error(`Header tabel harus: ${cols.join(', ')}`);
  }
  return lines.slice(1).map((line, index) => {
    const cells = line.split('\t');
    if (cells.length !== cols.length) {
      throw new Error(`Baris ${index + 2} memiliki ${cells.length} kolom, seharusnya ${cols.length}.`);
    }
    const row = {};
    cols.forEach((col, i) => {
      const raw = cells[i].trim();
      if (TEXT_COLUMNS.has(col)) row[col] = raw || null;
      else if (col === 'tahun' || col.startsWith('kode_')) row[col] = raw === '' ? null : Number(raw);
      else row[col] = raw === '' ? 0 : Number(raw.replace(/\./g, '').replace(',', '.'));
    });
    return row;
  });
}

export async function replacePeriod(table, tahun, bulan, rows) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query(
      `DELETE FROM ${table} WHERE CAST(tahun AS TEXT) = $1 AND bulan = $2`,
      [String(tahun), bulan],
    );
    await insertRows(client, table, rows);
    await client.query('COMMIT');
  } catch (err) {
    try { await client.query('ROLLBACK'); } catch { /* already aborted */ }
    throw err;
  } finally {
    client.release();
  }
}

export async function deleteFiltered(table, filters) {
  const where = [];
  const params = [];
  if (filters.province && filters.province !== 'SEMUA') {
    params.push(filters.province.toUpperCase());
    where.push(`UPPER(nama_provinsi) = $${params.length}`);
  }
  if (filters.year && filters.year !== 'SEMUA') {
    params.push(String(filters.year));
    where.push(`CAST(tahun AS TEXT) = $${params.length}`);
  }
  if (filters.month && filters.month !== 'SEMUA') {
    params.push(filters.month);
    where.push(`bulan = $${params.length}`);
  }
  const clause = where.length ? where.join(' AND ') : '1=1';
  const countSql = // ponytail: table name whitelisted by callers via ADMIN_TABLES
    `SELECT COUNT(*)::int AS n FROM ${table} WHERE ${clause}`;
  const { rows } = await pool.query(countSql, params);
  return { clause, params, count: rows[0].n };
}

export async function deleteRows(table, clause, params) {
  const { rowCount } = await pool.query(`DELETE FROM ${table} WHERE ${clause}`, params);
  return rowCount;
}
