import { Response, NextFunction } from 'express';
import { DomainHttpError } from '../../../libs/errors/domain-http.error';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  InternalServerError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  executeConnectorCommand,
  handleBackendError,
} from '../../tokens_manager/utils/connector.utils';
import {
  conversationContextOf,
  listFilterOf,
} from '../../enterprise_search/services/conversation/http/conversation-context';
import { ConversationTitleService } from '../services/conversation-title.service';

type ArtifactListItem = {
  conversationId?: string | null;
  conversationTitle?: string;
  agentKey?: string;
  projectId?: string;
  conversationArchived?: true;
  [key: string]: unknown;
};

type ArtifactListResponse = {
  items?: ArtifactListItem[];
  pagination?: {
    page?: number;
    limit?: number;
    totalCount?: number;
    totalPages?: number;
  };
};

type ArtifactVersionsResponse = {
  versions?: unknown;
};

function requirePayload(
  data: unknown,
  message: string,
): Record<string, unknown> {
  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    throw new InternalServerError(message);
  }
  return data as Record<string, unknown>;
}

function artifactUri(
  connectorBackend: string,
  artifactId: string,
  suffix = '',
): string {
  return `${connectorBackend}/api/v1/artifacts/${encodeURIComponent(artifactId)}${suffix}`;
}

function buildQueryString(query: Record<string, unknown>): string {
  const params = new URLSearchParams();
  const mapping: Record<string, string> = {
    page: 'page',
    limit: 'limit',
    search: 'search',
    artifactTypes: 'artifact_types',
    conversationId: 'conversation_id',
    dateFrom: 'date_from',
    dateTo: 'date_to',
    sortBy: 'sort_by',
    sortOrder: 'sort_order',
    ownership: 'ownership',
  };
  for (const [from, to] of Object.entries(mapping)) {
    const value = query[from];
    if (value !== undefined && value !== null && String(value).length > 0) {
      params.set(to, String(value));
    }
  }
  return params.toString();
}

async function enrichConversationTitles(
  items: ArtifactListItem[],
  req: AuthenticatedUserRequest,
): Promise<ArtifactListItem[]> {
  const conversationIds = items
    .map((item) => item.conversationId)
    .filter((id): id is string => Boolean(id));
  if (!conversationIds.length) {
    return items;
  }
  const titles = await ConversationTitleService.batchTitles(conversationIds, {
    readFilter: listFilterOf(req),
    userId: String(req.user?.userId),
    accessibleProjectIds: conversationContextOf(req).accessibleProjectIds ?? [],
  });
  return items.map((item) => {
    if (!item.conversationId) {
      return item;
    }
    const conversation = titles.get(item.conversationId);
    if (!conversation) {
      return item;
    }
    const { title, ...links } = conversation;
    return { ...item, conversationTitle: title, ...links };
  });
}

function requireIdentity(req: AuthenticatedUserRequest): {
  userId: string;
  orgId: string;
} {
  const { userId, orgId } = req.user || {};
  if (!userId || !orgId) {
    throw new UnauthorizedError('User not authenticated');
  }
  return { userId: String(userId), orgId: String(orgId) };
}

export const listArtifacts =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      requireIdentity(req);
      const qs = buildQueryString(req.query);
      const uri = `${appConfig.connectorBackend}/api/v1/artifacts${qs ? `?${qs}` : ''}`;
      const connectorResponse =
        await executeConnectorCommand<ArtifactListResponse>(
          uri,
          HttpMethod.GET,
          req.headers as Record<string, string>,
        );
      const statusCode = connectorResponse.statusCode;
      if (statusCode < 200 || statusCode >= 300) {
        throw handleBackendError(connectorResponse, 'list artifacts');
      }
      const data = requirePayload(
        connectorResponse.data,
        'Failed to list artifacts',
      );
      const items = Array.isArray(data.items)
        ? (data.items as ArtifactListItem[])
        : [];
      data.items = await enrichConversationTitles(items, req);
      res.status(statusCode).json(data);
    } catch (error: unknown) {
      next(handleBackendError(error, 'list artifacts'));
    }
  };

export const getArtifact =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      requireIdentity(req);
      const { artifactId } = req.params as { artifactId: string };
      const connectorResponse = await executeConnectorCommand<ArtifactListItem>(
        artifactUri(appConfig.connectorBackend, artifactId),
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );
      const statusCode = connectorResponse.statusCode;
      if (statusCode < 200 || statusCode >= 300) {
        throw handleBackendError(connectorResponse, 'get artifact');
      }
      const data = requirePayload(
        connectorResponse.data,
        'Failed to get artifact',
      ) as ArtifactListItem;
      const [enriched] = await enrichConversationTitles([data], req);
      res.status(statusCode).json(enriched);
    } catch (error: unknown) {
      next(handleBackendError(error, 'get artifact'));
    }
  };

export const listArtifactVersions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      requireIdentity(req);
      const { artifactId } = req.params as { artifactId: string };
      const connectorResponse =
        await executeConnectorCommand<ArtifactVersionsResponse>(
          artifactUri(appConfig.connectorBackend, artifactId, '/versions'),
          HttpMethod.GET,
          req.headers as Record<string, string>,
        );
      const statusCode = connectorResponse.statusCode;
      if (statusCode < 200 || statusCode >= 300) {
        throw handleBackendError(connectorResponse, 'list artifact versions');
      }
      const data = requirePayload(
        connectorResponse.data,
        'Failed to list artifact versions',
      );
      res.status(statusCode).json(data);
    } catch (error: unknown) {
      next(handleBackendError(error, 'list artifact versions'));
    }
  };

const NAMED_REFUSAL = /^[A-Z][A-Z0-9_]{2,63}$/;

/** The connectors service refused a share and named why, with a code a client can look up. */
class ArtifactShareRefusedError extends DomainHttpError {
  constructor(code: string, message: string) {
    super(code, message, 403);
  }
}

/** A 403 whose detail names a code keeps it, so a client can tell why the file was refused. */
const namedRefusal = (response: {
  statusCode: number;
  data?: unknown;
}): DomainHttpError | undefined => {
  if (response.statusCode !== 403) return undefined;
  const detail = (response.data as { detail?: unknown } | undefined)?.detail as
    | { code?: unknown; message?: unknown }
    | undefined;
  if (!detail || typeof detail.code !== 'string' || !NAMED_REFUSAL.test(detail.code)) {
    return undefined;
  }
  return new ArtifactShareRefusedError(
    detail.code,
    typeof detail.message === 'string' ? detail.message : 'This file cannot be shared',
  );
};

// Sharing is decided in the connectors service: only the owner may share, and
// only with users of the same org. This layer forwards the caller's identity.
const proxyArtifactPermissions =
  (method: HttpMethod, operation: string) =>
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      requireIdentity(req);
      const { artifactId } = req.params as { artifactId: string };
      const userIds = (req.body as { userIds?: string[] } | undefined)?.userIds;
      const connectorResponse = await executeConnectorCommand(
        artifactUri(appConfig.connectorBackend, artifactId, '/permissions'),
        method,
        req.headers as Record<string, string>,
        method === HttpMethod.GET ? undefined : { userIds },
      );
      const statusCode = connectorResponse.statusCode;
      if (statusCode < 200 || statusCode >= 300) {
        throw (
          namedRefusal(connectorResponse) ??
          handleBackendError(connectorResponse, operation)
        );
      }
      res
        .status(statusCode)
        .json(requirePayload(connectorResponse.data, `Failed to ${operation}`));
    } catch (error: unknown) {
      next(handleBackendError(error, operation));
    }
  };

export const listArtifactMembers = proxyArtifactPermissions(
  HttpMethod.GET,
  'list artifact members',
);
export const shareArtifact = proxyArtifactPermissions(
  HttpMethod.POST,
  'share artifact',
);
export const unshareArtifact = proxyArtifactPermissions(
  HttpMethod.DELETE,
  'unshare artifact',
);

export const deleteArtifact =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      requireIdentity(req);
      const { artifactId } = req.params as { artifactId: string };
      const connectorResponse = await executeConnectorCommand(
        artifactUri(appConfig.connectorBackend, artifactId),
        HttpMethod.DELETE,
        req.headers as Record<string, string>,
      );
      const statusCode = connectorResponse.statusCode;
      if (statusCode < 200 || statusCode >= 300) {
        throw handleBackendError(connectorResponse, 'delete artifact');
      }
      res.status(204).end();
    } catch (error: unknown) {
      next(handleBackendError(error, 'delete artifact'));
    }
  };
