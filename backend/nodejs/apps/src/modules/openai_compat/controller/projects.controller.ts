import { Request, Response } from 'express';

import { asString, isJson, Json } from '../utils/openai-format';
import { listPage, listParams } from '../utils/openai-objects';
import { apiJson } from '../utils/pipeshub-api';
import { callerOf } from './files.controller';
import {
  OpenAICompatDeps,
  sendError,
  sendRunError,
} from './openai_compat.controller';

type Handler = (req: Request, res: Response) => Promise<void>;

const PROJECT_ID = /^[a-f0-9]{24}$/i;

const seconds = (value: unknown): number | null => {
  const ms = typeof value === 'number' ? value : Date.parse(asString(value));
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
};

/** OpenAI's `organization.project` for a PipesHub project. */
export function projectObject(project: Json): Json {
  const archived = project.isArchived === true;
  return {
    id: asString(project._id) || asString(project.id),
    object: 'organization.project',
    name: asString(project.name),
    created_at: seconds(project.createdAt) ?? 0,
    archived_at: archived ? seconds(project.updatedAt) : null,
    status: archived ? 'archived' : 'active',
  };
}

export const listProjects =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    try {
      const caller = callerOf(deps, req);
      const includeArchived = req.query.include_archived === 'true';
      const projects: Json[] = [];
      // shortcut: reads at most 10 pages of 100 projects.
      for (let page = 1; page <= 10; page++) {
        const body = await apiJson(
          caller,
          '/projects',
          {
            query: {
              page,
              limit: 100,
              scope: 'all',
              includeArchived: includeArchived ? 'true' : 'false',
            },
          },
          'Listing projects',
        );
        const items =
          isJson(body) && Array.isArray(body.projects)
            ? body.projects.filter(isJson)
            : [];
        projects.push(...items.map(projectObject));
        const pagination =
          isJson(body) && isJson(body.pagination) ? body.pagination : {};
        if (pagination.hasNext !== true && pagination.hasNextPage !== true)
          break;
      }
      res.status(200).json(listPage(projects, listParams(req.query, 20, 100)));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const retrieveProject =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const projectId = asString(
      (req.params as Record<string, string | undefined>).projectId,
    );
    if (!PROJECT_ID.test(projectId)) {
      sendError(
        res,
        404,
        `No project found with id '${projectId}'.`,
        'not_found',
        'project_id',
      );
      return;
    }
    try {
      const body = await apiJson(
        callerOf(deps, req),
        `/projects/${projectId}`,
        {},
        'Reading the project',
      );
      const project =
        isJson(body) && isJson(body.project) ? body.project : body;
      res.status(200).json(projectObject(isJson(project) ? project : {}));
    } catch (error) {
      sendRunError(res, error);
    }
  };

/**
 * The project a chat runs in: OpenAI clients name it with the `OpenAI-Project`
 * header (the SDKs' `project` option). Null when absent; undefined when malformed.
 */
export function projectFromHeader(req: Request): string | null | undefined {
  const raw = req.headers['openai-project'];
  const value = (Array.isArray(raw) ? raw[0] : raw)?.trim() ?? '';
  if (value === '') return null;
  return PROJECT_ID.test(value) ? value : undefined;
}
