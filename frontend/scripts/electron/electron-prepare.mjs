/**
 * electron-prepare.mjs
 *
 * Runs after `npm run build` (Next.js static export) and before electron-builder.
 * 1. Copies the static export (out/) into electron/out/ so the Electron main
 *    process can serve it via the custom app:// protocol.
 * 2. Converts the SVG logo to a 1024×1024 PNG that Electron and electron-builder
 *    use as the application icon.
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
const LOGO_PNG = join(ELECTRON_OUT, 'logo', 'pipes-hub-1024.png');

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

// ── 2. Generate PNG icon from SVG ──────────────────────────────────────────
if (!existsSync(LOGO_SVG)) {
  console.warn('WARN: SVG logo not found at', LOGO_SVG, '— skipping icon generation.');
  process.exit(0);
}

console.log('Generating 1024×1024 PNG icon ...');
mkdirSync(dirname(LOGO_PNG), { recursive: true });

const svgBuffer = readFileSync(LOGO_SVG);
await sharp(svgBuffer, { density: 300 })
  .resize(1024, 1024, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toFile(LOGO_PNG);

console.log('Icon saved to', LOGO_PNG);
