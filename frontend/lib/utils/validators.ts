/**
 * Returns true if the string looks like a valid email address.
 */
export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// ─── Password ─────────────────────────────────────────────────────────────────

export const PASSWORD_RULES =
  'At least 8 characters: lowercase, uppercase, number, symbol.';

/**
 * Validates a password against the standard policy.
 * Returns an error message string if invalid, or null if valid.
 */
export function validatePassword(pw: string): string | null {
  if (pw.length < 8) return 'Password must be at least 8 characters.';
  if (!/[a-z]/.test(pw)) return 'Password must contain a lowercase letter.';
  if (!/[A-Z]/.test(pw)) return 'Password must contain an uppercase letter.';
  if (!/[0-9]/.test(pw)) return 'Password must contain a number.';
  if (!/[^a-zA-Z0-9]/.test(pw)) return 'Password must contain a symbol.';
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
