# Radix UI Color Scales

All colors follow a 1–12 scale (light → dark in light mode, dark → light in dark mode), plus alpha (`-a1` to `-a12`) variants.

Usage: `var(--{color}-{step})` e.g. `var(--blue-9)`

---

## Accent (Project-Specific)

The project uses **emerald** as its accent color, mapped to `--accent-*` variables.

| Variable | Alias |
|----------|-------|
| `--accent-1` … `--accent-12` | `var(--emerald-1)` … `var(--emerald-12)` |
| `--accent-a1` … `--accent-a12` | Alpha variants |
| `--accent-contrast` | Foreground on accent-9 |
| `--accent-surface` | Translucent accent panel |
| `--accent-indicator` | Progress/indicator color |
| `--accent-track` | Track color |

**Custom emerald scale (light mode):**

| Step | Hex |
|------|-----|
| `--emerald-1` | `#f5fefb` |
| `--emerald-2` | `#edfdf8` |
| `--emerald-3` | `#d4f7eb` |
| `--emerald-4` | `#bbf0dd` |
| `--emerald-5` | `#9fe7cd` |
| `--emerald-6` | `#7edabb` |
| `--emerald-7` | `#53c9a4` |
| `--emerald-8` | `#12b589` |
| `--emerald-9` | `#047857` ← primary brand |
| `--emerald-10` | `#10674C` |
| `--emerald-11` | `#035e44` |
| `--emerald-12` | `#0f3d2c` |

---

## Gray Scales (Neutral families)

These are tinted grays. Use the one matching your accent color for best harmony. This project uses **olive** (grayColor="olive" in Theme).

| Scale | Tint |
|-------|------|
| `--gray-1` … `--gray-12` | Pure/neutral gray |
| `--mauve-1` … `--mauve-12` | Purple-tinted gray |
| `--slate-1` … `--slate-12` | Blue-tinted gray |
| `--sage-1` … `--sage-12` | Green-tinted gray |
| `--olive-1` … `--olive-12` | Olive/green-tinted gray ← **used in this project** |
| `--sand-1` … `--sand-12` | Yellow-tinted gray |

---

## Chromatic Scales

All support `--{color}-1` to `--{color}-12` and `--{color}-a1` to `--{color}-a12` alpha variants.

| Color | Use case / personality |
|-------|------------------------|
| `--tomato` | Error, destructive actions (warm red) |
| `--red` | Error, danger |
| `--ruby` | Rich red, premium destructive |
| `--crimson` | Deep pink-red |
| `--pink` | Feminine, notifications |
| `--plum` | Purple-pink |
| `--purple` | Premium, AI, creative |
| `--violet` | Accent, branding |
| `--iris` | Blue-purple |
| `--indigo` | Deep blue, informational |
| `--blue` | Info, links, image mode ← used for image mode |
| `--cyan` | Data, technical |
| `--teal` | Success-adjacent |
| `--jade` | Rich green (Radix accent in Theme config) |
| `--green` | Success |
| `--grass` | Nature, growth |
| `--lime` | Bright green |
| `--yellow` | Warning |
| `--amber` | Warning, caution |
| `--orange` | Alert, web search mode ← used for web-search mode |
| `--brown` | Earthy, muted |
| `--gold` | Premium, special |
| `--bronze` | Metallic, muted warm |
| `--sky` | Light blue |
| `--mint` | Fresh, light teal |

---

## Step Guide

| Step | Typical use |
|------|-------------|
| `1` | App background |
| `2` | Subtle background |
| `3` | UI element background |
| `4` | Hovered UI element background |
| `5` | Active / selected UI element background |
| `6` | Subtle borders and separators |
| `7` | UI element border and focus rings |
| `8` | Hovered UI element border |
| `9` | **Solid color** — buttons, badges, primary |
| `10` | Hovered solid color |
| `11` | Low-contrast text |
| `12` | High-contrast text |

---

## Chat Mode Semantic Tokens

Defined in `globals.css`, these use Radix color scales under the hood:

| Token | Value |
|-------|-------|
| `--mode-chat-bg` | `var(--accent-a3)` |
| `--mode-chat-fg` | `var(--accent-a11)` |
| `--mode-chat-active-bg` | `var(--accent-3)` |
| `--mode-chat-active-toggle` | `var(--accent-9)` |
| `--mode-web-search-bg` | `var(--orange-a4)` |
| `--mode-web-search-fg` | `var(--orange-11)` |
| `--mode-web-search-active-bg` | `var(--orange-3)` |
| `--mode-web-search-toggle` | `var(--orange-9)` |
| `--mode-image-bg` | `var(--blue-3)` |
| `--mode-image-fg` | `var(--blue-11)` |
| `--mode-image-active-bg` | `var(--blue-3)` |
| `--mode-image-toggle` | `var(--blue-9)` |

---

## Other Tokens

| Token | Description |
|-------|-------------|
| `--page-background` | `#dcdcdc` (light) / `#111113` (dark) |
| `--effects-translucent` | `rgba(255,255,255,0.8)` (light) / `rgba(29,29,33,0.70)` (dark) |
| `--tokens-colors-surface` | Panel surface with alpha |

---

## Shadow Scale

| Token | Value |
|-------|-------|
| `--shadow-1` | `0 0 0 1px rgba(0,0,0,0.05)` |
| `--shadow-2` | `0 1px 2px 0 rgba(0,0,0,0.05)` |
| `--shadow-3` | `0 2px 4px 0 rgba(0,0,0,0.08)` |
| `--shadow-4` | `0 4px 8px 0 rgba(0,0,0,0.10)` |
| `--shadow-5` | `0 8px 16px 0 rgba(0,0,0,0.12)` |
| `--shadow-6` | Multi-layer panel shadow |

---

## Quick Reference

```css
/* Backgrounds */
var(--olive-1)   /* page bg */
var(--olive-2)   /* subtle bg */
var(--olive-3)   /* hover bg */

/* Borders */
var(--olive-6)   /* subtle border */
var(--olive-7)   /* default border */

/* Text */
var(--olive-11)  /* secondary text */
var(--olive-12)  /* primary text */

/* Brand */
var(--accent-9)  /* primary button, badge */
var(--accent-10) /* hovered button */
var(--accent-11) /* accent text */

/* Semantic */
var(--red-9)     /* error/destructive */
var(--orange-9)  /* warning */
var(--blue-9)    /* info */
var(--green-9)   /* success */
```
