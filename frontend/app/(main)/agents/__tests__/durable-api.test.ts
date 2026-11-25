import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { AgentsApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);

describe('AgentsApi durable lifecycle', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
  });

  it('reads a run through encoded agent, session, and run identifiers', async () => {
    const run = { runId: 'run/id', status: 'running', revision: 2 };
    mockedGet.mockResolvedValueOnce({ data: { run } });

    expect(await AgentsApi.getAgentRun('agent/id', 'session/id', 'run/id')).toBe(run);
    expect(mockedGet).toHaveBeenCalledWith(
      '/api/v1/agents/agent%2Fid/conversations/session%2Fid/runs/run%2Fid',
    );
  });

  it('sends optimistic revision and idempotency identity on resume', async () => {
    const receipt = { commandId: 'command-1' };
    mockedPost.mockResolvedValueOnce({ data: { receipt } });
    const input = { expectedRevision: 4, idempotencyKey: 'resume-click-1' };

    expect(await AgentsApi.controlAgentRun('agent', 'session', 'run', 'resume', input))
      .toBe(receipt);
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/agents/agent/conversations/session/runs/run/resume',
      input,
    );
  });

  it('cancels with the latest revision instead of the admission revision', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { run: { runId: 'run', status: 'running', revision: 9 } },
    });
    mockedPost.mockResolvedValueOnce({ data: { receipt: { commandId: 'cancel-1' } } });

    await AgentsApi.cancelCurrentAgentRun('agent', 'session', 'run');

    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/agents/agent/conversations/session/runs/run/cancel',
      expect.objectContaining({
        expectedRevision: 9,
        idempotencyKey: expect.stringMatching(/^cancel-run-/),
      }),
    );
  });

  it('does not send cancel for a terminal run', async () => {
    mockedGet.mockResolvedValueOnce({
      data: { run: { runId: 'run', status: 'completed', revision: 10 } },
    });

    expect(await AgentsApi.cancelCurrentAgentRun('agent', 'session', 'run')).toBeNull();
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it('refreshes the revision when a progress commit races with cancel', async () => {
    mockedGet
      .mockResolvedValueOnce({
        data: { run: { runId: 'run', status: 'running', revision: 9 } },
      })
      .mockResolvedValueOnce({
        data: { run: { runId: 'run', status: 'running', revision: 10 } },
      });
    mockedPost
      .mockRejectedValueOnce({ response: { status: 409 } })
      .mockResolvedValueOnce({ data: { receipt: { commandId: 'cancel-2' } } });

    const receipt = await AgentsApi.cancelCurrentAgentRun('agent', 'session', 'run');

    expect(receipt).toEqual({ commandId: 'cancel-2' });
    expect(mockedPost).toHaveBeenNthCalledWith(
      2,
      '/api/v1/agents/agent/conversations/session/runs/run/cancel',
      expect.objectContaining({ expectedRevision: 10 }),
    );
  });
});
