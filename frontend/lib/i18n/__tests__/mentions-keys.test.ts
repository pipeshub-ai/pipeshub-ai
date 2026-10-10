import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const LOCALES = ['en-US', 'de-DE', 'en-IN', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
type Tree = { [k: string]: string | Tree };
const load = (l: string) =>
  JSON.parse(readFileSync(resolve(process.cwd(), `lib/i18n/locales/${l}.json`), 'utf8')) as {
    chat: { mentions: Tree };
    notifications: { collab: { preferences: Tree } };
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

const SCOPES: Array<[string, (l: string) => unknown]> = [
  ['chat.mentions', (l) => load(l).chat.mentions],
  ['notifications.collab.preferences', (l) => load(l).notifications.collab.preferences],
];

describe('PR-10.6: chat.mentions.* and mention preference strings in nine locales', () => {
  for (const [scope, pick] of SCOPES) {
    const en = leaves(pick('en-US'));
    it(`${scope}: en-US has keys`, () => {
      expect(en.length).toBeGreaterThan(5);
    });
    for (const locale of LOCALES) {
      it(`${scope}: ${locale} has every key with the same placeholders`, () => {
        const other = new Map(leaves(pick(locale)).map(([k, v]) => [base(k), v]));
        for (const [key, value] of en) {
          const found = other.get(base(key));
          expect(found, `${locale} is missing ${scope}.${key}`).toBeTypeOf('string');
          expect(found as string).not.toBe('');
          expect(vars(found as string), `${locale} ${scope}.${key}`).toEqual(vars(value));
        }
      });
    }
  }

  // CLDR: a plural key needs exactly the categories the locale uses (ko/zh: other only).
  for (const locale of LOCALES) {
    it(`${locale}: every chat.mentions plural key has exactly the CLDR categories`, () => {
      const wanted = [...new Intl.PluralRules(locale).resolvedOptions().pluralCategories].sort();
      const byBase = new Map<string, string[]>();
      for (const [k] of leaves(load(locale).chat.mentions)) {
        if (PLURAL.test(k)) byBase.set(base(k), [...(byBase.get(base(k)) ?? []), k.match(PLURAL)![1]!]);
      }
      expect(byBase.size, 'there is at least one plural key').toBeGreaterThan(0);
      for (const [b, cats] of byBase) expect(cats.sort(), `${locale} ${b}`).toEqual(wanted);
    });
  }

  it('ko and zh use only the other form', () => {
    for (const locale of ['ko-KR', 'zh-CN', 'zh-SG', 'zh-TW']) {
      expect(new Intl.PluralRules(locale).resolvedOptions().pluralCategories).toEqual(['other']);
    }
  });
});
