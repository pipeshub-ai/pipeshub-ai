/**
 * Workflows proxy controller -- forwards workflow dashboard requests to
 * the Python query service's `/api/v1/workflows` router. Thin-proxy shape
 * (axios + `validateStatus: () => true` so upstream status codes/bodies pass
 * through verbatim) shared via `http-forward-proxy.util.ts`.
 *
 * Auth/org scoping happens entirely upstream: the Python side resolves
 * `orgId`/`userId` from the forwarded JWT, so this proxy only forwards
 * headers, body, and query params.
 */

import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { Logger } from '../../../libs/services/logger.service';
import { createForwardJsonHandler, encId } from '../../../libs/utils/http-forward-proxy.util';

const logger = Logger.getInstance({ service: 'Workflows Proxy' });
const forwardJson = createForwardJsonHandler(logger);

const WORKFLOWS_BASE = '/api/v1/workflows';

// ---- List / get -------------------------------------------------------

export const listWorkflows = forwardJson(HttpMethod.GET, () => `${WORKFLOWS_BASE}/`, 'List Workflows');
export const getWorkflow = forwardJson(
  HttpMethod.GET,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}`,
  'Get Workflow',
);
export const listWorkflowTriggers = forwardJson(
  HttpMethod.GET,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/triggers`,
  'List Workflow Triggers',
);
export const listWorkflowRuns = forwardJson(
  HttpMethod.GET,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/runs`,
  'List Workflow Runs',
);
export const getWorkflowRun = forwardJson(
  HttpMethod.GET,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/runs/${encId(req.params.runId)}`,
  'Get Workflow Run',
);
export const getRunTrace = forwardJson(
  HttpMethod.GET,
  (req) =>
    `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/runs/${encId(req.params.runId)}/trace`,
  'Get Run Trace',
);
export const answerWorkflowRun = forwardJson(
  HttpMethod.POST,
  (req) =>
    `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/runs/${encId(req.params.runId)}/answer`,
  'Answer Workflow Run',
);

// ---- Versions / source --------------------------------------------------

export const listWorkflowVersions = forwardJson(
  HttpMethod.GET,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/versions`,
  'List Workflow Versions',
);
export const getWorkflowVersionSource = forwardJson(
  HttpMethod.GET,
  (req) =>
    `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/versions/${encId(req.params.versionId)}/source`,
  'Get Workflow Version Source',
);
export const commitWorkflowVersion = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/versions/commit`,
  'Commit Workflow Version',
);
export const activateWorkflowVersion = forwardJson(
  HttpMethod.POST,
  (req) =>
    `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/versions/${encId(req.params.versionId)}/activate`,
  'Activate Workflow Version',
);

// ---- Lifecycle actions --------------------------------------------------

export const runWorkflowNow = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/run-now`,
  'Run Workflow Now',
);
export const dryRunWorkflow = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/dry-run`,
  'Dry Run Workflow',
);
export const pauseWorkflow = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/pause`,
  'Pause Workflow',
);
export const resumeWorkflow = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/resume`,
  'Resume Workflow',
);
export const cancelWorkflow = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/cancel`,
  'Cancel Workflow',
);
export const deleteWorkflow = forwardJson(
  HttpMethod.DELETE,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}`,
  'Delete Workflow',
);
export const promoteWorkflowToAgent = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/promote-to-agent`,
  'Promote Workflow To Agent',
);
export const editWorkflow = forwardJson(
  HttpMethod.POST,
  (req) => `${WORKFLOWS_BASE}/${encId(req.params.workflowId)}/edit`,
  'Edit Workflow',
);
