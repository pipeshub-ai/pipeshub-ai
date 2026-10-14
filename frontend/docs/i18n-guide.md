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

## Maintaining translations

`en-US.json` is the structural source of truth. Add new UI strings to it and to
every other locale, and reuse existing keys where appropriate. Keep interpolation
names unchanged.

German uses formal **Sie** and the terms **Arbeitsbereich**, **Konnektor**,
**Service-Agent**, **Skill**, **Tool**, **Toolset**, **MCP-Server**, **OAuth-App**
and **Reasoning-Aufwand**. Product names, protocol names and third-party service
names stay as they are in every language.

### Plural forms

Each language needs the plural categories CLDR defines for it, not the ones
English happens to use:

| Language | Cardinal categories |
| --- | --- |
| en-US, de-DE, en-IN, hi-IN | `_one`, `_other` |
| es-ES | `_one`, `_many`, `_other` |
| ko-KR, zh-CN, zh-TW | `_other` |

So a counted message needs `_many` in Spanish, and needs only `_other` in Chinese
and Korean. A `_zero` override is always optional. The parity check works this
out per language; do not copy English's set.

### Regional variants

Every locale is a complete file of its own, including close pairs such as
`zh-CN` and `zh-SG`. They hold the same text today, but they are separate
catalogues and are free to diverge: change one without touching the other.
A locale never inherits from another locale — anything it is missing falls back
to `en-US`, and the parity check requires it to be complete, so nothing is
missing for long.

### Casing

Do not call `.toLowerCase()` on a value you interpolate: German capitalises
nouns, so casing belongs in the translation. Use the `lowercase` formatter
instead, and let each language opt in:

```json
"selectFieldInline": "Select {{field, lowercase}}"
```

## Checks

From `frontend/`:

```sh
npm run i18n:check   # every locale against en-US
npm run i18n:keys    # every t('...') in the source resolves in en-US
npm run test:i18n    # the checkers' own tests
```

`i18n:check` compares nested objects and arrays, missing and orphaned leaves,
value types, interpolation variables and plural categories, for every locale it
finds in `lib/i18n/locales/`. Locales listed under `gated` in
`locale-policy.json` fail the build; anything else is reported as a warning, so a
new language can land and be filled in over several changes.

`i18n:keys` exists because parity alone cannot see a key deleted from en-US while
code still asks for it: both catalogues agree, and the UI renders the raw key
name. Pass one or more locale names to `i18n:check` to narrow it.

The frontend CI job and `scripts/verify.sh frontend` run all three. None of them
detects a hardcoded UI string, and none replaces opening the app in the language
you changed.

Keep reusable validation utilities free of presentation strings: return stable
error codes and translate them in the UI. Backend-provided connector/schema
labels, descriptions and help text, MCP and model-provider metadata, tool display
names, and third-party/user content remain in their source language; do not add
frontend translation tables for that content.
