import { describe, expect, it } from 'vitest';
import i18n from 'i18next';
import { locales } from '../locales';
import { SUPPORTED_LANGUAGES, SUPPORTED_LNG_KEYS } from '../supported-languages';

function instance(lng: string) {
  const created = i18n.createInstance();
  void created.init({
    lng,
    resources: Object.fromEntries(SUPPORTED_LNG_KEYS.map((key) => [key, { translation: locales[key as keyof typeof locales] }])),
    interpolation: { escapeValue: false },
    initAsync: false,
  });
  return created;
}

describe('locale registry', () => {
  it('ships a catalogue for every supported language', () => {
    for (const key of SUPPORTED_LNG_KEYS) {
      expect(locales[key as keyof typeof locales], key).toBeTypeOf('object');
    }
    expect(SUPPORTED_LANGUAGES['cs-CZ'].menuName).toBe('Čeština (Česko)');
  });

  it('renders Czech with its own text instead of the English fallback', () => {
    const t = instance('cs-CZ').t;
    expect(t('common.save')).toBe('Uložit');
    expect(t('chat.newChat')).not.toBe(instance('en-US').t('chat.newChat'));
  });

  it('picks the Czech plural form for 1, 2–4, fractions and 5+', () => {
    const t = instance('cs-CZ').t;
    const forms = [1, 2, 2.5, 5].map((count) => t('projects.chatCount', { count }));
    expect(forms).toEqual(['1 chat', '2 chaty', '2.5 chatu', '5 chatů']);
  });

  it('WP70-PLURAL: the sub-task merge line counts its files in every language ("1 file", never "1 files")', () => {
    const en = instance('en-US').t;
    expect(en('durableRun.merge.merged', { count: 1, revision: 3 })).toBe('1 file merged into workspace version 3.');
    expect(en('durableRun.merge.merged', { count: 2, revision: 3 })).toBe('2 files merged into workspace version 3.');
    const cs = instance('cs-CZ').t;
    expect([1, 3, 5].map((count) => cs('durableRun.merge.merged', { count, revision: 4 }))).toEqual([
      '1 soubor sloučen do verze pracovního prostoru 4.',
      '3 soubory sloučeny do verze pracovního prostoru 4.',
      '5 souborů sloučeno do verze pracovního prostoru 4.',
    ]);
    for (const key of SUPPORTED_LNG_KEYS) {
      const t = instance(key).t;
      for (const count of [1, 2, 7]) {
        const line = t('durableRun.merge.merged', { count, revision: 9 });
        expect(line, `${key} ${count}`).toContain(String(count));
        expect(line, `${key} ${count}`).not.toContain('durableRun.merge');
      }
    }
  });
});
