import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { ConnectorsApi } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/api/swr-cache', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api/swr-cache')>()),
  revalidateSwrNamespace: vi.fn(),
}));

const api = vi.mocked(apiClient);
const revalidate = vi.mocked(revalidateSwrNamespace);

describe('connector writes refresh the cached team connector lists (and the demo check)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.post.mockResolvedValue({ data: {} });
    api.put.mockResolvedValue({ data: {} });
    api.delete.mockResolvedValue({ data: null });
  });

  it.each([
    ['create', () => ConnectorsApi.createConnectorInstance({
      connectorType: 'Demo', instanceName: 'Acme', scope: 'team', authType: 'NONE', config: { auth: {} },
    })],
    ['delete', () => ConnectorsApi.deleteConnectorInstance('c1')],
    ['toggle', () => ConnectorsApi.toggleConnector('c1', 'sync')],
    ['rename', () => ConnectorsApi.updateConnectorInstanceName('c1', 'Acme 2')],
  ])('after a successful %s', async (_name, write) => {
    await write();

    expect(revalidate).toHaveBeenCalledWith(SWR_NS.teamConnectorLists);
  });

  it('not after a failed write, since nothing changed', async () => {
    api.post.mockRejectedValue(new Error('500'));

    await expect(ConnectorsApi.toggleConnector('c1', 'sync')).rejects.toThrow('500');

    expect(revalidate).not.toHaveBeenCalled();
  });
});
