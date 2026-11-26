import { defaultSchema } from 'rehype-sanitize';
import type { Schema } from 'hast-util-sanitize';
import type { Element, Root, RootContent } from 'hast';

/**
 * Sanitizing for raw HTML inside markdown we did not write: agent answers and
 * indexed documents. The text can be steered by a prompt injection, so it must
 * not be able to look like, or sit on top of, the app's own controls (approval
 * cards, status banners): a borrowed class name such as `rt-Button` would pick
 * up the app's button styling, and `position:fixed` could cover the screen.
 *
 * Classes are kept only where the markdown pipeline itself puts them (code
 * languages and math, GFM task lists and footnotes, callouts), and only on the
 * element that carries them. Inline styles go through `rehypeSafeInlineStyle`.
 */

const attrs = defaultSchema.attributes ?? {};
const keep = (tag: string) => (attrs[tag] ?? []).filter((def) => !isClassDefinition(def));

function isClassDefinition(def: unknown): boolean {
  return def === 'className' || (Array.isArray(def) && def[0] === 'className');
}

/**
 * Elements whose inline style survives (after property filtering). Wrappers
 * such as div and section are left out: models give them hardcoded light
 * backgrounds that show as white boxes in dark mode.
 */
const STYLED_TAGS = [
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
  'span', 'pre', 'code', 'img', 'a', 'figure', 'figcaption',
] as const;

const PIPELINE_CLASSES: Record<string, Array<string | RegExp>> = {
  // remark code fences and remark-math (`language-math math-inline|math-display`).
  code: [/^language-[\w+#.-]+$/, 'math-inline', 'math-display'],
  li: ['task-list-item'],
  ul: ['contains-task-list'],
  ol: ['contains-task-list'],
  section: ['footnotes'],
  h2: ['sr-only'],
  a: ['data-footnote-backref'],
  // Written by the answer renderer's callout plugin (`> [!NOTE]`).
  blockquote: [/^markdown-alert(-[a-z][a-z0-9_-]*)?$/i],
};

function buildAttributes(): NonNullable<Schema['attributes']> {
  const result: NonNullable<Schema['attributes']> = {};
  const tags = new Set([...Object.keys(attrs), ...STYLED_TAGS, ...Object.keys(PIPELINE_CLASSES)]);
  for (const tag of tags) {
    const list: NonNullable<Schema['attributes']>[string] = [...keep(tag)];
    const classes = PIPELINE_CLASSES[tag];
    if (classes) list.push(['className', ...classes]);
    if ((STYLED_TAGS as readonly string[]).includes(tag)) list.push('style');
    result[tag] = list;
  }
  return result;
}

export const UNTRUSTED_HTML_SCHEMA: Schema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), 'details', 'summary'],
  attributes: buildAttributes(),
};

/**
 * Inline-style properties that only colour or emphasise text in place. Nothing
 * that moves an element out of the flow, layers it, resizes or boxes it (so no
 * padding, border or radius: a padded rounded span reads as a button or badge),
 * hides it, or loads a resource (`background: url(...)` would fetch remotely).
 */
const SAFE_STYLE_PROPERTIES = new Set([
  'color',
  'background-color',
  'font-weight',
  'font-style',
  'font-variant',
  'text-decoration',
  'text-decoration-line',
  'text-decoration-style',
  'text-decoration-color',
  'text-align',
  'vertical-align',
  'white-space',
  'word-break',
  'overflow-wrap',
]);

// Quotes, backslashes, slashes and colons are refused so an escape, a comment
// or a url can never be smuggled into an otherwise allowed property.
const SAFE_STYLE_VALUE = /^[\w\s#.,%()+-]+$/;
const SAFE_STYLE_FUNCTIONS = new Set(['rgb', 'rgba', 'hsl', 'hsla', 'var']);

function isSafeStyleValue(value: string): boolean {
  if (!SAFE_STYLE_VALUE.test(value)) return false;
  // Transparent text hides words from the reader while copy and screen readers still get them.
  if (/\btransparent\b/i.test(value)) return false;
  for (const match of value.matchAll(/([\w-]+)\s*\(/g)) {
    if (!SAFE_STYLE_FUNCTIONS.has(match[1].toLowerCase())) return false;
  }
  return true;
}

/** Keeps only the safe declarations of an inline style; returns '' when none remain. */
export function filterInlineStyle(style: string): string {
  const kept: string[] = [];
  for (const declaration of style.split(';')) {
    const colon = declaration.indexOf(':');
    if (colon < 0) continue;
    const property = declaration.slice(0, colon).trim().toLowerCase();
    const value = declaration.slice(colon + 1).trim().replace(/\s*!important$/i, '');
    if (!SAFE_STYLE_PROPERTIES.has(property) || !value || !isSafeStyleValue(value)) continue;
    kept.push(`${property}: ${value}`);
  }
  return kept.join('; ');
}

function filterStyles(node: Root | RootContent): void {
  if (node.type === 'element') {
    const element = node as Element;
    const style = element.properties?.style;
    if (style !== undefined) {
      const safe = typeof style === 'string' ? filterInlineStyle(style) : '';
      if (safe) element.properties.style = safe;
      else delete element.properties.style;
    }
  }
  if ('children' in node) {
    for (const child of node.children) filterStyles(child);
  }
}

/** Rehype plugin; runs right after `rehype-sanitize` with `UNTRUSTED_HTML_SCHEMA`. */
export function rehypeSafeInlineStyle() {
  return (tree: Root) => filterStyles(tree);
}
