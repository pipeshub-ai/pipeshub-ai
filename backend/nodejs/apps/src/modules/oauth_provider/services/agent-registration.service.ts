import { injectable, inject } from 'inversify';
import crypto from 'crypto';
import { z } from 'zod';
import { Logger } from '../../../libs/services/logger.service';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { forOrg } from '../../../libs/context/orgContext';
import {
  DeviceGrantError,
  InvalidGrantError,
} from '../../../libs/errors/oauth.errors';
import {
  getOAuthTokenRepository,
  AgentRegistrationStatus,
  type AgentRegistrationDto,
} from '../../../libs/repositories/oauth-token';
import { hashOpaqueToken } from '../../../libs/repositories/oauth-token/token-hash';
import { getUserRepository } from '../../../libs/repositories/user';
import { getOrgRepository } from '../../../libs/repositories/org';
import { emailCheck } from '../../../libs/utils/zod-formats';
import { AppConfig } from '../../tokens_manager/config/config';
import { OAuthAppService } from './oauth.app.service';
import { OAuthTokenService } from './oauth_token.service';
import { ScopeValidatorService } from './scope.validator.service';
import { FirstPartyDeviceAppService } from './oauth.first_party_device.service';
import { FIRST_PARTY_DEVICE_CLIENT_ID } from '../constants/constants';
import {
  agentAuthEnabled,
  CLAIM_CODE_TTL_SECONDS,
  CLAIM_POLL_INTERVAL_SECONDS,
  CLAIM_TOKEN_PREFIX,
  MAX_FAILED_CODE_ATTEMPTS,
  REGISTRATION_ID_PREFIX,
  ASSERTION_RETENTION_SECONDS,
  REGISTRATION_WINDOW_SECONDS,
} from '../constants/agent-auth.constants';
import type { OAuthAppDto } from '../../../libs/repositories/oauth-app';
import type { ConsentData, TokenResponse } from '../types/oauth.types';

const BASE62 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
const randomBase62 = (length: number): string =>
  Array.from({ length }, () => BASE62[crypto.randomInt(BASE62.length)]).join(
    '',
  );

const emailSchema = z.string().max(320).check(emailCheck());

export interface ClaimCeremony {
  user_code: string;
  expires_in: number;
  verification_uri: string;
  interval: number;
}

export interface AgentIdentityResponse {
  registration_id: string;
  registration_type: 'service_auth';
  claim_url: string;
  claim_token: string;
  claim_token_expires: string;
  post_claim_scopes: string[];
  claim: ClaimCeremony;
}

export interface AgentClaimResponse {
  registration_id: string;
  claim_attempt_id: string;
  status: 'initiated';
  expires_at: string;
  claim_attempt: ClaimCeremony;
}

interface Attempt {
  token: string;
  userCode: string;
  hash: string;
  userCodeHash: string;
  expiresAt: Date;
}

interface SessionUser {
  userId: string;
  orgId: string;
  email: string;
}

type AgentTokenResponse = TokenResponse & {
  identity_assertion?: string;
  assertion_expires?: string;
};

const fail = (code: string, message: string): DeviceGrantError =>
  new DeviceGrantError(code, message);

const sameHash = (a: string, b: string): boolean =>
  a.length === b.length &&
  crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));

/**
 * auth.md agent registration, `service_auth` only: an agent names an email,
 * the person signs in and types a code, the agent polls for a token. Nothing
 * here creates a user: a registration binds to an existing, signed-in one.
 */
@injectable()
export class AgentRegistrationService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthAppService') private oauthAppService: OAuthAppService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('FirstPartyDeviceAppService')
    private firstPartyDeviceAppService: FirstPartyDeviceAppService,
    @inject('AppConfig') private appConfig: AppConfig,
  ) {}

  // ---- POST /agent/identity ----------------------------------------------------------------------------------------

  async register(
    type: unknown,
    loginHint: unknown,
  ): Promise<AgentIdentityResponse> {
    if (type === 'anonymous') {
      throw fail(
        'anonymous_not_enabled',
        'anonymous registration is not enabled',
      );
    }
    if (type === 'identity_assertion') {
      throw fail(
        'issuer_not_enabled',
        'no identity issuer is trusted by this service',
      );
    }
    if (type !== 'service_auth') {
      throw fail('invalid_request', 'type must be service_auth');
    }
    const app = await this.firstPartyApp();
    if (!app) {
      throw fail(
        'service_auth_not_enabled',
        'service_auth registration is not enabled',
      );
    }
    const email = emailSchema.safeParse(loginHint);
    if (!email.success) {
      throw fail('invalid_request', 'login_hint must be an email address');
    }

    const claimToken = CLAIM_TOKEN_PREFIX + randomBase62(43);
    const now = Date.now();
    const expiresAt = new Date(now + REGISTRATION_WINDOW_SECONDS * 1000);
    // Nothing here looks the email up: a known and an unknown address get the same answer.
    const attempt = this.newAttempt();
    const reg = await getOAuthTokenRepository().createAgentRegistration({
      claimEmail: email.data.trim().toLowerCase(),
      claimTokenHash: hashOpaqueToken(claimToken),
      attemptTokenHash: attempt.hash,
      userCodeHash: attempt.userCodeHash,
      attemptExpiresAt: attempt.expiresAt,
      expiresAt,
      // An unclaimed row goes with its window; consuming it moves this past the assertion.
      purgeAt: expiresAt,
    });
    const registrationId = REGISTRATION_ID_PREFIX + reg._id;
    this.logger.info('registration.created', { registrationId });

    return {
      registration_id: registrationId,
      registration_type: 'service_auth',
      claim_url: this.claimEndpoint(),
      claim_token: claimToken,
      claim_token_expires: expiresAt.toISOString(),
      post_claim_scopes: this.scopesOf(app),
      claim: this.ceremony(attempt),
    };
  }

  // ---- POST /agent/identity/claim ----------------------------------------------------------------------------------

  async startClaim(
    claimToken: string,
    email: string,
  ): Promise<AgentClaimResponse> {
    const repo = getOAuthTokenRepository();
    const reg = await repo.findAgentRegistrationByClaimHash(
      hashOpaqueToken(claimToken),
    );
    const claimEmail = email.trim().toLowerCase();
    // A wrong email reads like an unknown token, so the endpoint does not confirm which address a token is bound to.
    if (
      !reg ||
      reg.claimEmail !== claimEmail ||
      reg.status === AgentRegistrationStatus.REVOKED
    ) {
      throw fail(
        'invalid_claim_token',
        'claim_token is not valid for this email',
      );
    }
    if (
      reg.status === AgentRegistrationStatus.CLAIMED ||
      reg.status === AgentRegistrationStatus.CONSUMED
    ) {
      throw fail(
        'claimed_or_in_flight',
        'this registration has already been claimed',
      );
    }
    if (
      reg.status !== AgentRegistrationStatus.PENDING ||
      reg.expiresAt.getTime() <= Date.now()
    ) {
      throw fail('claim_expired', 'the claim window has closed');
    }
    const attempt = this.newAttempt();
    const started = await repo.startAgentClaimAttempt(reg._id, {
      attemptTokenHash: attempt.hash,
      userCodeHash: attempt.userCodeHash,
      attemptExpiresAt: attempt.expiresAt,
    });
    if (!started) {
      throw fail(
        'claimed_or_in_flight',
        'this registration has already been claimed',
      );
    }
    return {
      registration_id: REGISTRATION_ID_PREFIX + reg._id,
      claim_attempt_id: 'cla_' + crypto.randomBytes(12).toString('hex'),
      status: 'initiated',
      expires_at: attempt.expiresAt.toISOString(),
      claim_attempt: this.ceremony(attempt),
    };
  }

  // ---- the claim page (session) ------------------------------------------------------------------------------------

  /** What the signed-in person is shown before they type the code. */
  async getClaimContext(
    attemptToken: string,
    user: SessionUser,
  ): Promise<Pick<ConsentData, 'scopes' | 'user'>> {
    await this.pendingAttempt(attemptToken, user);
    const app = await this.firstPartyApp();
    if (!app) {
      throw fail(
        'service_auth_not_enabled',
        'service_auth registration is not enabled',
      );
    }
    return {
      scopes: this.scopeValidatorService.getScopeDefinitions(
        this.scopesOf(app),
      ),
      user: { email: user.email },
    };
  }

  async completeClaim(
    attemptToken: string,
    userCode: string | undefined,
    action: 'approve' | 'deny',
    user: SessionUser,
  ): Promise<'claimed' | 'revoked'> {
    const reg = await this.pendingAttempt(attemptToken, user);
    const repo = getOAuthTokenRepository();
    if (action === 'deny') {
      const denied = await repo.decideAgentRegistration(reg._id, {
        status: AgentRegistrationStatus.REVOKED,
      });
      // An approval that won the race has already made the registration usable.
      if (!denied) {
        throw fail('invalid_request', 'this claim link is no longer valid');
      }
      this.logger.info('claim.denied', {
        registrationId: REGISTRATION_ID_PREFIX + reg._id,
      });
      return 'revoked';
    }
    const typed = hashOpaqueToken((userCode ?? '').replace(/\s+/g, ''));
    if (!sameHash(typed, reg.userCodeHash)) {
      await repo.recordAgentFailedAttempt(reg._id, MAX_FAILED_CODE_ATTEMPTS);
      throw fail('invalid_user_code', 'that code is not right');
    }
    const app = await this.firstPartyApp();
    if (!app) {
      throw fail(
        'service_auth_not_enabled',
        'service_auth registration is not enabled',
      );
    }
    // The person and org come from the session, never from the request. The scopes are the list the claim page showed.
    const decided = await repo.decideAgentRegistration(reg._id, {
      status: AgentRegistrationStatus.CLAIMED,
      userId: user.userId,
      orgId: user.orgId,
      scopes: this.scopesOf(app),
    });
    if (!decided) {
      throw fail('invalid_request', 'this claim link is no longer valid');
    }
    this.logger.info('claim.confirmed', {
      registrationId: REGISTRATION_ID_PREFIX + reg._id,
      userId: user.userId,
      orgId: user.orgId,
    });
    return 'claimed';
  }

  // ---- token endpoint ----------------------------------------------------------------------------------------------

  async claimGrant(claimToken: string): Promise<AgentTokenResponse> {
    const repo = getOAuthTokenRepository();
    const reg = await repo.findAgentRegistrationByClaimHash(
      hashOpaqueToken(claimToken),
    );
    if (reg?.status === AgentRegistrationStatus.REVOKED) {
      throw fail(
        'access_denied',
        'the person denied or revoked this registration',
      );
    }
    if (reg?.status === AgentRegistrationStatus.CONSUMED) {
      throw new InvalidGrantError('claim_token has already been used');
    }
    if (
      !reg ||
      reg.status === AgentRegistrationStatus.EXPIRED ||
      reg.expiresAt.getTime() <= Date.now()
    ) {
      throw fail('expired_token', 'The claim ceremony window has closed.');
    }
    if (reg.status === AgentRegistrationStatus.PENDING) {
      const now = Date.now();
      if (
        reg.lastPolledAt &&
        now - reg.lastPolledAt.getTime() < CLAIM_POLL_INTERVAL_SECONDS * 1000
      ) {
        throw fail('slow_down', 'polling too frequently');
      }
      await repo.recordAgentPoll(reg._id, new Date(now));
      throw fail(
        'authorization_pending',
        'The user has not yet completed the ceremony.',
      );
    }
    // Claim before minting so two concurrent polls cannot both issue a token.
    const taken = await repo.consumeAgentRegistration(
      reg._id,
      new Date(Date.now() + ASSERTION_RETENTION_SECONDS * 1000),
    );
    if (!taken) {
      throw new InvalidGrantError('claim_token has already been used');
    }
    const registrationId = REGISTRATION_ID_PREFIX + taken._id;
    const tokens = await this.issue(taken, 'access_denied');
    const { assertion, expiresAt } =
      this.oauthTokenService.signAgentAssertion(registrationId);
    this.logger.info('token.issued', { registrationId, grant: 'claim' });
    return {
      ...tokens,
      identity_assertion: assertion,
      assertion_expires: expiresAt.toISOString(),
    };
  }

  async jwtBearerGrant(assertion: string): Promise<TokenResponse> {
    const registrationId =
      this.oauthTokenService.verifyAgentAssertion(assertion);
    const reg = await getOAuthTokenRepository().findAgentRegistrationById(
      registrationId.slice(REGISTRATION_ID_PREFIX.length),
    );
    if (!reg || reg.status !== AgentRegistrationStatus.CONSUMED) {
      throw new InvalidGrantError(
        'the registration behind this assertion is not active',
      );
    }
    const tokens = await this.issue(reg, 'invalid_grant');
    this.logger.info('token.issued', { registrationId, grant: 'jwt-bearer' });
    return tokens;
  }

  // ---- the approving person's own registrations (session) ---------------------------------------------------------

  async listForUser(user: SessionUser): Promise<{
    registrations: {
      id: string;
      status: AgentRegistrationStatus;
      created_at: string;
      updated_at: string;
    }[];
  }> {
    const rows = await getOAuthTokenRepository().listAgentRegistrationsForUser(
      user.userId,
    );
    return {
      registrations: rows.map((r) => ({
        id: REGISTRATION_ID_PREFIX + r._id,
        status: r.status,
        created_at: r.createdAt.toISOString(),
        updated_at: r.updatedAt.toISOString(),
      })),
    };
  }

  /** Another person's registration answers like one that does not exist. */
  async revokeForUser(id: string, user: SessionUser): Promise<{ ok: true }> {
    const revoked = await getOAuthTokenRepository().revokeAgentRegistration(
      id.slice(REGISTRATION_ID_PREFIX.length),
      user.userId,
    );
    if (!revoked) throw new NotFoundError('Registration not found');
    this.logger.info('registration.revoked', {
      registrationId: id,
      userId: user.userId,
    });
    return { ok: true };
  }

  // ---- helpers -----------------------------------------------------------------------------------------------------

  /** The first-party agent app, or null when registration is off or the app cannot be had. */
  private async firstPartyApp(): Promise<OAuthAppDto | null> {
    if (!agentAuthEnabled()) return null;
    if (!(await this.firstPartyDeviceAppService.getOrCreate())) return null;
    return this.oauthAppService.getAppByClientId(FIRST_PARTY_DEVICE_CLIENT_ID);
  }

  /** What a device sign-in by the agent app grants, less offline_access: these grants never issue a refresh token. */
  private scopesOf(app: OAuthAppDto): string[] {
    return this.scopeValidatorService
      .resolveGrantedScopes(app.allowedScopes, app.allowedScopes)
      .granted.filter((scope) => scope !== 'offline_access');
  }

  private async issue(
    reg: AgentRegistrationDto,
    goneCode: 'access_denied' | 'invalid_grant',
  ): Promise<TokenResponse> {
    const orgId = reg.claimedOrgId;
    const userId = reg.claimedUserId;
    const app = await this.firstPartyApp();
    // What the person approved, less anything the app has stopped allowing since; an app widened later adds nothing.
    const now = new Set(app ? this.scopesOf(app) : []);
    const scopes = (reg.approvedScopes ?? []).filter((s) => now.has(s));
    if (!orgId || !userId || !app || scopes.length === 0) {
      throw new InvalidGrantError('the registration cannot be used');
    }
    return forOrg(orgId, 'oauth.agent', async () => {
      // The person approved earlier; they or their org may have been removed since.
      const user = await getUserRepository().getLiveFields(
        orgId,
        userId,
        ['fullName'],
        { strict: true },
      );
      const org = await getOrgRepository().getAccountType(orgId);
      if (!user || !org) {
        throw goneCode === 'access_denied'
          ? fail(
              'access_denied',
              'the account that approved this registration no longer exists',
            )
          : new InvalidGrantError(
              'the account behind this registration no longer exists',
            );
      }
      const tokens = await this.oauthTokenService.generateTokens(
        app,
        userId,
        orgId,
        scopes,
        false,
        user.fullName,
        (org as { accountType?: string }).accountType,
        {
          recheckAppScopes: true,
          agentRegistrationId: REGISTRATION_ID_PREFIX + reg._id,
        },
      );
      return {
        access_token: tokens.accessToken,
        token_type: tokens.tokenType,
        expires_in: tokens.expiresIn,
        scope: tokens.scope,
      };
    });
  }

  /** The registration this link names, when it can still be claimed by this signed-in person. */
  private async pendingAttempt(
    attemptToken: string,
    user: SessionUser,
  ): Promise<AgentRegistrationDto> {
    const reg =
      await getOAuthTokenRepository().findAgentRegistrationByAttemptHash(
        hashOpaqueToken(attemptToken),
      );
    if (!reg || reg.status !== AgentRegistrationStatus.PENDING) {
      throw fail('invalid_request', 'this claim link is not valid');
    }
    if (
      reg.attemptExpiresAt.getTime() <= Date.now() ||
      reg.expiresAt.getTime() <= Date.now()
    ) {
      throw fail('claim_expired', 'this claim link has expired');
    }
    if (reg.claimEmail !== user.email.trim().toLowerCase()) {
      throw fail(
        'access_denied',
        'this claim was started for a different account',
      );
    }
    return reg;
  }

  private newAttempt(): Attempt {
    const token = randomBase62(43);
    const userCode = String(crypto.randomInt(0, 1_000_000)).padStart(6, '0');
    return {
      token,
      userCode,
      hash: hashOpaqueToken(token),
      userCodeHash: hashOpaqueToken(userCode),
      expiresAt: new Date(Date.now() + CLAIM_CODE_TTL_SECONDS * 1000),
    };
  }

  private ceremony(a: Attempt): ClaimCeremony {
    const uri = new URL('/agent/claim', this.appConfig.frontendUrl);
    uri.searchParams.set('claim_attempt_token', a.token);
    return {
      user_code: a.userCode,
      expires_in: CLAIM_CODE_TTL_SECONDS,
      verification_uri: uri.toString(),
      interval: CLAIM_POLL_INTERVAL_SECONDS,
    };
  }

  private claimEndpoint(): string {
    return `${this.appConfig.oauthIssuer}/api/v1/agent/identity/claim`;
  }
}
