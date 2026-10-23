import fs from 'node:fs';
import path from 'node:path';
import pg from 'pg';

function resolveDsn() {
  if (process.env.DATABASE_URL) return process.env.DATABASE_URL;
  // ponytail: the DSN already lives in Streamlit's secrets file; parse that one
  // line instead of duplicating config. Move to DATABASE_URL when this app
  // outlives Streamlit.
  const file = path.resolve(import.meta.dirname, '..', '.streamlit', 'secrets.toml');
  try {
    const found = fs.readFileSync(file, 'utf8').match(/^\s*DATABASE_URL\s*=\s*["']([^"']+)["']/m);
    return found?.[1];
  } catch {
    return undefined;
  }
}

const dsn = resolveDsn();
if (!dsn) {
  console.error('DATABASE_URL tidak ditemukan (env atau .streamlit/secrets.toml).');
  process.exit(1);
}

export const pool = new pg.Pool({
  connectionString: dsn,
  ssl: /supabase|sslmode=require/.test(dsn) ? { rejectUnauthorized: false } : undefined,
  max: 5,
  idleTimeoutMillis: 30_000,
});

async function query(sql, params = []) {
  const { rows } = await pool.query(sql, params);
  return rows;
}

export async function availablePeriods() {
  const rows = [];
  for (const table of ['transportasi_udara', 'transportasi_laut']) {
    try {
      rows.push(...await query(`SELECT DISTINCT tahun, bulan FROM ${table}`));
    } catch (err) {
      console.warn(`[db] ${table} tidak terbaca: ${err.message}`);
    }
  }
  return rows;
}

export function periodData(table, thn, bln) {
  return query(`SELECT * FROM ${table} WHERE CAST(tahun AS TEXT) = $1 AND bulan = $2`, [String(thn), bln]);
}

export function cumulativeData(table, thn, bln) {
  const months = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli',
    'Agustus', 'September', 'Oktober', 'November', 'Desember'];
  const upto = months.slice(0, months.indexOf(bln) + 1);
  return query(
    `SELECT * FROM ${table} WHERE CAST(tahun AS TEXT) = $1 AND bulan = ANY($2::text[])`,
    [String(thn), upto],
  );
}

export async function narrative(reportType, periodKey) {
  try {
    const rows = await query(
      'SELECT narrative_text FROM ai_narratives_cache WHERE report_type = $1 AND period_key = $2',
      [reportType, periodKey],
    );
    return rows[0]?.narrative_text || null;
  } catch (err) {
    console.warn(`[db] cache narasi tidak terbaca: ${err.message}`);
    return null;
  }
}

export async function pariwisataInfo() {
  try {
    return await query('SELECT DISTINCT kd_prov, jenis_akomodasi, year, month FROM all_data');
  } catch (err) {
    console.warn(`[db] all_data tidak terbaca: ${err.message}`);
    return [];
  }
}

export async function latestMetrics(year, month) {
  try {
    const rows = await query(
      'SELECT AVG(tpk) AS tpk, AVG(rlmtgab) AS rlmtgab FROM all_data WHERE year = $1 AND month = $2',
      [year, month],
    );
    return rows[0] || null;
  } catch {
    return null;
  }
}

const MAP_INDICATORS = new Set(['tpk', 'rlmtgab']);
export async function infographicRows(indicator, year, month, prevYear, prevMonth) {
  if (!MAP_INDICATORS.has(indicator)) throw new Error('Indikator tidak dikenal');
  try {
    return await query(
      `SELECT kd_prov AS province, jenis_akomodasi, month, year, AVG(${indicator}) AS val
       FROM all_data
       WHERE year IN ($1, $2) AND month IN ($3, $4)
       GROUP BY kd_prov, jenis_akomodasi, month, year`,
      [year, prevYear, month, prevMonth],
    );
  } catch (err) {
    console.warn(`[db] all_data tidak terbaca: ${err.message}`);
    return [];
  }
}

async function safeRows(sql, params, label) {
  try {
    return await query(sql, params);
  } catch (err) {
    console.warn(`[db] ${label} tidak terbaca: ${err.message}`);
    return [];
  }
}

// Two years of rows in one read: the current period, the previous month and the
// same month last year come from here, and the YoY chart reuses the full year.
export function reportRows(province, year) {
  return safeRows(
    `SELECT kd_prov, jenis_akomodasi, kelas_akomodasi, tpk, rlmtgab,
            tpk_rse, rlmtgab_rse, year, month
     FROM all_data WHERE kd_prov = $1 AND year IN ($2, $3)`,
    [province, year, year - 1],
    'all_data',
  );
}

export function reportDomain() {
  return safeRows(
    `SELECT jenis_akomodasi, kelas_akomodasi FROM all_data
     WHERE kelas_akomodasi IS NOT NULL GROUP BY jenis_akomodasi, kelas_akomodasi`,
    [],
    'all_data',
  );
}

export function reportRef(province) {
  return safeRows(
    'SELECT jenis_akomodasi, year, month, tpk, rlmtgab FROM pariwisata_ref_avg WHERE kd_prov = $1',
    [province],
    'pariwisata_ref_avg',
  );
}

export function reportNarratives(province, year, month) {
  return safeRows(
    `SELECT jenis_akomodasi, indicator, narrative FROM pariwisata_ai_narratives
     WHERE province = $1 AND year = $2 AND month = $3`,
    [province, year, month],
    'pariwisata_ai_narratives',
  );
}

const TRANSPORT_TABLES = new Set(['transportasi_udara', 'transportasi_laut']);

// Both years in one read so month, previous month and both cumulative windows
// all come from the same snapshot (report_page.get_comparison_data runs 4 queries).
export function transportReportRows(table, province, year) {
  if (!TRANSPORT_TABLES.has(table)) return Promise.resolve([]);
  return safeRows(
    `SELECT * FROM ${table} WHERE UPPER(nama_provinsi) = $1 AND tahun IN ($2, $3)`,
    [province.toUpperCase(), year, year - 1],
    table,
  );
}

export function transportNarratives(reportTypes, periodKey) {
  return safeRows(
    'SELECT report_type, narrative_text FROM ai_narratives_cache WHERE report_type = ANY($1) AND period_key = $2',
    [reportTypes, periodKey],
    'ai_narratives_cache',
  );
}
