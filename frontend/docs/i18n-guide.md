# i18n Guide

## File Naming Convention

Locale files use BCP 47 language tags: `<language>-<REGION>.json`

```
lib/i18n/locales/
├── en-US.json     # English (United States)
├── de-DE.json     # German (Germany)
└── index.ts       # Barrel — imports and exports all locales
```

## Adding a New Language

### 1. Create the locale file

Copy an existing file as a base and translate the values (not the keys):

```
lib/i18n/locales/fr-FR.json
```

### 2. Register the language

In `lib/i18n/supported-languages.ts`, add an entry:

```ts
export const SUPPORTED_LANGUAGES = {
  'en-US': { value: 'en-US', menuName: 'English (US)' },
  'de-DE': { value: 'de-DE', menuName: 'Deutsch (Deutschland)' },
  'fr-FR': { value: 'fr-FR', menuName: 'Français (France)' }, // ← add this
} as const;
```

### 3. Add to the locales barrel

In `lib/i18n/locales/index.ts`:

```ts
import frFR from './fr-FR.json';

export const locales: Record<Language, unknown> = {
  'en-US': enUS,
  'de-DE': deDE,
  'fr-FR': frFR, // ← add this
};
```

That's it — the i18n config, language store type, and switcher UI all derive from `SUPPORTED_LANGUAGES` automatically.
