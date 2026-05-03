/**
 * Single source of truth for all supported languages.
 *
 * Keys match both i18next language codes and the locale filenames in
 * lib/i18n/locales/  (e.g. 'en-US' → en-US.json, 'de-DE' → de-DE.json).
 *
 * Add a new language by adding an entry here — the i18n config,
 * language store type, and menu UI all derive from this object.
 */
export const SUPPORTED_LANGUAGES = {
  'en-US': { value: 'en-US', menuName: 'English (US)' },
  'de-DE': { value: 'de-DE', menuName: 'Deutsch (Deutschland)' },
  'es-ES': { value: 'es-ES', menuName: 'Español (España)' },
  'en-IN': { value: 'en-IN', menuName: 'English (India)' },
  'hi-IN': { value: 'hi-IN', menuName: 'हिन्दी (भारत)' },
} as const;

/** Union type of all supported language codes, e.g. 'en-US' | 'de-DE' */
export type Language = keyof typeof SUPPORTED_LANGUAGES;

/** Array of language code strings, for use in i18next `supportedLngs` */
export const SUPPORTED_LNG_KEYS: string[] = Object.keys(SUPPORTED_LANGUAGES);
