/**
 * electron-prepare.mjs
 *
 * Runs after `npm run build` (Next.js static export) and before electron-builder.
 * 1. Copies the static export (out/) into electron/out/ so the Electron main
 *    process can serve it via the custom app:// protocol.
 * 2. Converts the SVG logo to PNGs (256 / 512 / 1024): the app window uses 256px
 *    for a title-bar weight closer to the web favicon; electron-builder uses 1024.
 */

import { cpSync, mkdirSync, existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import sharp from 'sharp';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');

const SRC_OUT = join(ROOT, 'out');
const ELECTRON_OUT = join(ROOT, 'electron', 'out');
const LOGO_SVG = join(ROOT, 'public', 'logo', 'pipes-hub.svg');
const LOGO_DIR = join(ELECTRON_OUT, 'logo');
const LOGO_SIZES = [
  { name: 'pipes-hub-256.png', size: 256 },
  { name: 'pipes-hub-512.png', size: 512 },
  { name: 'pipes-hub-1024.png', size: 1024 },
];

// ── 1. Copy static export ──────────────────────────────────────────────────
if (!existsSync(SRC_OUT)) {
  console.error(
    'ERROR: out/ directory not found. For Electron, run `npm run build:electron` first (static export), then `npm run electron:prepare`.',
  );
  process.exit(1);
}

console.log('Copying static export to electron/out/ ...');
cpSync(SRC_OUT, ELECTRON_OUT, { recursive: true });
console.log('Done.');

// ── 2. Generate PNG icons from SVG (window uses 256 for OS bar; builder keeps 1024) ──
if (!existsSync(LOGO_SVG)) {
  console.warn('WARN: SVG logo not found at', LOGO_SVG, '— skipping icon generation.');
  process.exit(0);
}

console.log('Generating PNG icons (256 / 512 / 1024) ...');
mkdirSync(LOGO_DIR, { recursive: true });

const svgBuffer = readFileSync(LOGO_SVG);
for (const { name, size } of LOGO_SIZES) {
  const outPath = join(LOGO_DIR, name);
  await sharp(svgBuffer, { density: 300 })
    .resize(size, size, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png()
    .toFile(outPath);
  console.log('Icon saved to', outPath);
}
