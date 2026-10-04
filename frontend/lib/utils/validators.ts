/**
 * Returns true if the string looks like a valid email address.
 */
export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// ─── Password ─────────────────────────────────────────────────────────────────

export type PasswordValidationError = 'minLength' | 'lowercase' | 'uppercase' | 'number' | 'symbol';

/**
 * Validates a password against the standard policy.
 * Returns an error code if invalid, or null if valid.
 */
export function validatePassword(pw: string): PasswordValidationError | null {
  if (pw.length < 8) return 'minLength';
  if (!/[a-z]/.test(pw)) return 'lowercase';
  if (!/[A-Z]/.test(pw)) return 'uppercase';
  if (!/[0-9]/.test(pw)) return 'number';
  if (!/[^a-zA-Z0-9]/.test(pw)) return 'symbol';
  return null;
}

// ─── Destructive-confirmation keyword ─────────────────────────────────────────

/**
 * Compares typed confirmation input against the expected keyword.
 *
 * NFC-normalizes both sides: Hangul keywords such as "삭제" are 2 code points
 * composed but 5 decomposed, and some IMEs emit the decomposed form — a raw
 * `===` would then never match and the user could not complete the action.
 */
export function matchesConfirmationKeyword(input: string, keyword: string): boolean {
  const normalize = (v: string) => v.trim().normalize('NFC').toUpperCase();
  return keyword.trim().length > 0 && normalize(input) === normalize(keyword);
}
