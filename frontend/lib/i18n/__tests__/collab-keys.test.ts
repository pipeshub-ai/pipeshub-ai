import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const LOCALES = ['de-DE', 'en-IN', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const load = (l: string) =>
  JSON.parse(readFileSync(resolve(process.cwd(), `lib/i18n/locales/${l}.json`), 'utf8')) as {
    chat: { collab: Record<string, unknown> };
  };

const PLURAL = /_(zero|one|two|few|many|other)$/;
function leaves(value: unknown, path = ''): Array<[string, string]> {
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([k, v]) => leaves(v, path ? `${path}.${k}` : k));
  }
  return [[path, String(value)]];
}
const vars = (s: string) => [...s.matchAll(/{{\s*([^{}\s,]+)[^{}]*}}/g)].map((m) => m[1]).sort();
const base = (k: string) => k.replace(PLURAL, '');

describe('PH09-09: chat.collab.* parity', () => {
  const en = leaves(load('en-US').chat.collab);

  it('has keys', () => {
    expect(en.length).toBeGreaterThan(20);
  });

  for (const locale of LOCALES) {
    it(`${locale} has every en-US key with the same placeholders`, () => {
      const other = new Map(leaves(load(locale).chat.collab).map(([k, v]) => [base(k), v]));
      for (const [key, value] of en) {
        const found = other.get(base(key));
        expect(found, `${locale} is missing chat.collab.${key}`).toBeTypeOf('string');
        expect(vars(found as string), `${locale} chat.collab.${key}`).toEqual(vars(value));
      }
    });
  }
});
