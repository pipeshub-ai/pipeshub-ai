import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { ProjectApi } from '../project-api';

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedPut = vi.mocked(apiClient.put);
const mockedPatch = vi.mocked(apiClient.patch);
const mockedDelete = vi.mocked(apiClient.delete);

beforeEach(() => {
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPut.mockReset();
  mockedPatch.mockReset();
  mockedDelete.mockReset();
});

describe('ProjectApi.list', () => {
  it('defaults page/limit/scope and omits optional filters when unset', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { projects: [], pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0 } },
    });
    await ProjectApi.list();
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/projects', {
      params: { page: 1, limit: 20, scope: 'mine' },
    });
  });

  it('forwards search, scope, and archive filters when provided', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { projects: [], pagination: { page: 2, limit: 10, totalCount: 0, totalPages: 0 } },
    });
    await ProjectApi.list({
      page: 2,
      limit: 10,
      search: 'q3',
      scope: 'shared',
      includeArchived: true,
      isArchived: false,
    });
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/projects', {
      params: {
        page: 2,
        limit: 10,
        search: 'q3',
        scope: 'shared',
        includeArchived: true,
        isArchived: false,
      },
    });
  });
});

describe('ProjectApi.get / create / update / remove', () => {
  it('get() unwraps { project }', async () => {
    const project = { _id: 'p1', name: 'Q3' };
    mockedGet.mockResolvedValueOnce({ data: { project } });
    const result = await ProjectApi.get('p1');
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/projects/p1');
    expect(result).toEqual(project);
  });

  it('create() posts the input and unwraps { project }', async () => {
    const project = { _id: 'p1', name: 'Q3' };
    mockedPost.mockResolvedValueOnce({ data: { project } });
    const result = await ProjectApi.create({ name: 'Q3' });
    expect(mockedPost).toHaveBeenCalledWith('/api/v1/projects', { name: 'Q3' });
    expect(result).toEqual(project);
  });

  it('update() patches the project by id', async () => {
    const project = { _id: 'p1', name: 'Q3 renamed' };
    mockedPatch.mockResolvedValueOnce({ data: { project } });
    const result = await ProjectApi.update('p1', { name: 'Q3 renamed' });
    expect(mockedPatch).toHaveBeenCalledWith('/api/v1/projects/p1', { name: 'Q3 renamed' });
    expect(result).toEqual(project);
  });

  it('remove() deletes by id', async () => {
    mockedDelete.mockResolvedValueOnce({ data: { message: 'ok' } });
    await ProjectApi.remove('p1');
    expect(mockedDelete).toHaveBeenCalledWith('/api/v1/projects/p1');
  });

  it('URL-encodes ids containing special characters', async () => {
    mockedGet.mockResolvedValueOnce({ data: { project: {} } });
    await ProjectApi.get('p/1 x');
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/projects/p%2F1%20x');
  });
});

describe('ProjectApi.pin / unpin / archive / unarchive', () => {
  it.each([
    ['pin', 'pin'],
    ['unpin', 'unpin'],
    ['archive', 'archive'],
    ['unarchive', 'unarchive'],
  ] as const)('%s() posts to the right sub-route', async (method, route) => {
    mockedPost.mockResolvedValueOnce({ data: { project: { _id: 'p1' } } });
    await ProjectApi[method]('p1');
    expect(mockedPost).toHaveBeenCalledWith(`/api/v1/projects/p1/${route}`);
  });
});

describe('ProjectApi.listConversations', () => {
  it('defaults page/limit and unwraps the paginated result', async () => {
    const payload = { conversations: [], pagination: { page: 1, limit: 20, totalCount: 0, totalPages: 0 } };
    mockedGet.mockResolvedValueOnce({ data: payload });
    const result = await ProjectApi.listConversations('p1');
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/projects/p1/conversations', {
      params: { page: 1, limit: 20 },
    });
    expect(result).toEqual(payload);
  });
});

describe('ProjectApi.ensureKnowledgeBase / listMembers / upsertMembers / removeMember', () => {
  it('ensureKnowledgeBase() POSTs to the knowledge-base sub-route and returns the kbId', async () => {
    mockedPost.mockResolvedValueOnce({ data: { kbId: 'kb1' } });
    const result = await ProjectApi.ensureKnowledgeBase('p1');
    expect(mockedPost).toHaveBeenCalledWith('/api/v1/projects/p1/knowledge-base');
    expect(result).toEqual('kb1');
  });

  it('listMembers() defaults to [] on missing members', async () => {
    mockedGet.mockResolvedValueOnce({ data: {} });
    const result = await ProjectApi.listMembers('p1');
    expect(result).toEqual([]);
  });

  it('upsertMembers() PUTs the members array', async () => {
    const members = [{ principalId: 'u1', role: 'viewer' as const }];
    mockedPut.mockResolvedValueOnce({ data: { members } });
    const result = await ProjectApi.upsertMembers('p1', members);
    expect(mockedPut).toHaveBeenCalledWith('/api/v1/projects/p1/members', { members });
    expect(result).toEqual(members);
  });

  it('removeMember() deletes by projectId + memberUserId, defaulting principalType to user', async () => {
    mockedDelete.mockResolvedValueOnce({ data: { members: [] } });
    await ProjectApi.removeMember('p1', 'u1');
    expect(mockedDelete).toHaveBeenCalledWith('/api/v1/projects/p1/members/u1', {
      params: { principalType: 'user' },
    });
  });

  it('removeMember() forwards an explicit team principalType', async () => {
    mockedDelete.mockResolvedValueOnce({ data: { members: [] } });
    await ProjectApi.removeMember('p1', 't1', 'team');
    expect(mockedDelete).toHaveBeenCalledWith('/api/v1/projects/p1/members/t1', {
      params: { principalType: 'team' },
    });
  });
});

describe('ProjectApi.setConversationProject', () => {
  it('PUTs to the plain conversations route when no agentKey is given', async () => {
    mockedPut.mockResolvedValueOnce({ data: {} });
    await ProjectApi.setConversationProject('c1', 'p1');
    expect(mockedPut).toHaveBeenCalledWith('/api/v1/conversations/c1/project', { projectId: 'p1' });
  });

  it('PUTs projectId: null to unlink', async () => {
    mockedPut.mockResolvedValueOnce({ data: {} });
    await ProjectApi.setConversationProject('c1', null);
    expect(mockedPut).toHaveBeenCalledWith('/api/v1/conversations/c1/project', { projectId: null });
  });

  it('routes through the agent sub-router when agentKey is given', async () => {
    mockedPut.mockResolvedValueOnce({ data: {} });
    await ProjectApi.setConversationProject('c1', 'p1', { agentKey: 'a1' });
    expect(mockedPut).toHaveBeenCalledWith('/api/v1/agents/a1/conversations/c1/project', {
      projectId: 'p1',
    });
  });
});

describe('ProjectApi.setConversationProjectVisibility', () => {
  it('PATCHes visibility on the plain conversations route', async () => {
    mockedPatch.mockResolvedValueOnce({ data: {} });
    await ProjectApi.setConversationProjectVisibility('c1', 'project');
    expect(mockedPatch).toHaveBeenCalledWith('/api/v1/conversations/c1/project-visibility', {
      visibility: 'project',
    });
  });

  it('routes through the agent sub-router when agentKey is given', async () => {
    mockedPatch.mockResolvedValueOnce({ data: {} });
    await ProjectApi.setConversationProjectVisibility('c1', 'private', { agentKey: 'a1' });
    expect(mockedPatch).toHaveBeenCalledWith(
      '/api/v1/agents/a1/conversations/c1/project-visibility',
      { visibility: 'private' },
    );
  });
});
