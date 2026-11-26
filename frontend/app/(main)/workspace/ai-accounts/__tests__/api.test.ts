/**
 * U2 AC-1..AC-7: the API client of the accounts pages. Every response is
 * mapped field by field and every failure becomes one of the states the
 * pages render; the pages show their own errors, so no global toast.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { ModelAccountsApi, toAccountsError } from '../api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
const put = vi.mocked(apiClient.put);
const del = vi.mocked(apiClient.delete);

const QUIET = { suppressErrorToast: true };

const rule = (over: Record<string, unknown> = {}) => ({
  id: 'openai-api-key-user',
  provider: 'openai',
  accountKind: 'user_api_key',
  route: 'gateway',
  engines: null,
  status: 'allowed',
  consumer: false,
  source: 'https://openai.com/policies',
  built: true,
  outcome: 'allowed',
  ...over,
});

const refused = (statusCode: number | undefined, message = '') => ({ type: 'X', statusCode, message });

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ModelAccountsApi', () => {
  it.each([
    ['503 on a read', refused(503, 'store down'), false, { kind: 'unavailable', status: 503, message: null }],
    ['no response (network)', refused(undefined, 'Network error'), false, { kind: 'unavailable', status: null, message: null }],
    ['403', refused(403, 'Model settings are managed by the user'), true, { kind: 'forbidden', status: 403, message: null }],
    ['400 from userAdminCheck', refused(400, 'Admin access required'), false, { kind: 'forbidden', status: 400, message: null }],
    ['400 on a write', refused(400, 'apiKey: This is not an OpenAI API key.'), true, {
      kind: 'invalid',
      status: 400,
      message: 'apiKey: This is not an OpenAI API key.',
    }],
    ['404', refused(404, 'gone'), true, { kind: 'not_found', status: 404, message: null }],
    ['500', refused(500, 'boom'), false, { kind: 'failed', status: 500, message: null }],
  ])('AC-4: %s', (_name, error, write, expected) => {
    expect(toAccountsError(error, write)).toEqual(expected);
  });

  it('AC-4: a failed call rejects with the mapped error', async () => {
    get.mockRejectedValueOnce(refused(503));
    await expect(ModelAccountsApi.access()).rejects.toEqual({ kind: 'unavailable', status: 503, message: null });
  });

  it('AC-6: no call lists, stores or removes a key of the person\'s own', () => {
    expect(Object.keys(ModelAccountsApi).sort()).toEqual([
      'access',
      'codingEngine',
      'putCodingEngine',
      'putOrgAutoRoute',
      'putOrgCodingEngine',
    ]);
  });

  it('AC-5: maps the access view; an unknown outcome is kept as unknown, malformed rows dropped', async () => {
    get.mockResolvedValueOnce({
      data: {
        version: '2026-10-02.1',
        offering: 'managed',
        policy: { disabledRules: ['gemini-api-key-user', 7], consumerPlans: false },
        rules: [
          rule(),
          rule({ id: 'future', outcome: 'something_new', status: 'mystery', engines: ['codex', 3] }),
          rule({ accountKind: 'not_a_kind' }),
          'junk',
        ],
      },
    });
    const view = await ModelAccountsApi.access();
    expect(get).toHaveBeenCalledWith('/api/v1/agent-harness/model-accounts/access', QUIET);
    expect(view.version).toBe('2026-10-02.1');
    expect(view.offering).toBe('managed');
    expect(view.policy).toEqual({ disabledRules: ['gemini-api-key-user'], consumerPlans: false });
    expect(view.rules.map((r) => r.id)).toEqual(['openai-api-key-user', 'future']);
    expect(view.rules[1]).toMatchObject({ outcome: 'unknown', status: 'unknown', engines: ['codex'] });
  });

  it('AC-7: every call suppresses the global error toast', async () => {
    get.mockResolvedValue({ data: {} });
    put.mockResolvedValue({ data: {} });
    post.mockResolvedValue({ data: {} });
    del.mockResolvedValue({ data: '' });
    await ModelAccountsApi.access();
    await ModelAccountsApi.codingEngine();
    await ModelAccountsApi.putCodingEngine('opencode');
    await ModelAccountsApi.putOrgCodingEngine(null);
    const configs = [...get.mock.calls, ...del.mock.calls].map((call) => call[1]);
    const bodyConfigs = [...put.mock.calls, ...post.mock.calls].map((call) => call[2]);
    expect([...configs, ...bodyConfigs]).toHaveLength(4);
    for (const config of [...configs, ...bodyConfigs]) expect(config).toEqual(QUIET);
  });
});
