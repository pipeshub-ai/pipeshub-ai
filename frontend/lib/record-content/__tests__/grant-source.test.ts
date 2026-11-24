/**
 * A grant is minted in one place only: the details call of an open. A caller that could mint one
 * could claim a label hit without the server's answer for this open.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = join(__dirname, '..', '..', '..');

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '.next' || name === '__tests__') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

describe('grants', () => {
  it('mintGrant is referenced only by its module and getRecordDetailsForOpen', () => {
    const users = [...walk(join(root, 'app')), ...walk(join(root, 'lib'))]
      .filter((f) => /\bmintGrant\b/.test(readFileSync(f, 'utf8')))
      .map((f) => relative(root, f))
      .sort();
    expect(users).toEqual(['app/(main)/knowledge-base/api.ts', 'lib/record-content/grant.ts']);
    const api = readFileSync(join(root, 'app/(main)/knowledge-base/api.ts'), 'utf8');
    const calls = api.match(/\bmintGrant\(/g) ?? [];
    expect(calls).toHaveLength(1);
    const start = api.indexOf('async getRecordDetailsForOpen');
    expect(start).toBeGreaterThan(-1);
    const next = api.indexOf('\n  async ', start + 10);
    const body = api.slice(start, next === -1 ? undefined : next);
    expect(body).toContain('mintGrant(');
  });

  it('openRecordContent takes no details object', () => {
    const index = readFileSync(join(root, 'lib/record-content/index.ts'), 'utf8');
    const signature = /export function openRecordContent\(([^)]*)\)/.exec(index)?.[1] ?? '';
    expect(signature).toContain('grant?: Grant');
    expect(signature).not.toMatch(/details/i);
    expect(index).not.toMatch(/export[^;]*mintGrant/);
  });

  it('nothing under lib/record-content uses fetch(', () => {
    for (const f of walk(join(root, 'lib', 'record-content'))) {
      expect(readFileSync(f, 'utf8')).not.toMatch(/(?<![.\w])fetch\(/);
    }
  });
});
