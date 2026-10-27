import { describe, expect, it } from 'vitest';
import i18n from 'i18next';
import en from '@/lib/i18n/locales/en-US.json';
import de from '@/lib/i18n/locales/de-DE.json';
import { addFormatters } from '../formatters';

function instance(lng: string) {
  const created = i18n.createInstance();
  void created.init({
    lng,
    resources: { 'en-US': { translation: en }, 'de-DE': { translation: de } },
    interpolation: { escapeValue: false },
    initAsync: false,
  });
  addFormatters(created);
  return created;
}

describe('lowercase formatter', () => {
  it('writes a field name mid-sentence in lower case in English', () => {
    expect(instance('en-US').t('workspace.connectors.filters.selectFieldPlaceholder', { field: 'Project' })).toBe(
      'Select project',
    );
  });

  it('leaves German nouns capitalised', () => {
    expect(instance('de-DE').t('workspace.connectors.filters.selectFieldPlaceholder', { field: 'Projekt' })).toBe(
      'Projekt auswählen',
    );
  });
});
