'use client';

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { SUPPORTED_LNG_KEYS, SUPPORTED_LANGUAGES } from './supported-languages';
import { locales } from './locales';

const resources = Object.fromEntries(
  (Object.keys(SUPPORTED_LANGUAGES) as (keyof typeof SUPPORTED_LANGUAGES)[]).map(
    (lang) => [lang, { translation: locales[lang] }]
  )
);

i18n
  .use(LanguageDetector) // Detect user language
  .use(initReactI18next) // Pass i18n to react-i18next
  .init({
    resources,
    fallbackLng: SUPPORTED_LANGUAGES['en-US'].value,
    supportedLngs: SUPPORTED_LNG_KEYS,
    interpolation: {
      escapeValue: false, // React already escapes
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'i18nextLng',
    },
  });

export default i18n;
