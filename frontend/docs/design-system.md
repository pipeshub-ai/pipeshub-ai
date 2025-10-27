# PipesHub Design System Overview

This document maps the current design-system setup so upcoming UI/UX work can iterate quickly without introducing extra complexity. It highlights where tokens live, how themes are assembled, and the safest ways to update shared styling primitives.

## Theme Architecture
- `src/theme/theme-provider.tsx` wraps the app in MUI’s `CssVarsProvider`, so palettes, typography, and shadows are exposed as CSS variables (`theme.vars.*`).
- `src/theme/create-theme.ts` composes the base theme:
  - Imports core tokens (`src/theme/core`) for palette, typography, shadows, component overrides.
  - Injects runtime settings (direction, primary preset, contrast) via `with-settings/update-theme.ts`.
  - Applies hard-coded overrides (`overrides-theme.ts`, currently empty scaffolding).
- `src/components/settings` feeds user preferences into the theme. The provider currently forces the blue primary palette and disables reset; remove those guards if we want live switching.

## File Map
```
docs/
  design-system.md            <-- this document
src/
  global.css                  <-- global fonts and plugin CSS
  config-global.ts            <-- runtime configuration (non-visual)
  components/
    settings/                 <-- user settings controls + context
      config-settings.ts
      context/
        settings-provider.tsx <-- overrides theme settings persistence
      types.ts
  theme/
    create-theme.ts           <-- assembles base theme
    overrides-theme.ts        <-- optional hard overrides
    scheme-config.ts          <-- color-scheme storage keys
    theme-provider.tsx        <-- wraps app with CssVarsProvider
    styles/
      index.ts
      mixins.ts               <-- reusable style helpers (borderGradient, etc.)
      utils.ts                <-- varAlpha, media queries, font helpers
    core/
      colors.json             <-- canonical palette tokens
      palette.ts              <-- builds palette + channels
      typography.ts           <-- typography ramps
      shadows.ts              <-- elevation array
      custom-shadows.ts       <-- semantic shadow tokens
      components/
        index.ts              <-- exports all component overrides
        button.tsx            <-- per-component override example
        ...                   <-- additional component override files
    with-settings/
      primary-color.json      <-- alternate primary palettes
      right-to-left.tsx       <-- RTL cache provider
      update-theme.ts         <-- applies settings to tokens
```

## Token Sources
| Domain | Location | Notes / How to edit |
| --- | --- | --- |
| **Color palettes** | `src/theme/core/colors.json` | Master palette (primary, secondary, info, success, warning, error, grey, common). Update this file when redefining brand colors; `palette.ts` auto-derives `*Channel` values via `createPaletteChannel`. |
| **Primary presets** | `src/theme/with-settings/primary-color.json` | Alternate primary palettes used when settings permit switching (`cyan`, `purple`, etc.). Keep values in sync with `colors.json` or consolidate into a single source before the overhaul. |
| **Typography** | `src/theme/core/typography.ts` | Defines font families, weights, and responsive ramps. Uses helpers from `styles/utils.ts` (`pxToRem`, `responsiveFontSizes`). |
| **Shadows** | `src/theme/core/shadows.ts`, `custom-shadows.ts` | `shadows()` returns the 25-elevation array. `customShadows()` defines semantic shadows (`card`, `dropdown`, color shadows). Both rely on `varAlpha` for consistent opacity. |
| **Utilities** | `src/theme/styles/utils.ts` | Houses `varAlpha`, media-query tokens, font helpers. Central place for shared math/format conversions. |

When editing tokens:
1. Change the JSON or TS source (not the derived channels).
2. Run the app and inspect CSS variables (e.g., via dev tools on `[data-mui-color-scheme]`).
3. Update related design docs so component overrides stay consistent.

## Component Overrides
- Per-component theme rules live under `src/theme/core/components/<component>.tsx` and are merged in `core/components/index.ts`.
- Files follow a consistent pattern: declare `Components<Theme>` fragments, override `defaultProps`, `variants`, and `styleOverrides`, and export as plain objects.
- Reuse utilities (e.g., `varAlpha`, `paper`, `menuItem`) to avoid divergence. If multiple components need similar tweaks, add a helper in `src/theme/styles` instead of duplicating logic.
- New component overrides should be added as sibling files and re-exported from `index.ts`. Keep overrides lean—prefer tokens to hard-coded hex values so palette changes propagate automatically.

## Global Styles & Assets
- `src/global.css` imports font families and shared plugin CSS (scrollbar, image, chart). Any global resets or new plugin styles belong here.
- `src/config-global.ts` stores environment-dependent app metadata; it is not part of the visual system but worth noting to avoid mixing runtime config with tokens.

## Settings & Theming Controls
- `src/components/settings/types.ts` defines the user-adjustable knobs (`colorScheme`, `primaryColor`, `contrast`, `navLayout`, etc.). Note the existing typo (`'hight'`) for contrast—fix this before depending on the flag.
- `src/components/settings/context/settings-provider.tsx` currently:
  - Persists settings via `useLocalStorage`.
  - Forces `primaryColor` to `'blue'` by overriding `setField`/`setState`.
  - Disables reset functionality.
- For a flexible future: remove the forced preset, correct the contrast typo, and surface the drawer UI (`src/components/settings/drawer`) so designers can toggle real palettes during QA.

## Editing Workflow (Recommended)
1. **Plan token updates**: Decide whether changes belong in `colors.json`, typography, shadows, or component overrides.
2. **Edit the source file**:
   - Colors → update JSON, let `palette.ts` regenerate channels.
   - Typography → adjust weights/sizes in `typography.ts`; rely on provided helpers.
   - Component behavior → touch only the relevant `src/theme/core/components/*.tsx`.
3. **Keep utilities centralised**: if a new alpha blend or layout pattern appears, create/extend helpers in `src/theme/styles`.
4. **Test in both schemes**: Because `CssVarsProvider` emits `light` and `dark` schemes, verify both by toggling via settings (once re-enabled) or temporarily forcing `defaultSettings.colorScheme`.
5. **Document decisions**: Mirror significant token changes back into this doc or a design spec to prevent drift between code and Figma.

Following this structure keeps the system discoverable, enables quick palette/typography swaps, and avoids scattering hard-coded values across the app. Use the existing separation of concerns—tokens → utilities → overrides—to maintain a low-complexity, high-leverage design system. 
