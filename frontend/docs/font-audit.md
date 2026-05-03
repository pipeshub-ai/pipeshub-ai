# Font Audit — Non-Manrope Usage

Manrope is the project's canonical font, imported via Google Fonts in `app/globals.css` and applied as:
- `body { font-family: 'Manrope', sans-serif; }` (line 234)
- `.radix-themes { --default-font-family: 'Manrope', sans-serif; }` (line 261)

---

## Part 1 — Actual Violations (should use Manrope for UI text)

| Component / File | Current Font Used | Why It's Applied | Page / File Path | Line # | How to Fix |
|---|---|---|---|---|---|
| `HtmlRenderer` — body text | ~~`system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif`~~ | Injected via a dynamically created `<style>` tag for the `.ph-html-renderer` class | `app/components/file-preview/renderers/html-renderer.tsx` | 148 | ✅ Fixed — changed to `'Manrope', sans-serif` |
| `SpreadsheetRenderer` — `<td>` cell | ~~`"Segoe UI", system-ui, -apple-system, sans-serif`~~ | Inline `style` on `<td>` in the `TableCell` sub-component | `app/components/file-preview/renderers/spreadsheet-renderer.tsx` | 292 | ✅ Fixed — changed to `fontFamily: "'Manrope', sans-serif"` |
| `SpreadsheetRenderer` — `<table>` | ~~`"Segoe UI", system-ui, -apple-system, sans-serif`~~ | Inline `style` on the outer `<table>` element | `app/components/file-preview/renderers/spreadsheet-renderer.tsx` | 501 | ✅ Fixed — changed to `fontFamily: "'Manrope', sans-serif"` |
| `SpreadsheetRenderer` — sheet tab `<button>` | ~~`"Segoe UI", system-ui, -apple-system, sans-serif`~~ | Inline `style` on sheet-switcher `<button>` elements | `app/components/file-preview/renderers/spreadsheet-renderer.tsx` | 677 | ✅ Fixed — changed to `fontFamily: "'Manrope', sans-serif"` |

---

## Part 2 — Intentional Monospace (code/technical content)

Semantically correct, but the bare `'monospace'` keyword should be replaced with an explicit stack for cross-platform consistency.

| Component / File | Current Font Used | Why It's Applied | Page / File Path | Line # | How to Fix |
|---|---|---|---|---|---|
| `MarkdownRenderer` — inline `<code>` | `monospace` | Syntax display for inline code spans inside markdown previews | `app/components/file-preview/renderers/markdown-renderer.tsx` | 226 | Replace with `'ui-monospace', 'SFMono-Regular', Menlo, Consolas, monospace` |
| `MarkdownRenderer` — block `<code>` | `monospace` | Code block display inside markdown previews | `app/components/file-preview/renderers/markdown-renderer.tsx` | 228 | Same as above |
| `TextRenderer` — plain text content | `monospace` | Raw text / source-code file previews | `app/components/file-preview/renderers/text-renderer.tsx` | 232 | Replace with explicit monospace stack |
| `AnswerContent` — inline `<code>` | `monospace` | Inline code snippets inside chat responses | `app/(main)/chat/components/message-area/answer-content.tsx` | 170 | Replace with explicit monospace stack |
| `HtmlRenderer` — `<pre>` blocks | `monospace` | Code fences inside rendered HTML documents | `app/components/file-preview/renderers/html-renderer.tsx` | 163 | Replace with explicit monospace stack |
| `HtmlRenderer` — inline `<code>` | `monospace` | Inline code inside rendered HTML documents | `app/components/file-preview/renderers/html-renderer.tsx` | 166 | Replace with explicit monospace stack |
| `SchemaFormField` — JSON `<textarea>` | `monospace` | JSON/config values displayed in connector setup form | `app/(main)/workspace/connectors/components/schema-form-field.tsx` | 306 | Replace with explicit monospace stack |
| `TextareaField` — conditional monospace | `monospace` | Applied when `field.monospace === true` in auth configuration forms | `app/(main)/workspace/authentication/components/forms/textarea-field.tsx` | 24 | Replace with explicit monospace stack |

---

## Part 3 — Theme / Config Defaults

| Component / File | Current Font Used | Why It's Applied | Page / File Path | Line # | How to Fix |
|---|---|---|---|---|---|
| `@radix-ui/themes` base token | `-apple-system, BlinkMacSystemFont, 'Segoe UI (Custom)', Roboto, 'Helvetica Neue', system-ui, sans-serif, 'Apple Color Emoji'` | Radix sets its own `--default-font-family` in `tokens/base.css` as the default for all Radix components | `node_modules/@radix-ui/themes/tokens/base.css` | 1121 | ✅ Already overridden — `globals.css:261` reassigns `.radix-themes { --default-font-family: 'Manrope', sans-serif; }`. No action needed. |
| `pdfjs-dist` — XFA form fields | `monospace` | Internal PDF viewer `<style>` on `.xfaHighlight` form fields | `node_modules/react-pdf-highlighter/.../pdf_viewer.css` | 272 | Add to `globals.css`: `.xfaHighlight { font-family: 'Manrope', sans-serif !important; }` — affects PDF form fields only |
| `pdfjs-dist` — PDF viewer UI chrome | `sans-serif` (generic) | Internal thumbnail/sidebar labels via `font: calc(9px * var(--scale-factor)) sans-serif` | `node_modules/react-pdf-highlighter/.../pdf_viewer.css` | 177 | Low priority — internal PDF viewer UI. Override in `globals.css` targeting `.pdf-viewer` if desired. |

---

## Part 4 — `fontFamily: 'inherit'` (Correct — No Action Needed)

These correctly inherit Manrope from `body`:

| File | Line # |
|---|---|
| `app/(main)/components/chat-input.tsx` | 193 |
| `app/(main)/knowledge-base/components/search-bar.tsx` | 78 |
| `app/(main)/chat/components/chat-panel/expansion-panels/connectors-collections/collections-tab.tsx` | 133 |

---

## Summary

| Category | Count | Files |
|---|---|---|
| **True violations** (system-ui / Segoe UI for app UI text) | 4 occurrences in 2 files | `html-renderer.tsx`, `spreadsheet-renderer.tsx` |
| Intentional monospace (code/technical) — bare keyword | 8 occurrences in 5 files | `markdown-renderer.tsx`, `text-renderer.tsx`, `answer-content.tsx`, `html-renderer.tsx`, `schema-form-field.tsx`, `textarea-field.tsx` |
| Third-party defaults (already overridden or node_modules) | 3 | Radix ✅ handled; pdfjs-dist low priority |
| `inherit` (correct) | 3 | No action needed |
