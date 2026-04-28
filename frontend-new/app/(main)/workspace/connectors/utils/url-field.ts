/**
 * Shared helpers for connector schema fields with `fieldType === 'URL'`.
 * Normalization matches blur behavior so validation agrees with stored values.
 */

const HAS_HTTP_SCHEME = /^https?:\/\//i;

/** `ftp:`, `javascript:`, etc. — must not prepend `https://` on blur. */
function hasNonHttpAbsoluteScheme(s: string): boolean {
  return /^[a-z][a-z0-9+-]*:/i.test(s);
}

/**
 * Trim; empty → ''.
 * If the string already starts with `http://` or `https://` (any case), return trimmed as-is.
 * If it starts with another URI scheme (`ftp:`, `javascript:`, …), return as-is (blur does not rewrite).
 * Otherwise prepend `https://` (bare host/path input).
 */
export function normalizeUrlInputOnBlur(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === '') return '';
  return normalizedUrlForValidation(trimmed);
}

function normalizedUrlForValidation(trimmed: string): string {
  if (HAS_HTTP_SCHEME.test(trimmed)) return trimmed;
  if (hasNonHttpAbsoluteScheme(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

/**
 * Returns an error message if the value is non-empty but not a valid http(s) URL after
 * the same normalization as blur. Empty/whitespace-only → null (required checks elsewhere).
 */
export function getUrlValidationError(displayName: string, raw: string): string | null {
  const trimmed = raw.trim();
  if (trimmed === '') return null;

  const normalized = normalizedUrlForValidation(trimmed);
  try {
    const u = new URL(normalized);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') {
      return `${displayName} must use http or https`;
    }
  } catch {
    return `${displayName} must be a valid URL`;
  }
  return null;
}
