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
| `primary.lighter` | `#EFE8FF` | Hover/background tint for primary buttons, charts area gradients | `--palette-primary-lighter`, `--palette-primary-lighterChannel` |
| `primary.light` | `#C9B9FF` | Primary outlined button hover, progress indicators | `--palette-primary-light` |
| `primary.main` | `#9C83FF` | Primary buttons, highlighted states, key charts | `--palette-primary-main`, `--palette-primary-mainChannel` |
| `primary.dark` | `#5F4DC6` | Active button states, dark backgrounds in dark mode | `--palette-primary-dark` |
| `primary.darker` | `#35267E` | Emphasis text in dark mode, charts accents | `--palette-primary-darker` |
| `secondary.main` | `#8E33FF` | Secondary CTAs, badges, highlight ribbons | `--palette-secondary-main` |
| `info.main` | `#00B8D9` | Informational banners, callouts | `--palette-info-main` |
| `success.main` | `#22C55E` | Validation success messages, KPI badges | `--palette-success-main` |
| `warning.main` | `#FFAB00` | Warning alerts, pending statuses | `--palette-warning-main` |
| `error.main` | `#FF5630` | Errors, destructive confirmations | `--palette-error-main` |

> **Tip:** Alternate primary presets (cyan, classic purple, blue, orange, red) live in `src/theme/with-settings/primary-color.json`. The “default” preset now references the muted plum values above so the entire app inherits the new palette automatically.

## Neutral Scale

The neutral greys underpin backgrounds, borders, and typography ramps.

| Token | Hex | Usage |
| --- | --- | --- |
| `grey.50` | `#F6F6F7` | Highest elevation surfaces, subtle dividers |
| `grey.100` | `#EEEEF0` | Primary surface background (list rows, cards) |
| `grey.200` | `#E1E2E6` | Neutral background (`theme.palette.background.neutral`) |
| `grey.300` | `#C5C6CD` | Borders, outlines |
| `grey.400` | `#9D9EA8` | Disabled icons, low-emphasis text |
| `grey.500` | `#72737A` | Default icon color, secondary text |
| `grey.600` | `#54555B` | Disabled text in dark mode, headings on neutral bg |
| `grey.700` | `#3B3C40` | High emphasis text on light backgrounds |
| `grey.800` | `#252629` | Card background in dark mode |
| `grey.900` | `#141417` | Page background in dark mode |

## Background & Surface Tokens

| Scheme | Token | Hex | Usage |
| --- | --- | --- | --- |
| **Light** | `background.default` | `#FFFFFF` | Global page background |
|  | `background.paper` | `#FFFFFF` | Cards, drawers, dialogs |
|  | `background.neutral` | `#F4F6F8` | Alternate panel backgrounds, dashboard widgets |
| **Dark** | `background.default` | `#141417` | Global page background (near-black) |
|  | `background.paper` | `#19191D` | Cards, drawers, overlays |
|  | `background.neutral` | `#1C1C21` | Secondary surfaces |

The app background toggle on the auth split layout reads from `theme.palette.background.default`. To adjust the overall look (e.g., move to a warmer grey), change the relevant hex in `colors.json` and confirm gradients/overlays still meet contrast requirements.

The divider uses `rgba(116 110 177 / 20%)`, and action states reuse the same grey ramp for hover/selected/focus overlays to keep interaction feedback cohesive.

## Typography Colors

| Scheme | Token | Hex | Usage |
| --- | --- | --- | --- |
| **Light** | `text.primary` | `#15161D` | Body text, titles |
|  | `text.secondary` | `#505468` | Supporting copy |
|  | `text.disabled` | `#70758A` | Disabled text, placeholder hints |
| **Dark** | `text.primary` | `#F4F4F6` | Body text on dark surfaces |
|  | `text.secondary` | `#B5B6C1` | Supporting copy |
|  | `text.disabled` | `#6F7077` | Disabled text |

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

- **Auth split left panel overlay**: `src/layouts/auth-split/section.tsx` tints the media with `rgba(0 0 0 / 0.2)` (no blur) and inherits the container’s 24px radius. Increase/decrease the alpha there to control how much of the underlying video shows through.
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
