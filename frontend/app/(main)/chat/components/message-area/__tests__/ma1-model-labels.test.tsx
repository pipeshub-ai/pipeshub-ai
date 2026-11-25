/**
 * MA1 QA37-X3: a sub-agent's row names its role and the model it called. The
 * model is the child's own when a role set one and its parent's when it
 * inherited; the child's own timeline wins once it has loaded; a run that
 * names no model renders as it did before.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { SSEEvent } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import type { AvailableLlmModel, MessagePart, RunTimelineView } from '../../../types';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet: vi.fn(async () => undefined) }));

const { useChatStore } = await import('../../../store');
const { useJobsStore } = await import('@/lib/harness-jobs/jobs-store');
const { SubAgentGroup } = await import('../agent-activity');
const { createAGUIEventHandler } = await import('../../../agui-event-handler');
const { runModelLabel, toRunModel } = await import('../../../utils/run-model');

const h = React.createElement;
const SESSION = 'ses-1';
const initialState = useChatStore.getState();

const HAIKU = { provider: 'anthropic', model: 'claude-haiku-4-5' };
const GPT = { provider: 'openai', model: 'gpt-5.4' };

const configured = (patch: Partial<AvailableLlmModel>): AvailableLlmModel =>
  ({ modelType: 'llm', isMultimodal: false, isReasoning: false, isDefault: false, ...patch }) as AvailableLlmModel;

const CATALOG = [
  configured({ modelKey: 'k1', provider: 'anthropic', modelName: 'claude-haiku-4-5', modelFriendlyName: 'Claude Haiku 4.5' }),
  configured({ modelKey: 'k2', provider: 'openAI', modelName: 'gpt-5.4', modelFriendlyName: 'Team GPT' }),
];

const child = (patch: Partial<MessagePart> = {}): MessagePart => ({
  type: 'sub_agent',
  runId: 'run-child',
  roleName: 'researcher',
  status: 'completed',
  parts: [],
  ...patch,
});

function timeline(patch: Partial<RunTimelineView> = {}): RunTimelineView {
  return {
    runId: 'run-child',
    parentRunId: 'run-root',
    status: 'completed',
    parts: [],
    engine: null,
    approvals: [],
    children: [],
    merge: null,
    budget: null,
    ...patch,
  };
}

let childTimeline: RunTimelineView;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  // A job's timeline is read once and shared (ADR-CJ-01): each case starts with none read.
  useJobsStore.setState({ streams: {}, extras: {}, stops: {}, timelines: {}, timelineFailed: {} });
  useChatStore.setState((state) => ({
    settings: { ...state.settings, availableModels: { assistant: { models: CATALOG, fetchedAt: 0 } } },
  }));
  childTimeline = timeline();
  apiClient.get.mockImplementation(async (url: string) => {
    if (/\/runs\/run-child\/timeline$/.test(url)) return { data: childTimeline };
    if (url.endsWith('/interactions/authority')) return { data: { approve: true, deny: true } };
    throw new Error(`unexpected GET ${url}`);
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const view = (node: React.ReactElement) => render(h(Theme, null, node));

/** The conversation follows a durable run, so a child reads its own timeline when opened. */
function followDurableSession() {
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
}

describe('QA37-X3: the sub-agent row names its model', () => {
  it('QA37-X3: a child whose role set its model shows the role and the model, read together', () => {
    view(h(SubAgentGroup, { part: child({ model: HAIKU }) }));

    expect(screen.getByTestId('sub-agent-model').textContent).toBe(' · Claude Haiku 4.5');
    expect(screen.getByRole('button', { name: /^researcher · Claude Haiku 4\.5/ })).toBeTruthy();
  });

  it('QA37-X3: a child that inherited shows the model of the run that started it', () => {
    view(h(SubAgentGroup, { part: child({ roleName: 'writer', model: GPT }) }));

    expect(screen.getByRole('button', { name: /^writer · Team GPT/ })).toBeTruthy();
  });

  it('QA37-X3: a model the catalog does not know is shown by its own name', () => {
    view(h(SubAgentGroup, { part: child({ model: { provider: 'bedrock', model: 'anthropic.claude-haiku-4-5-v1:0' } }) }));

    expect(screen.getByTestId('sub-agent-model').textContent).toBe(' · anthropic.claude-haiku-4-5-v1:0');
  });

  it('QA37-X3: no model known renders the row as before', () => {
    const { container: without } = view(h(SubAgentGroup, { part: child() }));
    const before = without.innerHTML;
    expect(screen.queryByTestId('sub-agent-model')).toBeNull();
    expect(screen.getByText('researcher').childElementCount).toBe(0);
    cleanup();

    const { container: malformed } = view(
      h(SubAgentGroup, { part: child({ model: { provider: 'anthropic', model: 7 } as unknown as MessagePart['model'] }) }),
    );
    expect(malformed.innerHTML).toBe(before);
  });

  it('QA37-X3 (CJ-2), F10: a durable child’s job card names its goal, never its role id, and the model beside it', () => {
    followDurableSession();
    view(h(SubAgentGroup, { part: child({ parentRunId: 'run-root', goal: 'Research the Q3 market', model: HAIKU }) }));
    expect(screen.getByRole('group', { name: /^Research the Q3 market, / })).toBeTruthy();
    expect(screen.getByTestId('sub-agent-model').textContent).toBe('Claude Haiku 4.5');
    cleanup();

    view(h(SubAgentGroup, { part: child({ parentRunId: 'run-root' }) }));
    // A journal from before goals were named: the plain words, not the role id.
    expect(screen.getByRole('group', { name: /^Background job, / })).toBeTruthy();
    expect(screen.queryByText('researcher')).toBeNull();
    expect(screen.queryByTestId('sub-agent-model')).toBeNull();
  });

  it('QA37-X3: the child panel prefers its own timeline’s model over the part’s once it has loaded', async () => {
    followDurableSession();
    childTimeline = timeline({ model: HAIKU });
    view(h(SubAgentGroup, { part: child({ parentRunId: 'run-root', model: GPT }) }));
    expect(screen.getByTestId('sub-agent-model').textContent).toBe('Team GPT');

    fireEvent.click(screen.getByTestId('sub-agent-toggle'));
    await screen.findByTestId('child-run-panel');

    await waitFor(() => expect(screen.getByTestId('sub-agent-model').textContent).toBe('Claude Haiku 4.5'));
    expect(screen.getByTestId('sub-agent-toggle').getAttribute('aria-expanded')).toBe('true');
  });

  it('QA37-X3: a child timeline that names no model keeps the part’s', async () => {
    followDurableSession();
    view(h(SubAgentGroup, { part: child({ parentRunId: 'run-root', model: GPT }) }));

    fireEvent.click(screen.getByTestId('sub-agent-toggle'));
    await screen.findByTestId('child-run-panel');
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith(expect.stringMatching(/run-child\/timeline$/), expect.anything()));

    expect(screen.getByTestId('sub-agent-model').textContent).toBe('Team GPT');
  });
});

describe('QA37-X3: the live stream carries a child’s model', () => {
  const frame = (type: string, fields: Record<string, unknown> = {}): SSEEvent => ({ event: type, data: { type, ...fields } });

  function liveParts(childStarted: Record<string, unknown>): MessagePart[] {
    const onParts = vi.fn();
    const handle = createAGUIEventHandler({ onParts } as never);
    handle(frame('RUN_STARTED', { runId: 'run-root' }));
    handle(frame('STEP_STARTED', { runId: 'run-root', stepName: 'sub_agent:researcher' }));
    handle(frame('RUN_STARTED', { runId: 'run-child', parentRunId: 'run-root', ...childStarted }));
    return onParts.mock.calls[onParts.mock.calls.length - 1]![0] as MessagePart[];
  }

  it('QA37-X3: a child’s RUN_STARTED that names its model puts it on the sub-agent part', () => {
    expect(liveParts({ model: { ...HAIKU, role: 'researcher', apiKey: 'sk-secret' } })).toEqual([
      { type: 'sub_agent', runId: 'run-child', roleName: 'researcher', parts: [], status: 'running', model: HAIKU },
    ]);
  });

  it('QA37-X3: one that names none, or a malformed one, leaves the part as it was', () => {
    const today = [{ type: 'sub_agent', runId: 'run-child', roleName: 'researcher', parts: [], status: 'running' }];
    expect(liveParts({})).toEqual(today);
    expect(Object.keys(liveParts({})[0]!)).not.toContain('model');
    expect(liveParts({ model: 'claude-haiku-4-5' })).toEqual(today);
  });
});

describe('QA37-X3: the name a run’s model is shown under', () => {
  it('QA37-X3: the configured display name, matched by model and provider whatever its letter case', () => {
    expect(runModelLabel(GPT, CATALOG)).toBe('Team GPT');
    expect(runModelLabel({ provider: 'azure', model: 'gpt-5.4' }, CATALOG)).toBe('Team GPT');
    expect(runModelLabel(HAIKU, [])).toBe('claude-haiku-4-5');
  });

  it('QA37-X3: entries that disagree on the name fall back to the model name, never to one of them', () => {
    const two = [
      ...CATALOG,
      configured({ modelKey: 'k3', provider: 'openAI', modelName: 'gpt-5.4', modelFriendlyName: 'Finance GPT' }),
    ];
    expect(runModelLabel(GPT, two)).toBe('gpt-5.4');
    const otherProvider = [
      ...CATALOG,
      configured({ modelKey: 'k4', provider: 'azureOpenAI', modelName: 'gpt-5.4', modelFriendlyName: 'Azure GPT' }),
    ];
    expect(runModelLabel(GPT, otherProvider)).toBe('Team GPT');
  });

  it('QA37-X3: only a provider and a model make a model; nothing else is carried', () => {
    expect(toRunModel({ provider: 'openai', model: 'gpt-5.4', role: 'writer', key: 'x' })).toEqual(GPT);
    expect(toRunModel({ provider: 'openai' })).toBeUndefined();
    expect(toRunModel({ provider: 'openai', model: '' })).toBeUndefined();
    expect(toRunModel(null)).toBeUndefined();
    expect(toRunModel('gpt-5.4')).toBeUndefined();
  });
});
