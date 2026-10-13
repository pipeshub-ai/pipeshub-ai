// Any origin that is not ours: a value that resolves somewhere else against
// it would also leave the real one.
const PROBE_ORIGIN = 'http://return-to.invalid';

/**
 * A `returnTo` taken from the URL, kept only when it is a path inside this
 * app. Anything else is dropped, and the caller falls back to its default.
 *
 * `router.push` follows an absolute URL off the site and runs a `javascript:`
 * one, so the value has to be checked before it gets there. Starting with a
 * single `/` is not enough: URL parsers read `/\host` as `//host` and drop
 * tabs and newlines, so `/<tab>/host` is `//host` too.
 */
export function getSafeReturnTo(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string') return null;
  if (!raw.startsWith('/') || raw.startsWith('//')) return null;
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return null;

  try {
    const url = new URL(raw, PROBE_ORIGIN);
    if (url.origin !== PROBE_ORIGIN) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}
