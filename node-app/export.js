import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const BRIDGE = path.join(HERE, 'export_bridge.py');
const PYTHON = process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3');

export const EXPORT_MIME = {
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'idml-transport': 'application/zip',
  'idml-pariwisata': 'application/zip',
};

export function exportFileName(kind, payload) {
  const period = `${payload.bln}_${payload.thn}`;
  if (kind === 'pptx') return `dashboard_transportasi_${period}.pptx`;
  const prov = String(payload.prov || 'Papua').replace(/\s+/g, '_');
  if (kind === 'docx') return `Master_Laporan_Transportasi_${prov}_${period}.docx`;
  if (kind === 'idml-transport') return `BRS_Transportasi_${prov}_${period}.zip`;
  return `BRS_Pariwisata_${prov}_${period}.zip`;
}

// ponytail: python is required only for document exports; the dashboard itself
// stays pure Node. If that dependency ever has to go, the bridge is the single
// place to replace.
export async function runExport(kind, payload) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'dist-export-'));
  const payloadPath = path.join(dir, 'payload.json');
  const outPath = path.join(dir, 'out.bin');
  try {
    await writeFile(payloadPath, JSON.stringify(payload), 'utf8');
    const out = await new Promise((resolve, reject) => {
      const child = spawn(PYTHON, [BRIDGE, kind, payloadPath, outPath], {
        cwd: ROOT,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
        windowsHide: true,
      });
      let stderr = '';
      child.stderr.on('data', (chunk) => { stderr += chunk.toString('utf8'); });
      child.on('error', reject);
      child.on('close', (code) => {
        if (code === 0) return resolve(stderr);
        reject(new Error(stderr.trim().split(/\r?\n/).filter(Boolean).at(-1) || `ekspor gagal (kode ${code})`));
      });
    });
    if (out.trim()) console.log(`[export] ${out.trim()}`);
    return await readFile(outPath);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
