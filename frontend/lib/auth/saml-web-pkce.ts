import { pkceChallenge } from '@/lib/auth/desktop-oauth';

/**
 * PKCE for a web SAML sign-in started from the login page. The verifier stays in this tab's
 * sessionStorage on the frontend's origin, so the success page can redeem the code even when
 * the API is on another site, where the binder cookie would not be sent.
 */
const VERIFIER_KEY = 'saml_web_pkce_verifier';

function newVerifier(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/** The challenge to send with the sign-in, or null to fall back to the binder cookie. */
export async function beginSamlWebPkce(): Promise<string | null> {
  try {
    // Stored before the first await and reused, so a double click sends one challenge
    // whichever of its redirects wins.
    let verifier = sessionStorage.getItem(VERIFIER_KEY);
    if (!verifier) {
      verifier = newVerifier();
      sessionStorage.setItem(VERIFIER_KEY, verifier);
    }
    return await pkceChallenge(verifier);
  } catch {
    // No crypto.subtle (plain HTTP) or no sessionStorage: the binder cookie still works same-site.
    return null;
  }
}

/** The stored verifier, removed so it is used for one exchange only. */
export function takeSamlWebVerifier(): string | null {
  try {
    const verifier = sessionStorage.getItem(VERIFIER_KEY);
    sessionStorage.removeItem(VERIFIER_KEY);
    return verifier;
  } catch {
    return null;
  }
}
