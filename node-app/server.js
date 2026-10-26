import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import {
  availablePeriods, cumulativeData, infographicRows, latestMetrics, narrative, pariwisataInfo,
  periodData, pool, reportDomain, reportNarratives, reportRef, reportRows,
  transportNarratives, transportReportRows,
} from './db.js';
import { errorPage, loginPage, pariwisataPage, transportasiPage } from './views.js';
import {
  INV_MONTH_MAP, MONTH_ABBR, PROVINSI_ORDER, aggByProvinsi, computeStats, fallbackNarrative, formatIdNumber,
  MONTH_NAMES, growthRows, mean, monthNameEn, pctChange, periodLabel, periodsFromInfo, prevPeriod,
  reportTable, sortPeriods, uniqueSorted, yoySeries,
} from './lib.js';
import {
  fallbackTransportNarrative, normalizeNarrative, transportReport,
} from './lib-transport.js';
import {
  ADMIN_TABLES, buildSeriesQuery, deleteFiltered, deleteRows, distinctYears, entityOptions,
  kabupatenOptions, periodRows, replacePeriod, rowsToTsv, seriesData, tsvToRows,
} from './admin-transport.js';
import { PEMETAAN_WILAYAH, computeUploadStats, countExistingRows, detectFileMetadata, parseTransportFile, savePeriod } from './etl-transport.js';
import Busboy from 'busboy';
import { EXPORT_MIME, exportFileName, runExport } from './export.js';

const PORT = Number(process.env.PORT || 8502);
const SECRET = process.env.SESSION_SECRET || crypto.randomBytes(32).toString('hex');
const COOKIE = 'sid';
const SESSION_SECONDS = 8 * 3600;
const PUBLIC_DIR = fileURLToPath(new URL('./public/', import.meta.url));
const ROOT_DIR = fileURLToPath(new URL('..', import.meta.url));

const USERS = {
  admin: { password: process.env.LOGIN_ADMIN_PASSWORD || 'admin123', role: 'admin', name: 'Database Administrator' },
  user: { password: process.env.LOGIN_USER_PASSWORD || 'user123', role: 'user', name: 'General Analyst' },
};

const MODA = [
  {
    title: 'Laut',
    table: 'transportasi_laut',
    satuan: 'ton',
    colsP: ['dn_penumpang_naik', 'dn_penumpang_turun'],
    colsB: ['dn_muat_barang_ton', 'dn_bongkar_barang_ton'],
    labelsP: ['PENUMPANG BERANGKAT', 'PENUMPANG DATANG'],
    labelsB: ['MUAT BARANG', 'BONGKAR BARANG'],
    colorsP: ['#047857', '#6EE7B7'],
    colorsB: ['#B45309', '#FCD34D'],
  },
  {
    title: 'Udara',
    table: 'transportasi_udara',
    satuan: 'kg',
    colsP: ['penumpang_berangkat', 'penumpang_datang'],
    colsB: ['barang_muat_kg', 'barang_bongkar_kg'],
    labelsP: ['PENUMPANG BERANGKAT', 'PENUMPANG DATANG'],
    labelsB: ['MUAT BARANG', 'BONGKAR BARANG'],
    colorsP: ['#0F766E', '#5EEAD4'],
    colorsB: ['#9A3412', '#FDBA74'],
  },
];

const TARGET_PROVINCES = ['Papua', 'Papua Tengah', 'Papua Pegunungan', 'Papua Selatan'];
const JENIS_LABELS = {
  'Hotel Bintang': 'Klasifikasi Bintang',
  'Hotel Non Bintang': 'Klasifikasi NonBintang',
};
// Report tables: 4 metrics per modality, in the order modules/report_page.py
// collects them (udara first, then laut), numbering 1..8 across both.
const TRANSPORT_TARGETS = [
  {
    moda: 'Transportasi Udara',
    icon: '✈️',
    table: 'transportasi_udara',
    rowCol: 'nama_bandara',
    cols: [
      ['penumpang_datang', 'Penumpang Datang'],
      ['penumpang_berangkat', 'Penumpang Berangkat'],
      ['barang_bongkar_kg', 'Barang Bongkar (Ton)'],
      ['barang_muat_kg', 'Barang Muat (Ton)'],
    ],
  },
  {
    moda: 'Transportasi Laut',
    icon: '🚢',
    table: 'transportasi_laut',
    rowCol: 'nama_pelabuhan',
    cols: [
      ['dn_penumpang_turun', 'Penumpang Turun'],
      ['dn_penumpang_naik', 'Penumpang Naik'],
      ['dn_bongkar_barang_ton', 'Barang Bongkar (Ton)'],
      ['dn_muat_barang_ton', 'Barang Muat (Ton)'],
    ],
  },
];

const INDICATOR_META = {
  tpk: { label: 'TPK (Occupancy Rate)', unit: '%' },
  rlmtgab: { label: 'RLMTGAB (Length of Stay)', unit: ' malam' },
};

const MIME = {
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
  '.ico': 'image/x-icon',
};

function sign(payload) {
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const mac = crypto.createHmac('sha256', SECRET).update(body).digest('base64url');
  return `${body}.${mac}`;
}

function verify(token) {
  if (!token || !token.includes('.')) return null;
  const [body, mac] = token.split('.');
  const expected = crypto.createHmac('sha256', SECRET).update(body).digest('base64url');
  const given = Buffer.from(mac || '');
  const want = Buffer.from(expected);
  if (given.length !== want.length || !crypto.timingSafeEqual(given, want)) return null;
  try {
    const data = JSON.parse(Buffer.from(body, 'base64url').toString('utf8'));
    return data.exp > Math.floor(Date.now() / 1000) ? data : null;
  } catch {
    return null;
  }
}

function readCookie(req, name) {
  for (const part of (req.headers.cookie || '').split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return decodeURIComponent(rest.join('='));
  }
  return undefined;
}

function sendHtml(res, html, status = 200) {
  res.writeHead(status, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(html);
}

function redirect(res, location, headers = {}) {
  res.writeHead(302, { Location: location, ...headers });
  res.end();
}

function sendFile(res, body, contentType, filename) {
  res.writeHead(200, {
    'Content-Type': contentType,
    'Content-Disposition': `attachment; filename="${filename}"`,
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function readBody(req, limit = 10_000) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new Error('Body terlalu besar'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function serveStatic(res, pathname) {
  // ponytail: style.css/logo.png/map geometry stay in the repo root; serving them
  // from there avoids a second copy drifting from the Streamlit app.
  const inRoot = ['/style.css', '/logo.png', '/papua_provinces.json'].includes(pathname);
  const root = inRoot ? ROOT_DIR : PUBLIC_DIR;
  const file = path.join(root, path.basename(pathname));
  try {
    const data = await fs.readFile(file);
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'public, max-age=3600',
    });
    res.end(data);
  } catch {
    sendHtml(res, errorPage('Berkas tidak ditemukan.'), 404);
  }
}

function growthTable(rows, labels) {
  return {
    columns: ['PROVINSI', ...labels.map((label) => `${label} (%)`)],
    rows: rows.map((r) => [
      r.provinsi,
      ...r.values.map((v) => (v == null || !Number.isFinite(v) ? '' : formatIdNumber(v, 2))),
    ]),
  };
}

function pickPeriod(periods, raw) {  const fallback = periods.at(-1);
  const match = /^(\d{4})-(.+)$/.exec(raw || '');
  if (!match) return fallback;
  return periods.find((p) => String(p.thn) === match[1] && p.bln === match[2]) || fallback;
}

async function buildSection(moda, selected, prev) {
  const [rowsCurr, rowsPrev, cumCurr, cumPrev] = await Promise.all([
    periodData(moda.table, selected.thn, selected.bln),
    periodData(moda.table, prev.thn, prev.bln),
    cumulativeData(moda.table, selected.thn, selected.bln),
    cumulativeData(moda.table, prev.thn, prev.bln),
  ]);

  const allCols = [...moda.colsP, ...moda.colsB];
  const aggCurr = aggByProvinsi(rowsCurr, allCols);
  const aggPrev = aggByProvinsi(rowsPrev, allCols);
  const aggCumCurr = aggByProvinsi(cumCurr, allCols);
  const aggCumPrev = aggByProvinsi(cumPrev, allCols);

  const periode = periodLabel(selected.thn, selected.bln);
  const periodeLabel = `${MONTH_ABBR[prev.bln]}-${MONTH_ABBR[selected.bln]} ${selected.thn}`;
  const key = moda.title.toLowerCase();

  if (rowsCurr.length === 0) {
    return { moda: moda.title, periode, empty: true, charts: [] };
  }

  const penumpang = computeStats(aggCurr, aggPrev, aggCumCurr, aggCumPrev, moda.colsP);
  const barang = computeStats(aggCurr, aggPrev, aggCumCurr, aggCumPrev, moda.colsB);

  const cached = await narrative(`dashboard_${moda.table}`, `${selected.bln}|${selected.thn}`);
  const paras = cached
    ? cached.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean)
    : fallbackNarrative(moda.title, selected.bln, selected.thn, prev.bln, prev.thn, penumpang, barang, moda.satuan);

  const charts = [
    {
      id: `ch-${key}-p`,
      labels: PROVINSI_ORDER,
      title: `Penumpang ${moda.title} — ${selected.bln} ${selected.thn}`,
      datasets: moda.colsP.map((col, i) => ({
        label: moda.labelsP[i],
        color: moda.colorsP[i],
        data: PROVINSI_ORDER.map((p) => aggCurr[p][col]),
      })),
    },
    {
      id: `ch-${key}-b`,
      labels: PROVINSI_ORDER,
      title: `Barang (${moda.satuan}) ${moda.title} — ${selected.bln} ${selected.thn}`,
      datasets: moda.colsB.map((col, i) => ({
        label: moda.labelsB[i],
        color: moda.colorsB[i],
        data: PROVINSI_ORDER.map((p) => aggCurr[p][col]),
      })),
    },
  ];

  return {
    moda: moda.title,
    periode,
    periodeLabel,
    labelsP: moda.labelsP,
    labelsB: moda.labelsB,
    charts,
    growthP: growthRows(aggCurr, aggPrev, moda.colsP, moda.labelsP),
    growthB: growthRows(aggCurr, aggPrev, moda.colsB, moda.labelsB),
    narrative: { paras, source: cached ? 'Database (Cached)' : 'Template otomatis (tanpa AI)' },
    raw: rowsCurr,
    empty: false,
  };
}

async function loadTransportReport(url, periods, selected) {
  const years = uniqueSorted(periods.map((p) => p.thn));
  const provinces = ['Papua', 'Papua Selatan', 'Papua Tengah', 'Papua Pegunungan'];
  const wantProv = url.searchParams.get('provinsi');
  const province = provinces.includes(wantProv) ? wantProv : provinces[0];
  const wantYear = Number(url.searchParams.get('tahun'));
  const year = years.includes(wantYear) ? wantYear : selected.thn;
  const wantMonth = url.searchParams.get('bulan');
  const monthName = MONTH_NAMES.includes(wantMonth) ? wantMonth : selected.bln;
  const prev = prevPeriod(year, monthName);

  const [udara, laut] = await Promise.all([
    transportReportRows('transportasi_udara', province, year),
    transportReportRows('transportasi_laut', province, year),
  ]);
  const rowsBy = { transportasi_udara: udara, transportasi_laut: laut };

  const types = TRANSPORT_TARGETS.flatMap((t) => t.cols.map(([, label]) => `report_${t.moda}_${label}`));
  const cached = new Map();
  try {
    for (const r of await transportNarratives(types, `${province}|${monthName}|${year}`)) {
      cached.set(r.report_type, r.narrative_text);
    }
  } catch (err) {
    console.warn(`[report] cache narasi transportasi tidak terbaca: ${err.message}`);
  }

  let tableNo = 0;
  const groups = [];
  for (const target of TRANSPORT_TARGETS) {
    const rows = rowsBy[target.table];
    const hasCurrent = rows.some((r) => Number(r.tahun) === year && r.bulan === monthName);
    if (!hasCurrent) continue;
    const rowCol = target.moda === 'Transportasi Laut' && province === 'Papua Tengah'
      ? 'nama_kabkota'
      : target.rowCol;
    const items = target.cols.map(([col, label]) => {
      tableNo += 1;
      const table = transportReport(rows, {
        col, label, rowCol, moda: target.moda, prov: province,
        year, monthName, prevYear: prev.thn, prevMonthName: prev.bln,
      });
      const cachedText = cached.get(`report_${target.moda}_${label}`);
      const paras = cachedText
        ? normalizeNarrative(cachedText, col).split(/\n{2,}/).map((p) => p.trim()).filter(Boolean)
        : normalizeNarrative(fallbackTransportNarrative({
          total: table.total, meta: table.meta, moda: target.moda, prov: province,
          bln: monthName, thn: year, prevBln: prev.bln, prevThn: prev.thn,
        }).join('\n\n'), col).split(/\n{2,}/);
      const angkutan = target.moda === 'Transportasi Udara' ? 'Angkutan Udara' : 'Angkutan Laut';
      return {
        tableNo,
        label,
        title: `Tabel ${tableNo} Perkembangan ${label} ${angkutan} Dalam Negeri Provinsi ${province}, ${monthName} ${year}`,
        rowLabel: target.moda === 'Transportasi Udara' ? 'Bandara' : 'Pelabuhan/Kabupaten',
        columns: table.columns,
        rows: table.rows,
        narrative: { source: cachedText ? 'Database (Cached)' : 'Sistem Fallback', paras },
      };
    });
    groups.push({ moda: target.moda, icon: target.icon, items });
  }

  return {
    provinces, province, years, months: MONTH_NAMES, year, monthName,
    groups,
    warning: groups.length
      ? null
      : `No data found matching parameters for Province: ${province}, ${monthName} ${year}.`,
  };
}

async function buildTransportasi(session, url) {
  const periods = sortPeriods(await availablePeriods());
  const allowed = session.role === 'admin'
    ? ['dashboard', 'report', 'admin']
    : ['dashboard', 'report'];
  const requested = url.searchParams.get('tab');
  const tab = allowed.includes(requested) ? requested : 'dashboard';
  if (periods.length === 0) {
    return transportasiPage({ user: session, periods: [], sections: [], tab });
  }
  const selected = pickPeriod(periods, url.searchParams.get('periode'));
  const prev = prevPeriod(selected.thn, selected.bln);
  const sections = tab === 'dashboard'
    ? await Promise.all(MODA.map((moda) => buildSection(moda, selected, prev)))
    : [];
  const report = tab === 'report' ? await loadTransportReport(url, periods, selected) : null;
  const admin = tab === 'admin' && session.role === 'admin' ? await buildTransportAdmin(session, url) : null;
  return transportasiPage({ user: session, periods, selected, sections, tab, report, admin });
}

async function loadMap(url, info) {
  const indicator = url.searchParams.get('indikator') === 'rlmtgab' ? 'rlmtgab' : 'tpk';
  const years = uniqueSorted(info.map((r) => r.year));
  const months = uniqueSorted(info.map((r) => r.month));
  const wantYear = Number(url.searchParams.get('tahun'));
  const wantMonth = Number(url.searchParams.get('bulan'));
  // month_list is global in Streamlit (months 9-12 only exist for 2025), so the
  // default must come from the latest (year, month) pair, not the column maxima.
  const pairs = [...new Set(info.map((r) => `${Number(r.year)}|${Number(r.month)}`))]
    .map((s) => s.split('|').map(Number))
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const latest = pairs.at(-1) || [];
  const year = years.includes(wantYear) ? wantYear : latest[0];
  const month = months.includes(wantMonth) ? wantMonth : latest[1];
  const meta = INDICATOR_META[indicator];
  const data = {
    indicator,
    indicatorLabel: meta.label,
    unit: meta.unit,
    years,
    months,
    year,
    month,
    monthName: monthNameEn(month),
    values: {},
    labels: {},
    cards: [],
    rows: [],
    lo: null,
    hi: null,
    empty: true,
  };
  if (!year || !month) return data;

  const prevMonth = month > 1 ? month - 1 : 12;
  const prevYear = month > 1 ? year : year - 1;
  const rows = await infographicRows(indicator, year, month, prevYear, prevMonth);
  const cur = rows.filter((r) => Number(r.year) === year && Number(r.month) === month);
  const prev = rows.filter((r) => Number(r.year) === prevYear && Number(r.month) === prevMonth);
  if (cur.length === 0) return data;

  const find = (list, prov, jenis) => list.find(
    (r) => String(r.province) === prov && r.jenis_akomodasi === jenis,
  );

  for (const prov of TARGET_PROVINCES) {
    const avg = mean(cur.filter((r) => String(r.province) === prov).map((r) => r.val));
    if (avg != null) {
      data.values[prov] = avg;
      data.labels[prov] = `${formatIdNumber(avg, 2)}${meta.unit}`;
    }
  }

  data.cards = TARGET_PROVINCES.map((prov) => ({
    province: prov,
    rows: Object.entries(JENIS_LABELS).map(([jenis, label]) => {
      const c = find(cur, prov, jenis);
      const p = find(prev, prov, jenis);
      const value = c?.val == null ? null : Number(c.val);
      const before = p?.val == null ? null : Number(p.val);
      return { label, value, delta: pctChange(value, before) };
    }),
  }));

  const nums = Object.values(data.values);
  data.lo = nums.length ? Math.min(...nums) : null;
  data.hi = nums.length ? Math.max(...nums) : null;
  data.rows = cur;
  data.empty = nums.length === 0;
  return data;
}

async function loadReport(url, info) {
  const provinces = info.length ? TARGET_PROVINCES.slice().sort() : [];
  const years = uniqueSorted(info.map((r) => r.year));
  const months = uniqueSorted(info.map((r) => r.month));
  const pairs = [...new Set(info.map((r) => `${Number(r.year)}|${Number(r.month)}`))]
    .map((s) => s.split('|').map(Number))
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const latest = pairs.at(-1) || [];

  const wantProv = url.searchParams.get('provinsi');
  const wantYear = Number(url.searchParams.get('tahun'));
  const wantMonth = Number(url.searchParams.get('bulan'));
  const province = provinces.includes(wantProv) ? wantProv : provinces[0];
  const year = years.includes(wantYear) ? wantYear : latest[0];
  const month = months.includes(wantMonth) ? wantMonth : latest[1];
  const report = { provinces, province, years, months, year, month, indicators: [], warning: null };
  if (!province || !year || !month) return report;

  const prevMonth = month > 1 ? month - 1 : 12;
  const prevYear = month > 1 ? year : year - 1;
  const hasPrev = info.some((r) => String(r.kd_prov) === province
    && Number(r.year) === prevYear && Number(r.month) === prevMonth);
  if (!hasPrev) {
    report.warning = `Data untuk bulan sebelumnya (${periodLabel(prevYear, INV_MONTH_MAP[prevMonth])}) belum tersedia di `
      + `${province}. Perbandingan bulan-ke-bulan tidak dapat dihitung untuk `
      + `${periodLabel(year, INV_MONTH_MAP[month])} — pilih periode yang lebih akhir.`;
  }

  const [rows, domainRows, refRows, narratives] = await Promise.all([
    reportRows(province, year),
    reportDomain(),
    reportRef(province),
    reportNarratives(province, year, month),
  ]);

  const domain = {};
  for (const r of domainRows) {
    (domain[r.jenis_akomodasi] ||= []).push(Number(r.kelas_akomodasi));
  }
  const ref = new Map(refRows.map((r) => [`${r.jenis_akomodasi}|${Number(r.year)}|${Number(r.month)}`, r]));
  const narrativeBy = new Map(narratives.map((r) => [`${r.jenis_akomodasi}|${r.indicator}`, r.narrative]));
  const current = rows.filter((r) => Number(r.year) === year && Number(r.month) === month);
  if (!current.length) return report;

  report.indicators = Object.entries(INDICATOR_META).map(([key, meta]) => ({
    key,
    short: meta.short,
    unit: meta.unit,
    jenis: Object.keys(JENIS_LABELS).map((jenis) => ({
      jenis,
      table: reportTable(rows, {
        jenis,
        indicator: key,
        year,
        month,
        prevYear,
        prevMonth,
        lastYear: year - 1,
        domain: domain[jenis] || [],
        ref,
      }),
      series: yoySeries(rows, { jenis, indicator: key, year, month, ref }),
      narrative: narrativeBy.get(`${jenis}|${key}`) || null,
    })),
  }));
  return report;
}

async function buildPariwisata(session, url) {  const rows = await pariwisataInfo();
  const periods = periodsFromInfo(rows);
  const latest = periods.at(-1);
  const metrics = latest ? await latestMetrics(latest.thn, latest.bln) : null;
  const provCount = new Set(rows.map((r) => String(r.kd_prov))).size;
  const allowed = session.role === 'admin'
    ? ['home', 'map', 'report', 'admin']
    : ['home', 'map', 'report'];
  const requested = url.searchParams.get('tab');
  const tab = allowed.includes(requested) ? requested : 'home';
  const map = tab === 'map' && rows.length ? await loadMap(url, rows) : null;
  const report = tab === 'report' && rows.length ? await loadReport(url, rows) : null;
  return pariwisataPage({ user: session, info: { rows, provCount, metrics }, periods, tab, map, report });
}

const UPLOADS = new Map();
const UPLOAD_TTL = 30 * 60 * 1000;

function stageKey() {
  return crypto.randomBytes(12).toString('hex');
}

function pruneUploads() {
  const now = Date.now();
  for (const [key, value] of UPLOADS) {
    if (now - value.at > UPLOAD_TTL) UPLOADS.delete(key);
  }
}

function parseMultipart(req) {
  return new Promise((resolve, reject) => {
    const bb = Busboy({ headers: req.headers, limits: { fileSize: 25 * 1024 * 1024, files: 20 } });
    const files = [];
    const fields = {};
    bb.on('file', (name, stream, info) => {
      const chunks = [];
      stream.on('data', (c) => chunks.push(c));
      stream.on('end', () => files.push({ field: name, name: info.filename, buffer: Buffer.concat(chunks) }));
    });
    bb.on('field', (name, value) => { fields[name] = value; });
    bb.on('close', () => resolve({ files, fields }));
    bb.on('error', reject);
    req.pipe(bb);
  });
}

function brsAssets(files) {
  const find = (field) => files.find((f) => f.field === field && f.buffer.length);
  return { qr: find('qr'), infografis: find('infografis') };
}

function flash(url, text, ok) {
  return `${url}${url.includes('?') ? '&' : '?'}msg=${encodeURIComponent(text)}&ok=${ok ? 1 : 0}`;
}

function readFlash(url) {
  const text = url.searchParams.get('msg');
  return text ? { text, ok: url.searchParams.get('ok') === '1' } : null;
}

function adminTable(raw) {
  return Object.values(ADMIN_TABLES).some((m) => m.table === raw) ? raw : 'transportasi_udara';
}

async function buildTransportAdmin(session, url) {
  const sub = url.searchParams.get('sub') || 'series';
  const admin = { sub };

  if (sub === 'upload') {
    const stage = url.searchParams.get('stage');
    const record = stage ? UPLOADS.get(stage) : null;
    admin.upload = {
      stage: record ? stage : null,
      files: record
        ? record.files.map((f) => ({
          nama_file: f.nama_file,
          table_type: f.meta.tableType,
          tahun: f.meta.tahun || 2026,
          bulan: f.meta.bulan || 'Januari',
        }))
        : [],
      preview: record && url.searchParams.get('preview') === '1' ? record.preview : null,
      message: readFlash(url),
    };
    return admin;
  }

  if (sub === 'edit') {
    const table = adminTable(url.searchParams.get('moda'));
    const tahun = url.searchParams.get('tahun') || String(new Date().getFullYear());
    const bulan = MONTH_NAMES.includes(url.searchParams.get('bulan')) ? url.searchParams.get('bulan') : 'Januari';
    admin.edit = {
      table, tahun, bulan, months: MONTH_NAMES,
      tsv: url.searchParams.get('load') === '1'
        ? rowsToTsv(table, await periodRows(table, tahun, bulan))
        : null,
      message: readFlash(url),
    };
    return admin;
  }

  if (sub === 'delete') {
    const modaLabel = ADMIN_TABLES[url.searchParams.get('moda')] ? url.searchParams.get('moda') : 'Transportasi Udara';
    const moda = ADMIN_TABLES[modaLabel].table;
    const province = url.searchParams.get('province') || 'SEMUA';
    const tahun = url.searchParams.get('year') || 'SEMUA';
    const bulan = url.searchParams.get('month') || 'SEMUA';
    const years = await distinctYears(modaLabel);
    const filters = { province, year: tahun, month: bulan };
    const preview = url.searchParams.get('count') === '1'
      ? await deleteFiltered(moda, filters)
      : null;
    admin.del = {
      moda: modaLabel, province, tahun, bulan, years, provinces: provinceList(),
      months: MONTH_NAMES, count: preview ? preview.count : null, message: readFlash(url),
    };
    return admin;
  }

  const moda = ADMIN_TABLES[url.searchParams.get('moda')] ? url.searchParams.get('moda') : 'Transportasi Udara';
  const kabupaten = url.searchParams.get('kabupaten') || 'SEMUA';
  const entities = url.searchParams.getAll('entity');
  const variabel = url.searchParams.getAll('variabel');
  const meta = ADMIN_TABLES[moda];
  admin.seriesForm = {
    moda,
    kabupaten,
    kabupatenList: kabupatenOptions(),
    entityLabel: meta.entityLabel,
    entities: {
      options: await entityOptions(moda, kabupaten),
      selected: entities,
    },
    variabel: {
      options: meta.vars,
      selected: variabel.length ? variabel : [meta.vars[0]],
    },
    series: url.searchParams.get('go') === '1'
      ? await seriesData(moda, { kabupaten, entities, variabel })
      : null,
  };
  return admin;
}

function provinceList() {
  return Object.keys(PEMETAAN_WILAYAH);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

  try {
    if (req.method === 'GET' && /^\/(style\.css|app\.css|logo\.png|papua_provinces\.json)$/.test(url.pathname)) {
      return await serveStatic(res, url.pathname);
    }

    if (req.method === 'GET' && url.pathname === '/login') {
      return sendHtml(res, loginPage());
    }

    if (req.method === 'POST' && url.pathname === '/login') {
      const form = new URLSearchParams(await readBody(req));
      const username = form.get('username') || '';
      const account = USERS[username];
      if (!account || account.password !== form.get('password')) {
        return sendHtml(res, loginPage({ error: 'Username atau password salah. Silakan coba lagi.' }), 401);
      }
      const token = sign({
        name: account.name,
        role: account.role,
        exp: Math.floor(Date.now() / 1000) + SESSION_SECONDS,
      });
      return redirect(res, '/pariwisata', {
        'Set-Cookie': `${COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_SECONDS}`,
      });
    }

    const session = verify(readCookie(req, COOKIE));
    if (!session) return redirect(res, '/login');

    if (req.method === 'POST' && url.pathname === '/logout') {
      return redirect(res, '/login', {
        'Set-Cookie': `${COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`,
      });
    }

    if (url.pathname === '/') return redirect(res, '/pariwisata');
    if (url.pathname === '/pariwisata') return sendHtml(res, await buildPariwisata(session, url));
    if (url.pathname === '/transportasi') return sendHtml(res, await buildTransportasi(session, url));

    if (url.pathname.startsWith('/transportasi/admin/')) {
      if (session.role !== 'admin') return sendHtml(res, errorPage('Akses admin diperlukan.'), 403);
      pruneUploads();

      if (req.method === 'POST' && url.pathname === '/transportasi/admin/upload') {
        const { files } = await parseMultipart(req);
        if (!files.length) return redirect(res, flash('/transportasi?tab=admin&sub=upload', 'Tidak ada berkas yang diunggah.', false));
        const token = stageKey();
        UPLOADS.set(token, {
          at: Date.now(),
          files: files.map((f) => ({
            nama_file: f.name,
            buffer: f.buffer,
            meta: detectFileMetadata(f.buffer),
          })),
          preview: null,
        });
        return redirect(res, `/transportasi?tab=admin&sub=upload&stage=${token}`);
      }

      if (req.method === 'POST' && url.pathname === '/transportasi/admin/preview') {
        const form = new URLSearchParams(await readBody(req));
        const record = UPLOADS.get(form.get('stage'));
        if (!record) return redirect(res, flash('/transportasi?tab=admin&sub=upload', 'Sesi unggahan kedaluwarsa. Unggah ulang berkas.', false));
        const preview = [];
        for (const [index, file] of record.files.entries()) {
          const tahun = Number(form.get(`tahun_${index}`)) || record.files[index].meta.tahun || 2026;
          const bulan = MONTH_NAMES.includes(form.get(`bulan_${index}`)) ? form.get(`bulan_${index}`) : (file.meta.bulan || 'Januari');
          try {
            const parsed = parseTransportFile(file.buffer, tahun, bulan);
            preview.push({
              nama_file: file.nama_file,
              table_type: parsed.tableType,
              tahun,
              bulan,
              stats: computeUploadStats(parsed.tableType, parsed.rows),
              existing_rows: await countExistingRows(parsed.tableType, tahun, bulan),
              error: null,
              parsed,
            });
          } catch (err) {
            preview.push({ nama_file: file.nama_file, table_type: null, tahun, bulan, stats: null, existing_rows: 0, error: err.message, parsed: null });
          }
        }
        record.preview = preview;
        record.metaInputs = preview.map((p) => ({ tahun: p.tahun, bulan: p.bulan }));
        return redirect(res, `/transportasi?tab=admin&sub=upload&stage=${form.get('stage')}&preview=1`);
      }

      if (req.method === 'POST' && url.pathname === '/transportasi/admin/save') {
        const form = new URLSearchParams(await readBody(req));
        const token = form.get('stage');
        const record = UPLOADS.get(token);
        if (!record || !record.preview) return redirect(res, flash('/transportasi?tab=admin&sub=upload', 'Sesi unggahan kedaluwarsa. Unggah ulang berkas.', false));
        if (form.get('confirm') !== '1') return redirect(res, flash(`/transportasi?tab=admin&sub=upload&stage=${token}&preview=1`, 'Centang konfirmasi sebelum menyimpan.', false));
        const overwrite = form.get('overwrite') === '1';
        let saved = 0;
        const failures = [];
        for (const p of record.preview) {
          if (p.error) continue;
          try {
            await savePeriod(p.table_type, p.tahun, p.bulan, p.parsed.rows, overwrite);
            saved += 1;
          } catch (err) {
            failures.push(`${p.nama_file}: ${err.message}`);
          }
        }
        UPLOADS.delete(token);
        const text = failures.length
          ? `${saved} berkas tersimpan, ${failures.length} gagal — ${failures.join('; ')}`
          : `${saved} berkas berhasil disimpan ke database.`;
        return redirect(res, flash('/transportasi?tab=admin&sub=upload', text, failures.length === 0));
      }

      if (req.method === 'POST' && url.pathname === '/transportasi/admin/edit') {
        const form = new URLSearchParams(await readBody(req, 4_000_000));
        const table = adminTable(form.get('moda'));
        const tahun = form.get('tahun') || '';
        const bulan = form.get('bulan') || '';
        const back = `/transportasi?tab=admin&sub=edit&moda=${table}&tahun=${encodeURIComponent(tahun)}&bulan=${encodeURIComponent(bulan)}&load=1`;
        if (!MONTH_NAMES.includes(bulan) || !/^\d{4}$/.test(tahun)) {
          return redirect(res, flash(back, 'Tahun/bulan periode tidak valid.', false));
        }
        try {
          const rows = tsvToRows(table, form.get('tsv') || '');
          if (!rows.length) throw new Error('Tidak ada baris untuk disimpan.');
          await replacePeriod(table, tahun, bulan, rows);
          return redirect(res, flash(back, `✅ ${rows.length} baris ${table} ${bulan} ${tahun} berhasil disimpan.`, true));
        } catch (err) {
          return redirect(res, flash(back, `Gagal menyimpan data: ${err.message}`, false));
        }
      }

      if (req.method === 'POST' && url.pathname === '/transportasi/admin/delete') {
        const form = new URLSearchParams(await readBody(req));
        const modaLabel = ADMIN_TABLES[form.get('moda')] ? form.get('moda') : null;
        const back = `/transportasi?tab=admin&sub=delete&moda=${encodeURIComponent(modaLabel || '')}&province=${encodeURIComponent(form.get('province') || 'SEMUA')}&year=${encodeURIComponent(form.get('year') || 'SEMUA')}&month=${encodeURIComponent(form.get('month') || 'SEMUA')}&count=1`;
        if (!modaLabel) return redirect(res, flash(back, 'Moda transportasi tidak dikenal.', false));
        if (form.get('confirm') !== '1') return redirect(res, flash(back, 'Centang konfirmasi sebelum menghapus.', false));
        try {
          const filters = { province: form.get('province') || 'SEMUA', year: form.get('year') || 'SEMUA', month: form.get('month') || 'SEMUA' };
          const { clause, params, count } = await deleteFiltered(ADMIN_TABLES[modaLabel].table, filters);
          if (!count) return redirect(res, flash(back, 'Tidak ada baris yang cocok dengan filter.', false));
          const removed = await deleteRows(ADMIN_TABLES[modaLabel].table, clause, params);
          return redirect(res, flash('/transportasi?tab=admin&sub=delete', `🗑 ${removed} baris dihapus dari ${ADMIN_TABLES[modaLabel].table}.`, true));
        } catch (err) {
          return redirect(res, flash(back, `Gagal menghapus data: ${err.message}`, false));
        }
      }
    }

    if (url.pathname === '/api/narrative/generate' && req.method === 'POST') {
      if (!req.session?.user) return sendHtml(res, loginPage({ error: 'Silakan login terlebih dahulu.' }), 401);
      if (req.session.user.role !== 'admin') return json(res, { error: 'Hanya admin' }, 403);
      const form = await parseForm(req);
      const values = form.values || {};
      const kind = values.kind;
      if (!kind) return json(res, { error: 'kind required' }, 400);
      const child = spawn(PYTHON_BIN, ['gen_narrative.py'], { cwd: NODE_ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
      let out = '', err = '';
      child.stdout.on('data', d => { out += d.toString(); });
      child.stderr.on('data', d => { err += d.toString(); });
      child.on('error', e => { return json(res, { error: e.message }, 500); });
      child.on('close', code => {
        if (res.headersSent) return;
        if (code !== 0) return json(res, { error: (err || out).split('\n').filter(Boolean).slice(-3).join(' ') }, 500);
        try {
          const j = JSON.parse(out.trim());
          return json(res, j, j.error ? 500 : 200);
        } catch {
          return json(res, { error: 'bad json' }, 500);
        }
      });
      child.stdin.write(JSON.stringify({ ...values, action: 'generate' }));
      child.stdin.end();
      return;
    }

    if (url.pathname === '/api/narrative/regenerate' && req.method === 'POST') {
      if (!req.session?.user) return sendHtml(res, loginPage({ error: 'Silakan login terlebih dahulu.' }), 401);
      if (req.session.user.role !== 'admin') return json(res, { error: 'Hanya admin' }, 403);
      const form = await parseForm(req);
      const values = form.values || {};
      const kind = values.kind;
      if (!kind) return json(res, { error: 'kind required' }, 400);
      const child = spawn(PYTHON_BIN, ['gen_narrative.py'], { cwd: NODE_ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
      let out = '', err = '';
      child.stdout.on('data', d => { out += d.toString(); });
      child.stderr.on('data', d => { err += d.toString(); });
      child.on('error', e => { return json(res, { error: e.message }, 500); });
      child.on('close', code => {
        if (res.headersSent) return;
        if (code !== 0) return json(res, { error: (err || out).split('\n').filter(Boolean).slice(-3).join(' ') }, 500);
        try {
          const j = JSON.parse(out.trim());
          return json(res, j, j.error ? 500 : 200);
        } catch {
          return json(res, { error: 'bad json' }, 500);
        }
      });
      child.stdin.write(JSON.stringify({ ...values, action: 'regenerate' }));
      child.stdin.end();
      return;
    }

    if (url.pathname === '/export/transportasi/pptx') {
      const periods = sortPeriods(await availablePeriods());
      const selected = pickPeriod(periods, url.searchParams.get('periode'));
      const prev = prevPeriod(selected.thn, selected.bln);
      const sections = (await Promise.all(MODA.map((moda) => buildSection(moda, selected, prev))))
        .filter((s) => !s.empty);
      if (!sections.length) return sendHtml(res, errorPage('Belum ada data untuk periode ini.'), 404);
      const payload = {
        sections: sections.map((s) => ({
          moda: s.moda,
          bln: selected.bln,
          thn: selected.thn,
          charts: s.charts,
          tables: [growthTable(s.growthP, s.labelsP), growthTable(s.growthB, s.labelsB)],
          narration: s.narrative.paras,
        })),
      };
      const data = await runExport('pptx', payload);
      return sendFile(res, data, EXPORT_MIME.pptx, exportFileName('pptx', { bln: selected.bln, thn: selected.thn }));
    }

    const reportExport = url.pathname.startsWith('/export/transportasi/')
      ? url.pathname.slice('/export/transportasi/'.length)
      : null;
    if (reportExport === 'docx' || reportExport === 'idml') {
      const form = req.method === 'POST' ? await parseMultipart(req) : { files: [], fields: {} };
      const values = { ...Object.fromEntries(url.searchParams), ...form.fields };
      const prov = values.provinsi || 'Papua';
      const tahun = Number(values.tahun);
      const bulan = values.bulan || '';
      if (!MONTH_NAMES.includes(bulan) || !Number.isInteger(tahun)) {
        return sendHtml(res, errorPage('Provinsi, tahun, atau bulan tidak valid.'), 400);
      }
      const kind = reportExport === 'docx' ? 'docx' : 'idml-transport';
      const payload = {
        prov,
        thn: tahun,
        bln: bulan,
        nomor: values.nomor || '',
        tanggal: values.tanggal || '',
      };
      if (kind === 'idml-transport') {
        const assets = brsAssets(form.files);
        if (assets.qr) payload.qr_b64 = assets.qr.buffer.toString('base64');
        if (assets.qr) payload.qr_name = assets.qr.name;
        if (assets.infografis) payload.infografis_b64 = assets.infografis.buffer.toString('base64');
        if (assets.infografis) payload.infografis_name = assets.infografis.name;
      }
      const data = await runExport(kind, payload);
      return sendFile(res, data, EXPORT_MIME[kind], exportFileName(kind, payload));
    }

    if (url.pathname === '/export/pariwisata/idml') {
      const form = req.method === 'POST' ? await parseMultipart(req) : { files: [], fields: {} };
      const values = { ...Object.fromEntries(url.searchParams), ...form.fields };
      const prov = values.provinsi || 'Papua Selatan';
      const tahun = Number(values.tahun);
      const bulan = values.bulan || '';
      if (!MONTH_NAMES.includes(bulan) || !Number.isInteger(tahun)) {
        return sendHtml(res, errorPage('Provinsi, tahun, atau bulan tidak valid.'), 400);
      }
      const assets = brsAssets(form.files);
      const payload = {
        prov,
        thn: tahun,
        bln: bulan,
        nomor: values.nomor || '',
        tanggal: values.tanggal || '',
      };
      if (assets.qr) payload.qr_b64 = assets.qr.buffer.toString('base64');
      if (assets.qr) payload.qr_name = assets.qr.name;
      if (assets.infografis) payload.infografis_b64 = assets.infografis.buffer.toString('base64');
      if (assets.infografis) payload.infografis_name = assets.infografis.name;
      const data = await runExport('idml-pariwisata', payload);
      return sendFile(res, data, EXPORT_MIME['idml-pariwisata'], exportFileName('idml-pariwisata', payload));
    }

    if (url.pathname === '/export/pariwisata-map.csv') {
      const info = await pariwisataInfo();
      const map = await loadMap(url, info);
      const lines = ['province,jenis_akomodasi,month,year,val', ...map.rows.map(
        (r) => [r.province, r.jenis_akomodasi, r.month, r.year, r.val].join(','),
      )];
      return sendFile(
        res,
        `${lines.join('\n')}\n`,
        'text/csv; charset=utf-8',
        `infographic_${map.indicator}_${map.year}_${map.month}.csv`,
      );
    }

    return sendHtml(res, errorPage('Halaman tidak ditemukan.'), 404);
  } catch (err) {
    console.error(`[server] ${req.method} ${url.pathname}: ${err.message}`);
    return sendHtml(res, errorPage(err.message), 500);
  }
});

server.listen(PORT, () => {
  console.log(`Dashboard Node berjalan di http://localhost:${PORT}`);
});

process.on('SIGINT', async () => {
  await pool.end();
  server.close(() => process.exit(0));
});
