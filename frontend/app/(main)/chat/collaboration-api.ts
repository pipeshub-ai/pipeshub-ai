import { apiClient } from '@/lib/api';
import type {
  AccessChange,
  AccessChangePreview,
  CollaborationSettings,
  CollaboratorsResponse,
  CollaboratorsView,
  ConversationErrorCode,
  ConversationRef,
  ExplainResponse,
  FeedPage,
  FeedResult,
  PutCollaboratorsInput,
  Readiness,
  CollaboratorPrincipalType,
} from './collaboration-types';
import { FEED_NOT_MODIFIED } from './collaboration-types';

/** `/api/v1/conversations/:id` or `/api/v1/agents/:key/conversations/:id`. */
export function conversationApiPath(ref: ConversationRef): string {
  const id = encodeURIComponent(ref.id);
  return ref.kind === 'agent'
    ? `/api/v1/agents/${encodeURIComponent(ref.agentKey)}/conversations/${id}`
    : `/api/v1/conversations/${id}`;
}

/** The `chat:<id>` resource string the authz routes take. */
export function conversationResource(ref: ConversationRef): string {
  return `chat:${ref.id}`;
}

/** Works for both `ProcessedError` (axios calls) and `StreamError` (SSE calls). */
export function conversationErrorCode(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === 'string' && code ? code : undefined;
}

export function isConversationError(error: unknown, code: ConversationErrorCode): boolean {
  return conversationErrorCode(error) === code;
}

export function conversationErrorDetails(error: unknown): Record<string, unknown> | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const details = (error as { details?: unknown }).details;
  return details && typeof details === 'object' ? (details as Record<string, unknown>) : undefined;
}

/** HTTP status of a failed call (`statusCode` on `ProcessedError`, `status` on `StreamError`). */
export function conversationErrorStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null) return undefined;
  const e = error as { statusCode?: unknown; status?: unknown };
  const status = e.statusCode ?? e.status;
  return typeof status === 'number' ? status : undefined;
}

export interface FeedQuery {
  /** Return messages with a `seq` above this. Default -1 (from the start). */
  afterSeq: number;
  /** The `rev` of the last answer; omit on the first call. */
  rev?: number | null;
}

export const CollaborationApi = {
  async getCollaborators(ref: ConversationRef, options?: { signal?: AbortSignal }): Promise<CollaboratorsResponse> {
    const { data } = await apiClient.get<CollaboratorsResponse>(
      `${conversationApiPath(ref)}/collaborators`,
      { suppressErrorToast: true, signal: options?.signal },
    );
    return data;
  },

  async putCollaborators(
    ref: ConversationRef,
    input: PutCollaboratorsInput,
  ): Promise<CollaboratorsResponse> {
    const body: PutCollaboratorsInput = {
      collaborators: input.collaborators,
      ...(input.note ? { note: input.note } : {}),
      ...(input.confirmOrgWide ? { confirmOrgWide: true } : {}),
    };
    const { data } = await apiClient.put<CollaboratorsResponse>(
      `${conversationApiPath(ref)}/collaborators`,
      body,
      { suppressErrorToast: true },
    );
    return data;
  },

  async removeCollaborator(
    ref: ConversationRef,
    principalId: string,
    principalType: CollaboratorPrincipalType = 'user',
  ): Promise<CollaboratorsResponse> {
    const { data } = await apiClient.delete<CollaboratorsResponse>(
      `${conversationApiPath(ref)}/collaborators/${encodeURIComponent(principalId)}`,
      { params: { principalType }, suppressErrorToast: true },
    );
    return data;
  },

  async patchSettings(
    ref: ConversationRef,
    settings: Partial<CollaborationSettings>,
  ): Promise<CollaboratorsView> {
    const { data } = await apiClient.patch<CollaboratorsView>(
      `${conversationApiPath(ref)}/collaboration-settings`,
      settings,
      { suppressErrorToast: true },
    );
    return data;
  },

  async transferOwnership(
    ref: ConversationRef,
    newOwnerUserId: string,
  ): Promise<CollaboratorsResponse> {
    const { data } = await apiClient.post<CollaboratorsResponse>(
      `${conversationApiPath(ref)}/transfer-ownership`,
      { newOwnerUserId },
      { suppressErrorToast: true },
    );
    return data;
  },

  async leave(ref: ConversationRef): Promise<void> {
    await apiClient.post(`${conversationApiPath(ref)}/leave`);
  },

  /** Per-person once shared. Chats use PATCH, agent chats use POST. */
  async archiveSelf(ref: ConversationRef): Promise<void> {
    if (ref.kind === 'agent') await apiClient.post(`${conversationApiPath(ref)}/archive`);
    else await apiClient.patch(`${conversationApiPath(ref)}/archive`);
  },

  async unarchiveSelf(ref: ConversationRef): Promise<void> {
    if (ref.kind === 'agent') await apiClient.post(`${conversationApiPath(ref)}/unarchive`);
    else await apiClient.patch(`${conversationApiPath(ref)}/unarchive`);
  },

  /**
   * Conditional poll. A 304 resolves to `'not-modified'` (axios rejects it by default, D-a).
   * Errors are not toasted: the poller owns how a 404/403/429 is shown.
   */
  async fetchFeed(
    ref: ConversationRef,
    { afterSeq, rev }: FeedQuery,
    signal?: AbortSignal,
  ): Promise<FeedResult> {
    const response = await apiClient.get<FeedPage>(`${conversationApiPath(ref)}/feed`, {
      params: { afterSeq, ...(rev != null ? { rev } : {}) },
      signal,
      validateStatus: (s) => s === 200 || s === 304,
      suppressErrorToast: true,
    });
    return response.status === 304 ? FEED_NOT_MODIFIED : response.data;
  },

  async getReadiness(ref: ConversationRef, signal?: AbortSignal): Promise<Readiness> {
    const { data } = await apiClient.get<Readiness>(`${conversationApiPath(ref)}/readiness`, {
      signal,
      suppressErrorToast: true,
    });
    return data;
  },

  /** Without `subjectUserId` this explains the caller's own access. */
  async explain(
    ref: ConversationRef,
    subjectUserId?: string,
    signal?: AbortSignal,
  ): Promise<ExplainResponse> {
    const { data } = await apiClient.get<ExplainResponse>('/api/v1/authz/explain', {
      params: {
        resource: conversationResource(ref),
        ...(subjectUserId ? { subject: `user:${subjectUserId}` } : {}),
      },
      signal,
      suppressErrorToast: true,
    });
    return data;
  },

  async previewAccessChange(
    ref: ConversationRef,
    change: AccessChange,
    signal?: AbortSignal,
  ): Promise<AccessChangePreview> {
    const { data } = await apiClient.post<AccessChangePreview>(
      '/api/v1/authz/explain/preview',
      { resource: conversationResource(ref), change },
      { signal, suppressErrorToast: true },
    );
    return data;
  },
};
