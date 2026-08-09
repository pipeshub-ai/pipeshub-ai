/**
 * Workflows Routes
 *
 * Workflow dashboard gateway -- a thin, auth/scope-gated proxy in front of the
 * Python query service's `/api/v1/workflows` router (see
 * `workflows.controller.ts` for the forwarding logic): auth + `requireScopes`
 * + per-route handler.
 *
 * @module workflows/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  listWorkflows,
  getWorkflow,
  listWorkflowTriggers,
  listWorkflowRuns,
  getWorkflowRun,
  getRunTrace,
  answerWorkflowRun,
  listWorkflowVersions,
  getWorkflowVersionSource,
  commitWorkflowVersion,
  activateWorkflowVersion,
  runWorkflowNow,
  dryRunWorkflow,
  pauseWorkflow,
  resumeWorkflow,
  cancelWorkflow,
  deleteWorkflow,
  promoteWorkflowToAgent,
  editWorkflow,
} from '../controller/workflows.controller';

export function createWorkflowsRouter(container: Container): Router {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const appConfig = container.get<AppConfig>('AppConfig');

  // ---- List / get -------------------------------------------------------

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    listWorkflows(appConfig),
  );
  router.get(
    '/:workflowId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    getWorkflow(appConfig),
  );
  router.get(
    '/:workflowId/triggers',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    listWorkflowTriggers(appConfig),
  );
  router.get(
    '/:workflowId/runs',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    listWorkflowRuns(appConfig),
  );
  router.get(
    '/:workflowId/runs/:runId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    getWorkflowRun(appConfig),
  );
  router.get(
    '/:workflowId/runs/:runId/trace',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    getRunTrace(appConfig),
  );
  router.post(
    '/:workflowId/runs/:runId/answer',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_APPROVE),
    answerWorkflowRun(appConfig),
  );

  // ---- Versions / source --------------------------------------------------

  router.get(
    '/:workflowId/versions',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    listWorkflowVersions(appConfig),
  );
  router.get(
    '/:workflowId/versions/:versionId/source',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_READ),
    getWorkflowVersionSource(appConfig),
  );
  // Registered before `/:versionId/activate` so the literal `commit` segment
  // is never captured as a version id.
  router.post(
    '/:workflowId/versions/commit',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    commitWorkflowVersion(appConfig),
  );
  router.post(
    '/:workflowId/versions/:versionId/activate',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    activateWorkflowVersion(appConfig),
  );

  // ---- Lifecycle actions --------------------------------------------------

  router.post(
    '/:workflowId/run-now',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_EXECUTE),
    runWorkflowNow(appConfig),
  );
  router.post(
    '/:workflowId/dry-run',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_EXECUTE),
    dryRunWorkflow(appConfig),
  );
  router.post(
    '/:workflowId/pause',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    pauseWorkflow(appConfig),
  );
  router.post(
    '/:workflowId/resume',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    resumeWorkflow(appConfig),
  );
  router.post(
    '/:workflowId/cancel',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    cancelWorkflow(appConfig),
  );
  router.delete(
    '/:workflowId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    deleteWorkflow(appConfig),
  );
  router.post(
    '/:workflowId/promote-to-agent',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    promoteWorkflowToAgent(appConfig),
  );
  router.post(
    '/:workflowId/edit',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.WORKFLOW_WRITE),
    editWorkflow(appConfig),
  );

  return router;
}
