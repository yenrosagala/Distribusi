import { formatIdNumber, MONTH_MAP, MONTH_NAMES } from './lib.js';

// ---------------------------------------------------------------------------
// Transportasi: Laporan Komparatif (port of modules/report_page.py)
// ---------------------------------------------------------------------------
export const BRS_HIERARCHY = {
  'Papua Tengah': {
    'Transportasi Udara': {
      utama: ['Douw Aturure', 'Mozes Kilangin'],
      lainnya: ['Enarotali', 'Zugapa Bilorai', 'Moanamani', 'Sinak', 'Illaga', 'Beoga', 'Mulia'],
      labelSubtotal: 'Sub Total',
      labelTotal: 'Total',
      separator: 'Bandara Lainnya',
    },
    'Transportasi Laut': {
      utama: ['Mimika', 'Nabire'], lainnya: [], labelSubtotal: '', labelTotal: 'Total', separator: '',
    },
  },
  Papua: {
    'Transportasi Laut': {
      utama: ['Jayapura', 'Biak'],
      lainnya: ['Sarmi', 'Serui', 'Waren', 'Kasonaweja'],
      labelSubtotal: 'Subtotal',
      labelTotal: 'Total',
      separator: 'Pelabuhan Lainnya',
    },
    'Transportasi Udara': {
      utama: ['Sentani', 'Frans Kaisiepo'],
      lainnya: ['Mararena', 'Stevanus Rumbewas', 'Kasonaweja'],
      labelSubtotal: 'Subtotal',
      labelTotal: 'Total',
      separator: 'Bandara Lainnya',
    },
  },
  'Papua Pegunungan': {
    'Transportasi Udara': {
      utama: ['Wamena', 'Dekai', 'Batom'],
      lainnya: ['Oksibil', 'Karubaga'],
      labelSubtotal: 'Total',
      labelTotal: 'Total Keseluruhan',
      separator: 'Bandara Lainnya',
    },
  },
  'Papua Selatan': {
    'Transportasi Laut': {
      utama: ['Merauke'],
      lainnya: ['Bade', 'Habesilam', 'Agats', 'Atsy'],
      labelSubtotal: 'Jumlah',
      labelTotal: 'Total',
      separator: 'Pelabuhan lainnya',
    },
    'Transportasi Udara': {
      utama: ['Moppah'],
      lainnya: ['Okaba', 'Tanah Merah', 'Bomakia', 'Mindiptanah', 'Kepi', 'Bade', 'Ewer', 'Kamur'],
      labelSubtotal: 'Jumlah',
      labelTotal: 'Total',
      separator: 'Bandara lainnya',
    },
  },
};

export const NARRATIVE_META = {
  penumpang_datang: { subject: 'Jumlah penumpang yang datang', satuan: 'orang', penumpang: true },
  penumpang_berangkat: { subject: 'Jumlah penumpang yang berangkat', satuan: 'orang', penumpang: true },
  barang_bongkar_kg: { subject: 'Volume barang yang dibongkar', satuan: 'ton', penumpang: false },
  barang_muat_kg: { subject: 'Volume barang yang dimuat', satuan: 'ton', penumpang: false },
  dn_penumpang_turun: { subject: 'Jumlah penumpang yang datang', satuan: 'orang', penumpang: true },
  dn_penumpang_naik: { subject: 'Jumlah penumpang yang berangkat', satuan: 'orang', penumpang: true },
  dn_bongkar_barang_ton: { subject: 'Volume barang yang dibongkar', satuan: 'ton', penumpang: false },
  dn_muat_barang_ton: { subject: 'Volume barang yang dimuat', satuan: 'ton', penumpang: false },
};

export function normalisasiEntitas(nama, moda) {
  if (nama == null) return '';
  let clean = String(nama).trim();
  if (moda === 'Transportasi Udara') {
    if (clean.toLowerCase().startsWith('bandara ')) clean = clean.slice(8).trim();
    return clean === 'Nabire' ? 'Douw Aturure' : clean;
  }
  if (clean.toLowerCase().startsWith('pelabuhan ')) clean = clean.slice(10).trim();
  return clean;
}

function safePct(curr, prev) {
  if (curr == null || prev == null) return null;
  if (!Number.isFinite(curr) || !Number.isFinite(prev) || prev === 0) return null;
  const v = ((curr - prev) / prev) * 100;
  return Number.isFinite(v) ? v : null;
}

const fmtNum = (v, dec) => (v == null || !Number.isFinite(v) ? '' : formatIdNumber(v, dec));
const fmtPct = (v) => (v == null || !Number.isFinite(v) ? 'undefined' : formatIdNumber(v, 2));

function sumBy(rows, key, col, keep, divisor) {
  const map = new Map();
  for (const r of rows) {
    if (!keep(r)) continue;
    map.set(key(r), (map.get(key(r)) || 0) + (Number(r[col]) || 0));
  }
  for (const [k, v] of map) map.set(k, v / divisor);
  return map;
}

// One report table: current vs previous month plus both cumulative series, then
// reshaped into the BRS hierarchy (utama -> subtotal -> separator -> lainnya ->
// subtotal -> rest -> total) exactly like build_brs_display_table().
export function transportReport(rows, opts) {
  const { col, label, rowCol, moda, prov, year, monthName, prevYear, prevMonthName } = opts;
  const monthNum = MONTH_MAP[monthName] || 12;
  const cumMonths = new Set(MONTH_NAMES.slice(0, monthNum));
  const divisor = moda === 'Transportasi Udara' && col.includes('kg') ? 1000 : 1;

  const series = {
    prev: sumBy(rows, (r) => r[rowCol], col, (r) => Number(r.tahun) === prevYear && r.bulan === prevMonthName, divisor),
    curr: sumBy(rows, (r) => r[rowCol], col, (r) => Number(r.tahun) === year && r.bulan === monthName, divisor),
    cumPrev: sumBy(rows, (r) => r[rowCol], col, (r) => Number(r.tahun) === year - 1 && cumMonths.has(r.bulan), divisor),
    cumCurr: sumBy(rows, (r) => r[rowCol], col, (r) => Number(r.tahun) === year && cumMonths.has(r.bulan), divisor),
  };

  const order = [];
  const seen = new Set();
  for (const s of [series.curr, series.prev, series.cumCurr, series.cumPrev]) {
    for (const k of s.keys()) {
      if (seen.has(k)) continue;
      seen.add(k);
      order.push(k);
    }
  }

  // keyed by lowercase name so the BRS hierarchy matches regardless of how the
  // source table cased the entity (Papua Tengah stores nama_kabkota uppercase)
  const grouped = new Map();
  for (const name of order) {
    const clean = normalisasiEntitas(name, moda);
    const acc = grouped.get(clean.toLowerCase()) || { name: clean, prev: 0, curr: 0, cumPrev: 0, cumCurr: 0 };
    acc.prev += series.prev.get(name) || 0;
    acc.curr += series.curr.get(name) || 0;
    acc.cumPrev += series.cumPrev.get(name) || 0;
    acc.cumCurr += series.cumCurr.get(name) || 0;
    grouped.set(clean.toLowerCase(), acc);
  }

  const entity = (name) => {
    const v = grouped.get(String(name).toLowerCase());
    if (!v) return null;
    return { name: v.name, prev: v.prev, curr: v.curr, cumPrev: v.cumPrev, cumCurr: v.cumCurr, kind: 'entity' };
  };
  const subtotal = (items, name) => ({
    name,
    prev: items.reduce((a, b) => a + b.prev, 0),
    curr: items.reduce((a, b) => a + b.curr, 0),
    cumPrev: items.reduce((a, b) => a + b.cumPrev, 0),
    cumCurr: items.reduce((a, b) => a + b.cumCurr, 0),
    kind: 'subtotal',
  });

  const cfg = BRS_HIERARCHY[prov] && BRS_HIERARCHY[prov][moda];
  const arranged = [];
  if (cfg) {
    const utama = cfg.utama.map(entity).filter(Boolean);
    arranged.push(...utama);
    if (cfg.lainnya.length && utama.length) {
      arranged.push(subtotal(utama, cfg.labelSubtotal));
      arranged.push({ name: cfg.separator, prev: null, curr: null, cumPrev: null, cumCurr: null, kind: 'other' });
      const lain = cfg.lainnya.map(entity).filter(Boolean);
      arranged.push(...lain);
      if (lain.length) arranged.push(subtotal(lain, cfg.labelSubtotal + ' '));
    }
    const known = new Set([...cfg.utama, ...cfg.lainnya].map((n) => String(n).toLowerCase()));
    for (const [key, v] of grouped) {
      if (!known.has(key)) arranged.push({ name: v.name, prev: v.prev, curr: v.curr, cumPrev: v.cumPrev, cumCurr: v.cumCurr, kind: 'entity' });
    }
  } else {
    for (const v of grouped.values()) {
      arranged.push({ name: v.name, prev: v.prev, curr: v.curr, cumPrev: v.cumPrev, cumCurr: v.cumCurr, kind: 'entity' });
    }
  }

  const totals = {
    prev: [...series.prev.values()].reduce((a, b) => a + b, 0),
    curr: [...series.curr.values()].reduce((a, b) => a + b, 0),
    cumPrev: [...series.cumPrev.values()].reduce((a, b) => a + b, 0),
    cumCurr: [...series.cumCurr.values()].reduce((a, b) => a + b, 0),
  };
  const totalRow = { name: (cfg && cfg.labelTotal) || 'TOTAL', ...totals, kind: 'total' };

  const meta = NARRATIVE_META[col] || { subject: 'Total volume', satuan: 'ton', penumpang: false };
  const dec = meta.satuan === 'orang' ? 0 : 2;
  const display = [...arranged, totalRow].map((r) => ({
    label: r.name,
    kind: r.kind,
    mtm: r.kind === 'other' ? null : safePct(r.curr, r.prev),
    yoy: r.kind === 'other' ? null : safePct(r.cumCurr, r.cumPrev),
    prev: fmtNum(r.prev, dec),
    curr: fmtNum(r.curr, dec),
    cumPrev: fmtNum(r.cumPrev, dec),
    cumCurr: fmtNum(r.cumCurr, dec),
  })).map((r) => ({ ...r, mtm: fmtPct(r.mtm), yoy: fmtPct(r.yoy) }));

  return {
    label,
    columns: {
      prev: `${prevMonthName} ${prevYear}`,
      curr: `${monthName} ${year}`,
      cumPrev: `Jan-${monthName} ${year - 1}`,
      cumCurr: `Jan-${monthName} ${year}`,
    },
    rows: display,
    total: {
      ...totalRow,
      mtm: safePct(totalRow.curr, totalRow.prev),
      yoy: safePct(totalRow.cumCurr, totalRow.cumPrev),
    },
    meta,
  };
}

export function fallbackTransportNarrative(opts) {
  const { total, meta, moda, prov, bln, thn, prevBln, prevThn } = opts;
  const small = moda === 'Transportasi Udara' ? 'udara' : 'laut';
  const dec = meta.satuan === 'orang' ? 0 : 2;
  const f = (v) => formatIdNumber(v, dec);
  const fp = (v) => formatIdNumber(Math.abs(v == null ? NaN : v), 2);
  const subject = meta.penumpang ? `${meta.subject} angkutan ${small} domestik` : meta.subject;
  const dir = (p) => (p == null ? 'tercatat' : p >= 10 ? 'mengalami lonjakan' : p > 0 ? 'meningkat'
    : p <= -10 ? 'mengalami penurunan tajam' : p < 0 ? 'menurun' : 'stabil');
  const p1 = [
    `Berdasarkan hasil pencatatan data makro sektoral, ${subject.toLowerCase()} di Provinsi ${prov} pada periode ${bln} ${thn} `
      + `membukukan realisasi agregat sebesar ${f(total.curr)} ${meta.satuan}. `
      + `Kinerja bulanan tersebut menunjukkan pergerakan yang ${dir(total.mtm)} dengan tingkat fluktuasi `
      + `sebesar ${fp(total.mtm)} persen apabila dikomparasikan terhadap baseline operasional bulan ${prevBln} ${prevThn} `
      + `yang berada di angka ${f(total.prev)} ${meta.satuan}.`,
    `Dinamika sektor transportasi mencatat bahwa ${subject.toLowerCase()} di wilayah Provinsi ${prov} `
      + `mencapai volume total ${f(total.curr)} ${meta.satuan} selama bulan ${bln} ${thn}. `
      + `Realisasi ini ${dir(total.mtm)} sebesar ${fp(total.mtm)} persen secara month-to-month (M-to-M) `
      + `dibandingkan kondisi bulan ${prevBln} ${prevThn} yang mencatatkan angka ${f(total.prev)} ${meta.satuan}.`,
  ];
  const p2 = [
    `Secara kumulatif (Year-to-Date hingga ${bln} ${thn}), akumulasi realisasi ${subject.toLowerCase()} `
      + `telah menyentuh angka ${f(total.cumCurr)} ${meta.satuan}. `
      + `Capaian ini mencatatkan tren pertumbuhan yang ${dir(total.yoy)} sebesar ${fp(total.yoy)} persen `
      + `secara Year-on-Year (Y-on-Y) jika dibandingkan dengan akumulasi periode yang sama pada tahun sebelumnya `
      + `(${f(total.cumPrev)} ${meta.satuan}), merefleksikan stabilitas aktivitas ekonomi regional.`,
    `Meninjau kinerja tahun berjalan hingga bulan ${bln} ${thn}, total realisasi kumulatif tercatat sebesar ${f(total.cumCurr)} `
      + `${meta.satuan}. Dibandingkan dengan capaian kumulatif Januari-${bln} tahun sebelumnya (${f(total.cumPrev)} ${meta.satuan}), `
      + `indikator ini ${dir(total.yoy)} di level ${fp(total.yoy)} persen secara Y-on-Y.`,
  ];
  // ponytail: Python seeds random.Random with this key; JS can't reproduce Mersenne
  // Twister from a string, so a stable FNV-1a hash picks the variant instead.
  const seed = fnv(`${prov}|${thn}|${bln}|${subject}`);
  return [p1[seed % 2], p2[(seed >>> 8) % 2]];
}

function fnv(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
}

const NUM_TOKEN = String.raw`(?<![\d.,])(?:-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:[.,]\d+)?)(?![\d])`;
const NUM_UNIT = new RegExp('(' + NUM_TOKEN + ')\\s*(%|(?:orang|penumpang|ton|persen)\\b)', 'gi');
const NUM_LOOSE = /(?<![\d.,])(-?\d{1,3}(?:\.\d{3})*,\d+)(?![\d])(?!\s*persen\b)/g;
const SATUAN_DEC = { orang: 0, ton: 2, persen: 2 };

function toFloatId(tok) {
  if (tok.includes(',')) return Number(tok.replace(/\./g, '').replace(',', '.'));
  if (/^\d{1,3}(?:\.\d{3})+$/.test(tok)) return Number(tok.replace(/\./g, ''));
  return Number(tok);
}

export function normalizeNarrative(text, colTarget) {
  if (!text) return text;
  const meta = NARRATIVE_META[colTarget] || { satuan: 'ton' };
  const defaultDec = SATUAN_DEC[meta.satuan] ?? 2;
  const out = text.replace(NUM_UNIT, (m, tok, unit) => {
    const val = toFloatId(tok);
    if (!Number.isFinite(val)) return m;
    const dec = unit === '%' ? 2 : SATUAN_DEC[unit.toLowerCase()] ?? defaultDec;
    return `${formatIdNumber(val, dec)} ${unit === '%' ? 'persen' : unit}`;
  });
  return out
    .replace(NUM_LOOSE, (m, tok) => formatIdNumber(toFloatId(tok), defaultDec))
    .replace(/\s*%/g, ' persen');
}

const IT_TERMS = /\b(?:Year-to-Date|Year-on-Year|Month-to-Month|M-to-M|Y-on-Y|YTD|YoY|MoM)\b/gi;

export function italicizeEnglish(text, html = false) {
  if (!text) return text;
  const [open, close] = html ? ['<em>', '</em>'] : ['*', '*'];
  return text.replace(IT_TERMS, (m) => `${open}${m}${close}`);
}
