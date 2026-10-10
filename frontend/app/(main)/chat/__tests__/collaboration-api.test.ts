import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ErrorType } from '@/lib/api/api-error';
import { StreamError } from '@/lib/api/stream-errors';

const get = vi.fn();
const put = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const del = vi.fn();
vi.mock('@/lib/api', () => ({
  apiClient: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
    post: (...a: unknown[]) => post(...a),
    patch: (...a: unknown[]) => patch(...a),
    delete: (...a: unknown[]) => del(...a),
  },
}));

import {
  CollaborationApi,
  conversationApiPath,
  conversationErrorDetails,
  conversationErrorStatus,
  isConversationError,
} from '../collaboration-api';
import type { ConversationRef } from '../collaboration-types';

const chat: ConversationRef = { kind: 'chat', id: 'c1' };
const agent: ConversationRef = { kind: 'agent', agentKey: 'a 7', id: 'c1' };

beforeEach(() => {
  vi.clearAllMocks();
});

describe('conversationApiPath', () => {
  it('builds chat and agent paths and encodes ids', () => {
    expect(conversationApiPath(chat)).toBe('/api/v1/conversations/c1');
    expect(conversationApiPath(agent)).toBe('/api/v1/agents/a%207/conversations/c1');
  });
});

describe('CollaborationApi.fetchFeed (D-a)', () => {
  const page = {
    messages: [],
    rev: 4,
    nextSeq: 9,
    hasMore: false,
    activeRun: null,
    lastActivityAt: 1,
  };

  it('accepts 200 and 304 and sends afterSeq and rev', async () => {
    get.mockResolvedValue({ status: 200, data: page });
    const signal = new AbortController().signal;
    await CollaborationApi.fetchFeed(chat, { afterSeq: 8, rev: 3 }, signal);

    const [url, config] = get.mock.calls[0];
    expect(url).toBe('/api/v1/conversations/c1/feed');
    expect(config.params).toEqual({ afterSeq: 8, rev: 3 });
    expect(config.signal).toBe(signal);
    expect(config.suppressErrorToast).toBe(true);
    expect(config.validateStatus(200)).toBe(true);
    expect(config.validateStatus(304)).toBe(true);
    expect(config.validateStatus(404)).toBe(false);
    expect(config.validateStatus(500)).toBe(false);
  });

  it('returns the page on 200', async () => {
    get.mockResolvedValue({ status: 200, data: page });
    await expect(CollaborationApi.fetchFeed(chat, { afterSeq: -1 })).resolves.toEqual(page);
  });

  it('omits rev on the first poll', async () => {
    get.mockResolvedValue({ status: 200, data: page });
    await CollaborationApi.fetchFeed(agent, { afterSeq: -1, rev: null });
    expect(get.mock.calls[0][0]).toBe('/api/v1/agents/a%207/conversations/c1/feed');
    expect(get.mock.calls[0][1].params).toEqual({ afterSeq: -1 });
  });

  it('resolves to "not-modified" on 304', async () => {
    get.mockResolvedValue({ status: 304, data: '' });
    await expect(CollaborationApi.fetchFeed(chat, { afterSeq: 9, rev: 4 })).resolves.toBe('not-modified');
  });

  it('propagates a 404 so the poller can mark access lost', async () => {
    const error = { type: ErrorType.NOT_FOUND, message: 'x', statusCode: 404, code: 'CONVERSATION_NOT_FOUND' };
    get.mockRejectedValue(error);
    await expect(CollaborationApi.fetchFeed(chat, { afterSeq: 0 })).rejects.toBe(error);
  });
});

describe('CollaborationApi collaborators', () => {
  it('GETs the collaborators view', async () => {
    get.mockResolvedValue({ data: { owner: { userId: 'o', displayName: 'O' }, collaboratorCount: 0, myAccess: 'read' } });
    const res = await CollaborationApi.getCollaborators(agent);
    expect(get).toHaveBeenCalledWith('/api/v1/agents/a%207/conversations/c1/collaborators', { suppressErrorToast: true });
    expect(res).toMatchObject({ myAccess: 'read' });
  });

  it('PUTs people and teams with the note, and confirmOrgWide only when set', async () => {
    put.mockResolvedValue({ data: {} });
    const collaborators = [
      { principalType: 'user' as const, principalId: 'u2', accessLevel: 'write' as const },
      { principalType: 'team' as const, principalId: 't1', accessLevel: 'read' as const },
    ];
    await CollaborationApi.putCollaborators(chat, { collaborators, note: 'please take over' });
    expect(put).toHaveBeenCalledWith('/api/v1/conversations/c1/collaborators', {
      collaborators,
      note: 'please take over',
    }, { suppressErrorToast: true });

    await CollaborationApi.putCollaborators(chat, { collaborators, confirmOrgWide: true });
    expect(put).toHaveBeenLastCalledWith('/api/v1/conversations/c1/collaborators', {
      collaborators,
      confirmOrgWide: true,
    }, { suppressErrorToast: true });
  });

  it('DELETEs one principal with its type', async () => {
    del.mockResolvedValue({ data: {} });
    await CollaborationApi.removeCollaborator(chat, 'team/1', 'team');
    expect(del).toHaveBeenCalledWith('/api/v1/conversations/c1/collaborators/team%2F1', {
      params: { principalType: 'team' },
      suppressErrorToast: true,
    });
    await CollaborationApi.removeCollaborator(chat, 'u2');
    expect(del).toHaveBeenLastCalledWith('/api/v1/conversations/c1/collaborators/u2', {
      params: { principalType: 'user' },
      suppressErrorToast: true,
    });
  });

  it('PATCHes settings, POSTs transfer and leave', async () => {
    patch.mockResolvedValue({ data: { settings: {} } });
    post.mockResolvedValue({ data: {} });
    await CollaborationApi.patchSettings(chat, { editorsCanInvite: true });
    expect(patch).toHaveBeenCalledWith('/api/v1/conversations/c1/collaboration-settings', {
      editorsCanInvite: true,
    }, { suppressErrorToast: true });
    await CollaborationApi.transferOwnership(agent, 'u2');
    expect(post).toHaveBeenCalledWith('/api/v1/agents/a%207/conversations/c1/transfer-ownership', {
      newOwnerUserId: 'u2',
    }, { suppressErrorToast: true });
    await CollaborationApi.leave(chat);
    expect(post).toHaveBeenLastCalledWith('/api/v1/conversations/c1/leave');
  });

  it('archives per person: PATCH for chats, POST for agent chats', async () => {
    patch.mockResolvedValue({});
    post.mockResolvedValue({});
    await CollaborationApi.archiveSelf(chat);
    expect(patch).toHaveBeenCalledWith('/api/v1/conversations/c1/archive');
    await CollaborationApi.unarchiveSelf(chat);
    expect(patch).toHaveBeenLastCalledWith('/api/v1/conversations/c1/unarchive');
    await CollaborationApi.archiveSelf(agent);
    expect(post).toHaveBeenCalledWith('/api/v1/agents/a%207/conversations/c1/archive');
    await CollaborationApi.unarchiveSelf(agent);
    expect(post).toHaveBeenLastCalledWith('/api/v1/agents/a%207/conversations/c1/unarchive');
  });
});

describe('readiness, explain and preview', () => {
  it('GETs readiness without a toast', async () => {
    get.mockResolvedValue({ data: { canSend: false, reasons: ['OWNER_INACTIVE'] } });
    const res = await CollaborationApi.getReadiness(chat);
    expect(get.mock.calls[0][0]).toBe('/api/v1/conversations/c1/readiness');
    expect(get.mock.calls[0][1].suppressErrorToast).toBe(true);
    expect(res.reasons).toEqual(['OWNER_INACTIVE']);
  });

  it('explains the caller, or another subject, by chat resource', async () => {
    get.mockResolvedValue({ data: { role: 'none', via: [] } });
    await CollaborationApi.explain(agent);
    expect(get.mock.calls[0][0]).toBe('/api/v1/authz/explain');
    expect(get.mock.calls[0][1].params).toEqual({ resource: 'chat:c1' });
    await CollaborationApi.explain(chat, 'u9');
    expect(get.mock.calls[1][1].params).toEqual({ resource: 'chat:c1', subject: 'user:u9' });
  });

  it('POSTs the access-change preview', async () => {
    post.mockResolvedValue({ data: { gains: [], loses: [], becomesReadOnly: [], truncated: false } });
    await CollaborationApi.previewAccessChange(chat, { type: 'link', projectId: 'p1' });
    expect(post.mock.calls[0][0]).toBe('/api/v1/authz/explain/preview');
    expect(post.mock.calls[0][1]).toEqual({ resource: 'chat:c1', change: { type: 'link', projectId: 'p1' } });
    expect(post.mock.calls[0][2].suppressErrorToast).toBe(true);
  });
});

describe('error helpers', () => {
  const processed = {
    type: ErrorType.CONFLICT,
    message: 'busy',
    statusCode: 409,
    code: 'CONVERSATION_BUSY',
    details: { activeRun: { userId: 'u2' } },
  };
  const streamed = new StreamError('busy', 409, 'CONVERSATION_BUSY', { activeRun: { userId: 'u2' } });

  it('isConversationError matches a ProcessedError and a StreamError', () => {
    expect(isConversationError(processed, 'CONVERSATION_BUSY')).toBe(true);
    expect(isConversationError(streamed, 'CONVERSATION_BUSY')).toBe(true);
    expect(isConversationError(streamed, 'CONVERSATION_CHANGED')).toBe(false);
  });

  it('is false for errors without a code and non-objects', () => {
    expect(isConversationError(new Error('x'), 'CONVERSATION_BUSY')).toBe(false);
    expect(isConversationError(null, 'CONVERSATION_BUSY')).toBe(false);
    expect(isConversationError('CONVERSATION_BUSY', 'CONVERSATION_BUSY')).toBe(false);
    expect(isConversationError({ code: '' }, 'CONVERSATION_BUSY')).toBe(false);
  });

  it('reads details and status from either error shape', () => {
    expect(conversationErrorDetails(processed)).toEqual(processed.details);
    expect(conversationErrorDetails(streamed)).toEqual(processed.details);
    expect(conversationErrorDetails(new Error('x'))).toBeUndefined();
    expect(conversationErrorDetails(undefined)).toBeUndefined();
    expect(conversationErrorStatus(processed)).toBe(409);
    expect(conversationErrorStatus(streamed)).toBe(409);
    expect(conversationErrorStatus({})).toBeUndefined();
    expect(conversationErrorStatus(3)).toBeUndefined();
  });
});
