import { createHash, randomBytes, timingSafeEqual } from 'crypto';
import { injectable, inject } from 'inversify';
import { ICacheService } from '../../../libs/services/cache/cacheService.interface';
import { UnauthorizedError } from '../../../libs/errors/http.errors';

/**
 * Hands SAML sign-in tokens to the desktop app or the web app.
 *
 * The SAML assertion lands in the user's browser, not the app, and the only
 * way back is a `pipeshub://` deep link that any local app could register for.
 * So the link carries a short-lived code, never the tokens, and redeeming the
 * code needs the PKCE verifier that only the app that started the flow holds.
 * A web sign-in started from the login page does the same with a verifier kept
 * in sessionStorage. One started at the IdP has no challenge from the app, so
 * it uses a server-made verifier the browser holds as an HttpOnly cookie.
 */

export const HANDOFF_TTL_SECONDS = 120;
const KEY_PREFIX = 'saml_desktop_handoff:';

export const DESKTOP_STATE_PREFIX = 'phd.';
const STATE_PATTERN = /^phd\.[A-Za-z0-9_-]{1,124}$/;
const CODE_CHALLENGE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const CODE_VERIFIER_PATTERN = /^[A-Za-z0-9._~-]{43,128}$/;

export interface SamlDesktopTokens {
  accessToken: string;
  refreshToken: string;
}

interface HandoffRecord extends SamlDesktopTokens {
  codeChallenge: string;
}

export function isValidDesktopState(value: unknown): value is string {
  return typeof value === 'string' && STATE_PATTERN.test(value);
}

export function isValidCodeChallenge(value: unknown): value is string {
  return typeof value === 'string' && CODE_CHALLENGE_PATTERN.test(value);
}

function s256(verifier: string): string {
  return createHash('sha256').update(verifier).digest('base64url');
}

function matchesChallenge(verifier: string, codeChallenge: string): boolean {
  const actual = Buffer.from(s256(verifier));
  const expected = Buffer.from(codeChallenge);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

@injectable()
export class SamlDesktopHandoffService {
  constructor(@inject('RedisService') private redisService: ICacheService) {}

  async issue(tokens: SamlDesktopTokens, codeChallenge: string): Promise<string> {
    const code = randomBytes(32).toString('hex');
    const record: HandoffRecord = { ...tokens, codeChallenge };
    await this.redisService.set(`${KEY_PREFIX}${code}`, record, {
      ttl: HANDOFF_TTL_SECONDS,
    });
    return code;
  }

  async issueForBrowser(
    tokens: SamlDesktopTokens,
  ): Promise<{ code: string; binder: string }> {
    const binder = randomBytes(32).toString('base64url');
    return { code: await this.issue(tokens, s256(binder)), binder };
  }

  /**
   * Redeems the code if any of the verifiers matches its challenge. A web
   * exchange may hold both a sessionStorage verifier and a binder cookie and
   * cannot tell which the callback used; either way the code is claimed once.
   */
  async redeem(
    code: string,
    codeVerifiers: string | readonly string[],
  ): Promise<SamlDesktopTokens> {
    const candidates = (
      typeof codeVerifiers === 'string' ? [codeVerifiers] : codeVerifiers
    ).filter((verifier) => CODE_VERIFIER_PATTERN.test(verifier));
    if (!/^[0-9a-f]{64}$/.test(code) || candidates.length === 0) {
      throw new UnauthorizedError('Invalid or expired sign-in code');
    }
    const key = `${KEY_PREFIX}${code}`;
    // Looked up first so a guessed code never creates a claim key in Redis.
    const record = await this.redisService.get<HandoffRecord>(key);
    if (!record) {
      throw new UnauthorizedError('Invalid or expired sign-in code');
    }
    // INCR is atomic, so of two concurrent redeems only the first gets past here.
    const claims = await this.redisService.increment(`${key}:claimed`, {
      ttl: HANDOFF_TTL_SECONDS,
    });
    if (claims !== 1) {
      throw new UnauthorizedError('Invalid or expired sign-in code');
    }
    // Deleted before the verifier check, so a wrong guess burns the code.
    await this.redisService.delete(key);
    if (
      !candidates.some((verifier) =>
        matchesChallenge(verifier, record.codeChallenge),
      )
    ) {
      throw new UnauthorizedError('Invalid or expired sign-in code');
    }
    return { accessToken: record.accessToken, refreshToken: record.refreshToken };
  }
}
