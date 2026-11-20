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
const ENTITY_RECORDS_FIELDS = [
  'entityId',
  'entityType',
  'recordTypes',
  'limit',
  'cursor',
] as const;
const CO_MENTIONED_FIELDS = ['entityIds', 'kinds', 'limit'] as const;
const PERSON_RECORDS_FIELDS = [
  'person',
  'roles',
  'recordTypes',
  'limit',
  'cursor',
] as const;
const VALUE_RECORDS_FIELDS = [
  'kinds',
  'name',
  'entityIds',
  'dateFrom',
  'dateTo',
  'amountMin',
  'amountMax',
  'currency',
  'quantityMin',
  'quantityMax',
  'dimension',
  'percentMin',
  'percentMax',
  'recordTypes',
  'sourceIds',
  'limit',
  'cursor',
] as const;
const ENTITY_RECORDS_SEARCH_FIELDS = [
  'mentions',
  'match',
  'query',
  'limit',
  'maxChunksPerRecord',
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
    fields: readonly string[] | null,
    what: string,
  ): Handler =>
  async (req, res, next) => {
    try {
      // `null` fields: a GET, which sends no body.
      let body: Record<string, unknown> | undefined;
      if (fields) {
        const sent = req.body as Record<string, unknown>;
        body = {};
        for (const key of fields) {
          if (sent[key] !== undefined) body[key] = sent[key];
        }
      }
      let aiResponse;
      try {
        aiResponse = await new AIServiceCommand<Record<string, unknown>>({
          uri: `${appConfig.aiBackend}/api/v1/search/${path}`,
          method: fields ? HttpMethod.POST : HttpMethod.GET,
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

/** GET /api/v1/search/entities/capabilities */
export const entityCapabilities = (appConfig: AppConfig): Handler =>
  forward(appConfig, 'entities/capabilities', null, 'Entity lookup');

/** POST /api/v1/search/entities/records */
export const entityRecords = (appConfig: AppConfig): Handler =>
  forward(
    appConfig,
    'entities/records',
    ENTITY_RECORDS_FIELDS,
    'Entity lookup',
  );

/** POST /api/v1/search/entities/co-mentioned */
export const coMentionedEntities = (appConfig: AppConfig): Handler =>
  forward(
    appConfig,
    'entities/co-mentioned',
    CO_MENTIONED_FIELDS,
    'Entity lookup',
  );

/** POST /api/v1/search/records/by-person */
export const recordsByPerson = (appConfig: AppConfig): Handler =>
  forward(
    appConfig,
    'records/by-person',
    PERSON_RECORDS_FIELDS,
    'Record lookup',
  );

/** POST /api/v1/search/records/by-value */
export const recordsByValue = (appConfig: AppConfig): Handler =>
  forward(appConfig, 'records/by-value', VALUE_RECORDS_FIELDS, 'Record lookup');

/** POST /api/v1/search/records/by-entities */
export const recordsByEntities = (appConfig: AppConfig): Handler =>
  forward(
    appConfig,
    'records/by-entities',
    ENTITY_RECORDS_SEARCH_FIELDS,
    'Entity search',
  );
