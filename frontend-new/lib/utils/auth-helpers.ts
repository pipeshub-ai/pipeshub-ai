// ─── JWT User ─────────────────────────────────────────────────────────────────

export interface JwtUser {
  name?: string;
  email?: string;
  initials?: string;
}

/**
 * Decodes the payload of a JWT and extracts name / email / initials.
 * Does NOT verify the signature. Safe for display purposes only.
 */
export function decodeJwtUser(token: string): JwtUser {
  try {
    const payload = token.split('.')[1];
    if (!payload) return {};
    // Convert base64url → base64 and restore required '=' padding.
    // Without padding, atob throws a DOMException for payloads whose
    // length mod 4 is not 0 (true for most real JWTs).
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const json = JSON.parse(atob(padded));
    // Backend reset-password JWT uses `userEmail`; regular auth JWTs use `email` or `sub`
    const email: string = json.email ?? json.userEmail ?? json.sub ?? '';
    const name: string = json.name ?? json.fullName ?? '';
    const initials = name
      ? name
          .split(' ')
          .slice(0, 2)
          .map((w: string) => w[0]?.toUpperCase() ?? '')
          .join('')
      : email.slice(0, 2).toUpperCase();
    return { name, email, initials };
  } catch {
    return {};
  }
}


