/**
 * Skills Routes
 *
 * Personal Settings > Skills gateway — a thin, auth/scope-gated proxy in
 * front of the Python query service's `/api/v1/skills` router (see
 * `skills.controller.ts` for the forwarding logic). Mirrors the
 * enterprise_search `agents` router's structure (auth + `requireScopes` +
 * per-route handler), not the connector-facing `toolsets` router.
 *
 * @module skills/routes
 */

import { Router } from 'express';
import { z } from 'zod';
import { Container } from 'inversify';
import multer from 'multer';
import { createMulter } from '../../../libs/utils/multer.utils';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { createSkillsImportRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  listSkills,
  getSkillCategories,
  searchSkills,
  getSkill,
  createSkill,
  updateSkill,
  patchSkillBody,
  deprecateSkill,
  renameSkill,
  disableSkill,
  enableSkill,
  getSkillUsage,
  deleteSkill,
  listSkillVersions,
  getSkillVersion,
  rollbackSkill,
  getCommandShareTargets,
  getCommandProvenance,
  publishCommand,
  getSkillResource,
  writeSkillResource,
  removeSkillResource,
  getPendingSkillCandidates,
  approveSkillCandidate,
  rejectSkillCandidate,
  previewNpmSkillImport,
  previewUrlSkillImport,
  previewUploadSkillImport,
  finalizeSkillImport,
  exportSkill,
  decideSkillCandidateThroughWorkflow,
  type CandidateWorkflowFactory,
} from '../controller/skills.controller';
import { CONTROL_PLANE_APPROVALS_FACTORY } from '../../agent-harness/composition';
import type { RequestHandler } from 'express';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';

// A skill import archive is markdown + small scripts, never model weights —
// mirrors the npm-tarball/URL-archive ceiling enforced server-side in
// `package_importer.py` (`_MAX_ARCHIVE_BYTES`).
const SKILL_UPLOAD_MAX_BYTES = 25 * 1024 * 1024;

/** UI-B4: the name people see, checked here as Python's `RenameRequest` checks it. */
export const skillRenameSchema = z.object({
  params: z.object({ name: z.string().min(1).max(200) }),
  body: z
    .object({ displayName: z.string().max(120).nullable().optional() })
    .strict(),
});

/** Candidate review: the admin's own decision, or through configuration-change approvals where they are bound. */
function candidateDecisions(
  container: Container,
  appConfig: AppConfig,
): { approve: RequestHandler; reject: RequestHandler } {
  if (!container.isBound(CONTROL_PLANE_APPROVALS_FACTORY)) {
    return {
      approve: approveSkillCandidate(appConfig) as RequestHandler,
      reject: rejectSkillCandidate(appConfig) as RequestHandler,
    };
  }
  const bound = container.get<CandidateWorkflowFactory>(
    CONTROL_PLANE_APPROVALS_FACTORY,
  );
  return {
    approve: decideSkillCandidateThroughWorkflow('approve')(
      appConfig,
      bound,
    ) as RequestHandler,
    reject: decideSkillCandidateThroughWorkflow('reject')(
      appConfig,
      bound,
    ) as RequestHandler,
  };
}

export function createSkillsRouter(container: Container): Router {
  const router = Router();
  guardPathParams(router, 'name', 'version', 'candidateId');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  // eslint-disable-next-line @typescript-eslint/unbound-method -- AuthMiddleware binds authenticate in its constructor, and the route tests match the injected function itself
  const { authenticate } = authMiddleware;
  const appConfig = container.get<AppConfig>('AppConfig');
  const logger = (container.get('Logger') as Logger | undefined)
    ?? Logger.getInstance({ service: 'SkillsRoutes' });
  const importLimiter = createSkillsImportRateLimiter(logger);

  const skillUpload = createMulter({
    storage: multer.memoryStorage(),
    limits: { fileSize: SKILL_UPLOAD_MAX_BYTES, files: 1 },
  });

  // ---- Catalog / search --------------------------------------------------

  router.get('/', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), listSkills(appConfig));
  router.get('/categories', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillCategories(appConfig));
  router.get('/search', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), searchSkills(appConfig));

  // ---- Learning-loop candidate review (before '/:name' so 'candidates' never matches as a skill name) --

  router.get('/candidates/pending', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getPendingSkillCandidates(appConfig));
  const candidates = candidateDecisions(container, appConfig);
  router.post(
    '/candidates/:candidateId/approve',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    candidates.approve,
  );
  router.post(
    '/candidates/:candidateId/reject',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    candidates.reject,
  );

  // ---- Package import (same ordering guard — 'import' before '/:name') --

  router.post(
    '/import/npm/preview',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    importLimiter,
    previewNpmSkillImport(appConfig),
  );
  router.post(
    '/import/url/preview',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    importLimiter,
    previewUrlSkillImport(appConfig),
  );
  router.post(
    '/import/upload/preview',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    importLimiter,
    skillUpload.single('file'),
    previewUploadSkillImport(appConfig),
  );
  router.post(
    '/import/finalize',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    importLimiter,
    finalizeSkillImport(appConfig),
  );

  // ---- CRUD -----------------------------------------------------------------

  router.get(
    '/commands/provenance',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_READ),
    getCommandProvenance(appConfig),
  );
  router.get(
    '/commands/share-targets',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_READ),
    getCommandShareTargets(appConfig),
  );
  router.post(
    '/',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    createSkill(appConfig),
  );
  router.get(
    '/:name',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_READ),
    getSkill(appConfig),
  );
  router.put(
    '/:name',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    updateSkill(appConfig),
  );
  router.patch(
    '/:name/body',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    patchSkillBody(appConfig),
  );
  router.post(
    '/:name/rename',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    ValidationMiddleware.validate(skillRenameSchema),
    renameSkill(appConfig),
  );
  router.post('/:name/deprecate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), deprecateSkill(appConfig));
  router.post('/:name/disable', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), disableSkill(appConfig));
  router.post('/:name/enable', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), enableSkill(appConfig));
  router.get('/:name/usage', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillUsage(appConfig));
  router.delete('/:name', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), deleteSkill(appConfig));
  router.get('/:name/export', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), exportSkill(appConfig));

  // ---- Version history ------------------------------------------------------

  router.get(
    '/:name/versions',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_READ),
    listSkillVersions(appConfig),
  );
  router.get(
    '/:name/versions/:version',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_READ),
    getSkillVersion(appConfig),
  );
  router.post(
    '/:name/rollback',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    rollbackSkill(appConfig),
  );
  router.post(
    '/:name/publish',
    authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    publishCommand(appConfig),
  );

  // ---- Bundled resources ------------------------------------------------------

  router.get('/:name/resource', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillResource(appConfig));
  router.put('/:name/resource', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), writeSkillResource(appConfig));
  router.delete('/:name/resource', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), removeSkillResource(appConfig));

  return router;
}
