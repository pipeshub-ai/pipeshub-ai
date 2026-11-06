import { describe, expect, it, vi } from 'vitest';

const get = vi.hoisted(() => vi.fn());
vi.mock('../axios-instance', () => ({ apiClient: { get } }));

import { sharedGet } from '../shared-get';

describe('sharedGet', () => {
  it('calls that overlap share one request; a call after it answered asks again', async () => {
    let answer: (value: unknown) => void = () => {};
    get.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));

    const first = sharedGet<{ name: string }>('/api/v1/org');
    const second = sharedGet<{ name: string }>('/api/v1/org');
    answer({ data: { name: 'Acme' } });

    await expect(first).resolves.toEqual({ name: 'Acme' });
    await expect(second).resolves.toEqual({ name: 'Acme' });
    expect(get).toHaveBeenCalledTimes(1);

    get.mockResolvedValueOnce({ data: { name: 'Acme 2' } });
    await expect(sharedGet('/api/v1/org')).resolves.toEqual({ name: 'Acme 2' });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('a failed request fails every caller that shared it, and the next call asks again', async () => {
    get.mockReset();
    get.mockRejectedValueOnce(new Error('500'));
    await expect(Promise.all([sharedGet('/x'), sharedGet('/x')])).rejects.toThrow('500');
    get.mockResolvedValueOnce({ data: 1 });
    await expect(sharedGet('/x')).resolves.toBe(1);
    expect(get).toHaveBeenCalledTimes(2);
  });
});
