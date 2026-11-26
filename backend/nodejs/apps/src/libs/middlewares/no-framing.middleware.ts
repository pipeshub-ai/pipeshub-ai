/**
 * Pages that never render inside another site's frame, even where an embed
 * allowlist (CSP_FRAME_ANCESTORS) lets the rest of the app be framed: a consent
 * or sign-in page overlaid by its embedder can be clickjacked into "Allow".
 */
export const UNFRAMEABLE_PAGE_PATHS = [
  '/login',
  '/sign-up',
  '/oauth',
  '/slack/link',
  '/agent/claim',
];

/**
 * Matched as Express routes them (case-insensitive, on segment boundaries), and
 * also after percent-decoding and collapsing repeated slashes, so no spelling of a
 * path that serves one of these pages escapes the rule. `editionPaths` are an
 * edition's own decision pages (EditionBoot.unframeablePaths), held to the same rule.
 */
export function isUnframeablePage(
  path: string,
  editionPaths: readonly string[] = [],
): boolean {
  let normalised = path;
  try {
    normalised = decodeURIComponent(path);
  } catch {
    // A malformed escape cannot name one of the pages; match the raw path.
  }
  normalised = normalised.replace(/\/{2,}/g, '/').toLowerCase();
  return [...UNFRAMEABLE_PAGE_PATHS, ...editionPaths].some((raw) => {
    const prefix = raw.toLowerCase();
    return normalised === prefix || normalised.startsWith(`${prefix}/`);
  });
}
