import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { ApiToolsetsApi } from '../api';
import { useApiToolsetsStore } from '../store';
import { toolset } from './fixtures';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

const client = vi.mocked(apiClient);
const QUIET = { suppressErrorToast: true };
const ID = 'a'.repeat(32);

beforeEach(() => {
  for (const fn of [client.get, client.post, client.put, client.delete]) fn.mockReset().mockResolvedValue({ data: {} });
});

describe('ApiToolsetsApi', () => {
  it('validates a spec and lists instances', async () => {
    client.get.mockResolvedValueOnce({ data: { instances: [toolset()] } });
    await ApiToolsetsApi.validateSpec('openapi: 3.1.0');
    expect(client.post).toHaveBeenCalledWith('/api/v1/api-toolsets/validate', { spec: 'openapi: 3.1.0' }, QUIET);
    expect(await ApiToolsetsApi.listInstances()).toHaveLength(1);
    expect(client.get).toHaveBeenCalledWith('/api/v1/api-toolsets/instances');
  });

  it('returns an empty list when the response has none', async () => {
    expect(await ApiToolsetsApi.listInstances()).toEqual([]);
  });

  it('creates, reads settings of, updates and deletes an instance', async () => {
    const payload = { name: 'n', baseUrl: 'https://x', operations: ['a'] };
    await ApiToolsetsApi.createInstance(payload);
    await ApiToolsetsApi.getConfig(ID);
    await ApiToolsetsApi.updateInstance(ID, payload);
    await ApiToolsetsApi.deleteInstance(ID);
    expect(client.post).toHaveBeenCalledWith('/api/v1/api-toolsets/instances', payload, QUIET);
    expect(client.get).toHaveBeenCalledWith(`/api/v1/api-toolsets/instances/${ID}/config`);
    expect(client.put).toHaveBeenCalledWith(`/api/v1/api-toolsets/instances/${ID}`, payload, QUIET);
    expect(client.delete).toHaveBeenCalledWith(`/api/v1/api-toolsets/instances/${ID}`, QUIET);
  });

  it("uses the caller's own credential routes or the shared ones", async () => {
    await ApiToolsetsApi.saveCredentials(ID, { apiKey: 'k' }, false);
    await ApiToolsetsApi.saveCredentials(ID, { apiKey: 'k' }, true);
    await ApiToolsetsApi.deleteCredentials(ID, false);
    await ApiToolsetsApi.deleteCredentials(ID, true);
    expect(client.put).toHaveBeenNthCalledWith(1, `/api/v1/api-toolsets/instances/${ID}/credentials`, { apiKey: 'k' }, QUIET);
    expect(client.put).toHaveBeenNthCalledWith(2, `/api/v1/api-toolsets/instances/${ID}/shared-credentials`, { apiKey: 'k' }, QUIET);
    expect(client.delete).toHaveBeenNthCalledWith(1, `/api/v1/api-toolsets/instances/${ID}/credentials`, QUIET);
    expect(client.delete).toHaveBeenNthCalledWith(2, `/api/v1/api-toolsets/instances/${ID}/shared-credentials`, QUIET);
  });

  it('starts an own or shared sign-in, completes the callback and tests the connection', async () => {
    await ApiToolsetsApi.getAuthorizationUrl(ID, false);
    await ApiToolsetsApi.getAuthorizationUrl(ID, true);
    await ApiToolsetsApi.completeOAuthCallback({ code: 'c', state: 's' });
    await ApiToolsetsApi.testConnection(ID);
    // In a browser there is no desktop marker to send.
    expect(client.get).toHaveBeenNthCalledWith(1, `/api/v1/api-toolsets/instances/${ID}/oauth/authorize`, { ...QUIET, params: {} });
    expect(client.get).toHaveBeenNthCalledWith(2, `/api/v1/api-toolsets/instances/${ID}/shared-credentials/oauth/authorize`, { ...QUIET, params: {} });
    expect(client.get).toHaveBeenNthCalledWith(3, '/api/v1/api-toolsets/oauth/callback', { params: { code: 'c', state: 's' }, ...QUIET });
    expect(client.post).toHaveBeenCalledWith(`/api/v1/api-toolsets/instances/${ID}/test`, {}, QUIET);
  });
});

describe('useApiToolsetsStore', () => {
  beforeEach(() => useApiToolsetsStore.getState().reset());

  it('opens the editor to add or to edit one toolset', () => {
    const s = useApiToolsetsStore.getState();
    s.openCreate();
    expect(useApiToolsetsStore.getState().editor).toEqual({ open: true, editingId: null });
    s.openEdit(toolset());
    expect(useApiToolsetsStore.getState().editor).toEqual({ open: true, editingId: ID });
    s.closeEditor();
    expect(useApiToolsetsStore.getState().editor.open).toBe(false);
  });

  it('keeps the open connection dialog on the freshly loaded copy, and closes it when the toolset is gone', () => {
    const s = useApiToolsetsStore.getState();
    s.setConnectTarget(toolset());
    s.setInstances([toolset({ isConnected: true })]);
    expect(useApiToolsetsStore.getState().connectTarget?.isConnected).toBe(true);
    s.setInstances([]);
    expect(useApiToolsetsStore.getState().connectTarget).toBeNull();
  });
});
