/**
 * R41OPS-FU-7 (ADR-WEB-02): no frontend code keeps a session token where a
 * script on the page can read it. The access token is in memory, the web
 * app's refresh token an HttpOnly cookie, the desktop's in the OS keychain.
 */
import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

const ROOT = path.resolve(__dirname, '../..');
const SCANNED = ['app', 'lib', 'components', 'config.ts', 'config-pages.ts', 'electron'];
// The one module that reads the old keys, to move them out and delete them.
const MIGRATION = path.join('lib', 'auth', 'session-credential.ts');

function sources(entry: string): string[] {
  const full = path.join(ROOT, entry);
  if (!fs.existsSync(full)) return [];
  if (fs.statSync(full).isFile()) return [full];
  return fs.readdirSync(full, { withFileTypes: true }).flatMap((d) => {
    if (['node_modules', '__tests__', 'compile', 'out', '.next'].includes(d.name)) return [];
    const child = path.join(entry, d.name);
    if (d.isDirectory()) return sources(child);
    return /\.(ts|tsx)$/.test(d.name) && !/\.test\.tsx?$/.test(d.name) ? [path.join(ROOT, child)] : [];
  });
}

const files = SCANNED.flatMap(sources).map((file) => ({
  rel: path.relative(ROOT, file),
  text: fs.readFileSync(file, 'utf8'),
}));

/** A write to web storage whose key or value names a token, in a call that fits on a few lines. */
const TOKEN_WRITE = /(localStorage|sessionStorage)\s*\.\s*setItem\s*\(([^;]{0,240}?)\)/g;
const TOKENISH = /(access_?token|refresh_?token|accessToken|refreshToken|jwt|bearer)/i;

export function tokenWrites(text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(TOKEN_WRITE)) {
    if (TOKENISH.test(match[2])) found.push(match[0].replace(/\s+/g, ' '));
  }
  return found;
}

describe('no session token in localStorage or sessionStorage', () => {
  it('scans the frontend sources', () => {
    expect(files.length).toBeGreaterThan(500);
    expect(files.some((f) => f.rel === MIGRATION)).toBe(true);
  });

  it('no source writes a token into web storage', () => {
    const offenders = files.flatMap((f) => tokenWrites(f.text).map((w) => `${f.rel}: ${w}`));
    expect(offenders).toEqual([]);
  });

  it('only the migration names the old jwt_* keys', () => {
    const offenders = files
      .filter((f) => f.rel !== MIGRATION && /jwt_(access|refresh)_token/.test(f.text))
      .map((f) => f.rel);
    expect(offenders).toEqual([]);
  });

  it('the rule catches the writes the old store made', () => {
    expect(tokenWrites("window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken);")).toHaveLength(1);
    expect(tokenWrites("localStorage.setItem('jwt_refresh_token', t)")).toHaveLength(1);
    expect(tokenWrites("sessionStorage.setItem(key, refreshToken)")).toHaveLength(1);
    expect(tokenWrites("localStorage.setItem(SESSION_HINT_KEY, value)")).toHaveLength(0);
  });
});
