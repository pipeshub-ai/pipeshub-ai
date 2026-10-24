import DOMPurify from 'dompurify';
import type { PreviewCitation } from './types';
import { HIGHLIGHT_CSS, highlightCitations } from './use-text-highlighter';

/**
 * Untrusted HTML previews (HTML artifacts/records, rendered DOCX) run in an
 * `<iframe sandbox="allow-scripts">` loaded via `srcdoc`: an opaque origin
 * with no forms, popups, top navigation or access to the app. The embedded
 * CSP lets exactly one script run (the bridge below, by nonce) and stops every
 * network fetch from inside the frame.
 */
export const PREVIEW_SANDBOX = 'allow-scripts';

export function previewCsp(nonce: string): string {
  return `default-src 'none'; script-src 'nonce-${nonce}'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:`;
}

export const PREVIEW_CONTENT_CLASS = 'ph-html-rendered-content';

export const SET_ACTIVE_CITATION = 'pipeshub:setActiveCitation';
export const CITATION_CLICK = 'pipeshub:citationClick';
const MAX_CITATION_ID_LENGTH = 256;

export function createPreviewNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

interface SanitizeOptions {
  /** DOCX output: keep `<style>` blocks, MathML (equations) and SVG (shapes). */
  rich?: boolean;
}

export function sanitizePreviewHtml(rawHtml: string, { rich = false }: SanitizeOptions = {}): string {
  return DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: rich ? { html: true, svg: true, mathMl: true } : { html: true },
    ADD_ATTR: ['target', 'id', 'class', 'style', 'href', 'src', 'alt', 'title'],
    ADD_TAGS: rich ? ['figure', 'figcaption', 'style'] : ['figure', 'figcaption'],
    FORCE_BODY: rich,
    FORBID_TAGS: [
      'script', 'link', 'base', 'meta',
      'form', 'input', 'button', 'textarea', 'select', 'option',
      'iframe', 'frame', 'object', 'embed',
    ],
    FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'autofocus', 'formaction', 'action', 'nonce'],
  });
}

/**
 * The only script allowed in the frame. It takes the active citation from the
 * parent and reports highlight clicks; every message field is checked.
 */
const BRIDGE_SCRIPT = `(function () {
  var ACTIVE = 'ph-highlight-active';
  var MAX = ${MAX_CITATION_ID_LENGTH};
  function validId(id) { return typeof id === 'string' && id.length > 0 && id.length <= MAX; }
  function setActive(id) {
    var current = document.querySelectorAll('.' + ACTIVE);
    for (var i = 0; i < current.length; i++) current[i].classList.remove(ACTIVE);
    if (!validId(id)) return;
    var matches = document.getElementsByClassName('highlight-' + id);
    if (!matches.length) return;
    matches[0].classList.add(ACTIVE);
    matches[0].scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
  }
  window.addEventListener('message', function (event) {
    if (event.source !== window.parent) return;
    var data = event.data;
    if (!data || typeof data !== 'object' || data.type !== '${SET_ACTIVE_CITATION}') return;
    if (data.id !== null && !validId(data.id)) return;
    setActive(data.id);
  });
  document.addEventListener('click', function (event) {
    var target = event.target;
    var el = target && target.closest ? target.closest('[data-highlight-id]') : null;
    if (!el) return;
    var id = el.getAttribute('data-highlight-id');
    if (!validId(id)) return;
    event.preventDefault();
    window.parent.postMessage({ type: '${CITATION_CLICK}', id: id }, '*');
  });
})();`;

const PALETTE = {
  light: { bg: '#ffffff', fg: '#1c2024', muted: '#60646c', subtle: '#f0f0f3', border: '#d9d9e0', link: '#0d74ce' },
  dark: { bg: '#18191b', fg: '#edeef0', muted: '#b0b4ba', subtle: '#212225', border: '#363a3f', link: '#70b8ff' },
};

function previewCss(dark: boolean): string {
  const c = dark ? PALETTE.dark : PALETTE.light;
  const k = `.${PREVIEW_CONTENT_CLASS}`;
  return `
    html { color-scheme: ${dark ? 'dark' : 'light'}; }
    body { margin: 0; padding: 1rem 1.5rem; background: ${c.bg}; color: ${c.fg}; }
    ${k} { font-family: 'Manrope', system-ui, sans-serif; line-height: 1.6; max-width: 100%; word-wrap: break-word; }
    ${k} img { max-width: 100%; height: auto; }
    ${k} pre { background-color: ${c.subtle}; padding: 1em; border-radius: 6px; overflow-x: auto; font-family: monospace; }
    ${k} code:not(pre > code) { font-family: monospace; background-color: ${c.subtle}; padding: 0.2em 0.4em; border-radius: 4px; }
    ${k} table { border-collapse: collapse; margin-bottom: 1em; width: auto; }
    ${k} td, ${k} th { padding: 0.5em; text-align: left; border: 1px solid ${c.border}; }
    ${k} th { background-color: ${c.subtle}; font-weight: 600; }
    ${k} a { color: ${c.link}; text-decoration: underline; }
    ${k} blockquote { border-left: 4px solid ${c.border}; padding-left: 1em; margin-left: 0; color: ${c.muted}; }
    ${k} h1, ${k} h2, ${k} h3, ${k} h4, ${k} h5, ${k} h6 { margin-top: 1.5em; margin-bottom: 0.8em; }
    ${k} p { margin-bottom: 0.8em; }
    ${k} ul, ${k} ol { margin-left: 1.5em; margin-bottom: 1em; }
    ${k} li { margin-bottom: 0.25em; }
    ${k} hr { border: none; border-top: 1px solid ${c.border}; margin: 1.5em 0; }
  `;
}

interface PreviewDocumentOptions {
  sanitizedHtml: string;
  nonce: string;
  dark: boolean;
  citations?: PreviewCitation[];
  /** Replaces the default HTML-preview typography (DOCX brings its own styles). */
  baseCss?: string;
}

/**
 * Build the `srcdoc`. Highlights are applied on a detached document (no
 * browsing context, so nothing in it loads); the active citation is set later
 * by message, so changing it does not rebuild the frame.
 */
export function buildPreviewDocument({
  sanitizedHtml,
  nonce,
  dark,
  citations,
  baseCss,
}: PreviewDocumentOptions): string {
  const inert = document.implementation.createHTMLDocument('');
  const wrapper = inert.createElement('div');
  wrapper.className = PREVIEW_CONTENT_CLASS;
  wrapper.innerHTML = sanitizedHtml;
  inert.body.appendChild(wrapper);

  if (citations?.length) {
    try {
      highlightCitations(wrapper, citations);
    } catch (err) {
      console.warn('[sandboxed-html] citation highlighting failed:', err);
    }
  }

  return [
    '<!doctype html><html><head>',
    `<meta http-equiv="Content-Security-Policy" content="${previewCsp(nonce)}">`,
    '<meta charset="utf-8">',
    `<style>${baseCss ?? previewCss(dark)}${HIGHLIGHT_CSS}</style>`,
    '</head><body>',
    wrapper.outerHTML,
    `<script nonce="${nonce}">${BRIDGE_SCRIPT}</script>`,
    '</body></html>',
  ].join('');
}

/** Citation id from a frame message, or null when it is not a valid click on a known citation. */
export function parseCitationClick(data: unknown, knownIds: ReadonlySet<string>): string | null {
  if (!data || typeof data !== 'object') return null;
  const { type, id } = data as { type?: unknown; id?: unknown };
  if (type !== CITATION_CLICK) return null;
  if (typeof id !== 'string' || id.length === 0 || id.length > MAX_CITATION_ID_LENGTH) return null;
  return knownIds.has(id) ? id : null;
}
