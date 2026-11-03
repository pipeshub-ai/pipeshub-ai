import { injectable, inject } from 'inversify';
import crypto from 'crypto';
import { Logger } from '../../../libs/services/logger.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { ConfigService } from '../../tokens_manager/services/cm.service';
import { OAuthTokenService } from './oauth_token.service';
import { ScopeValidatorService } from './scope.validator.service';
import { DelegationService } from './delegation.service';
import {
  getOAuthAppRepository,
  OAuthAppStatus,
  type OAuthAppDto,
} from '../../../libs/repositories/oauth-app';
import { isValidId } from '../../../libs/repositories/ids';
import {
  BadRequestError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  SERVICE_TOKEN_PREFIX,
  SERVICE_TOKEN_APP_CLIENT_ID_PREFIX,
} from '../constants/constants';
import { getUserRepository } from '../../../libs/repositories/user';

const CLIENT_SECRET_BYTES = 32;
const SECONDS_PER_DAY = 86400;

/**
 * The most active tokens one service account may hold at once.
 *
 * This is what makes the list below complete rather than merely large: with
 * issuance bounded well under the list limit, there is no number of tokens an
 * administrator could hold and not be shown, so none can hide from revocation.
 * It is also a reasonable thing to bound on its own — an account accumulating
 * dozens of live credentials is a sign of a rotation that never revokes.
 */
export const SERVICE_TOKEN_MAX_ACTIVE = 50;

/**
 * How many of a service account's tokens the list returns. Far above
 * SERVICE_TOKEN_MAX_ACTIVE, so the revocation view is always complete — the
 * headroom covers tokens issued before the cap existed.
 */
export const SERVICE_TOKEN_LIST_LIMIT = 1000;

export const SERVICE_TOKEN_DEFAULT_EXPIRY_DAYS = 90;
export const SERVICE_TOKEN_MAX_EXPIRY_DAYS = 365;

/**
 * Scopes a service token may not hold in version one, which is read-only.
 *
 * Only `agent:execute` is listed, and that is deliberate. It is the write
 * surface: an agent run can reach connector tools that create Jira issues and
 * send Slack messages. Nothing else in the MCP scope set writes.
 *
 * `semantic:write` is NOT a write scope despite its name — it is what running
 * a search requires. Banning scopes by their name would take reading away and
 * leave the actual write path open, which is exactly backwards.
 */
export const SERVICE_TOKEN_DENIED_SCOPES: readonly string[] = ['agent:execute'];

export interface CreateServiceTokenRequest {
  serviceAccountId: string;
  name: string;
  scopes: string[];
  expiryDays?: number;
}

export interface ServiceTokenListItem {
  id: string;
  name: string;
  serviceAccountId: string;
  scopes: string[];
  createdAt: Date;
  expiresAt: Date;
  lastUsedAt?: Date;
}

export interface ServiceTokenWithSecret
  extends Omit<ServiceTokenListItem, 'lastUsedAt'> {
  accessToken: string;
}

function serviceTokenClientId(orgId: string): string {
  return `${SERVICE_TOKEN_APP_CLIENT_ID_PREFIX}${orgId}`;
}

/**
 * Issues, lists and revokes service tokens: the credential a service account
 * authenticates with.
 *
 * This is deliberately the same machinery as personal access tokens. A service
 * token is an OAuth access token, so signing, hashing, revocation and the
 * `/mcp` auth path are reused unchanged rather than reimplemented. What
 * differs is not the cryptography but the policy around it, and that is the
 * whole of this class:
 *
 *   - it is minted *for* a service account by an administrator, rather than by
 *     a person for themselves;
 *   - scopes must be chosen explicitly, because the PAT path's convenience of
 *     "omit scopes and get everything" is the wrong default for a credential
 *     nobody is watching;
 *   - `agent:execute` is refused, which is what makes version one read-only;
 *   - it always expires.
 */
@injectable()
export class ServiceTokenService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EncryptionService') private encryptionService: EncryptionService,
    @inject('ConfigService') private configService: ConfigService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('DelegationService')
    private delegationService: DelegationService,
  ) {}

  /**
   * The scopes an administrator may choose from: whatever the instance allows
   * for MCP, less the ones version one refuses.
   */
  async getAvailableScopes(): Promise<string[]> {
    const mcpScopes = await this.configService.getMcpScopes();
    return mcpScopes.filter(
      (scope) => !SERVICE_TOKEN_DENIED_SCOPES.includes(scope),
    );
  }

  private async getOrCreateServiceTokenApp(
    orgId: string,
    actingUserId: string,
  ): Promise<OAuthAppDto> {
    const clientId = serviceTokenClientId(orgId);
    const repo = getOAuthAppRepository();
    const existing = await repo.findByClientId(clientId);
    if (existing) {
      return existing;
    }

    const mcpScopes = await this.configService.getMcpScopes();
    const clientSecret = crypto
      .randomBytes(CLIENT_SECRET_BYTES)
      .toString('hex');

    try {
      return await repo.create({
        clientId,
        // Never authenticates anything — service tokens do not go through the
        // /token endpoint — but the schema requires a value, so this is
        // generated and immediately discarded.
        clientSecretEncrypted: this.encryptionService.encrypt(clientSecret),
        name: 'Service Tokens',
        description:
          'Internal pseudo-client that owns service-account tokens for this ' +
          'org. Not a registered OAuth app: never used through the authorize ' +
          'or token-exchange endpoints.',
        orgId,
        createdBy: actingUserId,
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: mcpScopes,
        isConfidential: true,
        status: OAuthAppStatus.ACTIVE,
      });
    } catch (error) {
      // Two admins minting an org's first service token concurrently can both
      // pass the findOne above; the unique clientId index fails the loser's
      // create() — fetch what the winner inserted instead of erroring.
      const createdByWinner = await repo.findByClientId(clientId);
      if (createdByWinner) {
        return createdByWinner;
      }
      throw error;
    }
  }

  /**
   * Confirms the target is a service account in this organisation that is fit
   * to hold a token.
   *
   * `kind` is part of the query rather than checked afterwards, so this cannot
   * be used to mint a token that authenticates as a colleague by passing their
   * user id.
   */
  private async findServiceAccount(
    orgId: string,
    serviceAccountId: string,
    options: { mustBeEnabled: boolean },
  ): Promise<{ id: string; fullName?: string }> {
    if (!isValidId(serviceAccountId)) {
      throw new NotFoundError('Service account not found');
    }
    const account = await getUserRepository().getLiveFields(
      orgId,
      serviceAccountId,
      ['fullName', 'isDisabled'],
      { serviceOnly: true },
    );

    if (!account) {
      throw new NotFoundError('Service account not found');
    }
    // Only minting requires the account to be enabled. Listing and revoking
    // must keep working while it is disabled, or an administrator who has just
    // switched one off could not then clean up the tokens it still holds.
    if (options.mustBeEnabled && account.isDisabled === true) {
      throw new BadRequestError(
        'This service account is disabled. Enable it before issuing a token.',
      );
    }
    return { id: serviceAccountId, fullName: account.fullName };
  }

  private resolveScopes(requested: string[], allowed: string[]): string[] {
    // Unlike a personal access token, an empty list is not a shorthand for
    // "everything the instance allows". A credential that runs unattended
    // should hold the access someone chose for it, not the access nobody
    // thought about.
    if (requested.length === 0) {
      throw new BadRequestError(
        'Choose at least one scope. Service tokens do not default to every scope.',
      );
    }

    const denied = requested.filter((scope) =>
      SERVICE_TOKEN_DENIED_SCOPES.includes(scope),
    );
    if (denied.length > 0) {
      throw new BadRequestError(
        `Service tokens are read-only and cannot hold: ${denied.join(', ')}`,
      );
    }

    const unknown = requested.filter((scope) => !allowed.includes(scope));
    if (unknown.length > 0) {
      throw new BadRequestError(
        `Not available on this instance: ${unknown.join(', ')}`,
      );
    }

    return [...new Set(requested)];
  }

  private resolveExpiryDays(expiryDays: number | undefined): number {
    const days = expiryDays ?? SERVICE_TOKEN_DEFAULT_EXPIRY_DAYS;
    if (!Number.isInteger(days) || days < 1) {
      throw new BadRequestError('Expiry must be a whole number of days');
    }
    // There is no "never" here, unlike personal access tokens. A credential
    // for an unattended process is the one most likely to outlive the reason
    // it was created, so it gets an end date whether or not anyone wants one.
    if (days > SERVICE_TOKEN_MAX_EXPIRY_DAYS) {
      throw new BadRequestError(
        `Expiry cannot exceed ${String(SERVICE_TOKEN_MAX_EXPIRY_DAYS)} days`,
      );
    }
    return days;
  }

  /**
   * Mint a token for a service account. The raw token is returned once and
   * never again — only its hash is stored, as with every other OAuth token.
   */
  async createToken(
    orgId: string,
    actingUserId: string,
    request: CreateServiceTokenRequest,
  ): Promise<ServiceTokenWithSecret> {
    const account = await this.findServiceAccount(
      orgId,
      request.serviceAccountId,
      { mustBeEnabled: true },
    );

    const available = await this.getAvailableScopes();
    const scopes = this.resolveScopes(request.scopes, available);

    // Still run the instance-wide check, so a scope removed from MCP_SCOPES
    // after this method computed `available` cannot slip through.
    const mcpScopes = await this.configService.getMcpScopes();
    this.scopeValidatorService.validateScopesForApp(scopes, mcpScopes);

    const expiryDays = this.resolveExpiryDays(request.expiryDays);
    const lifetimeSeconds = expiryDays * SECONDS_PER_DAY;

    const app = await this.getOrCreateServiceTokenApp(orgId, actingUserId);

    // The token is issued to the service account, not to the administrator
    // minting it. That is the point of the whole feature: what it can read is
    // decided by the service account's place in the permission graph.
    const tokens = await this.oauthTokenService.generateTokens(
      app,
      account.id,
      orgId,
      scopes,
      false, // the long-lived credential itself — no refresh token
      account.fullName,
      undefined,
      {
        accessTokenLifetimeOverrideSeconds: lifetimeSeconds,
        name: request.name,
      },
    );

    // Checked after the token exists rather than before, because counting
    // first and creating second is not a limit: two requests can both count
    // 49 and both create. Counting afterwards means every token is already in
    // the number it is measured against, so of two racing requests at the
    // boundary at least one sees itself over and withdraws — and the token it
    // withdraws is revoked, not merely unreported.
    const active = await this.oauthTokenService.countActiveAccessTokensForUser(
      app.clientId,
      account.id,
    );
    if (active > SERVICE_TOKEN_MAX_ACTIVE) {
      await this.oauthTokenService.revokeAccessTokenById(
        tokens.accessTokenId,
        app.clientId,
        account.id,
        actingUserId,
        'Exceeded the active token limit',
      );
      throw new BadRequestError(
        `This service account already holds ${String(SERVICE_TOKEN_MAX_ACTIVE)} active tokens. Revoke one before issuing another.`,
      );
    }

    this.logger.info('Service token created', {
      orgId,
      serviceAccountId: account.id,
      mintedBy: actingUserId,
      name: request.name,
      scopes,
      expiryDays,
    });

    return {
      id: tokens.accessTokenId,
      name: request.name,
      serviceAccountId: account.id,
      scopes,
      createdAt: new Date(),
      expiresAt: new Date(Date.now() + lifetimeSeconds * 1000),
      accessToken: `${SERVICE_TOKEN_PREFIX}${tokens.accessToken}`,
    };
  }

  /** Active tokens held by one service account. */
  async listTokens(
    orgId: string,
    serviceAccountId: string,
  ): Promise<ServiceTokenListItem[]> {
    const account = await this.findServiceAccount(orgId, serviceAccountId, {
      mustBeEnabled: false,
    });
    const clientId = serviceTokenClientId(orgId);
    const app = await getOAuthAppRepository().findByClientId(clientId);
    if (!app) {
      return [];
    }

    // Every one of them, not the most recent page. This list is what an
    // administrator revokes from, and a token that does not appear here is a
    // credential nobody can switch off. A service account holding more than
    // this is already wrong, and the service logs when the ceiling is reached.
    const tokens = await this.oauthTokenService.listAccessTokensForUser(
      clientId,
      account.id,
      SERVICE_TOKEN_LIST_LIMIT,
    );
    return tokens.map((t) => ({
      id: t.id,
      name: t.name ?? '(unnamed token)',
      serviceAccountId: account.id,
      scopes: t.scopes,
      createdAt: t.createdAt,
      expiresAt: t.expiresAt,
      lastUsedAt: t.lastUsedAt,
    }));
  }

  /**
   * Revoke every token a service account holds.
   *
   * Called when the account is deleted, and again if it is later restored.
   * Restoring reuses the original record, so without this a token issued
   * before the deletion would start working again the moment the name was
   * reused — by whoever reused it, who may not be the person who held it.
   *
   * Deliberately does not check whether the account is enabled, or whether it
   * still exists: it is called precisely at the moments it does not.
   */
  async revokeAllForServiceAccount(
    orgId: string,
    serviceAccountId: string,
  ): Promise<void> {
    // Across every client, not only the service-token app. A service token is
    // not necessarily the only credential the account holds: one minted
    // before the rule against service accounts minting their own, or an
    // access token from an app it was once pointed at, is stored under a
    // different clientId. Leaving those alive is exactly the failure this
    // method exists to prevent, since restoring the account reuses the
    // record and they would start working again for whoever reused the name.
    await this.oauthTokenService.revokeEveryTokenForUser(serviceAccountId);

    // And the tokens that act as this account without being stored against
    // it. A client_credentials token is minted with no userId at all — the
    // identity is resolved per request from the application's
    // tokenIdentityUserId — so it is invisible to the revocation above while
    // authenticating as this account all the same. Deleting or restoring the
    // account has to reach those too, or an old bearer would come back to
    // life along with the name.
    const actingClientIds =
      await getOAuthAppRepository().listClientIdsActingAs(serviceAccountId);

    for (const actingClientId of actingClientIds) {
      await this.oauthTokenService.revokeAllTokensForApp(actingClientId);
    }

    // Tokens this account obtained to act as other people. They are stored
    // against the person they speak as, not against this account, so the
    // sweep above cannot see them — and the middleware cannot stop them
    // either, because the identity it checks is a live colleague's.
    const delegated =
      await this.oauthTokenService.revokeDelegatedTokensForActor(
        serviceAccountId,
      );

    // And the allow-list itself. Same reasoning as the tokens: restoring
    // reuses the original record, so a list of people this account could
    // become would otherwise come back to life under whoever reused the name.
    await this.delegationService.revokeAllForServiceAccount(
      orgId,
      serviceAccountId,
    );

    this.logger.info('Revoked every token held by a service account', {
      orgId,
      serviceAccountId,
      actingClientIds: actingClientIds.length,
      delegatedTokensRevoked: delegated,
    });
  }

  /**
   * Stop a service account acting as anyone, without forgetting who it may
   * act as.
   *
   * This is the disable path, and the difference from
   * {@link revokeAllForServiceAccount} is deliberate: disabling is meant to be
   * reversible, so the allow-list an administrator built is left alone while
   * every token minted from it is killed. Those tokens would otherwise keep
   * working for their last few minutes, because what the auth middleware
   * checks is whether the *person* is disabled, and that person is a
   * colleague in good standing.
   */
  async revokeDelegatedTokensForServiceAccount(
    orgId: string,
    serviceAccountId: string,
  ): Promise<void> {
    const revoked =
      await this.oauthTokenService.revokeDelegatedTokensForActor(
        serviceAccountId,
      );
    if (revoked > 0) {
      this.logger.info('Revoked a service account’s delegated tokens', {
        orgId,
        serviceAccountId,
        revoked,
      });
    }
  }

  /** Revoke one token belonging to a service account. */
  async revokeToken(
    orgId: string,
    actingUserId: string,
    serviceAccountId: string,
    tokenId: string,
    reason?: string,
  ): Promise<void> {
    const account = await this.findServiceAccount(orgId, serviceAccountId, {
      mustBeEnabled: false,
    });
    const clientId = serviceTokenClientId(orgId);
    const app = await getOAuthAppRepository().findByClientId(clientId);
    if (!app) {
      throw new NotFoundError('Service token not found');
    }

    const revoked = await this.oauthTokenService.revokeAccessTokenById(
      tokenId,
      clientId,
      account.id,
      actingUserId,
      reason ?? 'Revoked by administrator',
    );
    if (!revoked) {
      throw new NotFoundError('Service token not found');
    }

    this.logger.info('Service token revoked', {
      orgId,
      serviceAccountId: account.id,
      revokedBy: actingUserId,
      tokenId,
    });
  }
}
