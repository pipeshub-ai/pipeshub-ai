import { ValidationError } from '../../errors/validation.error';

// Lower-case words joined by single hyphens, at most 64 characters. `org-<digits>` is the store's own counter: a caller's value
// there would take a number the counter hands out later.
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COUNTER = /^org-[0-9]+$/;

/** A taken slug is refused exactly as a malformed one, so the answer tells nothing about another organisation. */
export const slugRefused = (): ValidationError =>
  new ValidationError('slug is not available', []);

/** The caller's slug, or undefined when the store assigns one; throws `slugRefused()` for a value it may not take. */
export function callerSlug(slug: string | undefined): string | undefined {
  if (slug === undefined || slug === '') return undefined;
  if (slug.length > 64 || !SLUG.test(slug) || COUNTER.test(slug))
    throw slugRefused();
  return slug;
}
