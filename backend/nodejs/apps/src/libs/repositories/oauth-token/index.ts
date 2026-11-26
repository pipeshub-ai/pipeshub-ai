import { MongoOAuthTokenRepository } from './mongo';
import { PgOAuthTokenRepository } from './pg';
import type { OAuthTokenRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: OAuthTokenRepository | undefined;

export const getOAuthTokenRepository = (): OAuthTokenRepository =>
  (repo ??= pickStore<OAuthTokenRepository>('oauth-token', {
    postgres: () => new PgOAuthTokenRepository(),
    mongo: () => new MongoOAuthTokenRepository(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setOAuthTokenRepository = (r?: OAuthTokenRepository): void => {
  repo = r;
};
