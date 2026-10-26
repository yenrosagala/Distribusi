import { INV_MONTH_MAP, MONTH_NAMES, escapeHtml, formatIdNumber, monthNameEn, periodLabel } from './lib.js';
import { italicizeEnglish } from './lib-transport.js';

const CHARTS_CDN = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';

function chartScript(charts) {
  const json = JSON.stringify(charts).replace(/</g, '\\u003c');
  return `<script src="${CHARTS_CDN}"></script>
<script>
(function () {
  if (typeof Chart === 'undefined') return;
  var CFG = ${json};
  var nf = new Intl.NumberFormat('id-ID');
  CFG.forEach(function (c) {
    var el = document.getElementById(c.id);
    if (!el) return;
    var line = c.type === 'line';
    new Chart(el, {
      type: c.type || 'bar',
      data: {
        labels: c.labels,
        datasets: c.datasets.map(function (d) {
          return line
            ? { label: d.label, data: d.data, borderColor: d.color, backgroundColor: d.color, tension: 0.25, pointRadius: 3 }
            : { label: d.label, data: d.data, backgroundColor: d.color, borderRadius: 4 };
        })
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: !line, grid: { display: false } },
          y: { stacked: !line, beginAtZero: true, ticks: { callback: function (v) { return nf.format(v); } } }
        },
        plugins: {
          legend: { position: 'bottom' },
          title: { display: true, text: c.title, font: { size: 13, weight: '600' } },
          tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + ': ' + nf.format(ctx.parsed.y); } } }
        }
      }
    });
  });
})();
</script>`;
}

function layout({ title, body, user, section, scripts = '' }) {
  const nav = [
    ['pariwisata', 'Pariwisata'],
    ['transportasi', 'Transportasi'],
  ].map(([id, label]) => (
    `<a class="side-link${section === id ? ' active' : ''}" href="/${id}">${label}</a>`
  )).join('');

  return `<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)} · Dashboard Papua</title>
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/app.css">
</head>
<body>
<div class="app-shell">
  <aside class="app-side">
    <a class="brand-block" href="/pariwisata">
      <img class="brand-logo" src="/logo.png" alt="Logo">
      <div>
        <p class="sidebar-title">Pariwisata &amp; Transportasi Papua</p>
        <p class="sidebar-subtitle">Dashboard Statistik</p>
      </div>
    </a>
    <nav class="side-nav">${nav}</nav>
    <div class="side-foot">
      <div class="user-card"><b>USER:</b> ${escapeHtml(user.name)}<br><b>ROLE:</b> ${user.role.toUpperCase()}</div>
      <form method="post" action="/logout"><button class="btn btn-ghost" type="submit">Logout</button></form>
      <p class="side-note">Mode baca-saja. Unggah data, regenerasi narasi AI, dan ekspor hanya tersedia di aplikasi Streamlit.</p>
    </div>
  </aside>
  <main class="app-main">
${body}
    <div class="app-footer">Pariwisata &amp; Transportasi Papua · BPS Provinsi Papua</div>
  </main>
</div>
${scripts}
</body>
</html>`;
}

export function loginPage({ error = '' } = {}) {
  return `<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login · Dashboard Papua</title>
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/app.css">
</head>
<body>
<div class="login-wrap">
  <div class="login-card">
    <div class="brand-block">
      <img class="brand-logo" src="/logo.png" alt="Logo">
      <div>
        <p class="sidebar-title">Pariwisata &amp; Transportasi Papua</p>
        <p class="sidebar-subtitle">Dashboard Statistik</p>
      </div>
    </div>
    <h1 class="hero-title">Selamat Datang</h1>
    <p class="login-sub">Masuk untuk mengakses dashboard statistik Pariwisata dan Transportasi Provinsi Papua.</p>
    ${error ? `<div class="alert alert-error">${escapeHtml(error)}</div>` : ''}
    <form method="post" action="/login" class="login-form">
      <label class="field"><span>Username</span><input name="username" autocomplete="username" required></label>
      <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
      <button class="btn btn-primary" type="submit">Masuk</button>
    </form>
    <p class="login-foot">Akses terbatas untuk analis data dan administrator portal.</p>
  </div>
</div>
</body>
</html>`;
}

function growthTable(caption, rows, labels) {
  const head = labels.map((l) => `<th>PERKEMBANGAN ${l} ${escapeHtml(caption)} (%)</th>`).join('');
  const body = rows.map((row) => `<tr><td><b>${escapeHtml(row.provinsi)}</b></td>${row.values.map((v) => (
    `<td>${v == null
      ? '<span class="stat-delta-na">&mdash;</span>'
      : `<span class="${v >= 0 ? 'pos' : 'neg'}">${formatIdNumber(v)}</span>`}</td>`
  )).join('')}</tr>`).join('');
  return `<table class="data-table"><thead><tr><th>PROVINSI</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function rawTable(rows, caption) {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  return `<details class="raw-block"><summary>${escapeHtml(caption)}</summary>
<div class="table-scroll"><table class="data-table"><thead><tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>
<tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${escapeHtml(r[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
}

function periodSelect(periods, selected) {
  const value = `${selected.thn}-${selected.bln}`;
  const options = periods.map((p) => {
    const v = `${p.thn}-${p.bln}`;
    return `<option value="${escapeHtml(v)}"${v === value ? ' selected' : ''}>${escapeHtml(periodLabel(p.thn, p.bln))}</option>`;
  }).join('');
  return `<form method="get" action="/transportasi" class="toolbar">
  <label class="field-inline"><span>Periode</span><select name="periode" onchange="this.form.submit()">${options}</select></label>
  <noscript><button class="btn btn-primary" type="submit">Tampilkan</button></noscript>
</form>`;
}

function sectionCard(section) {
  if (section.empty) {
    return `<section class="dashboard-card">
  <div class="section-header"><div><div class="section-title">PERKEMBANGAN TRANSPORTASI ${section.moda.toUpperCase()}</div></div></div>
  <div class="alert alert-warn">Belum ada data Transportasi ${escapeHtml(section.moda)} untuk periode ${escapeHtml(section.periode)}.</div>
</section>`;
  }

  const charts = section.charts.map((c) => `<div class="chart-box"><canvas id="${c.id}"></canvas></div>`).join('');
  const paras = section.narrative.paras.map((p) => `<p>${escapeHtml(p)}</p>`).join('');

  return `<section class="dashboard-card">
  <div class="section-header">
    <div>
      <div class="section-title">PERKEMBANGAN TRANSPORTASI ${section.moda.toUpperCase()}</div>
      <div class="section-subtitle">Periode ${escapeHtml(section.periode)} &middot; perbandingan bulanan dan kumulatif</div>
    </div>
  </div>
  <div class="chart-grid">${charts}</div>
  <div class="table-grid">
    <div>${growthTable(section.periodeLabel, section.growthP, section.labelsP)}</div>
    <div>${growthTable(section.periodeLabel, section.growthB, section.labelsB)}</div>
  </div>
  <div class="narrative">
    <div class="narrative-head"><h3>Ringkasan Naratif</h3><span class="source-tag">${escapeHtml(section.narrative.source)}</span></div>
    ${paras}
  </div>
  ${rawTable(section.raw, `Data detail Transportasi ${section.moda} — ${section.periode}`)}
</section>`;
}

function brsTable(item) {
  const head = `<tr><th>${escapeHtml(item.rowLabel)}</th><th>${escapeHtml(item.columns.prev)}</th>`
    + `<th>${escapeHtml(item.columns.curr)}</th><th>M-to-M (%)</th>`
    + `<th>${escapeHtml(item.columns.cumPrev)}</th><th>${escapeHtml(item.columns.cumCurr)}</th>`
    + '<th>Y-on-Y (%)</th></tr>';
  const cell = (v, kind) => {
    if (kind === 'other') return '';
    if (v === '') return '<td></td>';
    const neg = v.startsWith('-');
    const cls = v === 'undefined' ? ' class="muted"' : neg ? ' class="num-neg"' : '';
    return `<td${cls}>${v}</td>`;
  };
  const body = item.rows.map((r) => {
    if (r.kind === 'other') {
      return `<tr class="brs-separator"><th colspan="7">${escapeHtml(r.label)}</th></tr>`;
    }
    const cls = r.kind === 'subtotal' ? ' class="brs-subtotal"' : '';
    return `<tr${cls}><th>${escapeHtml(r.label)}</th>${cell(r.prev, r.kind)}${cell(r.curr, r.kind)}`
      + `${cell(r.mtm, r.kind)}${cell(r.cumPrev, r.kind)}${cell(r.cumCurr, r.kind)}${cell(r.yoy, r.kind)}</tr>`;
  }).join('');
  return `<div class="table-scroll"><table class="data-table brs-table">
  <thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

function transportNarrativeBox(item, isAdmin) {
  const fields = `<input type="hidden" name="kind" value="transportasi"><input type="hidden" name="provinsi" value="${escapeHtml(item.prov)}"><input type="hidden" name="tahun" value="${escapeHtml(item.tahun)}"><input type="hidden" name="bulan" value="${escapeHtml(item.bulan)}"><input type="hidden" name="moda" value="${escapeHtml(item.moda)}"><input type="hidden" name="label" value="${escapeHtml(item.label)}">`;
  const regen = isAdmin
    ? `<form method="post" action="/api/narrative/regenerate" class="inline-form">${fields}<button class="btn btn-secondary" type="submit">🔄 Regenerasi</button></form>`
    : '';
  if (!item.narrative) {
    if (isAdmin) {
      return `<div class="alert alert-info">Narasi AI belum dibuat untuk tabel ini.<form method="post" action="/api/narrative/generate" class="inline-form">${fields}<button class="btn btn-primary" type="submit">🤖 Generate Narasi AI</button></form></div>`;
    }
    return `<div class="alert alert-info">${escapeHtml('Narasi AI belum tersedia untuk tabel ini. Hubungi admin untuk membuatnya.')}</div>`;
  }
  const paras = item.narrative.paras
    .map((p) => `<p>${italicizeEnglish(escapeHtml(p), true)}</p>`)
    .join('');
  return `<div class="ai-note"><strong>📝 Executive Summary — ${escapeHtml(item.label)}</strong>`
    + `<p class="muted">Executive Summary — ${escapeHtml(item.narrative.source)}</p>${paras}${regen}</div>`;
}

function transportReportTab(report, isAdmin) {
  const control = `<form method="get" action="/transportasi" class="toolbar">
  <input type="hidden" name="tab" value="report">
  <label class="field-inline"><span>Provinsi</span><select name="provinsi" onchange="this.form.submit()">
    ${report.provinces.map((p) => option(p, p, p === report.province)).join('')}
  </select></label>
  <label class="field-inline"><span>Tahun</span><select name="tahun" onchange="this.form.submit()">
    ${report.years.map((y) => option(y, String(y), y === report.year)).join('')}
  </select></label>
  <label class="field-inline"><span>Bulan</span><select name="bulan" onchange="this.form.submit()">
    ${report.months.map((m) => option(m, m, m === report.monthName)).join('')}
  </select></label>
  <noscript><button class="btn btn-primary" type="submit">Tampilkan</button></noscript>
</form>`;

  const head = `<h2 class="hero-title">📋 Laporan Komparatif Strategis</h2>${control}`;  if (report.warning) {
    return `${head}<section class="dashboard-card"><div class="alert alert-warn">${escapeHtml(report.warning)}</div></section>`;
  }
  const groups = report.groups.map((group) => {
    const cards = group.items.map((item) => `<section class="dashboard-card report-card">
  <div class="card-header"><span>${escapeHtml(item.title)}</span></div>
  ${brsTable(item)}
  ${transportNarrativeBox(item, isAdmin)}
</section>`).join('');
    return `<h3 class="section-title">${group.icon} Moda: ${escapeHtml(group.moda)}</h3>${cards}`;
  }).join('');
  const exportCard = `<section class="dashboard-card">
  <h2>📥 Unduh Laporan</h2>
  <form method="post" action="/export/transportasi/idml" enctype="multipart/form-data" class="admin-form">
    <input type="hidden" name="provinsi" value="${escapeHtml(report.province)}">
    <input type="hidden" name="tahun" value="${report.year}">
    <input type="hidden" name="bulan" value="${escapeHtml(report.monthName)}">
    <div class="admin-grid">
      <label>Infografis (PNG/JPG) <input type="file" name="infografis" accept="image/png,image/jpeg"></label>
      <label>Kode QR (PNG/JPG) <input type="file" name="qr" accept="image/png,image/jpeg"></label>
      <label>Nomor BRS <input name="nomor" placeholder="mis. 235/10/94/Th. XXIX"></label>
      <label>Tanggal Rilis <input name="tanggal" placeholder="mis. 1 Oktober 2026"></label>
    </div>
    <div class="metric-row">
      <a class="btn btn-secondary" href="/export/transportasi/docx?provinsi=${encodeURIComponent(report.province)}&tahun=${report.year}&bulan=${encodeURIComponent(report.monthName)}">📄 Unduh Word (Master Laporan)</a>
      <button class="btn btn-primary" type="submit">🎨 Unduh Paket InDesign (.zip)</button>
    </div>
  </form>
</section>`;
  return head + groups + exportCard;
}

function seriesChart(series) {
  const palette = ['#ED7D31', '#70AD47', '#4472C4', '#A5A5A5', '#FFC000', '#5B9BD5', '#264478', '#9E480E'];
  return {
    id: 'admin-series-chart',
    type: 'line',
    title: series.title,
    labels: series.periods,
    datasets: series.series.map((s, i) => ({ label: s.name, data: s.data, color: palette[i % palette.length] })),
  };
}

function checkList(name, options, values, idPrefix) {
  return options.map((opt, i) => `<label class="check-row">
      <input type="checkbox" name="${name}" value="${escapeHtml(opt)}"${values.includes(opt) ? ' checked' : ''} id="${idPrefix}${i}">
      <span>${escapeHtml(opt.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()))}</span>
    </label>`).join('');
}

function adminSeriesPanel(admin) {
  const { moda, kabupaten, entities, variabel, series, kabupatenList } = admin.seriesForm;
  const chart = series && !series.empty ? `<div class="chart-frame"><canvas id="admin-series-chart"></canvas></div>` : '';
  const table = series && !series.empty
    ? `<details class="admin-raw"><summary>📋 Lihat Tabel Data Mentah di Balik Grafik</summary>
    <table class="data-table"><thead><tr><th>Periode</th><th>Seri</th><th class="num">Nilai</th></tr></thead>
    <tbody>${series.rows.map((r) => `<tr><td>${escapeHtml(r.Periode)}</td><td>${escapeHtml(r.Seri)}</td><td class="num">${formatIdNumber(r.Nilai, 2)}</td></tr>`).join('')}</tbody></table></details>`
    : '';
  const warning = series && series.empty
    ? '<div class="alert alert-warn">⚠️ Tidak ditemukan data yang cocok dengan kombinasi filter Anda.</div>'
    : '';

  return `<section class="dashboard-card">
  <h2>📊 Filter Analisis Tren</h2>
  <form method="get" action="/transportasi" class="admin-form">
    <input type="hidden" name="tab" value="admin">
    <input type="hidden" name="sub" value="series">
    <div class="admin-grid">
      <label>Moda Transportasi
        <select name="moda">${['Transportasi Udara', 'Transportasi Laut'].map((m) => `<option value="${m}"${m === moda ? ' selected' : ''}>${m}</option>`).join('')}</select>
      </label>
      <label>Kabupaten/Kota
        <select name="kabupaten"><option value="SEMUA">SEMUA</option>${kabupatenList.map((k) => `<option value="${escapeHtml(k)}"${k === kabupaten ? ' selected' : ''}>${escapeHtml(k)}</option>`).join('')}</select>
      </label>
    </div>
    <fieldset class="check-grid"><legend>${admin.seriesForm.entityLabel} (kosongkan untuk agregat)</legend>${checkList('entity', entities.options, entities.selected, 'ent')}</fieldset>
    <fieldset class="check-grid"><legend>Indikator Variabel</legend>${checkList('variabel', variabel.options, variabel.selected, 'var')}</fieldset>
    <button type="submit" class="btn btn-primary">📈 Buat Visualisasi Grafik</button>
  </form>
  ${warning}${chart}${table}
</section>`;
}

function adminUploadPanel(admin) {
  const { stage, files, preview, message } = admin.upload;
  const flash = message ? `<div class="alert ${message.ok ? 'alert-info' : 'alert-warn'}">${escapeHtml(message.text)}</div>` : '';

  if (preview) {
    const cards = preview.map((p, i) => {
      if (p.error) {
        return `<article class="admin-file"><h4>⚠️ ${escapeHtml(p.nama_file)} (${escapeHtml(p.bulan)} ${p.tahun})</h4>
          <div class="alert alert-warn">Gagal memproses file: ${escapeHtml(p.error)}</div></article>`;
      }
      const modaLabel = p.table_type === 'transportasi_udara' ? '✈️ Transportasi Udara' : '🚢 Transportasi Laut';
      const note = p.existing_rows > 0
        ? `<div class="alert alert-warn">⚠️ Terdapat <strong>${p.existing_rows} baris data eksisting</strong> untuk periode ${escapeHtml(p.bulan)} ${p.tahun}. Data lama akan ditimpa jika opsi aktif.</div>`
        : '<div class="alert alert-info">✅ Periode ini bersih (belum ada data pada database).</div>';
      const s = p.stats;
      return `<article class="admin-file">
        <h4>${modaLabel} — ${escapeHtml(p.nama_file)} (${escapeHtml(p.bulan)} ${p.tahun})</h4>
        ${note}
        <div class="metric-row">
          <div class="metric"><span>Jumlah Baris</span><strong>${formatIdNumber(s.jumlah_baris, 0)}</strong></div>
          <div class="metric"><span>Total Penumpang</span><strong>${formatIdNumber(s.total_penumpang, 0)}</strong></div>
          <div class="metric"><span>Total Barang</span><strong>${formatIdNumber(s.total_barang, 2)} ${s.satuan_barang}</strong></div>
          <div class="metric"><span>Lokasi (${s.label_lokasi})</span><strong>${s.jumlah_lokasi}</strong></div>
        </div>
      </article>`;
    }).join('');
    const anyValid = preview.some((p) => !p.error);
    const confirm = anyValid ? `<div class="admin-confirm">
      <input type="hidden" name="stage" value="${escapeHtml(stage)}">
      <label class="check-row"><input type="checkbox" name="overwrite" value="1" checked><span>Timpa data lama jika periode sama sudah tersedia</span></label>
      <label class="check-row"><input type="checkbox" name="confirm" value="1" required><span>Saya telah memverifikasi statistik di atas dan siap menyimpannya ke database utama.</span></label>
      <button type="submit" class="btn btn-primary">✅ Proses &amp; Simpan ke Database</button>
    </div>` : '';
    return `<section class="dashboard-card">${flash}<h2>📥 Langkah 2 &amp; 3: Ringkasan, Validasi &amp; Simpan</h2>
  <form method="post" action="/transportasi/admin/save">${cards}${confirm}</form>
  <p class="muted"><a href="/transportasi?tab=admin&amp;sub=upload">↩︎ Mulai ulang unggahan</a></p></section>`;
  }

  if (stage && files.length) {
    const rows = files.map((f, i) => `<tr>
      <td><strong>📄 ${escapeHtml(f.nama_file)}</strong><br><span class="muted">${f.table_type === 'transportasi_udara' ? 'Moda: ✈️ Transportasi Udara' : f.table_type === 'transportasi_laut' ? 'Moda: 🚢 Transportasi Laut' : '<span class="num-neg">Format header tidak dikenali.</span>'}</span></td>
      <td><input type="number" name="tahun_${i}" value="${f.tahun}" min="2020" max="2035" required></td>
      <td><select name="bulan_${i}">${MONTH_NAMES.map((m) => `<option value="${m}"${m === f.bulan ? ' selected' : ''}>${m}</option>`).join('')}</select></td>
    </tr>`).join('');
    const validCount = files.filter((f) => f.table_type).length;
    return `<section class="dashboard-card">${flash}<h2>📥 Langkah 1: Verifikasi Deteksi Otomatis</h2>
  <form method="post" action="/transportasi/admin/preview">
    <input type="hidden" name="stage" value="${escapeHtml(stage)}">
    <table class="data-table admin-files"><thead><tr><th>Berkas</th><th>Tahun</th><th>Bulan</th></tr></thead><tbody>${rows}</tbody></table>
    ${validCount ? '<button type="submit" class="btn btn-primary">🔍 Analisis &amp; Pratinjau Berkas</button>' : '<div class="alert alert-warn">Tidak ada berkas dengan format yang dikenali.</div>'}
  </form></section>`;
  }

  return `<section class="dashboard-card">${flash}
  <h2>📥 Unggah Data BPS Baru</h2>
  <p class="muted">Unggah satu atau beberapa berkas Excel/HTML BPS sekaligus (.xls, .xlsx). Sistem mendeteksi metadata secara otomatis.</p>
  <form method="post" action="/transportasi/admin/upload" enctype="multipart/form-data" class="admin-form">
    <input type="file" name="files" accept=".xls,.xlsx,.html,.htm" multiple required>
    <button type="submit" class="btn btn-primary">⬆️ Unggah &amp; Deteksi</button>
  </form></section>`;
}

function adminEditPanel(admin) {
  const { table, tahun, bulan, tsv, message, months } = admin.edit;
  const flash = message ? `<div class="alert ${message.ok ? 'alert-info' : 'alert-warn'}">${escapeHtml(message.text)}</div>` : '';
  return `<section class="dashboard-card">${flash}
  <h2>🛠️ Koreksi Data Tabel Langsung</h2>
  <form method="get" action="/transportasi" class="admin-form">
    <input type="hidden" name="tab" value="admin"><input type="hidden" name="sub" value="edit">
    <div class="admin-grid">
      <label>Moda Transportasi
        <select name="moda">${['transportasi_udara', 'transportasi_laut'].map((t) => `<option value="${t}"${t === table ? ' selected' : ''}>${t === 'transportasi_udara' ? 'Transportasi Udara' : 'Transportasi Laut'}</option>`).join('')}</select>
      </label>
      <label>Tahun Periode <input type="number" name="tahun" value="${tahun}" min="2020" max="2035" required></label>
      <label>Bulan Periode
        <select name="bulan">${months.map((m) => `<option value="${m}"${m === bulan ? ' selected' : ''}>${m}</option>`).join('')}</select>
      </label>
    </div>
    <button type="submit" class="btn btn-primary">🔍 Muat Data untuk Diedit</button>
  </form>
  ${tsv == null ? '' : tsv === '' ? '<div class="alert alert-warn">Data tidak ditemukan untuk periode tersebut.</div>' : `
  <form method="post" action="/transportasi/admin/edit" class="admin-form">
    <input type="hidden" name="moda" value="${table}">
    <input type="hidden" name="tahun" value="${tahun}">
    <input type="hidden" name="bulan" value="${bulan}">
    <p class="muted">Sunting nilai pada kotak di bawah (pemisah TAB), lalu simpan. Seluruh baris periode ini diganti isi kotak.</p>
    <textarea name="tsv" rows="18" spellcheck="false">${escapeHtml(tsv)}</textarea>
    <button type="submit" class="btn btn-primary">💾 Simpan Perubahan Baris</button>
  </form>`}</section>`;
}

function adminDeletePanel(admin) {
  const { moda, province, tahun, bulan, years, provinces, months, count, message } = admin.del;
  const flash = message ? `<div class="alert ${message.ok ? 'alert-info' : 'alert-warn'}">${escapeHtml(message.text)}</div>` : '';
  return `<section class="dashboard-card">${flash}
  <h2>⚠️ Zona Pemeliharaan &amp; Penghapusan Data</h2>
  <p class="muted">Hapus data berdasarkan kriteria filter tertentu untuk membersihkan entri yang salah atau duplikat.</p>
  <form method="get" action="/transportasi" class="admin-form">
    <input type="hidden" name="tab" value="admin"><input type="hidden" name="sub" value="delete">
    <div class="admin-grid">
      <label>Moda Transportasi
        <select name="moda">${['Transportasi Udara', 'Transportasi Laut'].map((m) => `<option value="${m}"${m === moda ? ' selected' : ''}>${m}</option>`).join('')}</select>
      </label>
      <label>Provinsi
        <select name="province"><option value="SEMUA">SEMUA</option>${provinces.map((p) => `<option value="${escapeHtml(p)}"${p === province ? ' selected' : ''}>${escapeHtml(p)}</option>`).join('')}</select>
      </label>
      <label>Tahun
        <select name="year"><option value="SEMUA">SEMUA</option>${years.map((y) => `<option value="${escapeHtml(y)}"${y === tahun ? ' selected' : ''}>${escapeHtml(y)}</option>`).join('')}</select>
      </label>
      <label>Bulan
        <select name="month"><option value="SEMUA">SEMUA</option>${months.map((m) => `<option value="${m}"${m === bulan ? ' selected' : ''}>${m}</option>`).join('')}</select>
      </label>
    </div>
    <button type="submit" class="btn btn-primary">🔍 Hitung Data yang Akan Dihapus</button>
  </form>
  ${count == null ? '' : `<form method="post" action="/transportasi/admin/delete" class="admin-form">
    <input type="hidden" name="moda" value="${escapeHtml(moda)}">
    <input type="hidden" name="province" value="${escapeHtml(province)}">
    <input type="hidden" name="year" value="${escapeHtml(tahun)}">
    <input type="hidden" name="month" value="${escapeHtml(bulan)}">
    <div class="alert ${count ? 'alert-warn' : 'alert-info'}">${count ? `Akan menghapus <strong>${formatIdNumber(count, 0)} baris</strong> dari ${escapeHtml(moda)}.` : 'Tidak ada baris yang cocok dengan filter.'}</div>
    ${count ? '<label class="check-row"><input type="checkbox" name="confirm" value="1" required><span>Saya yakin ingin menghapus data ini secara permanen.</span></label><button type="submit" class="btn btn-danger">🗑 Hapus Data Terfilter</button>' : ''}
  </form>`}</section>`;
}

export function transportAdminTab(admin) {
  const sub = admin.sub || 'series';
  const tabs = [
    { key: 'series', label: '📊 Analisis Tren' },
    { key: 'upload', label: '📥 Ingest Data (Upload)' },
    { key: 'edit', label: '🛠️ Koreksi Manual' },
    { key: 'delete', label: '⚠️ Pengaturan & Hapus' },
  ];
  const nav = tabs.map((t) => `<a class="tab${sub === t.key ? ' active' : ''}" href="/transportasi?tab=admin&amp;sub=${t.key}">${t.label}</a>`).join('');
  const panel = sub === 'upload' ? adminUploadPanel(admin)
    : sub === 'edit' ? adminEditPanel(admin)
      : sub === 'delete' ? adminDeletePanel(admin)
        : adminSeriesPanel(admin);
  const chart = sub === 'series' && admin.seriesForm.series && !admin.seriesForm.series.empty
    ? chartScript([seriesChart(admin.seriesForm.series)])
    : '';
  return {
    body: `<div class="admin-shell">
  <div class="admin-hero"><h1>📈 Pusat Analisis &amp; Manajemen Data</h1>
    <p>Jelajahi tren data transportasi antar periode secara bebas. Gunakan panel di bawah untuk mengunggah, mengoreksi, atau menghapus data.</p></div>
  <div class="tabs">${nav}</div>
  ${panel}
  <div class="admin-logout"><form method="post" action="/logout"><button type="submit" class="btn btn-secondary">🚪 Keluar (Log Out)</button></form></div>
</div>`,
    scripts: chart,
  };
}

export function transportasiPage({ user, periods, selected, sections, tab = 'dashboard', report = null, admin = null }) {
  const periodQuery = selected ? `?periode=${selected.thn}-${selected.bln}` : '';
  const tabs = [
    { label: '📊 Dashboard Statistik', href: `/transportasi${periodQuery}`, active: tab === 'dashboard' },
    { label: '📄 Laporan Komparatif', href: `/transportasi?tab=report${selected ? `&periode=${selected.thn}-${selected.bln}` : ''}`, active: tab === 'report' },
  ];
  if (user.role === 'admin') {
    tabs.push({ label: '🔐 Admin & Analisis Series', href: '/transportasi?tab=admin', active: tab === 'admin' });
  }

  const header = `<div class="section-hero">
  <div>
    <div class="section-title">Transportasi</div>
    <div class="section-subtitle">Arus penumpang dan barang empat provinsi di Tanah Papua.</div>
  </div>
</div>${tabNav(tabs)}`;

  let content;
  let scripts = '';
  if (tab === 'report') {
    content = report
      ? transportReportTab(report, user.role === 'admin')
      : '<section class="dashboard-card"><div class="alert alert-warn">Pilih periode laporan terlebih dahulu.</div></section>';
  } else if (tab === 'admin') {
    const adminView = transportAdminTab(admin || {});
    content = adminView.body;
    scripts = adminView.scripts;
  } else if (periods.length) {
    const deck = selected
      ? `<div class="toolbar"><a class="btn btn-primary" href="/export/transportasi/pptx?periode=${selected.thn}-${selected.bln}">📊 Unduh Deck PowerPoint</a></div>`
      : '';
    content = `${periodSelect(periods, selected)}${deck}${sections.map(sectionCard).join('')}`;
  } else {
    content = `<section class="dashboard-card"><div class="alert alert-warn">Belum ada data transportasi di database. Unggah data lewat tab Admin &amp; Analisis Series terlebih dahulu.</div></section>`;
  }

  const body = header + content;
  const charts = tab === 'dashboard' ? sections.flatMap((s) => s.charts || []) : [];
  return layout({
    title: 'Transportasi',
    body,
    user,
    section: 'transportasi',
    scripts: charts.length ? chartScript(charts) : scripts,
  });
}

export function pariwisataPage({ user, info, periods, tab = 'home', map = null, report = null }) {
  const latest = periods.at(-1);
  const numberFmt = (v, unit = '') => (v == null || !Number.isFinite(Number(v))
    ? '&mdash;'
    : `${formatIdNumber(v, 1)}${unit}`);

  const tabs = [
    { label: '🏠 Home Dashboard', href: '/pariwisata', active: tab === 'home' },
    { label: '🗺️ Infographic Stat Map', href: '/pariwisata?tab=map', active: tab === 'map' },
    { label: '📋 Report', href: '/pariwisata?tab=report', active: tab === 'report' },
  ];
  if (user.role === 'admin') {
    tabs.push({ label: '🛠️ Admin ETL Uploads', href: '/pariwisata?tab=admin', active: tab === 'admin' });
  }

  let body = `<div class="section-hero">
  <div>
    <div class="section-title">Pariwisata</div>
    <div class="section-subtitle">Tingkat penghunian kamar dan rata-rata lama menginap.</div>
  </div>
</div>${tabNav(tabs)}`;

  if (!info.rows.length || !latest) {
    body += `<section class="dashboard-card"><div class="alert alert-warn">Belum ada data pariwisata yang dimuat. Jika Anda admin, unggah matriks Excel pertama lewat tab Admin ETL Uploads.</div></section>`;
    return layout({ title: 'Pariwisata', body, user, section: 'pariwisata' });
  }

  const scripts = [];
  if (tab === 'map') {
    body += mapTab(map);
    scripts.push(mapScript());
  } else if (tab === 'report' && report) {
    const parts = reportTab(report, user.role === 'admin');
    body += parts.html;
    if (parts.configs.length) scripts.push(reportScript(parts.configs));
  } else if (tab === 'admin') {
    body += `<section class="dashboard-card"><div class="alert alert-warn">Tab Admin belum dimuat.</div></section>`;
  } else {
    body += `<h2 class="hero-title">Selamat datang, ${escapeHtml(user.name)}</h2>
<div class="metric-grid">
  <div class="metric-card"><span class="stat-label">Provinsi Tercakup</span><span class="metric-value">${info.provCount}</span></div>
  <div class="metric-card"><span class="stat-label">Periode Terakhir</span><span class="metric-value">${escapeHtml(periodLabel(latest.thn, latest.bln))}</span></div>
  <div class="metric-card"><span class="stat-label">Rata-rata TPK (terakhir)</span><span class="metric-value">${numberFmt(info.metrics?.tpk, '%')}</span></div>
  <div class="metric-card"><span class="stat-label">Rata-rata RLMTGAB (terakhir)</span><span class="metric-value">${numberFmt(info.metrics?.rlmtgab, ' malam')}</span></div>
</div>
<section class="dashboard-card">
  <div class="card-header"><h3>Cakupan Data</h3><span>${info.rows.length} baris</span></div>
  <p>Data mencakup <b>${escapeHtml(periodLabel(periods[0].thn, periods[0].bln))}</b> sampai
  <b>${escapeHtml(periodLabel(latest.thn, latest.bln))}</b>, meliputi ${info.provCount} provinsi
  dengan klasifikasi Hotel Bintang dan Non Bintang.</p>
</section>`;
  }

  return layout({
    title: 'Pariwisata',
    body,
    user,
    section: 'pariwisata',
    scripts: scripts.join(''),
  });
}

export function tabNav(tabs) {
  return `<nav class="tabs">${tabs.map((t) => (
    `<a class="tab${t.active ? ' active' : ''}" href="${escapeHtml(t.href)}">${escapeHtml(t.label)}</a>`
  )).join('')}</nav>`;
}

function provinceCard(card, unit) {
  const rows = card.rows.map((r) => {
    const value = r.value == null ? '&mdash;' : `${formatIdNumber(r.value, 2)}${unit}`;
    const delta = r.delta == null
      ? '<span class="stat-delta-na">&ndash; N/A</span>'
      : `<span class="${r.delta >= 0 ? 'badge-up' : 'badge-down'}">${r.delta >= 0 ? '▲' : '▼'} ${Math.abs(r.delta).toFixed(1)}%</span>`;
    return `<div class="stat-row"><div><span class="stat-label">${escapeHtml(r.label)}</span><span class="stat-value">${value}</span></div>${delta}</div>`;
  }).join('');
  return `<div class="province-card"><div class="province-header">${escapeHtml(card.province)}</div>${rows}</div>`;
}

function mapTab(map) {
  if (!map || map.empty) {
    return `<section class="dashboard-card"><div class="alert alert-warn">No records match this period yet. Try a different month/year, or ask an admin to ingest data for this range in Admin ETL Uploads.</div></section>`;
  }

  const option = (value, label, selected) => `<option value="${escapeHtml(String(value))}"${selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  const control = `<form method="get" action="/pariwisata" class="toolbar">
  <input type="hidden" name="tab" value="map">
  <label class="field-inline"><span>Indikator</span><select name="indikator" onchange="this.form.submit()">
    ${option('tpk', 'TPK (Occupancy Rate)', map.indicator === 'tpk')}
    ${option('rlmtgab', 'RLMTGAB (Length of Stay)', map.indicator === 'rlmtgab')}
  </select></label>
  <label class="field-inline"><span>Tahun</span><select name="tahun" onchange="this.form.submit()">
    ${map.years.map((y) => option(y, String(y), y === map.year)).join('')}
  </select></label>
  <label class="field-inline"><span>Bulan</span><select name="bulan" onchange="this.form.submit()">
    ${map.months.map((m) => option(m, monthNameEn(m), m === map.month)).join('')}
  </select></label>
  <noscript><button class="btn btn-primary" type="submit">Tampilkan</button></noscript>
</form>`;

  const left = map.cards.filter((c) => ['Papua Tengah', 'Papua Selatan'].includes(c.province));
  const right = map.cards.filter((c) => ['Papua', 'Papua Pegunungan'].includes(c.province));
  const csvUrl = `/export/pariwisata-map.csv?indikator=${encodeURIComponent(map.indicator)}&tahun=${map.year}&bulan=${map.month}`;

  return `<h2 class="hero-title">Papua Regional Performance — ${escapeHtml(map.monthName)} ${map.year}</h2>
${control}
<div class="map-layout">
  <div>${left.map((c) => provinceCard(c, map.unit)).join('')}</div>
  <div>
    <div class="map-panel">
      <div class="map-panel-title">Papua Regional Performance — ${escapeHtml(map.monthName)} ${map.year}</div>
      <div id="papua-svg" class="map-stage" data-values="${escapeHtml(JSON.stringify(map.values))}" data-labels="${escapeHtml(JSON.stringify(map.labels))}" data-title="${escapeHtml(map.indicator)}"></div>
      <div class="map-legend">
        <span>${escapeHtml(map.indicatorLabel)} (${escapeHtml(map.unit.trim())})</span>
        <i class="map-ramp"></i>
        <span>${map.lo == null ? '&mdash;' : formatIdNumber(map.lo, 2)}</span>
        <span>${map.hi == null ? '&mdash;' : formatIdNumber(map.hi, 2)}</span>
      </div>
    </div>
    <a class="btn" href="${escapeHtml(csvUrl)}">📥 Download Summary CSV</a>
  </div>
  <div>${right.map((c) => provinceCard(c, map.unit)).join('')}</div>
</div>`;
}

function option(value, label, selected) {
  return `<option value="${escapeHtml(String(value))}"${selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
}

function narrativeBox(entry, isAdmin) {
  const fields = `<input type="hidden" name="kind" value="pariwisata"><input type="hidden" name="provinsi" value="${escapeHtml(entry.prov || entry.province)}"><input type="hidden" name="tahun" value="${entry.tahun || entry.year}"><input type="hidden" name="bulan" value="${entry.bulan || entry.month}"><input type="hidden" name="jenis_akomodasi" value="${escapeHtml(entry.jenis || entry.jenis_akomodasi)}"><input type="hidden" name="indikator" value="${escapeHtml(entry.indikator || entry.indicator)}">`;
  if (entry.narrative) {
    const paras = entry.narrative.split(/\n{2,}/).map((p) => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`).join('');
    const regen = isAdmin
      ? `<form method="post" action="/api/narrative/regenerate" class="inline-form">${fields}<button class="btn btn-secondary" type="submit">🔄 Regenerasi</button></form>`
      : '';
    return `<div class="ai-note"><strong>🤖 AI Narrative (from database):</strong>${paras}<div class="narrative-actions">${regen}</div></div>`;
  }
  if (isAdmin) {
    return `<div class="alert alert-info">Narasi AI belum dibuat untuk indikator ini.<form method="post" action="/api/narrative/generate" class="inline-form">${fields}<button class="btn btn-primary" type="submit">🤖 Generate Narasi AI</button></form></div>`;
  }
  return `<div class="alert alert-info">${escapeHtml('Narasi AI belum tersedia untuk indikator ini. Hubungi admin untuk membuatnya.')}</div>`;
}

function valueGrid(series) {
  const months = series.labels.map((label, i) => ({ label, i }))
    .filter(({ i }) => series.prev[i] != null || series.cur[i] != null);
  if (!months.length) return '';
  const cell = (v) => (v == null ? '' : formatIdNumber(v, 2));
  const row = (label, values) => `<tr><th>${escapeHtml(label)}</th>${months.map(({ i }) => `<td>${cell(values[i])}</td>`).join('')}</tr>`;
  return `<div class="table-scroll"><table class="data-table yoy-grid">
  <thead><tr><th>Tahun</th>${months.map(({ label }) => `<th>${escapeHtml(label)}</th>`).join('')}</tr></thead>
  <tbody>${row(String(series.prevYear), series.prev)}${row(String(series.year), series.cur)}</tbody>
</table></div>`;
}

function reportTab(report, isAdmin) {
  const control = `<form method="get" action="/pariwisata" class="toolbar">
  <input type="hidden" name="tab" value="report">
  <label class="field-inline"><span>Provinsi</span><select name="provinsi" onchange="this.form.submit()">
    ${report.provinces.map((p) => option(p, p, p === report.province)).join('')}
  </select></label>
  <label class="field-inline"><span>Tahun</span><select name="tahun" onchange="this.form.submit()">
    ${report.years.map((y) => option(y, String(y), y === report.year)).join('')}
  </select></label>
  <label class="field-inline"><span>Bulan</span><select name="bulan" onchange="this.form.submit()">
    ${report.months.map((m) => option(m, INV_MONTH_MAP[m], m === report.month)).join('')}
  </select></label>
  <noscript><button class="btn btn-primary" type="submit">Tampilkan</button></noscript>
</form>`;

  const head = `<h2 class="hero-title">Laporan &amp; Narasi AI</h2>${control}`;
  if (report.warning) {
    return { html: `${head}<section class="dashboard-card"><div class="alert alert-warn">${escapeHtml(report.warning)}</div></section>`, configs: [] };
  }
  if (!report.indicators.length) {
    return { html: `${head}<section class="dashboard-card"><div class="alert alert-warn">No data found matching parameters for Province: ${escapeHtml(report.province)}.</div></section>`, configs: [] };
  }

  const configs = [];
  const cards = report.indicators.map((ind) => ind.jenis.map((entry) => {
    const table = `<div class="table-scroll"><table class="data-table">
  <thead><tr><th>Kelas Akomodasi</th><th>${escapeHtml(entry.table.columns.last)}</th><th>${escapeHtml(entry.table.columns.prev)}</th><th>${escapeHtml(entry.table.columns.cur)}</th><th>${escapeHtml(entry.table.columns.dPrev)}</th><th>${escapeHtml(entry.table.columns.dLast)}</th></tr></thead>
  <tbody>${entry.table.body.map((r) => `<tr><td>${escapeHtml(r.label)}</td><td>${r.last}</td><td>${r.prev}</td><td>${r.current}</td><td>${r.dPrev}</td><td>${r.dLast}</td></tr>`).join('')}</tbody>
  <tfoot><tr><th>Total</th><td>${entry.table.total.last}</td><td>${entry.table.total.prev}</td><td>${entry.table.total.current}</td><td>${entry.table.total.dPrev}</td><td>${entry.table.total.dLast}</td></tr></tfoot>
</table></div>`;
    const canvasId = `yoy-${ind.key}-${entry.jenis.replace(/\s+/g, '-')}`;
    configs.push({
      id: canvasId,
      series: entry.series,
      unit: ind.unit,
      axis: `${ind.short} (${ind.unit.trim()})`,
      curColor: '#ED7D31',
      prevColor: '#70AD47',
    });
    return `<section class="dashboard-card report-card">
  <div class="card-header"><h3>Indicator: ${escapeHtml(ind.key.toUpperCase())} — ${escapeHtml(entry.jenis)}</h3></div>
  ${narrativeBox(entry, isAdmin)}
  ${table}
  <div class="chart-frame" style="height:340px"><canvas id="${canvasId}"></canvas></div>
  ${valueGrid(entry.series)}
</section>`;
  }).join('')).join('');

  return { html: `${head}${cards}${pariwisataExportCard(report)}`, configs };
}

function pariwisataExportCard(report) {
  return `<section class="dashboard-card">
  <h2>📥 Unduh BRS Pariwisata (InDesign)</h2>
  <form method="post" action="/export/pariwisata/idml" class="admin-form" enctype="multipart/form-data">
    <input type="hidden" name="provinsi" value="${escapeHtml(report.province)}">
    <input type="hidden" name="tahun" value="${report.year}">
    <input type="hidden" name="bulan" value="${escapeHtml(report.month)}">
    <div class="admin-grid">
      <label>Infografis (PNG/JPG) <input type="file" name="infografis" accept="image/png,image/jpeg"></label>
      <label>Kode QR (PNG/JPG) <input type="file" name="qr" accept="image/png,image/jpeg"></label>
      <label>Nomor BRS <input name="nomor" placeholder="mis. 235/10/94/Th. XXIX"></label>
      <label>Tanggal Rilis <input name="tanggal" placeholder="mis. 1 Oktober 2026"></label>
    </div>
    <div class="metric-row">
      <button class="btn btn-primary" type="submit">🎨 Unduh Paket InDesign (.zip)</button>
    </div>
  </form>
</section>`;
}

function reportScript(configs) {
  const json = JSON.stringify(configs).replace(/</g, '\\u003c');
  return `<script src="${CHARTS_CDN}"></script>
<script>
(function () {
  if (typeof Chart === 'undefined') return;
  var CFG = ${json};
  var nf = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 2 });
  CFG.forEach(function (c) {
    var el = document.getElementById(c.id);
    if (!el) return;
    new Chart(el, {
      type: 'line',
      data: {
        labels: c.series.labels,
        datasets: [
          { label: String(c.series.prevYear), data: c.series.prev, borderColor: c.prevColor, backgroundColor: c.prevColor, pointRadius: 3, borderWidth: 2, tension: 0.2 },
          { label: String(c.series.year), data: c.series.cur, borderColor: c.curColor, backgroundColor: c.curColor, pointRadius: 3, borderWidth: 2, tension: 0.2 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: false, title: { display: true, text: c.axis }, ticks: { callback: function (v) { return nf.format(v); } } }
        },
        plugins: {
          legend: { position: 'bottom', title: { display: true, text: 'Tahun' } },
          tooltip: { callbacks: { label: function (ctx) { return 'Tahun ' + ctx.dataset.label + ': ' + nf.format(ctx.parsed.y) + c.unit; } } }
        }
      }
    });
  });
})();
</script>`;
}

function mapScript() {  return `<script>
(async function () {
  var host = document.getElementById('papua-svg');
  if (!host) return;
  var values = JSON.parse(host.dataset.values || '{}');
  var labels = JSON.parse(host.dataset.labels || '{}');
  var res = await fetch('/papua_provinces.json');
  if (!res.ok) { host.textContent = 'Map geometry file (papua_provinces.json) could not be loaded.'; return; }
  var fc = await res.json();
  var pts = [];
  (function walk(node) {
    if (typeof node[0] === 'number') { pts.push(node); return; }
    for (var i = 0; i < node.length; i++) walk(node[i]);
  })(fc.features.map(function (f) { return f.geometry.coordinates; }));
  var lonMin = Infinity, lonMax = -Infinity, latMin = Infinity, latMax = -Infinity;
  pts.forEach(function (p) {
    if (p[0] < lonMin) lonMin = p[0];
    if (p[0] > lonMax) lonMax = p[0];
    if (p[1] < latMin) latMin = p[1];
    if (p[1] > latMax) latMax = p[1];
  });
  var W = 780, k = Math.cos(((latMin + latMax) / 2) * Math.PI / 180);
  var scale = W / ((lonMax - lonMin) * k);
  var H = Math.round((latMax - latMin) * scale);
  function xy(p) { return [((p[0] - lonMin) * k * scale).toFixed(1), ((latMax - p[1]) * scale).toFixed(1)]; }
  function ringPath(ring) {
    var d = '';
    for (var i = 0; i < ring.length; i++) {
      var q = xy(ring[i]);
      d += (i ? 'L' : 'M') + q[0] + ' ' + q[1];
    }
    return d + 'Z';
  }
  var nums = Object.keys(values).map(function (p) { return values[p]; }).filter(function (v) { return typeof v === 'number'; });
  var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums);
  var stops = [[58, 42, 14], [245, 158, 11], [253, 230, 138]];
  function fill(v) {
    if (typeof v !== 'number' || hi === lo) return '#9CA3AF';
    var t = (v - lo) / (hi - lo), a = t < 0.5 ? 0 : 1, local = t < 0.5 ? t * 2 : (t - 0.5) * 2;
    var c = [0, 1, 2].map(function (i) {
      return Math.round(stops[a][i] + (stops[a + 1][i] - stops[a][i]) * local);
    });
    return 'rgb(' + c.join(',') + ')';
  }
  var ns = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Peta statistik ' + host.dataset.title);
  fc.features.forEach(function (f) {
    var name = f.properties.PROVINSI, g = f.geometry;
    var polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
    var d = polys.map(function (poly) { return ringPath(poly[0]); }).join(' ');
    var path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', fill(values[name]));
    path.setAttribute('stroke', '#0B0F14');
    path.setAttribute('stroke-width', '1');
    var t = document.createElementNS(ns, 'title');
    t.textContent = name + ': ' + (labels[name] || 'tidak ada data');
    path.appendChild(t);
    svg.appendChild(path);
  });
  fc.features.forEach(function (f) {
    var name = f.properties.PROVINSI;
    if (!labels[name]) return;
    var g = f.geometry;
    var ring = g.type === 'Polygon' ? g.coordinates[0] : g.coordinates[0][0];
    var sx = 0, sy = 0;
    ring.forEach(function (p) { var q = xy(p); sx += +q[0]; sy += +q[1]; });
    var tx = document.createElementNS(ns, 'text');
    tx.setAttribute('x', (sx / ring.length).toFixed(1));
    tx.setAttribute('y', (sy / ring.length).toFixed(1));
    tx.setAttribute('text-anchor', 'middle');
    tx.setAttribute('class', 'map-label');
    tx.textContent = name;
    svg.appendChild(tx);
  });
  host.appendChild(svg);
})();
</script>`;
}

export function errorPage(message) {
  return `<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terjadi kesalahan</title>
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/app.css">
</head>
<body>
<div class="login-wrap">
  <div class="login-card">
    <h1 class="hero-title">Terjadi kesalahan</h1>
    <div class="alert alert-error">${escapeHtml(message)}</div>
    <p><a class="btn btn-ghost" href="/pariwisata">Kembali ke dashboard</a></p>
  </div>
</div>
</body>
</html>`;
}

