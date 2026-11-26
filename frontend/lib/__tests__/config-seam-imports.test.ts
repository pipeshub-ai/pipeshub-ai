/**
 * `@/config` is the frontend's extension point: an edition may re-export each
 * of its names from a different module. Code that imports one of those names
 * from the module behind it reads that module's copy, not the configured one.
 * For a store that means two stores, and the code reading the wrong one sees
 * no user, no org and no drafts.
 *
 * Source code takes every configured name from `@/config` (the API client is
 * the exception: `@/lib/api` is its own barrel, and the client reads the
 * session through `@/config`). Tests take configured store names from
 * `@/config` too, so the state a test sets is the state the code under test
 * reads.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..', '..');
const SCANNED = ['app', 'lib', 'components'];

type Seam = Map<string, Set<string>>;

/** Module specifier -> names config.ts re-exports from it under their own name. Commented blocks count too. */
export function configuredNames(configSource: string): Seam {
  const source = configSource
    .split('\n')
    .map((line) => line.replace(/^\s*\/\/\s?/, ''))
    .join('\n');
  const seam: Seam = new Map();
  for (const m of source.matchAll(/export\s+(?:type\s+)?\{([^}]*)\}\s*from\s*'([^']+)'/g)) {
    const names = seam.get(m[2]) ?? new Set<string>();
    for (const raw of m[1].split(',')) {
      const part = raw.trim().replace(/^type\s+/, '');
      if (!part) continue;
      const [local, exported = local] = part.split(/\s+as\s+/).map((s) => s.trim());
      if (local === exported) names.add(local);
    }
    seam.set(m[2], names);
  }
  return seam;
}

export interface Bypass {
  file: string;
  module: string;
  names: string[];
}

const IMPORT = /import\s+(?:type\s+)?\{([^}]*)\}\s*from\s*'([^']+)'/g;
const DYNAMIC = /const\s*\{([^}]*)\}\s*=\s*await\s+import\(\s*'([^']+)'\s*\)/g;

function importedNames(list: string): string[] {
  return list
    .split(',')
    .map((s) => s.trim().replace(/^type\s+/, '').split(/\s+as\s+|\s*:\s*/)[0].trim())
    .filter(Boolean);
}

const isTest = (file: string) => /(^|\/)__tests__\//.test(file) || /\.test\.tsx?$/.test(file);

/** Imports in one file that take a configured name from the module behind `@/config`. */
export function bypassesIn(file: string, source: string, seam: Seam): Bypass[] {
  const own = `@/${file.replace(/\.tsx?$/, '').replace(/\/index$/, '')}`;
  const found: Bypass[] = [];
  // A test that replaces `@/config` itself decides what the code under test reads.
  if (isTest(file) && /vi\.mock\(\s*'@\/config'/.test(source)) return found;
  for (const pattern of [IMPORT, DYNAMIC]) {
    for (const m of source.matchAll(pattern)) {
      const target = m[2];
      const configured = seam.get(target);
      if (!configured || target === own) continue;
      if (target.startsWith('@/lib/api/')) continue;
      if (isTest(file) && !target.startsWith('@/lib/store/')) continue;
      // A module's own unit tests, next to it, test that module and not the configured one.
      if (isTest(file) && path.posix.dirname(file.replace(/\/__tests__\/.*$/, '/x')) === path.posix.dirname(target.slice(2))) continue;
      const names = importedNames(m[1]).filter((n) => configured.has(n));
      if (names.length) found.push({ file, module: target, names });
    }
  }
  return found;
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sourceFiles(full));
    else if (/\.tsx?$/.test(entry.name) && !entry.name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

describe('the @/config extension point', () => {
  const seam = configuredNames(fs.readFileSync(path.join(ROOT, 'config.ts'), 'utf8'));

  it('names at least the auth and org stores', () => {
    expect(seam.get('@/lib/store/auth-store')?.has('useAuthStore')).toBe(true);
    expect(seam.get('@/lib/store/org-store')?.has('useOrgStore')).toBe(true);
  });

  it('is the only way shared code reaches a configured name', () => {
    const bypasses = SCANNED.filter((d) => fs.existsSync(path.join(ROOT, d)))
      .flatMap((d) => sourceFiles(path.join(ROOT, d)))
      .flatMap((full) => {
        const file = path.relative(ROOT, full).split(path.sep).join('/');
        // This file's own examples are strings, not imports.
        if (full === __filename) return [];
        return bypassesIn(file, fs.readFileSync(full, 'utf8'), seam);
      })
      .map((b) => `${b.file}: ${b.names.join(', ')} from ${b.module}`);
    expect(bypasses).toEqual([]);
  });

  it('flags a direct store import in source and in a test, and leaves a mock factory alone', () => {
    const s: Seam = new Map([['@/lib/store/auth-store', new Set(['useAuthStore'])]]);
    expect(bypassesIn('app/x.ts', "import { useAuthStore } from '@/lib/store/auth-store';", s)).toHaveLength(1);
    expect(
      bypassesIn('app/__tests__/x.test.ts', "const { useAuthStore } = await import('@/lib/store/auth-store');", s),
    ).toHaveLength(1);
    expect(
      bypassesIn(
        'app/__tests__/x.test.ts',
        "const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');",
        s,
      ),
    ).toEqual([]);
    expect(bypassesIn('app/x.ts', "import { ACCESS_TOKEN_STORAGE_KEY } from '@/lib/store/auth-store';", s)).toEqual([]);
  });
});
