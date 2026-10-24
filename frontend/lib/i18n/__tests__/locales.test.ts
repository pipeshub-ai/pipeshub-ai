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
});
