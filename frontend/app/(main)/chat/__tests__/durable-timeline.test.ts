/**
 * U3 FR-1..FR-7: a saved durable answer's engine steps and approvals as the
 * cards show them, approvals when the run ends, budget refusal copy in ten
 * locales, spend as text, the owner routes, and a budget-stopped run's error.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { i18n } from '@/lib/i18n';
import deDE from '@/lib/i18n/locales/de-DE.json';
import enIN from '@/lib/i18n/locales/en-IN.json';
import enUS from '@/lib/i18n/locales/en-US.json';
import esES from '@/lib/i18n/locales/es-ES.json';
import hiIN from '@/lib/i18n/locales/hi-IN.json';
import koKR from '@/lib/i18n/locales/ko-KR.json';
import zhCN from '@/lib/i18n/locales/zh-CN.json';
import zhSG from '@/lib/i18n/locales/zh-SG.json';
import zhTW from '@/lib/i18n/locales/zh-TW.json';
import type { MessagePart, PendingApproval, RunBudgetView } from '../types';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn() }));

const { approvalFromPart, withRunEnded, formatSpend, budgetRefusalKey } = await import(
  '../durable-run-ui'
);
const { ChatApi } = await import('../api');
const { createAGUIEventHandler } = await import('../agui-event-handler');

const LOCALES = { deDE, enIN, enUS, esES, hiIN, koKR, zhCN, zhSG, zhTW };
const lookup = (bundle: unknown, key: string): unknown =>
  key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], bundle);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => vi.clearAllMocks());

describe('durable run UI: saved parts (U3)', () => {
  it('FR-2: approval parts become read-only cards with their outcome', () => {
    const base: MessagePart = {
      type: 'approval',
      interactionId: 'ix.approve.k1',
      toolCallId: 'ix.approve.k1',
      toolName: 'create_issue',
      args: '{"title":"Q3"}',
    };
    for (const status of ['approved', 'denied', 'expired', 'closed'] as const) {
      expect(approvalFromPart({ ...base, status })).toMatchObject({ status, toolName: 'create_issue', args: '{"title":"Q3"}' });
    }
    expect(approvalFromPart({ ...base, status: 'completed' })?.status).toBe('closed');
    expect(approvalFromPart({ type: 'text', content: 'x' })).toBeNull();
  });

  it('FR-3: approvals nobody decided expire when the run ends', () => {
    const approval = (interactionId: string, status: PendingApproval['status']): PendingApproval => ({
      interactionId,
      toolCallId: interactionId,
      toolName: 't',
      args: '{}',
      argsSummary: null,
      status,
    });
    const ended = withRunEnded([approval('a', 'pending'), approval('b', 'approved'), approval('c', 'sending')]);
    expect(ended.map((a) => a.status)).toEqual(['expired', 'approved', 'expired']);
    const settled = [approval('b', 'denied')];
    expect(withRunEnded(settled)).toBe(settled);
  });

  it('FR-4: every budget refusal has copy in all ten locales; other codes fall back', () => {
    const codes = [
      'budget_exhausted',
      'budget_insufficient',
      'budget_unavailable',
      'budget_account_closed',
      'child_budget_exceeds_remaining',
    ];
    for (const code of codes) {
      const key = budgetRefusalKey(code);
      expect(key, code).not.toBeNull();
      for (const [name, bundle] of Object.entries(LOCALES)) {
        expect(typeof lookup(bundle, key as string), `${name} ${code}`).toBe('string');
      }
    }
    expect(budgetRefusalKey('model_error')).toBeNull();
  });

  it('FR-5: spend as dollars and tokens against the caps; no cap of its own says so', () => {
    const budget: RunBudgetView = {
      cap: { usdMicros: 5_000_000, tokens: 1_000_000 },
      committed: { usdMicros: 420_000, tokens: 1_234 },
      reserved: { usdMicros: 0, tokens: 0 },
      available: { usdMicros: 4_580_000, tokens: 998_766 },
      exhausted: false,
    };
    expect(formatSpend(budget)).toEqual({ spent: '$0.42', cap: '$5.00', tokens: '1,234', tokenCap: '1,000,000' });
    expect(formatSpend({ ...budget, cap: { usdMicros: null, tokens: null } })).toMatchObject({ cap: null, tokenCap: null });
    expect(formatSpend({ ...budget, committed: { usdMicros: 1_234, tokens: 0 } }).spent).toBe('$0.0012');
  });
});

describe('durable run UI: the owner routes (FR-6)', () => {
  it('reads timelines, authority and versions on agent and chat sessions', async () => {
    apiClient.get.mockResolvedValue({ data: { approve: true, deny: false } });
    await ChatApi.getRunTimeline({ agentId: 'agent 1', sessionId: 's1', runId: 'run-1' });
    await ChatApi.getRunTimeline({ sessionId: 's1', runId: 'run-1' });
    expect(await ChatApi.getDecisionAuthority({ sessionId: 's1', runId: 'run-1' })).toEqual({ approve: true, deny: false });
    await ChatApi.listWorkspaceVersions({ sessionId: 's1' }, { limit: 20 });
    expect(apiClient.get.mock.calls.map((call) => call[0])).toEqual([
      '/api/v1/agents/agent%201/conversations/s1/runs/run-1/timeline',
      '/api/v1/conversations/s1/runs/run-1/timeline',
      '/api/v1/conversations/s1/runs/run-1/interactions/authority',
      '/api/v1/conversations/s1/workspace/versions',
    ]);
    expect(apiClient.get.mock.calls[3]?.[1]).toEqual({ params: { limit: 20 }, suppressErrorToast: true });
  });

  it('an unreadable authority leaves the decision to the server (both buttons)', async () => {
    apiClient.get.mockRejectedValue(new Error('down'));
    expect(await ChatApi.getDecisionAuthority({ sessionId: 's1', runId: 'run-1' })).toEqual({ approve: true, deny: true });
  });

  it('a restore names the version, the head it saw and a key that replays a retried click', async () => {
    apiClient.post.mockResolvedValue({ data: { revision: 5 } });
    expect(await ChatApi.restoreWorkspaceVersion({ agentId: 'a', sessionId: 's1' }, 2, 4)).toEqual({ revision: 5 });
    await ChatApi.restoreWorkspaceVersion({ agentId: 'a', sessionId: 's1' }, 2, 4);
    expect(apiClient.post.mock.calls[0]).toEqual([
      '/api/v1/agents/a/conversations/s1/workspace/versions/2/restore',
      { baseRevision: 4, idempotencyKey: 'restore-2-on-4' },
    ]);
    expect(apiClient.post.mock.calls[1]?.[1]).toEqual(apiClient.post.mock.calls[0]?.[1]);
    apiClient.post.mockRejectedValue({
      originalError: { response: { data: { error: { code: 'HTTP_AGENT_HARNESS_WORKSPACE_CONFLICT' } } } },
    });
    expect(await ChatApi.restoreWorkspaceVersion({ sessionId: 's1' }, 2, 3)).toEqual({
      refused: 'HTTP_AGENT_HARNESS_WORKSPACE_CONFLICT',
    });
  });
});

describe('durable run UI: the same steps live and after a reload (FR-1, CTL-4, Node’s own frames and parts)', () => {
  const vector = <T>(name: string): T =>
    JSON.parse(
      readFileSync(join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals', name), 'utf8'),
    ) as T;

  it('u3_engine_run: the live frames and the saved parts give the same engine part and the same approval', () => {
    const { frames } = vector<{ frames: Array<{ frame: Record<string, unknown> }> }>('u3_engine_run.frames.json');
    const { parts } = vector<{ parts: MessagePart[] }>('u3_engine_run.parts.json');
    let live: MessagePart[] = [];
    const approvals: string[] = [];
    const handle = createAGUIEventHandler({
      onParts: (next) => (live = next),
      onApprovalRequested: (value) => approvals.push(value.interactionId),
    });
    for (const { frame } of frames) handle({ event: String(frame.type), data: frame });
    const engine = (all: MessagePart[]) => all.find((part) => part.type === 'engine_task');
    const shape = (part: MessagePart | undefined) => (part?.frames ?? []).map(({ kind, text, data }) => ({ kind, text, data }));
    expect(shape(engine(live))).toEqual(shape(engine(parts)));
    expect(shape(engine(live)).length).toBeGreaterThan(0);
    const approval = parts.map(approvalFromPart).find((a) => a !== null);
    expect(approvals).toEqual([approval?.interactionId]);
    expect(approval?.status).toBe('approved');
  });
});

describe('durable run UI: a run stopped by its budget (FR-7)', () => {
  it('RUN_ERROR with a budget code says so in the user’s language', () => {
    const onError = vi.fn();
    const handle = createAGUIEventHandler({ onError });
    handle({ event: 'RUN_ERROR', data: { type: 'RUN_ERROR', code: 'budget_exhausted', message: 'Something went wrong.' } });
    // WP-100: the words the saved failure shows on reload (they name the next step), so live and reload agree.
    expect(onError.mock.calls[0]?.[0].message).toBe(enUS.chatStream.answerError.budgetExhausted);
    handle({ event: 'RUN_ERROR', data: { type: 'RUN_ERROR', code: 'budget_unavailable', message: 'Something went wrong.' } });
    expect(onError.mock.calls[1]?.[0].message).toBe(enUS.durableRun.budget.unavailable);
  });

  it('any other RUN_ERROR keeps its message', () => {
    const onError = vi.fn();
    const handle = createAGUIEventHandler({ onError });
    handle({ event: 'RUN_ERROR', data: { type: 'RUN_ERROR', code: 'worker_failed', message: 'Something went wrong.' } });
    expect(onError.mock.calls[0]?.[0].message).toBe('Something went wrong.');
  });
});
