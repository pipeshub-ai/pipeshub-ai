import { MongoOAuthAppRepository } from './mongo';
import { PgOAuthAppRepository } from './pg';
import type { OAuthAppRepository } from './types';
import { pickStore } from '../store-select';

export * from './types';

let repo: OAuthAppRepository | undefined;

export const getOAuthAppRepository = (): OAuthAppRepository =>
  (repo ??= pickStore<OAuthAppRepository>('oauth-app', {
    postgres: () => new PgOAuthAppRepository(),
    mongo: () => new MongoOAuthAppRepository(),
  }));

/** Tests: install a fake, or call with no argument to restore the default. */
export const setOAuthAppRepository = (r?: OAuthAppRepository): void => {
  repo = r;
};
