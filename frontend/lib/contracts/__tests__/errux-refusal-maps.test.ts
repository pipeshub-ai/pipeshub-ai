import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { harnessCodesOf } from '@/lib/contracts/harness/codes';
import { admissionRefusalText } from '@/app/(main)/chat/harness-errors';
import { PUBLICATION_REFUSALS } from '@/app/(main)/knowledge-base/publications/api';
import { SCHEDULE_REFUSALS } from '@/app/(main)/workspace/schedules/copy';

/** EU-21 (ERR-UX, QA-7 open item): every refusal the web app words is a catalogued code, and every one is worded. */

const LOCALES = ['de-DE', 'en-IN', 'en-US', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];

const resolves = (locale: string, key: string): boolean => {
  let node: unknown = JSON.parse(readFileSync(join(__dirname, '..', '..', 'i18n', 'locales', `${locale}.json`), 'utf8'));
  for (const part of key.split('.')) node = (node as Record<string, unknown> | undefined)?.[part];
  return typeof node === 'string' && node.length > 0;
};

const MAPS: Array<[string, Readonly<Record<string, string>>]> = [
  ['schedule', SCHEDULE_REFUSALS],
  ['publication', PUBLICATION_REFUSALS],
];

describe('EU-21: the refusal maps are the catalogue\'s groups', () => {
  it.each(MAPS)('%s: one key per catalogued code, each worded in every locale', (group, map) => {
    expect(Object.keys(map).sort()).toEqual([...harnessCodesOf(group)].sort());
    for (const key of Object.values(map)) {
      for (const locale of LOCALES) expect(resolves(locale, key), `${locale} ${key}`).toBe(true);
    }
  });

  const start = i18n.language;
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it.each(['quota', 'turn', 'model_access', 'model_account', 'engine_profile', 'run_config'])(
    'admission refusals of group %s have their own words, never the generic failure',
    async (group) => {
      for (const locale of LOCALES) {
        await i18n.changeLanguage(locale);
        for (const code of harnessCodesOf(group)) {
          const text = admissionRefusalText(`HTTP_AGENT_HARNESS_${code}`);
          expect(text, `${locale} ${code}`).not.toBe(i18n.t('chatStream.admission.failed'));
        }
      }
    },
  );
});
