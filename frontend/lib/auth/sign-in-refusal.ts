/**
 * A failed sign-in that a build answers in its own words once the credential
 * was proven (a page or a notice instead of the error). True when it was
 * handled; this build handles none, so the caller shows its ordinary error.
 */
export function takeSignInRefusal(_error: unknown): boolean {
  return false;
}
