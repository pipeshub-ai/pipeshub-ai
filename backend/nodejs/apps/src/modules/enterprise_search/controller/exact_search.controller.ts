/**
 * Exact-text (grep) search and knowledge-graph entities.
 *
 * Thin proxies to the AI service. It decides what the caller may read, in the
 * places search and the assistant's own tools decide it; nothing here widens
 * or narrows that. The caller is the signed-in user (the token is forwarded);
 * the body is cut to the fields below, so it cannot name another user or
 * organisation. These requests are not saved to search history.
 */
import { NextFunction, Response } from 'express';
import {
  InternalServerError,
  NotImplementedError,
} from '../../../libs/errors/http.errors';
import { handleBackendError } from '../../../libs/errors/backend-error';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { AppConfig } from '../../tokens_manager/config/config';

/** The fields each route sends on; anything else in the body is dropped. */
const GREP_FIELDS = [
  'pattern',
  'regex',
  'caseSensitive',
  'filters',
  'paths',
  'limit',
  'contextLines',
  'maxMatchesPerRecord',
  'timeBudgetMs',
] as const;
const ENTITY_SEARCH_FIELDS = [
  'query',
  'entityTypes',
  'namedEntityKinds',
  'limit',
] as const;
const NEIGHBOR_FIELDS = [
  'entityId',
  'entityType',
  'recordTypes',
  'recordLimit',
  'relatedLimit',
  'cursor',
] as const;

type Handler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

const forward =
  (
    appConfig: AppConfig,
    path: string,
    fields: readonly string[],
    what: string,
  ): Handler =>
  async (req, res, next) => {
    try {
      const sent = req.body as Record<string, unknown>;
      const body: Record<string, unknown> = {};
      for (const key of fields) {
        if (sent[key] !== undefined) body[key] = sent[key];
      }
      let aiResponse;
      try {
        aiResponse = await new AIServiceCommand<Record<string, unknown>>({
          uri: `${appConfig.aiBackend}/api/v1/search/${path}`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body,
        }).execute();
      } catch (error: unknown) {
        // an unreachable or slow AI service is a 503 or 504, as for every other route that calls it
        throw handleBackendError(error, what);
      }
      if (!aiResponse.data) {
        throw new InternalServerError('Failed to get response from AI service');
      }
      if (aiResponse.statusCode === 501) {
        // the AI service says this deployment cannot serve the route; handleBackendError has no 501 and would make it a 500
        const data = aiResponse.data as { detail?: unknown } | undefined;
        throw new NotImplementedError(
          typeof data?.detail === 'string'
            ? data.detail
            : 'This deployment does not support this request.',
        );
      }
      if (aiResponse.statusCode !== 200) {
        throw handleBackendError(
          {
            response: { status: aiResponse.statusCode, data: aiResponse.data },
          },
          what,
        );
      }
      res.status(200).json(aiResponse.data);
    } catch (error) {
      next(error);
    }
  };

/** POST /api/v1/search/grep */
export const grepRecords = (appConfig: AppConfig): Handler =>
  forward(appConfig, 'grep', GREP_FIELDS, 'Exact-text search');

/** POST /api/v1/search/entities */
export const searchEntities = (appConfig: AppConfig): Handler =>
  forward(appConfig, 'entities', ENTITY_SEARCH_FIELDS, 'Entity search');

/** POST /api/v1/search/entities/neighbors */
export const entityNeighbors = (appConfig: AppConfig): Handler =>
  forward(appConfig, 'entities/neighbors', NEIGHBOR_FIELDS, 'Entity lookup');
