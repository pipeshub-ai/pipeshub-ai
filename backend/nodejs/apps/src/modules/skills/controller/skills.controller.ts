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
import { Logger } from '../../../libs/services/logger.service';
import {
  BadGatewayError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AppConfig } from '../../tokens_manager/config/config';

const logger = Logger.getInstance({ service: 'Skills Proxy' });

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

function mapAxiosError(error: unknown, action: string): Error {
  const err = error as {
    response?: { status: number; data: unknown };
    code?: string;
    message?: string;
  };
  if (err?.response) {
    logger.error(`${action} upstream returned ${err.response.status}`, {
      status: err.response.status,
      data: err.response.data,
    });
    return new BadGatewayError(
      typeof err.response.data === 'object' && err.response.data !== null
        ? ((err.response.data as Record<string, unknown>).detail as string) ||
          ((err.response.data as Record<string, unknown>).message as string) ||
          `${action} failed (upstream ${err.response.status})`
        : `${action} failed (upstream ${err.response.status})`,
    );
  }
  logger.error(`${action} upstream unreachable`, {
    code: err?.code,
    message: err?.message,
  });
  return new ServiceUnavailableError(`${action} failed: skills service is unavailable`);
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
        res.status(response.status).json(response.data);
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
export const deprecateSkill = forwardJson(HttpMethod.POST, (req) => `${SKILLS_BASE}/${encName(req)}/deprecate`, 'Deprecate Skill');
export const getSkillUsage = forwardJson(HttpMethod.GET, (req) => `${SKILLS_BASE}/${encName(req)}/usage`, 'Get Skill Usage');
export const deleteSkill = forwardJson(HttpMethod.DELETE, (req) => `${SKILLS_BASE}/${encName(req)}`, 'Delete Skill');

// ---- Version history ------------------------------------------------------

export const listSkillVersions = forwardJson(HttpMethod.GET, (req) => `${SKILLS_BASE}/${encName(req)}/versions`, 'List Skill Versions');
export const getSkillVersion = forwardJson(
  HttpMethod.GET,
  (req) => `${SKILLS_BASE}/${encName(req)}/versions/${encParam(req.params.version)}`,
  'Get Skill Version',
);
export const rollbackSkill = forwardJson(HttpMethod.POST, (req) => `${SKILLS_BASE}/${encName(req)}/rollback`, 'Rollback Skill');

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

      res.status(response.status).json(response.data);
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

      res.status(response.status);
      res.setHeader('Content-Type', String(response.headers['content-type'] || 'text/markdown'));
      if (response.headers['content-disposition']) {
        res.setHeader('Content-Disposition', String(response.headers['content-disposition']));
      }
      res.send(response.data);
    } catch (error) {
      next(mapAxiosError(error, 'Export Skill'));
    }
  };
