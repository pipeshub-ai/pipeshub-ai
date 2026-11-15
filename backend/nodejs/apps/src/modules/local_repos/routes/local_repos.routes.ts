/**
 * Local repositories routes
 *
 * Auth- and scope-gated proxy in front of the Python connectors service's
 * `/api/v1/local-repos` router: a client pushes a working tree (manifest, changed
 * files, commit) and reads indexing status. Reads need `code:read` or
 * `code:write`; everything else needs `code:write`.
 *
 * @module local_repos/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  commitLocalRepo,
  deleteLocalRepo,
  diffLocalRepoManifest,
  getLocalRepo,
  listLocalRepos,
  registerLocalRepo,
  uploadLocalRepoFiles,
} from '../controller/local_repos.controller';

export function createLocalReposRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'repoKey');
  const config = container.get<AppConfig>('AppConfig');
  // eslint-disable-next-line @typescript-eslint/unbound-method -- AuthMiddleware binds authenticate in its constructor
  const { authenticate } = container.get<AuthMiddleware>('AuthMiddleware');
  const read = requireScopes(
    OAuthScopeNames.CODE_READ,
    OAuthScopeNames.CODE_WRITE,
  );
  const write = requireScopes(OAuthScopeNames.CODE_WRITE);

  router.get('/', authenticate, read, listLocalRepos(config));
  router.put('/:repoKey', authenticate, write, registerLocalRepo(config));
  router.get('/:repoKey', authenticate, read, getLocalRepo(config));
  router.delete('/:repoKey', authenticate, write, deleteLocalRepo(config));
  router.post(
    '/:repoKey/manifest',
    authenticate,
    write,
    diffLocalRepoManifest(config),
  );
  router.put(
    '/:repoKey/files',
    authenticate,
    write,
    uploadLocalRepoFiles(config),
  );
  router.post('/:repoKey/commit', authenticate, write, commitLocalRepo(config));

  return router;
}
