/**
 * Skills proxy controller — forwards Personal Settings > Skills requests to
 * the Python query service's `/api/v1/skills` router
 * (`backend/python/app/api/routes/skills.py`). Follows the same thin-proxy
 * shape as `speech.controller.ts` (axios + `validateStatus: () => true` so
 * upstream status codes/bodies — including the safe-delete 409 payload with
 * `usedByAgents`/`requiredBySkills` — pass through to the client verbatim,
 * rather than every 4xx collapsing into a generic 500).
 *
 * Auth/org scoping happens entirely upstream: the Python side resolves
 * `orgId`/`userId` from the forwarded JWT (see `skills.py::_get_user_context`),
 * so this proxy never needs to read or inject those itself — it only forwards
 * headers, body, query params, and (for the upload-import route) a
 * re-encoded multipart body.
 */

import { Response, NextFunction } from 'express';
import axios, { AxiosRequestConfig, AxiosResponse } from 'axios';
import FormData from 'form-data';

import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadGatewayError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { withCause } from '../../../libs/errors/base.error';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  candidateTargetId,
  controlPlaneHttpError,
  SkillCandidateApprovalTarget,
  type ConfigChangeApprovalsFactory,
} from '../../control-plane';
import { getCurrentOrgId } from '../../../libs/context/orgContext';
import { markRequestError } from '../../../libs/middlewares/access-log.middleware';
import { classifyError } from '../../../libs/utils/error-class';

const SKILLS_BASE = '/api/v1/skills';

// Mirrors speech.controller.ts's hop-by-hop header stripping — same
// rationale (avoids stale content-length/connection headers breaking the
// re-encoded outbound request).
const HOP_BY_HOP_HEADERS = new Set([
  'host',
  'content-length',
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'accept-encoding',
]);

function buildForwardHeaders(
  req: AuthenticatedUserRequest,
  extra: Record<string, string> = {},
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(req.headers)) {
    if (!value) continue;
    if (HOP_BY_HOP_HEADERS.has(key.toLowerCase())) continue;
    headers[key] = Array.isArray(value) ? value.join(', ') : String(value);
  }
  if (req.headers.authorization && !headers.authorization) {
    headers.authorization = String(req.headers.authorization);
  }
  return { ...headers, ...extra };
}

// Not logged here: the call's upstream.call.end line and the error middleware's
// line (which shows the axios error as the cause, without its body) report it.
function mapAxiosError(error: unknown, action: string): Error {
  const err = error as {
    response?: { status: number; data: unknown };
    code?: string;
    message?: string;
  };
  if (err?.response) {
    const mapped = new BadGatewayError(
      typeof err.response.data === 'object' && err.response.data !== null
        ? ((err.response.data as Record<string, unknown>).detail as string) ||
          ((err.response.data as Record<string, unknown>).message as string) ||
          `${action} failed (upstream ${err.response.status})`
        : `${action} failed (upstream ${err.response.status})`,
    );
    return withCause(mapped, error);
  }
  return withCause(
    new ServiceUnavailableError(`${action} failed: skills service is unavailable`),
    error,
  );
}

/**
 * Sets the status of an answer passed through from the skills service. A 5xx is that service's failure, so the access line
 * says `upstream_5xx`, as the call's own line does, not `internal`.
 */
function passThroughStatus(res: Response, status: number): Response {
  if (status >= 500) markRequestError(res, classifyError(status));
  return res.status(status);
}

type PathBuilder = (req: AuthenticatedUserRequest) => string;
type HttpMethodValue = (typeof HttpMethod)[keyof typeof HttpMethod];

/**
 * Factory for the common case: JSON in, JSON out, one upstream call.
 * `pathBuilder` returns only the path (e.g. `/api/v1/skills/foo/versions`) —
 * `req.query` is forwarded via axios's `params` for every method (harmless
 * for POST/PUT/PATCH, and is how `?detach=true` on DELETE and the `list`/
 * `search` filter params reach Python without each handler re-deriving a
 * query string).
 */
function forwardJson(method: HttpMethodValue, pathBuilder: PathBuilder, action: string) {
  return (appConfig: AppConfig) =>
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        const url = `${appConfig.aiBackend}${pathBuilder(req)}`;
        const requestConfig: AxiosRequestConfig = {
          url,
          method,
          params: req.query,
          data: method === HttpMethod.GET || method === HttpMethod.DELETE ? undefined : req.body,
          headers: buildForwardHeaders(req, { 'Content-Type': 'application/json' }),
          timeout: 30_000,
          validateStatus: () => true,
        };
        const response: AxiosResponse = await axios.request(requestConfig);
        passThroughStatus(res, response.status).json(response.data);
      } catch (error) {
        next(mapAxiosError(error, action));
      }
    };
}

const encName = (req: AuthenticatedUserRequest) => encodeURIComponent(String(req.params.name));
const encParam = (value: string | undefined) => encodeURIComponent(String(value ?? ''));

// ---- Catalog / search --------------------------------------------------

export const listSkills = forwardJson(HttpMethod.GET, () => `${SKILLS_BASE}/`, 'List Skills');
export const getSkillCategories = forwardJson(HttpMethod.GET, () => `${SKILLS_BASE}/categories`, 'Get Skill Categories');
export const searchSkills = forwardJson(HttpMethod.GET, () => `${SKILLS_BASE}/search`, 'Search Skills');
export const getSkill = forwardJson(HttpMethod.GET, (req) => `${SKILLS_BASE}/${encName(req)}`, 'Get Skill');

// ---- CRUD ---------------------------------------------------------------

export const createSkill = forwardJson(HttpMethod.POST, () => `${SKILLS_BASE}/`, 'Create Skill');
export const updateSkill = forwardJson(HttpMethod.PUT, (req) => `${SKILLS_BASE}/${encName(req)}`, 'Update Skill');
export const patchSkillBody = forwardJson(HttpMethod.PATCH, (req) => `${SKILLS_BASE}/${encName(req)}/body`, 'Patch Skill Body');
// UI-B4: the name people see; the skill's id stays (Python checks the owner and that no other skill shows it).
export const renameSkill = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/${encName(req)}/rename`,
  'Rename Skill',
);
export const deprecateSkill = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/${encName(req)}/deprecate`,
  'Deprecate Skill',
);
export const disableSkill = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/${encName(req)}/disable`,
  'Disable Skill',
);
export const enableSkill = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/${encName(req)}/enable`,
  'Enable Skill',
);
export const getSkillUsage = forwardJson(
  HttpMethod.GET,
  (req) => `${SKILLS_BASE}/${encName(req)}/usage`,
  'Get Skill Usage',
);
export const deleteSkill = forwardJson(
  HttpMethod.DELETE,
  (req) => `${SKILLS_BASE}/${encName(req)}`,
  'Delete Skill',
);

// ---- Version history ------------------------------------------------------

export const listSkillVersions = forwardJson(HttpMethod.GET, (req) => `${SKILLS_BASE}/${encName(req)}/versions`, 'List Skill Versions');
export const getSkillVersion = forwardJson(
  HttpMethod.GET,
  (req) => `${SKILLS_BASE}/${encName(req)}/versions/${encParam(req.params.version)}`,
  'Get Skill Version',
);
export const rollbackSkill = forwardJson(HttpMethod.POST, (req) => `${SKILLS_BASE}/${encName(req)}/rollback`, 'Rollback Skill');

// ---- Sharing a /command (SLASH1 S4) -----------------------------------------

export const getCommandShareTargets = forwardJson(
  HttpMethod.GET,
  () => `${SKILLS_BASE}/commands/share-targets`,
  'Get Command Share Targets',
);
export const getCommandProvenance = forwardJson(
  HttpMethod.GET,
  () => `${SKILLS_BASE}/commands/provenance`,
  'Get Command Provenance',
);
export const publishCommand = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/${encName(req)}/publish`,
  'Publish Command',
);

// ---- Bundled resources ------------------------------------------------------

export const getSkillResource = forwardJson(HttpMethod.GET, (req) => `${SKILLS_BASE}/${encName(req)}/resource`, 'Get Skill Resource');
export const writeSkillResource = forwardJson(HttpMethod.PUT, (req) => `${SKILLS_BASE}/${encName(req)}/resource`, 'Write Skill Resource');
export const removeSkillResource = forwardJson(HttpMethod.DELETE, (req) => `${SKILLS_BASE}/${encName(req)}/resource`, 'Remove Skill Resource');

// ---- Learning-loop candidate review ----------------------------------------

export const getPendingSkillCandidates = forwardJson(HttpMethod.GET, () => `${SKILLS_BASE}/candidates/pending`, 'Get Pending Skill Candidates');
export const approveSkillCandidate = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/candidates/${encParam(req.params.candidateId)}/approve`,
  'Approve Skill Candidate',
);
export const rejectSkillCandidate = forwardJson(
  HttpMethod.POST,
  (req) => `${SKILLS_BASE}/candidates/${encParam(req.params.candidateId)}/reject`,
  'Reject Skill Candidate',
);

// ---- Package import (npm / URL preview + finalize; upload is bespoke below) --

export const previewNpmSkillImport = forwardJson(HttpMethod.POST, () => `${SKILLS_BASE}/import/npm/preview`, 'Preview npm Skill Import');
export const previewUrlSkillImport = forwardJson(HttpMethod.POST, () => `${SKILLS_BASE}/import/url/preview`, 'Preview URL Skill Import');
export const finalizeSkillImport = forwardJson(HttpMethod.POST, () => `${SKILLS_BASE}/import/finalize`, 'Finalize Skill Import');

/**
 * POST /skills/import/upload/preview
 *
 * Re-encodes the multer-buffered upload as multipart form-data for Python's
 * `UploadFile = File(...)` param — same technique as `transcribeAudio` in
 * speech.controller.ts.
 */
export const previewUploadSkillImport =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const file = (req as AuthenticatedUserRequest & { file?: Express.Multer.File }).file;
      if (!file) {
        res.status(400).json({ detail: "A file upload is required (field 'file')" });
        return;
      }

      const form = new FormData();
      form.append('file', file.buffer, {
        filename: file.originalname || 'skill.zip',
        contentType: file.mimetype || 'application/octet-stream',
      });

      const url = `${appConfig.aiBackend}${SKILLS_BASE}/import/upload/preview`;
      const headers = buildForwardHeaders(req);
      delete headers['content-type'];
      delete headers['Content-Type'];

      const response = await axios.post(url, form, {
        headers: { ...headers, ...form.getHeaders() },
        timeout: 60_000,
        maxBodyLength: Infinity,
        maxContentLength: Infinity,
        validateStatus: () => true,
      });

      passThroughStatus(res, response.status).json(response.data);
    } catch (error) {
      next(mapAxiosError(error, 'Preview Upload Skill Import'));
    }
  };

/**
 * GET /skills/:name/export
 *
 * Python returns `text/markdown` with a `Content-Disposition` attachment
 * header (see `skills.py::export_skill`) — passed through as text, not
 * JSON, so the browser download behaves correctly.
 */
export const exportSkill =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const url = `${appConfig.aiBackend}${SKILLS_BASE}/${encName(req)}/export`;
      const response = await axios.get(url, {
        headers: buildForwardHeaders(req),
        timeout: 15_000,
        responseType: 'text',
        validateStatus: () => true,
      });

      passThroughStatus(res, response.status);
      res.setHeader('Content-Type', String(response.headers['content-type'] || 'text/markdown'));
      if (response.headers['content-disposition']) {
        res.setHeader('Content-Disposition', String(response.headers['content-disposition']));
      }
      res.send(response.data);
    } catch (error) {
      next(mapAxiosError(error, 'Export Skill'));
    }
  };

/** The upstream's refusal of a candidate decision, answered to the caller as the upstream gave it. */
class UpstreamAnswer extends Error {
  constructor(readonly response: AxiosResponse) {
    super(`upstream answered ${String(response.status)}`);
    this.name = 'UpstreamAnswer';
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/**
 * Whether Python lists `candidateId` as learned from the caller's own run (`mine`), read with the caller's own
 * credential. Any doubt (an error, an unreadable answer) is `false`: the request then takes the admin's path.
 */
async function learnedForCaller(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  candidateId: string,
): Promise<boolean> {
  try {
    const response = await axios.request({
      url: `${appConfig.aiBackend}${SKILLS_BASE}/candidates/pending`,
      method: HttpMethod.GET,
      headers: buildForwardHeaders(req, { 'Content-Type': 'application/json' }),
      timeout: 30_000,
      validateStatus: () => true,
    });
    const candidates: unknown = isRecord(response.data)
      ? response.data['candidates']
      : undefined;
    return (
      response.status === 200 &&
      Array.isArray(candidates) &&
      candidates.some(
        (c) =>
          isRecord(c) &&
          c['candidate_id'] === candidateId &&
          c['mine'] === true,
      )
    );
  } catch {
    return false;
  }
}

/** The configuration-change approvals a candidate decision goes through, when bound. */
export type CandidateWorkflowFactory = ConfigChangeApprovalsFactory;

/**
 * With configuration-change approvals bound (`CONTROL_PLANE_APPROVALS_FACTORY`),
 * approving or rejecting a learned candidate is an `ApprovalRequest{change:
 * publish}` the caller asks and decides in one audited control-plane write,
 * the skill store's own write (the Python route, with the caller's
 * credential) its effect.
 *
 * UI-B4: a candidate learned from the caller's own run becomes their own
 * personal skill, which publishes nothing to the org, so it is not a publish
 * request: it is decided as a skill they write themselves, with Python
 * checking that it is theirs. The reviewer's edits are forwarded either way.
 */
export function decideSkillCandidateThroughWorkflow(
  verdict: 'approve' | 'reject',
) {
  const action =
    verdict === 'approve'
      ? 'Approve Skill Candidate'
      : 'Reject Skill Candidate';
  return (appConfig: AppConfig, workflowFor: CandidateWorkflowFactory) =>
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      const userId: unknown = req.user?.userId;
      const orgId: unknown = getCurrentOrgId();
      const candidateId = req.params.candidateId ?? '';
      const forward = async (
        step: 'approve' | 'reject',
      ): Promise<AxiosResponse> => {
        const response = await axios.request({
          url: `${appConfig.aiBackend}${SKILLS_BASE}/candidates/${encParam(candidateId)}/${step}`,
          method: HttpMethod.POST,
          data: step === 'approve' && isRecord(req.body) ? req.body : {},
          headers: buildForwardHeaders(req, {
            'Content-Type': 'application/json',
          }),
          timeout: 30_000,
          validateStatus: () => true,
        });
        if (response.status < 200 || response.status >= 300)
          throw new UpstreamAnswer(response);
        return response;
      };
      try {
        if (typeof userId !== 'string' || typeof orgId !== 'string')
          throw new UnauthorizedError('User authentication required');
        if (await learnedForCaller(appConfig, req, candidateId)) {
          const own = await forward(verdict);
          res.status(own.status).json(own.data);
          return;
        }
        const target = new SkillCandidateApprovalTarget({
          approve: () => forward('approve'),
          reject: () => forward('reject'),
        });
        const { applied } = await workflowFor([target]).submit({
          actor: { orgId, userId },
          target: { type: 'catalog-item', id: candidateTargetId(candidateId) },
          change: { type: 'publish' },
          decision: { decision: verdict },
        });
        const response = applied?.value as AxiosResponse;
        res.status(response.status).json(response.data);
      } catch (error) {
        if (error instanceof UpstreamAnswer) {
          res.status(error.response.status).json(error.response.data);
          return;
        }
        const mapped = controlPlaneHttpError(error);
        next(
          mapped !== error || error instanceof UnauthorizedError
            ? mapped
            : mapAxiosError(error, action),
        );
      }
    };
}
