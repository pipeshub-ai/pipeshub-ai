import { describe, expect, it } from 'vitest';
import { createInstance } from 'i18next';
import { validatePassword } from '../validators';
import en from '@/lib/i18n/locales/en-US.json';
import de from '@/lib/i18n/locales/de-DE.json';

describe('password validation', () => {
  it.each([
    ['Ab1!abc', 'minLength'],
    ['ABCDEFG1!', 'lowercase'],
    ['abcdefg1!', 'uppercase'],
    ['Abcdefgh!', 'number'],
    ['Abcdefg1', 'symbol'],
  ])('returns a stable code for %s', (password, code) => {
    expect(validatePassword(password)).toBe(code);
  });

  it('preserves the eight-character boundary and existing symbol policy', () => {
    expect(validatePassword('Abcdef1!')).toBeNull();
    expect(validatePassword('Abcdef1 ')).toBeNull();
    expect(validatePassword('Abcdef1ä')).toBeNull();
  });

  it('renders validation errors in the selected language without changing validation', async () => {
    const i18n = createInstance();
    await i18n.init({ lng: 'de-DE', fallbackLng: 'en-US', resources: {
      'en-US': { translation: en }, 'de-DE': { translation: de },
    } });
    const code = validatePassword('short');
    expect(i18n.t(`validation.password.${code}`)).toBe('Das Passwort muss mindestens 8 Zeichen lang sein.');
    await i18n.changeLanguage('en-US');
    expect(i18n.t(`validation.password.${code}`)).toBe('Password must be at least 8 characters.');
  });
});
