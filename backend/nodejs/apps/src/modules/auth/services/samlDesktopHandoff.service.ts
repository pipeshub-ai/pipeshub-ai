import { createHash, randomBytes } from 'crypto';
import { injectable, inject } from 'inversify';
import { ICacheService } from '../../../libs/services/cache/cacheService.interface';
import { UnauthorizedError } from '../../../libs/errors/http.errors';

/**
 * Hands SAML sign-in tokens to the client that started the sign-in.
 *
 * The SAML assertion lands in the user's browser, so tokens must not travel in
 * cookies (readable by page scripts) or links. The redirect carries a
 * short-lived code, never the tokens. A sign-in that started on the PipesHub
 * login page also stores a PKCE challenge; redeeming then needs the verifier
 * from that tab. An IdP-initiated sign-in (Okta app tile) has no such start,
 * so the code alone is enough — still single-use and 120s.
 */

const HANDOFF_TTL_SECONDS = 120;
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
  codeChallenge?: string;
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

@injectable()
export class SamlDesktopHandoffService {
  constructor(@inject('RedisService') private redisService: ICacheService) {}

  async issue(tokens: SamlDesktopTokens, codeChallenge?: string): Promise<string> {
    const code = randomBytes(32).toString('hex');
    const record: HandoffRecord = { ...tokens };
    if (codeChallenge) record.codeChallenge = codeChallenge;
    await this.redisService.set(`${KEY_PREFIX}${code}`, record, {
      ttl: HANDOFF_TTL_SECONDS,
    });
    return code;
  }

  async redeem(code: string, codeVerifier?: string): Promise<SamlDesktopTokens> {
    if (!/^[0-9a-f]{64}$/.test(code)) {
      throw new UnauthorizedError('Invalid or expired sign-in code');
    }
    const key = `${KEY_PREFIX}${code}`;
    const record = await this.redisService.get<HandoffRecord>(key);
    // Deleted before the verifier check, so a wrong guess burns a PKCE code.
    await this.redisService.delete(key);
    if (!record) {
      throw new UnauthorizedError('Invalid or expired sign-in code');
    }
    if (record.codeChallenge) {
      if (
        !codeVerifier ||
        !CODE_VERIFIER_PATTERN.test(codeVerifier) ||
        s256(codeVerifier) !== record.codeChallenge
      ) {
        throw new UnauthorizedError('Invalid or expired sign-in code');
      }
    }
    return { accessToken: record.accessToken, refreshToken: record.refreshToken };
  }
}
