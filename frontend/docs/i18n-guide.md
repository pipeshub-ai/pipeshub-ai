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

## Maintaining German translations

`en-US.json` is the structural source of truth for `de-DE.json`. Add new UI
strings to both files and reuse existing keys where appropriate. German uses
formal **Sie** and the terms **Arbeitsbereich**, **Konnektor**, **Service-Agent**,
**Skill**, **Tool**, **Toolset**, **MCP-Server**, **OAuth-App**, and **Reasoning-Aufwand**.
Keep interpolation names unchanged and provide `_one` / `_other` forms for
count-dependent messages.

From `frontend/`, run:

```sh
npm run i18n:check
npm run test:i18n
```

The dependency-free check compares nested objects and arrays, missing and
orphaned leaves, value types, interpolation variables, and German plural forms.
It exits with status 1 on parity errors or invalid JSON. The frontend CI job and
`scripts/verify.sh frontend` also run it. Structural parity does not detect every
hardcoded UI string or replace a browser check with German selected.

Keep reusable validation utilities free of presentation strings: return stable
error codes and translate them in the UI. Backend-provided connector/schema
labels, descriptions and help text, MCP and model-provider metadata, tool display
names, and third-party/user content remain in their source language; do not add
frontend translation tables for that content.
