import { describe, it, expect, vi } from 'vitest';

const get = vi.fn();
vi.mock('@/lib/api', () => ({ apiClient: { get: (...a: unknown[]) => get(...a), put: vi.fn() } }));

import { PromptsApi } from '../api';

describe('PromptsApi.getSystemPrompts', () => {
  it('rejects instead of returning blank prompts when the request fails', async () => {
    get.mockRejectedValueOnce(new Error('500'));
    await expect(PromptsApi.getSystemPrompts()).rejects.toThrow('500');
  });

  it('defaults missing fields to empty strings', async () => {
    get.mockResolvedValueOnce({ data: {} });
    await expect(PromptsApi.getSystemPrompts()).resolves.toEqual({
      customSystemPrompt: '',
      customSystemPromptWebSearch: '',
      customSystemPromptAgent: '',
    });
  });
});
