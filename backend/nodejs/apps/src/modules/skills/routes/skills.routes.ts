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
import { Container } from 'inversify';
import multer from 'multer';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
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
  getSkillUsage,
  deleteSkill,
  listSkillVersions,
  getSkillVersion,
  rollbackSkill,
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
} from '../controller/skills.controller';

// A skill import archive is markdown + small scripts, never model weights —
// mirrors the npm-tarball/URL-archive ceiling enforced server-side in
// `package_importer.py` (`_MAX_ARCHIVE_BYTES`).
const SKILL_UPLOAD_MAX_BYTES = 25 * 1024 * 1024;

export function createSkillsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  const skillUpload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: SKILL_UPLOAD_MAX_BYTES, files: 1 },
  });

  // ---- Catalog / search --------------------------------------------------

  router.get('/', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), listSkills(appConfig));
  router.get('/categories', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillCategories(appConfig));
  router.get('/search', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), searchSkills(appConfig));

  // ---- Learning-loop candidate review (before '/:name' so 'candidates' never matches as a skill name) --

  router.get('/candidates/pending', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getPendingSkillCandidates(appConfig));
  router.post('/candidates/:candidateId/approve', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), approveSkillCandidate(appConfig));
  router.post('/candidates/:candidateId/reject', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), rejectSkillCandidate(appConfig));

  // ---- Package import (same ordering guard — 'import' before '/:name') --

  router.post('/import/npm/preview', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), previewNpmSkillImport(appConfig));
  router.post('/import/url/preview', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), previewUrlSkillImport(appConfig));
  router.post(
    '/import/upload/preview',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.SKILL_WRITE),
    skillUpload.single('file'),
    previewUploadSkillImport(appConfig),
  );
  router.post('/import/finalize', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), finalizeSkillImport(appConfig));

  // ---- CRUD -----------------------------------------------------------------

  router.post('/', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), createSkill(appConfig));
  router.get('/:name', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkill(appConfig));
  router.put('/:name', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), updateSkill(appConfig));
  router.patch('/:name/body', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), patchSkillBody(appConfig));
  router.post('/:name/deprecate', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), deprecateSkill(appConfig));
  router.get('/:name/usage', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillUsage(appConfig));
  router.delete('/:name', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), deleteSkill(appConfig));
  router.get('/:name/export', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), exportSkill(appConfig));

  // ---- Version history ------------------------------------------------------

  router.get('/:name/versions', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), listSkillVersions(appConfig));
  router.get('/:name/versions/:version', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillVersion(appConfig));
  router.post('/:name/rollback', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), rollbackSkill(appConfig));

  // ---- Bundled resources ------------------------------------------------------

  router.get('/:name/resource', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_READ), getSkillResource(appConfig));
  router.put('/:name/resource', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), writeSkillResource(appConfig));
  router.delete('/:name/resource', authMiddleware.authenticate, requireScopes(OAuthScopeNames.SKILL_WRITE), removeSkillResource(appConfig));

  return router;
}
