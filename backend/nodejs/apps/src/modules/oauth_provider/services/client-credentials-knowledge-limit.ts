import { ForbiddenError } from '../../../libs/errors/http.errors';
import { UnauthorizedClientError } from '../../../libs/errors/oauth.errors';
import { KnowledgeLimit } from '../types/oauth.types';
import { knowledgeLimitSize, sameKnowledgeLimit } from '../types/knowledge_limit';

export const CLIENT_CREDENTIALS_KNOWLEDGE_LIMIT_UNAVAILABLE_MESSAGE =
  'An application that uses client_credentials cannot be given a knowledge ' +
  'limit. Remove the limit, or leave the one it already has unchanged.';

export const CLIENT_CREDENTIALS_TOKEN_WITH_LIMIT_MESSAGE =
  'This application has a knowledge limit, which client_credentials tokens ' +
  'cannot carry. Remove the limit from the application to get a token.';

/** What an application that uses client_credentials does with a knowledge limit. */
export interface ClientCredentialsKnowledgeLimit {
  /**
   * Throws when the application may not be saved with `next`. `previous` is
   * the stored limit, and `hadClientCredentials` whether the stored
   * application already allowed the grant.
   */
  checkSave(
    next: KnowledgeLimit | undefined,
    previous: KnowledgeLimit | undefined,
    hadClientCredentials: boolean,
  ): void;
  /** The limit a client_credentials token carries, given the stored one; throws when no token may be issued. */
  tokenLimit(stored: KnowledgeLimit | undefined): KnowledgeLimit | undefined;
}

/**
 * The default: no knowledge limit for client_credentials. A limit stored
 * before may stay unchanged or be removed, and while it is stored the app gets
 * no token, so the token never reaches more than the stored limit allows.
 */
export const clientCredentialsKnowledgeLimitDefault: ClientCredentialsKnowledgeLimit = {
  checkSave(next, previous, hadClientCredentials) {
    if (
      knowledgeLimitSize(next) > 0 &&
      (!hadClientCredentials || !sameKnowledgeLimit(next, previous))
    ) {
      throw new ForbiddenError(CLIENT_CREDENTIALS_KNOWLEDGE_LIMIT_UNAVAILABLE_MESSAGE);
    }
  },
  tokenLimit(stored) {
    if (knowledgeLimitSize(stored) > 0) {
      throw new UnauthorizedClientError(CLIENT_CREDENTIALS_TOKEN_WITH_LIMIT_MESSAGE);
    }
    return undefined;
  },
};

let current: ClientCredentialsKnowledgeLimit = clientCredentialsKnowledgeLimitDefault;

export function clientCredentialsKnowledgeLimit(): ClientCredentialsKnowledgeLimit {
  return current;
}

export function setClientCredentialsKnowledgeLimit(
  impl: ClientCredentialsKnowledgeLimit,
): void {
  current = impl;
}
