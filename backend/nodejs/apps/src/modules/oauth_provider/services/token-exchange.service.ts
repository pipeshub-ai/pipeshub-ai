import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { TokenExchangeError } from '../../../libs/errors/oauth.errors';
import { Users } from '../../user_management/schema/users.schema';
import { OAuthApp } from '../schema/oauth.app.schema';
import { OAuthTokenService } from './oauth_token.service';
import { DelegationService } from './delegation.service';
import { SERVICE_TOKEN_DENIED_SCOPES } from './service-token.service';
import {
  SERVICE_TOKEN_APP_CLIENT_ID_PREFIX,
  SERVICE_TOKEN_PREFIX,
} from '../constants/constants';
import { TokenResponse } from '../types/oauth.types';

/**
 * How long a delegated token lives.
 *
 * Short on purpose, and the main reason delegation is defensible at all. The
 * allow-list says who may be acted as; this says for how long a mistake lasts.
 * Five minutes is long enough for a request to be made and retried, and short
 * enough that a token captured in a log or a crash dump is worthless by the
 * time anyone reads it.
 */
export const DELEGATED_TOKEN_LIFETIME_SECONDS = 300;

/**
 * The RFC 8693 identifier for the tokens on both ends of this exchange. The
 * service token going in and the delegated token coming out are both ordinary
 * OAuth access tokens, so both are `access_token`.
 */
export const ACCESS_TOKEN_TYPE =
  'urn:ietf:params:oauth:token-type:access_token';

/** One description for every refusal about the subject. See TokenExchangeError. */
const SUBJECT_REFUSED_MESSAGE =
  'This service account may not act as the requested subject.';

export interface TokenExchangeRequest {
  subjectToken: string;
  subjectTokenType?: string;
  requestedSubject?: string;
  /** Space-separated, as RFC 6749 sends scopes. */
  scope?: string;
}

/**
 * Issues a short-lived token that lets a service account act as one named
 * person, following RFC 8693.
 *
 * The design in one line: the token that comes out is an ordinary user token.
 * It names the person in `userId`, which is the claim every authorisation
 * check in both halves of the product already reads, so nothing downstream
 * needs to learn what delegation is. That is the whole reason this shape was
 * chosen over passing a header alongside a service token — a header would
 * have to be understood correctly by every route that exists now and every
 * route added later, and the first one to forget would be a silent privilege
 * bug.
 *
 * What makes it safe is not the exchange but what surrounds it: an allow-list
 * an administrator maintains by name, a refusal to delegate to administrators,
 * a lifetime measured in minutes, no refresh token, and a record of every
 * attempt including the ones that failed.
 */
@injectable()
export class TokenExchangeService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('DelegationService') private delegationService: DelegationService,
  ) {}

  async exchange(request: TokenExchangeRequest): Promise<TokenResponse> {
    if (
      request.subjectTokenType !== undefined &&
      request.subjectTokenType !== ACCESS_TOKEN_TYPE
    ) {
      throw new TokenExchangeError(
        'invalid_request',
        `subject_token_type must be ${ACCESS_TOKEN_TYPE}`,
      );
    }

    const actor = await this.resolveActor(request.subjectToken);

    const requestedSubject = (request.requestedSubject ?? '').trim();
    if (requestedSubject === '') {
      throw new TokenExchangeError(
        'invalid_request',
        'requested_subject is required: name the person to act as.',
      );
    }

    const subject = await this.delegationService.resolveSubject(
      actor.orgId,
      requestedSubject,
    );

    if (!subject.ok) {
      await this.delegationService.recordEvent({
        orgId: actor.orgId,
        serviceAccountId: actor.serviceAccountId,
        subjectUserId: subject.userId,
        requestedSubject,
        outcome: 'refused',
        refusedReason: subject.refusedReason,
      });
      throw new TokenExchangeError('invalid_target', SUBJECT_REFUSED_MESSAGE);
    }

    const allowed = await this.delegationService.isDelegationAllowed(
      actor.orgId,
      actor.serviceAccountId,
      subject.userId,
    );
    if (!allowed) {
      await this.delegationService.recordEvent({
        orgId: actor.orgId,
        serviceAccountId: actor.serviceAccountId,
        subjectUserId: subject.userId,
        requestedSubject,
        outcome: 'refused',
        refusedReason: 'not_on_allow_list',
      });
      throw new TokenExchangeError('invalid_target', SUBJECT_REFUSED_MESSAGE);
    }

    const scopes = this.resolveScopes(request.scope, actor.scopes);

    const app = await OAuthApp.findOne({ clientId: actor.clientId }).exec();
    if (!app) {
      // The service token verified against a client that no longer exists.
      throw new TokenExchangeError(
        'invalid_grant',
        'The service token is no longer valid.',
      );
    }

    const tokens = await this.oauthTokenService.generateTokens(
      app,
      subject.userId,
      actor.orgId,
      scopes,
      false,
      subject.fullName,
      undefined,
      {
        accessTokenLifetimeOverrideSeconds: DELEGATED_TOKEN_LIFETIME_SECONDS,
        actorUserId: actor.serviceAccountId,
        name: `Delegated to ${subject.email}`,
      },
    );

    await this.delegationService.recordEvent({
      orgId: actor.orgId,
      serviceAccountId: actor.serviceAccountId,
      subjectUserId: subject.userId,
      requestedSubject,
      outcome: 'issued',
      scopes,
      accessTokenId: tokens.accessTokenId,
      jti: tokens.jti,
      expiresAt: new Date(Date.now() + tokens.expiresIn * 1000),
    });

    this.logger.info('Delegated token issued', {
      orgId: actor.orgId,
      serviceAccountId: actor.serviceAccountId,
      subjectUserId: subject.userId,
      scopes,
      expiresIn: tokens.expiresIn,
    });

    return {
      access_token: `${SERVICE_TOKEN_PREFIX}${tokens.accessToken}`,
      issued_token_type: ACCESS_TOKEN_TYPE,
      token_type: tokens.tokenType,
      expires_in: tokens.expiresIn,
      scope: tokens.scope,
    };
  }

  /**
   * Establishes that the caller is a live service account presenting its own
   * service token, and nothing else.
   *
   * The subject token is the only authentication this grant has — there is no
   * client secret — so these checks are the door. Each one closes a different
   * way of turning some other credential into a delegated one.
   */
  private async resolveActor(subjectToken: string): Promise<{
    serviceAccountId: string;
    orgId: string;
    clientId: string;
    scopes: string[];
  }> {
    if (subjectToken.trim() === '') {
      throw new TokenExchangeError(
        'invalid_request',
        'subject_token is required.',
      );
    }

    let payload;
    try {
      payload = await this.oauthTokenService.verifyAccessToken(subjectToken);
    } catch {
      throw new TokenExchangeError(
        'invalid_grant',
        'The subject token is not valid.',
      );
    }

    // Only a service token may be exchanged. A personal access token must not
    // be, because a PAT already speaks as its owner and exchanging one would
    // let any employee act as a colleague.
    if (!payload.client_id.startsWith(SERVICE_TOKEN_APP_CLIENT_ID_PREFIX)) {
      throw new TokenExchangeError(
        'invalid_grant',
        'Only a service token can be exchanged.',
      );
    }

    // And not a token that is itself delegated. A delegated token is minted
    // against this same client, so the check above does not separate them;
    // without this, a service account allowed to act as one person could hop
    // from there to anyone that person's own delegations reached.
    if (payload.act !== undefined) {
      throw new TokenExchangeError(
        'invalid_grant',
        'A delegated token cannot be exchanged again.',
      );
    }

    const account = await Users.findOne({
      _id: payload.userId,
      orgId: payload.orgId,
      kind: 'service',
      isDeleted: false,
    })
      .select('isDisabled')
      .lean()
      .exec();

    // Belt and braces with the clause above: a token whose `userId` names a
    // human cannot reach here, so a delegated token is refused twice over.
    if (!account) {
      throw new TokenExchangeError(
        'invalid_grant',
        'Only a service token can be exchanged.',
      );
    }
    if (account.isDisabled === true) {
      throw new TokenExchangeError(
        'invalid_grant',
        'This service account is disabled.',
      );
    }

    return {
      serviceAccountId: payload.userId,
      orgId: payload.orgId,
      clientId: payload.client_id,
      scopes: payload.scope.split(' ').filter((s) => s !== ''),
    };
  }

  /**
   * What the delegated token may do: at most what the service token itself
   * holds, never more.
   *
   * Asking for nothing means "everything this service token has", which is the
   * convenient default and still bounded by the service token. Asking for
   * something narrower is honoured, because an integration that only needs to
   * search should be able to hold a token that can only search for the five
   * minutes it exists.
   */
  private resolveScopes(
    requested: string | undefined,
    serviceTokenScopes: string[],
  ): string[] {
    const held = serviceTokenScopes.filter(
      (scope) => !SERVICE_TOKEN_DENIED_SCOPES.includes(scope),
    );

    if (requested === undefined || requested.trim() === '') {
      if (held.length === 0) {
        throw new TokenExchangeError(
          'invalid_scope',
          'The service token carries no scope that can be delegated.',
        );
      }
      return held;
    }

    const asked = [
      ...new Set(requested.split(' ').filter((scope) => scope !== '')),
    ];
    const excess = asked.filter((scope) => !held.includes(scope));
    if (excess.length > 0) {
      // Named rather than hidden: these are the service token's own scopes,
      // which the caller already knows, so saying which were refused leaks
      // nothing and saves an integration a blind guess.
      throw new TokenExchangeError(
        'invalid_scope',
        `A delegated token cannot exceed the service token: ${excess.join(', ')}`,
      );
    }
    return asked;
  }
}
