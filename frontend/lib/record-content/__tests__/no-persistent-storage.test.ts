/**
 * No record content reaches a place that outlives the tab: Cache Storage, IndexedDB, service
 * workers, the storage manager, or Web Storage. The scan covers the cache module and every file that
 * holds a lease on its content or reads a preview's blob/url. A new match outside the allowlist fails.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = join(__dirname, '..', '..', '..');
const FORBIDDEN = /\bcaches\.|\bindexedDB\b|navigator\.serviceWorker|navigator\.storage\b|\b(?:local|session)Storage\.setItem|\.setItem\(/;

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '.next' || name === '__tests__') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

// Each entry says what the file stores and why it is not content.
const ALLOWED: Record<string, string> = {
  'app/components/file-preview/use-pdf-zoom.ts': 'zoom preference',
  'app/components/file-preview/use-citations-column-resize.ts': 'column width',
  'app/components/file-preview/file-preview-sidebar.tsx': 'panel width',
  'app/(main)/chat/page.tsx': 'preview panel width (CHAT_PANEL_LS_KEY)',
  'app/(main)/chat/store.ts': 'agent capability toggles',
};

describe('record content stays in memory', () => {
  const files = walk(join(root, 'lib', 'record-content')).concat(
    walk(join(root, 'app')).filter((f) => {
      const text = readFileSync(f, 'utf8');
      return /@\/lib\/record-content|previewFile\.(blob|url)|\bLease\b/.test(text);
    }),
    walk(join(root, 'app', 'components', 'file-preview')),
  );

  it('scans a meaningful set of files', () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it.each([...new Set(files)].map((f) => [relative(root, f), f]))('%s', (rel, full) => {
    const hit = FORBIDDEN.exec(readFileSync(full, 'utf8'));
    if (ALLOWED[rel]) return;
    expect(hit?.[0]).toBeUndefined();
  });
});
