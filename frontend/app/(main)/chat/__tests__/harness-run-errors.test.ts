import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { runErrorText } from '../harness-errors';

const LOCALES = ['de-DE', 'en-IN', 'en-US', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW'];
const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'run-error-codes.json'), 'utf8'),
) as { codes: string[] };

const catalogue = (locale: string): Record<string, string> =>
  (JSON.parse(readFileSync(join(__dirname, '..', '..', '..', '..', 'lib', 'i18n', 'locales', `${locale}.json`), 'utf8')) as {
    chatStream: { runError: Record<string, string> };
  }).chatStream.runError;

describe('QA17-U1: a run a degraded dependency ended is worded by the web app in every locale', () => {
  const start = i18n.language;
  afterAll(async () => {
    await i18n.changeLanguage(start);
  });

  it.each(VECTOR.codes)('%s', async (code) => {
    for (const locale of LOCALES) {
      await i18n.changeLanguage(locale);
      const text = runErrorText(code);
      expect(text, `${locale} ${code}`).not.toBeNull();
      expect(Object.values(catalogue(locale)), `${locale} ${code}`).toContain(text);
    }
  });
});
