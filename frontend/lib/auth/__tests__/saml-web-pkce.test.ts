import { describe, it, expect, afterEach, vi } from 'vitest';
import { beginSamlWebPkce, takeSamlWebVerifier } from '../saml-web-pkce';
import { pkceChallenge } from '../desktop-oauth';

afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe('saml web PKCE', () => {
  it('stores a verifier and returns its S256 challenge', async () => {
    const challenge = await beginSamlWebPkce();
    const verifier = sessionStorage.getItem('saml_web_pkce_verifier');

    expect(verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(challenge).toBe(await pkceChallenge(verifier as string));
  });

  it('sends one challenge for a double click, whichever redirect wins', async () => {
    const [first, second] = await Promise.all([beginSamlWebPkce(), beginSamlWebPkce()]);

    expect(first).toBe(second);
    expect(first).toBe(await pkceChallenge(sessionStorage.getItem('saml_web_pkce_verifier') as string));
  });

  it('hands the verifier out once', async () => {
    await beginSamlWebPkce();
    const verifier = sessionStorage.getItem('saml_web_pkce_verifier');

    expect(takeSamlWebVerifier()).toBe(verifier);
    expect(takeSamlWebVerifier()).toBeNull();
  });

  it('falls back to the cookie flow when the verifier cannot be kept', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });

    expect(await beginSamlWebPkce()).toBeNull();
  });
});
