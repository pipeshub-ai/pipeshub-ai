import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProcessedError } from '@/lib/api';

const get = vi.fn();
vi.mock('@/lib/api', () => ({ apiClient: { get: (...args: unknown[]) => get(...args) } }));

import { KnowledgeHubApi } from '../api';

const error = (statusCode: number) => ({ statusCode, message: 'x' }) as ProcessedError;

function suppress(): (e: ProcessedError) => boolean {
  const predicate = get.mock.calls[0][1].suppressErrorToast;
  expect(typeof predicate).toBe('function');
  return predicate;
}

describe('All Records listing refusals', () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: {} });
  });

  it('the root listing leaves a 422 or 503 to the table, not a toast', async () => {
    await KnowledgeHubApi.getAllRootItems({});
    const quiet = suppress();
    expect([quiet(error(422)), quiet(error(503)), quiet(error(500))]).toEqual([true, true, false]);
  });

  it('a drilled-down listing for the table does the same', async () => {
    await KnowledgeHubApi.loadFolderData('app', 'a1', {}, { quietRefusals: true });
    const quiet = suppress();
    expect([quiet(error(422)), quiet(error(503)), quiet(error(500))]).toEqual([true, true, false]);
  });

  it('other folder reads still toast a 503', async () => {
    await KnowledgeHubApi.loadFolderData('app', 'a1', {});
    expect(suppress()(error(503))).toBe(false);
  });
});
