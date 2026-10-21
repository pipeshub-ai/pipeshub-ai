import { TokenScopes } from '../enums/token-scopes.enum';
import { AuthTokenService } from './authtoken.service';

// User-action scopes are signed with a derived key (token-scopes.enum.ts), so
// they cannot be minted by a service-to-service issuer.
export type UserActionScope =
  | typeof TokenScopes.PASSWORD_RESET
  | typeof TokenScopes.VALIDATE_EMAIL
  | typeof TokenScopes.TOKEN_REFRESH
  | typeof TokenScopes.ORG_EMAIL_VERIFY
  | typeof TokenScopes.EMAIL_VERIFIED;

export type ServiceScope = Exclude<TokenScopes, UserActionScope>;

export interface ServiceTokenClaims {
  userId: string;
  orgId: string;
  scopes: readonly ServiceScope[];
  conversationId?: string;
  messageId?: string;
  runId?: string;
  isServiceAccount?: boolean;
}

export interface IServiceTokenIssuer {
  issue(claims: ServiceTokenClaims, ttl: '1m' | '5m'): string;
}

export class JwtServiceTokenIssuer implements IServiceTokenIssuer {
  constructor(private readonly auth: AuthTokenService) {}

  issue(claims: ServiceTokenClaims, ttl: '1m' | '5m'): string {
    const {
      userId,
      orgId,
      scopes,
      conversationId,
      messageId,
      runId,
      isServiceAccount,
    } = claims;
    return this.auth.generateScopedToken(
      {
        userId,
        orgId,
        scopes: [...scopes],
        ...(isServiceAccount ? { isServiceAccount: true } : {}),
        ...(conversationId !== undefined ? { conversationId } : {}),
        ...(messageId !== undefined ? { messageId } : {}),
        ...(runId !== undefined ? { runId } : {}),
      },
      ttl,
    );
  }
}
