const STORAGE_KEY = 'signupSource';
// The server's channel format: a tag it would drop must not hide a usable one.
const CHANNEL_TAG = /^[a-z0-9][a-z0-9-]{0,39}$/;

export interface SignupSource {
  ref?: string;
  referrerDomain?: string;
}

/**
 * Remember where this tab first arrived from: the link's `ref` (or
 * `utm_source`) tag, and the referring site's domain when it is another site.
 * The first page seen wins, so the redirect to /login and the walk to
 * /sign-up do not overwrite it. Only the domain of the referrer is kept.
 */
export function rememberSignupSource(): void {
  try {
    if (sessionStorage.getItem(STORAGE_KEY) !== null) return;
    const params = new URLSearchParams(window.location.search);
    const source: SignupSource = {};
    const ref = [params.get('ref'), params.get('utm_source')].find(
      (tag) => tag !== null && CHANNEL_TAG.test(tag),
    );
    if (ref) source.ref = ref;
    if (document.referrer) {
      const host = new URL(document.referrer).hostname;
      if (host && host !== window.location.hostname) source.referrerDomain = host;
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(source));
  } catch {
    // Storage blocked or a malformed referrer: the signup is recorded as direct.
  }
}

/** What `rememberSignupSource` kept, to send with the signup request. */
export function getSignupSource(): SignupSource {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (typeof parsed !== 'object' || parsed === null) return {};
    const { ref, referrerDomain } = parsed as Record<string, unknown>;
    return {
      ...(typeof ref === 'string' ? { ref } : {}),
      ...(typeof referrerDomain === 'string' ? { referrerDomain } : {}),
    };
  } catch {
    return {};
  }
}
