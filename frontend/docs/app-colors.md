# Application Color Reference

This document lists every palette token that currently drives the PipesHub UI, how each color is applied in the layouts, and where to update the source values when we refresh the visual system.

## Source of Truth

- **Primary palette JSON**: `src/theme/core/colors.json`
- **Derived palettes + CSS variables**: `src/theme/core/palette.ts`
- **Runtime overrides (optional)**: `src/theme/with-settings/primary-color.json`, `src/theme/with-settings/update-theme.ts`
- **Left-auth overlay filter**: `src/layouts/auth-split/section.tsx`

All colors are fed through MUI’s `CssVarsProvider`, so every tone is available as a CSS variable under `theme.vars.palette.*`. When changing a hex value, update the JSON file, restart the dev server, and verify both light and dark schemes (they share the same base tokens but derive different text/background pairs).

## Brand & Accent Palettes

| Token | Hex | Typical Usage | CSS Variable Examples |
| --- | --- | --- | --- |
| `primary.lighter` | `#C8FAD6` | Hover/background tint for primary buttons, charts area gradients | `--palette-primary-lighter`, `--palette-primary-lighterChannel` |
| `primary.light` | `#5BE49B` | Primary outlined button hover, progress indicators | `--palette-primary-light` |
| `primary.main` | `#00A76F` | Primary buttons, highlighted states, key charts | `--palette-primary-main`, `--palette-primary-mainChannel` |
| `primary.dark` | `#007867` | Active button states, dark backgrounds in dark mode | `--palette-primary-dark` |
| `primary.darker` | `#004B50` | Emphasis text in dark mode, charts accents | `--palette-primary-darker` |
| `secondary.main` | `#8E33FF` | Secondary CTAs, badges, highlight ribbons | `--palette-secondary-main` |
| `info.main` | `#00B8D9` | Informational banners, callouts | `--palette-info-main` |
| `success.main` | `#22C55E` | Validation success messages, KPI badges | `--palette-success-main` |
| `warning.main` | `#FFAB00` | Warning alerts, pending statuses | `--palette-warning-main` |
| `error.main` | `#FF5630` | Errors, destructive confirmations | `--palette-error-main` |

> **Tip:** Alternate primary presets (cyan, purple, etc.) live in `src/theme/with-settings/primary-color.json`. We currently lock the UI to the “blue / emerald” preset inside `settings-provider.tsx`; remove that guard when exposing theme switching.

## Neutral Scale

The neutral greys underpin backgrounds, borders, and typography ramps.

| Token | Hex | Usage |
| --- | --- | --- |
| `grey.50` | `#FCFDFD` | Highest elevation surfaces, subtle dividers |
| `grey.100` | `#F9FAFB` | Primary surface background (list rows, cards) |
| `grey.200` | `#F4F6F8` | Neutral background (`theme.palette.background.neutral`) |
| `grey.300` | `#DFE3E8` | Borders, outlines |
| `grey.400` | `#C4CDD5` | Disabled icons, low-emphasis text |
| `grey.500` | `#919EAB` | Default icon color, secondary text |
| `grey.600` | `#637381` | Disabled text in dark mode, headings on neutral bg |
| `grey.700` | `#454F5B` | High emphasis text on light backgrounds |
| `grey.800` | `#1C252E` | Card background in dark mode |
| `grey.900` | `#141A21` | Page background in dark mode |

## Background & Surface Tokens

| Scheme | Token | Hex | Usage |
| --- | --- | --- | --- |
| **Light** | `background.default` | `#FFFFFF` | Global page background |
|  | `background.paper` | `#FFFFFF` | Cards, drawers, dialogs |
|  | `background.neutral` | `#F4F6F8` | Alternate panel backgrounds, dashboard widgets |
| **Dark** | `background.default` | `#141A21` | Global page background |
|  | `background.paper` | `#1C252E` | Cards, drawers, overlays |
|  | `background.neutral` | `#28323D` | Secondary surfaces |

The app background toggle on the auth split layout reads from `theme.palette.background.default`. To adjust the overall look (e.g., move to a warmer grey), change the relevant hex in `colors.json` and confirm gradients/overlays still meet contrast requirements.

## Typography Colors

| Scheme | Token | Hex | Usage |
| --- | --- | --- | --- |
| **Light** | `text.primary` | `#1C252E` | Body text, titles |
|  | `text.secondary` | `#454F5B` | Supporting copy |
|  | `text.disabled` | `#919EAB` | Disabled text, placeholder hints |
| **Dark** | `text.primary` | `#FFFFFF` | Body text on dark surfaces |
|  | `text.secondary` | `#919EAB` | Supporting copy |
|  | `text.disabled` | `#637381` | Disabled text |

## Action & State Colors

These are alpha blends derived from `grey.500Channel`.

| Token | Effective Color | Usage |
| --- | --- | --- |
| `action.hover` | `grey.500 @ 8%` | Hover states for list items, buttons |
| `action.selected` | `grey.500 @ 16%` | Selected menu rows, chips |
| `action.focus` | `grey.500 @ 24%` | Focus ring fallback |
| `action.disabled` | `grey.500 @ 80%` | Disabled icon/text overlay |
| `action.disabledBackground` | `grey.500 @ 24%` | Disabled button fill |

## Special Treatments

- **Auth split left panel overlay**: `src/layouts/auth-split/section.tsx` now applies `alpha(white, 0.4)` with `backdrop-filter: blur(6px)` and inherits the container’s 24px radius. Edit `bgcolor` or blur intensity there for future tweaks.
- **Loading spinner**: Uses `theme.palette.primary.main` for the glyph and `border: 1px solid primary` in `src/components/loading-screen/logo-spinner.tsx`. Adjust `primary.*` tokens to recolor the animation globally.
- **Divider**: Derived from `varAlpha(grey['500Channel'], 0.2)` inside `palette.ts`.

## How to Update the Palette

1. Modify `src/theme/core/colors.json` with the new brand hex values.
2. If the primary preset options must change, mirror the edits in `src/theme/with-settings/primary-color.json`.
3. Restart the dev server to regenerate CSS variables.
4. Verify light/dark contrast (especially `text.primary` on the updated `background.paper`).
5. Update any hard-coded overlays (e.g., the auth panel) if the new palette requires different tints.
6. Document the change in this file to keep designers and engineers aligned.

Keeping the palette centralized ensures future color refreshes only touch the JSON tokens and a handful of overlay helpers, making the transition to a new visual language straightforward.
