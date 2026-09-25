import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from '@/lib/i18n/locales/en-US.json';

// Importing this module makes `useTranslation()` return the real English
// strings, so tests assert on the words a user actually reads. The app's own
// config also wires a browser language detector, which tests must not depend on.
const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en-US',
  fallbackLng: 'en-US',
  resources: { 'en-US': { translation: en } },
  interpolation: { escapeValue: false },
  initAsync: false,
});

export { en };
export default testI18n;
