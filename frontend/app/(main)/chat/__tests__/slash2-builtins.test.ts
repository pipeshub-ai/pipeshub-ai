/**
 * SLASH2 C0/C1: the built-in /commands. The registry is the contract's; the menu groups and greys out rows
 * by it; each command runs the action its button runs; a built-in never reaches a model.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import enUS from '@/lib/i18n/locales/en-US.json';
import { BUILTIN_COMMANDS } from '@/lib/contracts/harness/generated/ext/pipeshub/policy-data';
import { useCommandStore } from '@/lib/store/command-store';
import {
  BUILTIN_BUS,
  BUILTIN_FIRST_PAGE,
  SHIPPED_BUILTINS,
  builtinReason,
  findBuiltin,
  type BuiltinCommand,
  type BuiltinContext,
} from '../builtin-commands';
import { composerCommandState, type ChatCommand, type MenuEntry } from '../commands';
import { useBuiltinResults } from '../builtin-results';

const api = vi.hoisted(() => ({
  renameConversation: vi.fn(),
  getConversationUsage: vi.fn(),
  getRunTimeline: vi.fn(),
  getRunResolved: vi.fn(),
  getDecisionAuthority: vi.fn(),
  exportConversation: vi.fn(),
}));
vi.mock('../api', () => ({
  ChatApi: {
    renameConversation: (...a: unknown[]) => api.renameConversation(...a),
    getConversationUsage: (...a: unknown[]) => api.getConversationUsage(...a),
    getRunTimeline: (...a: unknown[]) => api.getRunTimeline(...a),
    getRunResolved: (...a: unknown[]) => api.getRunResolved(...a),
    getDecisionAuthority: (...a: unknown[]) => api.getDecisionAuthority(...a),
  },
}));
vi.mock('../utils/export-conversation', () => ({
  exportConversation: (...a: unknown[]) => api.exportConversation(...a),
}));

import { findModel, runBuiltin, type BuiltinDeps } from '../builtin-actions';

const REGISTRY = JSON.parse(
  readFileSync(
    path.resolve(__dirname, '../../../../../contracts/harness/ext/pipeshub/commands/builtin-commands.json'),
    'utf8',
  ),
);

const ALL: BuiltinContext = {
  running: false,
  conversation: true,
  answer: true,
  harness: true,
  forkable: true,
  reasoningModel: true,
  planStrategy: true,
  mcp: true,
  knowledge: true,
  memory: true,
  skills: true,
  workspace: true,
  agentChat: true,
  agentEditor: true,
  answers: 2,
};

const get = (name: string): BuiltinCommand => {
  const c = findBuiltin(name);
  if (!c) throw new Error(name);
  return c;
};

const lookup = (key: string): unknown =>
  key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], enUS);

const menu = (text: string, commands: ChatCommand[] = [], ctx: BuiltinContext = ALL, showAll = false): MenuEntry[] => {
  const state = composerCommandState(text, commands, {}, { reason: (c) => builtinReason(c, ctx), showAll });
  if (state.phase !== 'menu') throw new Error(`not a menu: ${state.phase}`);
  return state.entries;
};

const user = (name: string, owner: ChatCommand['owner']): ChatCommand => ({
  name,
  description: name,
  version: '1.0.0',
  owner,
  arguments: [],
});

describe('the registry', () => {
  it('is the contract file, generated', () => {
    expect(BUILTIN_COMMANDS).toEqual(REGISTRY);
  });

  it('ships all 25: C0 and C1 (with the owner\'s /knowledge), C2\'s /compact, C3\'s /diff /undo /review and C4\'s /init', () => {
    // SLASH3 adds the 8 Sandbox commands and WP-26 `/repo` (`slash3-sandbox-commands.test.tsx`).
    expect(BUILTIN_COMMANDS.commands).toHaveLength(34);
    expect(SHIPPED_BUILTINS.filter((c) => c.group !== 'sandbox').map((c) => c.name).sort()).toEqual(
      ['agents', 'compact', 'copy', 'diff', 'effort', 'export', 'feedback', 'fork', 'help', 'init', 'knowledge', 'mcp', 'memory', 'model',
        'new', 'permissions', 'plan', 'rename', 'resume', 'review', 'skills', 'status', 'stop', 'undo', 'usage'].sort(),
    );
  });

  it('has a description in English for every command', () => {
    for (const c of BUILTIN_COMMANDS.commands) expect(typeof lookup(`${c.i18n}.description`)).toBe('string');
  });
});

describe('the menu', () => {
  it('groups Built-in, Your commands, Team, Organization; PipesHub\'s prompt commands sit under Built-in', () => {
    const entries = menu('/', [user('weekly', 'team'), user('triage', 'you'), user('summarize', 'pipeshub'), user('policy', 'org')]);
    const sandbox = SHIPPED_BUILTINS.filter((c) => c.group === 'sandbox').length;
    expect(entries.map((e) => e.group)).toEqual([
      ...Array(BUILTIN_FIRST_PAGE + 2).fill('builtin'),
      ...Array(sandbox).fill('sandbox'),
      'you',
      'team',
      'org',
    ]);
    expect(entries.slice(0, BUILTIN_FIRST_PAGE).map((e) => (e.kind === 'builtin' ? e.command.name : ''))).toEqual(
      SHIPPED_BUILTINS.slice(0, BUILTIN_FIRST_PAGE).map((c) => c.name),
    );
    expect(entries[BUILTIN_FIRST_PAGE]).toMatchObject({ kind: 'more', count: SHIPPED_BUILTINS.length - sandbox - BUILTIN_FIRST_PAGE });
    expect(entries[BUILTIN_FIRST_PAGE + 1]).toMatchObject({ kind: 'user', command: { name: 'summarize' } });
  });

  it('lists every built-in after "More built-ins", and always while filtering', () => {
    expect(menu('/', [], ALL, true).filter((e) => e.kind === 'builtin')).toHaveLength(SHIPPED_BUILTINS.length);
    expect(menu('/s').map((e) => e.id)).toEqual(
      expect.arrayContaining(['builtin-stop', 'builtin-status', 'builtin-skills', 'builtin-usage']),
    );
  });

  it('finds a command by its alias and says which alias matched', () => {
    expect(menu('/clea')[0]).toMatchObject({ kind: 'builtin', command: { name: 'new' }, alias: 'clear' });
    expect(menu('/cos')[0]).toMatchObject({ command: { name: 'usage' }, alias: 'cost' });
  });

  it('O2: a user command under a built-in\'s name is hidden; the built-in runs', () => {
    const entries = menu('/stat', [user('status', 'you'), user('statistics', 'you')]);
    expect(entries.map((e) => e.id)).toEqual(['builtin-status', 'user-statistics']);
    const typed = composerCommandState('/status now', [user('status', 'you')], {});
    expect(typed).toMatchObject({ phase: 'builtin', command: { name: 'status' }, args: 'now' });
  });

  it('a terminal-only word is not listed; typed, it is text with a hint', () => {
    expect(composerCommandState('/login', [], {})).toEqual({ phase: 'word', word: 'login' });
    expect(composerCommandState('/exit now', [], {})).toEqual({ phase: 'word', word: 'exit' });
    expect(composerCommandState('/nothing-here', [], {})).toEqual({ phase: 'none' });
  });

  it('greys out with the reason what does not apply here (O8)', () => {
    const idle = { ...ALL, answer: false, harness: false, forkable: false, reasoningModel: false, planStrategy: false, mcp: false, knowledge: false };
    const reasons = Object.fromEntries(
      menu('/', [], idle, true).flatMap((e) => (e.kind === 'builtin' ? [[e.command.name, e.reason]] : [])),
    );
    expect(reasons).toMatchObject({
      stop: 'notRunning',
      copy: 'noAnswer',
      feedback: 'noAnswer',
      usage: 'legacy',
      permissions: 'legacy',
      fork: 'noFork',
      effort: 'noEffort',
      plan: 'agentOnly',
      mcp: 'off',
      knowledge: 'fixed',
      new: null,
      help: null,
      model: null,
      status: null,
    });
    expect(builtinReason(get('rename'), { ...ALL, conversation: false })).toBe('noConversation');
  });
});

describe('O7: while a reply runs', () => {
  const running = { ...ALL, running: true };
  it('/stop /status /usage /copy /help and the links act at once', () => {
    for (const name of ['stop', 'status', 'usage', 'copy', 'help', 'new', 'resume', 'skills', 'mcp', 'agents', 'memory', 'permissions']) {
      expect([name, builtinReason(get(name), running)]).toEqual([name, null]);
    }
  });
  it('/model /effort /plan apply to the next message', () => {
    for (const name of ['model', 'effort', 'plan', 'knowledge']) {
      expect(get(name).duringRun).toBe('next');
      expect(builtinReason(get(name), running)).toBeNull();
    }
  });
  it('the rest wait for the run to end', () => {
    for (const name of ['fork', 'rename', 'export', 'feedback']) expect([name, builtinReason(get(name), running)]).toEqual([name, 'wait']);
  });
});

describe('each command runs what its button runs', () => {
  const t = ((key: string, opts?: Record<string, unknown>) => (opts ? `${key} ${JSON.stringify(opts)}` : key)) as never;
  const models = [
    { modelKey: 'k1', modelName: 'gpt-5.2', modelFriendlyName: 'GPT-5.2', provider: 'openAI', isReasoning: true },
    { modelKey: 'k2', modelName: 'claude-sonnet-4-7', modelFriendlyName: 'Claude Sonnet 4.7', provider: 'anthropic', isReasoning: true },
  ] as never[];
  const run = { sessionId: 'conv-1', runId: 'run-1' };
  let deps: BuiltinDeps;
  const rows = () => useBuiltinResults.getState().rows['slot-1'] ?? [];

  beforeEach(() => {
    useBuiltinResults.setState({ rows: {} });
    useCommandStore.setState({ handlers: {}, pending: {} });
    Object.values(api).forEach((f) => f.mockReset());
    deps = {
      t,
      rowKey: 'slot-1',
      afterPairs: 2,
      slot: { convId: 'conv-1', threadAgentId: null, durableRun: run, lastDurableRun: null, messages: [] } as never,
      agentId: null,
      running: false,
      harness: true,
      isAdmin: false,
      models,
      currentModel: { modelKey: 'k2', modelName: 'claude-sonnet-4-7', modelFriendlyName: 'Claude Sonnet 4.7' },
      selectModel: vi.fn(),
      setEffort: vi.fn(),
      setStrategy: vi.fn(),
      modeLabel: 'Agent',
      openModelPanel: vi.fn(),
      openKnowledgePanel: vi.fn(),
      knowledgeSelection: [],
      stop: vi.fn(),
      navigate: vi.fn(),
    };
  });
  afterEach(() => vi.restoreAllMocks());

  it('/model name picks from exactly the picker\'s list, for the next message', async () => {
    await runBuiltin(get('model'), 'gpt-5.2', { ...deps, running: true });
    expect(deps.selectModel).toHaveBeenCalledWith({ modelKey: 'k1', modelName: 'gpt-5.2', modelFriendlyName: 'GPT-5.2', modelProvider: 'openAI' });
    expect(rows()[0]).toMatchObject({ typed: '/model gpt-5.2', afterPairs: 2, result: { kind: 'model', model: 'GPT-5.2', was: 'Claude Sonnet 4.7', running: true } });
    expect(findModel(models, 'nope')).toBeNull();
    await runBuiltin(get('model'), 'nope', deps);
    expect(deps.openModelPanel).toHaveBeenCalled();
    await runBuiltin(get('model'), '', deps);
    expect(deps.openModelPanel).toHaveBeenCalledTimes(2);
  });

  it('/effort and /plan set the chip the next message is sent with', async () => {
    await runBuiltin(get('effort'), 'high', deps);
    expect(deps.setEffort).toHaveBeenCalledWith('high');
    await runBuiltin(get('effort'), 'extreme', deps);
    expect(deps.setEffort).toHaveBeenCalledTimes(1);
    await runBuiltin(get('plan'), '', deps);
    expect(deps.setStrategy).toHaveBeenCalledWith('plan-execute');
  });

  it('/stop is the Stop button', async () => {
    await runBuiltin(get('stop'), '', deps);
    expect(deps.stop).toHaveBeenCalledTimes(1);
  });

  it('/rename and /export call the sidebar\'s own APIs', async () => {
    api.renameConversation.mockResolvedValue(undefined);
    await runBuiltin(get('rename'), 'Travel policy', deps);
    expect(api.renameConversation).toHaveBeenCalledWith('conv-1', 'Travel policy');
    api.exportConversation.mockResolvedValue(undefined);
    await runBuiltin(get('export'), '', deps);
    expect(api.exportConversation).toHaveBeenCalledWith(expect.objectContaining({ conversationId: 'conv-1', agentId: null }));
  });

  it('/copy, /feedback, /fork, /new and /resume use the handlers on screen, and only when mounted', async () => {
    const handlers = { copy: vi.fn(), feedback: vi.fn(), fork: vi.fn(), newChat: vi.fn(), search: vi.fn() };
    await runBuiltin(get('copy'), '', deps);
    expect(useCommandStore.getState().pending).toEqual({});
    const { register } = useCommandStore.getState();
    register(BUILTIN_BUS.copyLastAnswer, handlers.copy);
    register(BUILTIN_BUS.feedbackLastAnswer, handlers.feedback);
    register(BUILTIN_BUS.continuePrivately, handlers.fork);
    register(BUILTIN_BUS.newChat, handlers.newChat);
    register(BUILTIN_BUS.openSearch, handlers.search);
    await runBuiltin(get('copy'), '', deps);
    await runBuiltin(get('feedback'), 'good', deps);
    await runBuiltin(get('fork'), '', deps);
    await runBuiltin(get('resume'), 'travel', deps);
    await runBuiltin(get('help'), '', deps);
    await runBuiltin(get('new'), '', deps);
    expect(handlers.copy).toHaveBeenCalled();
    expect(handlers.feedback).toHaveBeenCalledWith('like');
    expect(handlers.fork).toHaveBeenCalled();
    expect(handlers.search).toHaveBeenCalledWith('travel');
    expect(handlers.newChat).toHaveBeenCalled();
    expect(rows()).toEqual([]);
  });

  it('/usage reads the conversation total and the last reply\'s budget', async () => {
    api.getConversationUsage.mockResolvedValue({ runs: 9, committed: { usdMicros: 840_000, tokens: 312_000 } });
    api.getRunTimeline.mockResolvedValue({ status: 'completed', budget: { cap: { usdMicros: 5_000_000, tokens: null }, committed: { usdMicros: 110_000, tokens: 41_000 }, reserved: { usdMicros: 0, tokens: 0 }, available: { usdMicros: 4_890_000, tokens: null }, exhausted: false } });
    await runBuiltin(get('usage'), '', deps);
    expect(api.getConversationUsage).toHaveBeenCalledWith({ sessionId: 'conv-1' });
    expect(rows()[0].result).toMatchObject({ kind: 'usage', run, usage: { chat: { runs: 9, tokens: 312_000 }, last: { committed: { tokens: 41_000 } } } });
  });

  it('/status reads the run\'s pin, its digest as the settings version (C4.04); a classic chat shows its model and mode only', async () => {
    api.getRunResolved.mockResolvedValue({
      resolved: {
        digest: 'sha256:0f3a9c4e1b2d7a6e5f40c1d2e3f4a5b6c7d8e9f00112233445566778899aabbcc',
        llm: { model: 'gpt-5.2' },
        effectiveSet: { items: [{ state: 'ready', tools: [{}, {}] }, { state: 'unavailable', tools: [{}] }] },
        effectiveConfig: { dimensions: { 'engineProfile.reasoningEffort': { value: 'high' }, 'engineProfile.engine': { value: 'codex' }, 'engineProfile.approvals.write': { value: 'ask' } } },
      },
    });
    api.getRunTimeline.mockResolvedValue({ status: 'completed', budget: null });
    await runBuiltin(get('status'), '', deps);
    expect(rows()[0].result).toMatchObject({ kind: 'status', status: { model: 'gpt-5.2', effort: 'high', executor: 'codex', tools: 2, approvals: 'write ask', runStatus: 'completed', policyVersion: '0f3a9c4e1b2d' } });
    await runBuiltin(get('status'), '', { ...deps, harness: false });
    expect(rows()[1].result).toMatchObject({ kind: 'status', run: null, status: { legacy: true, model: 'Claude Sonnet 4.7', mode: 'Agent', policyVersion: null } });
  });

  it('QAFIX-7: /status on a loop run without a pin says what the run did, never a bare dash', async () => {
    api.getRunResolved.mockRejectedValue(new Error('404'));
    api.getRunTimeline.mockResolvedValue({
      status: 'completed', engine: null, approvals: [{ interactionId: 'ix-1', status: 'approved' }], budget: null,
      model: { provider: 'openai', model: 'e2e-llm' }, executor: 'loop', reasoningEffort: null, toolsGranted: 4,
    });
    await runBuiltin(get('status'), '', deps);
    expect(rows()[0].result).toMatchObject({
      kind: 'status',
      status: { model: 'e2e-llm', effort: 'default', executor: 'loop', tools: 4, approvals: null, approvalsAsked: 1, runStatus: 'completed' },
    });
  });

  it('QAFIX-7: a loop run\'s pin keeps its approval rules under `approvals`', async () => {
    api.getRunResolved.mockResolvedValue({
      resolved: {
        digest: 'sha256:aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66',
        llm: { model: 'e2e-llm' },
        effectiveSet: { items: [] },
        effectiveConfig: { dimensions: { approvals: { value: { classes: { read: 'allow', workspace: 'allow', write: 'ask', execute: 'ask' } } } } },
      },
    });
    api.getRunTimeline.mockResolvedValue({ status: 'completed', budget: null, executor: 'loop', reasoningEffort: 'low', toolsGranted: 0 });
    await runBuiltin(get('status'), '', deps);
    expect(rows()[0].result).toMatchObject({
      kind: 'status',
      status: { effort: 'low', executor: 'loop', tools: 0, approvals: 'read allow · workspace allow · write ask · execute ask' },
    });
  });

  it('/knowledge opens the Collections selector and says what the next message searches', async () => {
    await runBuiltin(get('knowledge'), '', { ...deps, knowledgeSelection: ['Handbook', 'Jira'] });
    expect(deps.openKnowledgePanel).toHaveBeenCalledTimes(1);
    expect(rows()[0].result).toEqual({ kind: 'note', text: 'slashCommands.result.knowledgeNext {"names":"Handbook, Jira"}' });
    await runBuiltin(get('knowledge'), '', deps);
    expect(rows()[1].result).toEqual({ kind: 'note', text: 'slashCommands.result.knowledgeAll' });
  });

  it('/memory opens the memories page', async () => {
    await runBuiltin(get('memory'), '', deps);
    expect(deps.navigate).toHaveBeenCalledWith('/workspace/memories');
  });
});
