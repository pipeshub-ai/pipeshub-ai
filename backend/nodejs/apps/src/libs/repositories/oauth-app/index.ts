import { MongoOAuthAppRepository } from './mongo';
import type { OAuthAppRepository } from './types';

export * from './types';

let repo: OAuthAppRepository | undefined;

export const getOAuthAppRepository = (): OAuthAppRepository =>
  (repo ??= new MongoOAuthAppRepository());

/** Tests: install a fake, or call with no argument to restore the default. */
export const setOAuthAppRepository = (r?: OAuthAppRepository): void => {
  repo = r;
};
