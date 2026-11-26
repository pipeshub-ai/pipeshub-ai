import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { AgentPublishIncompleteError, AgentsApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedPut = vi.mocked(apiClient.put);
const version = {
  agentKey: 'a/1',
  version: 5,
  digest: 'sha256:x',
  source: 'publish',
  publishedBy: 'u1',
  publishedAtTimestamp: 1,
  rolledBackFrom: null,
  note: null,
};

describe('WP-21 OWD-14: agent versions API', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedPut.mockReset();
  });

  it('OWD-14 API-1: lists versions with the draft state, and tolerates a body without them', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { versions: [version], latestVersion: 5, latestDigest: 'sha256:x', unpublishedChanges: true },
    });
    expect(await AgentsApi.listAgentVersions('a/1')).toEqual({
      versions: [version],
      latestVersion: 5,
      latestDigest: 'sha256:x',
      unpublishedChanges: true,
    });
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/agents/a%2F1/versions', undefined);

    // A schedule's version line reads it quietly: a refusal there is not worth a global error toast.
    mockedGet.mockResolvedValueOnce({ data: {} });
    expect(await AgentsApi.listAgentVersions('a1', { quiet: true })).toEqual({
      versions: [],
      latestVersion: 0,
      latestDigest: '',
      unpublishedChanges: false,
    });
    expect(mockedGet).toHaveBeenLastCalledWith('/api/v1/agents/a1/versions', { suppressErrorToast: true });
  });

  it('OWD-14 API-2: reads one version with its definition', async () => {
    mockedGet.mockResolvedValueOnce({ data: { version: { ...version, definition: { name: 'x' } } } });
    expect((await AgentsApi.getAgentVersion('a1', 5)).definition).toEqual({ name: 'x' });
    expect(mockedGet).toHaveBeenCalledWith('/api/v1/agents/a1/versions/5');
  });

  it('OWD-14 API-3: publishes and rolls back with a trimmed note, and sends no note when it is blank', async () => {
    mockedPost.mockResolvedValue({ data: { version, created: true, discardedUnpublishedChanges: true } });
    await AgentsApi.publishAgentVersion('a1', '  Escalate P1.  ');
    expect(mockedPost).toHaveBeenLastCalledWith('/api/v1/agents/a1/versions', { note: 'Escalate P1.' });
    await AgentsApi.publishAgentVersion('a1', '   ');
    expect(mockedPost).toHaveBeenLastCalledWith('/api/v1/agents/a1/versions', {});

    const result = await AgentsApi.rollbackAgentVersion('a1', 2, 'Too eager');
    expect(mockedPost).toHaveBeenLastCalledWith('/api/v1/agents/a1/versions/2/rollback', { note: 'Too eager' });
    expect(result.discardedUnpublishedChanges).toBe(true);
  });

  it('OWD-14 API-4: a draft save sends publish false; a publishing save sends its note; neither is sent otherwise', async () => {
    mockedPut.mockResolvedValue({ data: { status: 'success', agent: { _key: 'a1' } } });
    await AgentsApi.updateAgent('a1', { name: 'n' }, { publish: false });
    expect(mockedPut).toHaveBeenLastCalledWith('/api/v1/agents/a1', { name: 'n', publish: false });
    await AgentsApi.updateAgent('a1', { name: 'n' }, { versionNote: ' Renamed ' });
    expect(mockedPut).toHaveBeenLastCalledWith('/api/v1/agents/a1', { name: 'n', versionNote: 'Renamed' });
    await AgentsApi.updateAgent('a1', { name: 'n' });
    expect(mockedPut).toHaveBeenLastCalledWith('/api/v1/agents/a1', { name: 'n' });
  });

  it('OWD-14 API-5: a save whose publish failed is an AgentPublishIncompleteError carrying the saved draft', async () => {
    mockedPut.mockResolvedValueOnce({
      data: { status: 'partial_success', message: 'Agent saved, but publishing it failed.', version: null },
    });
    mockedGet.mockResolvedValueOnce({ data: { agent: { _key: 'a1', name: 'draft', toolsets: [] } } });
    const failure = await AgentsApi.updateAgent('a1', { name: 'draft' }).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(AgentPublishIncompleteError);
    expect((failure as AgentPublishIncompleteError).agent?.name).toBe('draft');
  });
});
