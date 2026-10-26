import { MongoOAuthTokenRepository } from './mongo';
import type { OAuthTokenRepository } from './types';

export * from './types';

let repo: OAuthTokenRepository | undefined;

export const getOAuthTokenRepository = (): OAuthTokenRepository =>
  (repo ??= new MongoOAuthTokenRepository());

/** Tests: install a fake, or call with no argument to restore the default. */
export const setOAuthTokenRepository = (r?: OAuthTokenRepository): void => {
  repo = r;
};
