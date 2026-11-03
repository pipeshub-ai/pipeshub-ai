import { describe, it, expect, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn() } }));

import { apiClient } from '@/lib/api';
import { getVectorDbMemoryReport } from '../api';

const get = vi.mocked(apiClient.get);

describe('getVectorDbMemoryReport', () => {
  it('asks for the report without an error toast', async () => {
    get.mockResolvedValueOnce({ data: { status: 'ok' } });

    await expect(getVectorDbMemoryReport()).resolves.toEqual({ status: 'ok' });
    expect(get).toHaveBeenCalledWith('/api/v1/health/vector-db/memory', {
      suppressErrorToast: true,
    });
  });

  it('returns null instead of throwing when the report is unavailable', async () => {
    get.mockRejectedValueOnce(new Error('502'));

    await expect(getVectorDbMemoryReport()).resolves.toBeNull();
  });
});
