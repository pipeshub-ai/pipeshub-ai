/**
 * Barrel for all locale translation files.
 *
 * When adding a new language:
 *   1. Add the entry to lib/i18n/supported-languages.ts
 *   2. Create the locale JSON file (e.g. fr.json)
 *   3. Add an import + entry here
 *
 * config.ts iterates this object to build the i18next resources map,
 * so no further changes are needed there.
 */
import enUS from './en-US.json';
import deDE from './de-DE.json';
import esES from './es-ES.json';
import enIN from './en-IN.json';
import hiIN from './hi-IN.json';

import type { Language } from '../supported-languages';

export const locales: Record<Language, unknown> = {
  'en-US': enUS,
  'de-DE': deDE,
  'es-ES': esES,
  'en-IN': enIN,
  'hi-IN': hiIN,
};
