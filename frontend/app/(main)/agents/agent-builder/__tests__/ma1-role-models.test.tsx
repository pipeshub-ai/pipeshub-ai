/**
 * MA1 QA37-X1, QA37-X2: a model per sub-agent role in the agent builder. The
 * author adds "All sub-agents" and named roles in the agent node and picks a
 * model the workspace allows for each; the save carries exactly that map.
 */
import React from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useToastStore } from '@/lib/store/toast-store';
import type { AgentFormPayload } from '../types';
import type { RoleModels } from '../../types';
import { choicesForRole, normalizeRoleKey, roleCostCeiling, roleModelsPayload } from '../role-models';
import { agentDetail, installBrowserShims, knowledgeBase, model, publishEdits, renderInTheme, toolset } from './agent-builder-harness';

vi.setConfig({ testTimeout: 20_000 });

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/agents/edit',
}));
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  useUserPermission: () => true,
  PermissionLockIcon: () => null,
  InheritedConfigNotice: () => null,
}));
vi.mock('@/lib/api/axios-instance', () => {
  const refuse = () => Promise.reject(new Error('Unexpected network call in a unit test'));
  const apiClient = { get: refuse, post: refuse, put: refuse, patch: refuse, delete: refuse };
  return { apiClient, default: apiClient };
});

const agentsApi = vi.hoisted(() => ({
  getAgent: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  knowledgeBasePage: vi.fn(),
  knowledgeHubAppPage: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api')>()),
  AgentsApi: agentsApi,
}));
const fetchAvailableLlms = vi.hoisted(() => vi.fn());
// The org model list now comes from the catalog route (main #4365); it answers with the same models.
vi.mock('@/chat/api', () => ({
  ChatApi: {
    fetchAvailableLlms,
    fetchOrgLlmCatalog: async () => {
      const models = await fetchAvailableLlms();
      return { models, response: { models } };
    },
  },
}));
vi.mock('@/app/(main)/toolsets/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/toolsets/api')>()),
  ToolsetsApi: {
    getMyToolsets: vi.fn(async () => ({ toolsets: [toolset()] })),
    getAgentToolsets: vi.fn(async () => ({ toolsets: [] })),
  },
}));
vi.mock('@/app/(main)/workspace/skills/personal/api', () => ({
  SkillsApi: { listAssignableSkills: vi.fn(async () => []) },
}));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({
  McpServersApi: {
    getMyMcpServers: vi.fn(async () => ({ instances: [] })),
    getAgentMcpServers: vi.fn(async () => ({ instances: [] })),
  },
}));
vi.mock('@/app/(main)/workspace/web-search/api', () => ({
  WebSearchApi: { getConfig: vi.fn(async () => ({ providers: [] })) },
}));
vi.mock('@/app/(main)/workspace/users/api', () => ({
  UsersApi: { getUsersByIds: vi.fn(async () => [{ name: 'Priya Owner', email: 'priya@example.com' }]) },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const modelAccess = vi.hoisted(() => ({ access: vi.fn() }));
vi.mock('@/app/(main)/workspace/ai-accounts/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/workspace/ai-accounts/api')>()),
  ModelAccountsApi: modelAccess,
}));

import { AgentBuilder } from '../agent-builder';
import { toAccessView } from '@/app/(main)/workspace/ai-accounts/api';

const GPT = { modelKey: 'model-key-1', modelName: 'gpt-4o', provider: 'openAI' };
const HAIKU = { modelKey: 'model-key-2', modelName: 'claude-haiku-4-5', provider: 'anthropic' };
const OPUS = { modelKey: 'model-key-3', modelName: 'claude-opus-4-5', provider: 'anthropic' };
const LOCAL = { modelKey: 'model-key-4', modelName: 'llama-local', provider: 'ollama' };

const CONFIGURED = [
  model(),
  model({ ...HAIKU, modelFriendlyName: 'Claude Haiku 4.5', isDefault: false }),
  model({ ...OPUS, modelFriendlyName: 'Claude Opus 4.5', isDefault: false }),
  model({ ...LOCAL, modelFriendlyName: 'Local Llama', isDefault: false }),
];

const choice = (ref: typeof GPT, usdPerMTok: number | null, allowed = true) => ({ ...ref, usdPerMTok, allowed });

/** The org's model-access view; without `extra` it is an older server's, which names no models. */
function accessView(extra: Record<string, unknown> = {}) {
  return { version: 'v', offering: 'managed', policy: { disabledRules: [], consumerPlans: false }, rules: [], ...extra };
}

const ALL_ALLOWED = [choice(GPT, 12.5), choice(HAIKU, 6), choice(OPUS, 90), choice(LOCAL, null)];

function lastSavedPayload(fn: ReturnType<typeof vi.fn>): AgentFormPayload {
  const call = fn.mock.calls[fn.mock.calls.length - 1];
  if (!call) throw new Error('Nothing was saved');
  // updateAgent(key, payload, options) or createAgent(payload).
  return (typeof call[0] === 'string' ? call[1] : call[0]) as AgentFormPayload;
}

const nameField = () => screen.getByPlaceholderText('e.g. Support bot') as HTMLInputElement;

async function renderNewAgent() {
  renderInTheme(<AgentBuilder agentKey={null} />);
  await screen.findByRole('group', { name: 'Sub-agent models' });
  fireEvent.change(nameField(), { target: { value: 'Sales helper' } });
}

async function renderExistingAgent(roleModels?: RoleModels) {
  const agent = agentDetail(roleModels ? { roleModels } : {});
  agentsApi.getAgent.mockResolvedValue({ agent });
  renderInTheme(<AgentBuilder agentKey={agent._key} />);
  await waitFor(() => expect(nameField()).toHaveProperty('value', agent.name));
  return agent;
}

const section = () => within(screen.getByRole('group', { name: 'Sub-agent models' }));
const rows = () => section().queryAllByTestId('sub-agent-model-row');

async function modelSelect(role: string) {
  const trigger = await section().findByRole('combobox', { name: `Model for ${role}` });
  await waitFor(() => expect(trigger).toHaveProperty('disabled', false));
  return trigger;
}

async function openSelect(role: string) {
  const trigger = await modelSelect(role);
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'Enter' });
  await screen.findByRole('listbox');
  return trigger;
}

async function chooseModel(role: string, option: string | RegExp) {
  await openSelect(role);
  fireEvent.click(screen.getByRole('option', { name: option }));
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
}

async function offeredModels(role: string): Promise<string[]> {
  await openSelect(role);
  const names = screen.getAllByRole('option').map((option) => option.textContent ?? '');
  fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  return names;
}

function addRole(name: string) {
  fireEvent.click(section().getByRole('button', { name: 'Add a role' }));
  const inputs = section().getAllByRole('textbox', { name: 'Role name' });
  fireEvent.change(inputs[inputs.length - 1]!, { target: { value: name } });
}

/** "Publish" is offered only once something changed; a rename is the smallest change. */
const rename = () => fireEvent.change(nameField(), { target: { value: 'Sales helper v2' } });

async function save(fn: ReturnType<typeof vi.fn>, button: RegExp | 'publish') {
  if (button === 'publish') await publishEdits();
  else fireEvent.click(screen.getByRole('button', { name: button }));
  await waitFor(() => expect(fn).toHaveBeenCalledTimes(1));
  return lastSavedPayload(fn);
}

beforeEach(() => {
  installBrowserShims();
  // The agent harness on for the org (ADR-OPS-08): agents have versions there. No new turn is admitted (kill switch),
  // which does not change what the builder offers.
  useFeatureFlagsStore.setState({
    flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false, ENABLE_SKILLS: false },
    agentHarness: { useHarness: false, reason: 'kill_switch', harnessMajor: 2, resumeInFlightRuns: true, harnessOn: true },
  } as never);
  useToastStore.setState({ toasts: [] });
  agentsApi.knowledgeBasePage.mockResolvedValue({ items: [knowledgeBase()], next: null });
  agentsApi.knowledgeHubAppPage.mockResolvedValue({ items: [], next: null });
  agentsApi.createAgent.mockImplementation(async (payload: AgentFormPayload) =>
    agentDetail({ _key: 'new-agent', id: 'new-agent', name: payload.name }),
  );
  agentsApi.updateAgent.mockImplementation(async (key: string, payload: AgentFormPayload) =>
    agentDetail({ _key: key, id: key, name: payload.name, ...(payload.roleModels ? { roleModels: payload.roleModels } : {}) }),
  );
  fetchAvailableLlms.mockResolvedValue(CONFIGURED);
  modelAccess.access.mockResolvedValue(accessView({ modelChoices: ALL_ALLOWED }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('QA37-X1: a model per sub-agent role', () => {
  it('QA37-X1: "All sub-agents" and a named role, each with a model, are saved as exactly that map', async () => {
    await renderNewAgent();
    expect(rows()).toHaveLength(0);

    fireEvent.click(section().getByRole('button', { name: 'Set a model for all sub-agents' }));
    expect(within(rows()[0]!).getByText('All sub-agents')).toBeTruthy();
    await chooseModel('All sub-agents', /^Claude Haiku 4\.5/);
    addRole('Researcher One');
    expect(section().getByText('Saved as researcher-one')).toBeTruthy();
    await chooseModel('Researcher One', /^GPT-4o/);

    const payload = await save(agentsApi.createAgent, /create agent/i);
    expect(payload.roleModels).toEqual({ default: HAIKU, 'researcher-one': GPT });
    expect(payload.models).toEqual([]);
  });

  it('QA37-X1: a role left on "Same as the agent" is not saved', async () => {
    await renderNewAgent();
    fireEvent.click(section().getByRole('button', { name: 'Set a model for all sub-agents' }));
    addRole('writer');
    await chooseModel('writer', /^Claude Opus 4\.5/);
    expect((await modelSelect('All sub-agents')).textContent).toBe('Same as the agent');

    const payload = await save(agentsApi.createAgent, /create agent/i);
    expect(payload.roleModels).toEqual({ writer: OPUS });
  });

  it('QA37-X1: reopening the agent shows its stored roles and models, "All sub-agents" first', async () => {
    await renderExistingAgent({ 'researcher-one': GPT, default: HAIKU });

    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(within(rows()[0]!).getByText('All sub-agents')).toBeTruthy();
    expect((await modelSelect('All sub-agents')).textContent).toContain('Claude Haiku 4.5');
    expect(section().getByRole('textbox', { name: 'Role name' })).toHaveProperty('value', 'researcher-one');
    expect((await modelSelect('researcher-one')).textContent).toContain('GPT-4o');
    expect(section().queryByRole('alert')).toBeNull();

    rename();
    const payload = await save(agentsApi.updateAgent, 'publish');
    expect(payload.roleModels).toEqual({ default: HAIKU, 'researcher-one': GPT });
  });

  it('QA37-X1: removing every row of an agent that had some sends an empty map, so the server clears it', async () => {
    await renderExistingAgent({ default: HAIKU, writer: OPUS });
    await waitFor(() => expect(rows()).toHaveLength(2));

    fireEvent.click(section().getByRole('button', { name: 'Remove writer' }));
    fireEvent.click(section().getByRole('button', { name: 'Remove All sub-agents' }));
    expect(rows()).toHaveLength(0);

    const payload = await save(agentsApi.updateAgent, 'publish');
    expect(payload).toHaveProperty('roleModels', {});
  });

  it('QA37-X1: an agent without role models is saved as before, and the access view is not read for it', async () => {
    await renderExistingAgent();
    rename();
    const without = await save(agentsApi.updateAgent, 'publish');

    expect(Object.keys(without)).toEqual([
      'name',
      'description',
      'startMessage',
      'systemPrompt',
      'instructions',
      'defaultReasoningEffort',
      'sendUserContext',
      'toolsets',
      'knowledge',
      'skills',
      'mcpServers',
      'models',
      'webSearch',
      'tags',
      'shareWithOrg',
      'isServiceAccount',
    ]);
    expect(modelAccess.access).not.toHaveBeenCalled();

    cleanup();
    agentsApi.updateAgent.mockClear();
    await renderExistingAgent({ default: HAIKU });
    await waitFor(() => expect(rows()).toHaveLength(1));
    rename();
    const { roleModels, ...rest } = await save(agentsApi.updateAgent, 'publish');
    expect(roleModels).toEqual({ default: HAIKU });
    expect(JSON.stringify(rest)).toBe(JSON.stringify(without));
  });

  it('QA37-X1: a second row of the same role is flagged and only the first is saved', async () => {
    await renderNewAgent();
    addRole('Writer');
    await chooseModel('Writer', /^Claude Haiku 4\.5/);
    addRole('writer');
    expect(section().getByText('This role is already in the list. Only the first one is saved.')).toBeTruthy();

    const payload = await save(agentsApi.createAgent, /create agent/i);
    expect(payload.roleModels).toEqual({ writer: HAIKU });
  });

  it('QA37-X1: the payload rule for an agent node that is missing keeps what the agent stored', () => {
    expect(roleModelsPayload(undefined, undefined)).toEqual({});
    expect(roleModelsPayload({}, {})).toEqual({});
    expect(roleModelsPayload({ default: HAIKU }, undefined)).toEqual({ roleModels: { default: HAIKU } });
    expect(roleModelsPayload({}, { default: HAIKU })).toEqual({ roleModels: {} });
    expect(roleModelsPayload({ writer: { modelKey: 'k' } }, undefined)).toEqual({});
  });
});

describe('QA37-X2: only allowed choices', () => {
  it('QA37-X2: a model the workspace does not allow is not offered; each allowed one shows its name and price', async () => {
    modelAccess.access.mockResolvedValue(
      accessView({ modelChoices: [choice(GPT, 12.5), choice(HAIKU, 6), choice(OPUS, 90, false), choice(LOCAL, null)] }),
    );
    await renderNewAgent();
    fireEvent.click(section().getByRole('button', { name: 'Set a model for all sub-agents' }));

    expect(await offeredModels('All sub-agents')).toEqual([
      'Same as the agent',
      'GPT-4o · $12.50 per 1M tokens',
      'Claude Haiku 4.5 · $6.00 per 1M tokens',
      'Local Llama',
    ]);
    expect(section().queryByText(/limited to/)).toBeNull();
  });

  it('QA37-X2: a ceiling hides dearer and unpriced models for the role it bounds, not for another role', async () => {
    modelAccess.access.mockResolvedValue(
      accessView({ modelChoices: ALL_ALLOWED, roleCostCeilings: { researcher: 12.5 } }),
    );
    await renderNewAgent();
    addRole('Researcher');
    addRole('writer');

    expect(await offeredModels('Researcher')).toEqual([
      'Same as the agent',
      'GPT-4o · $12.50 per 1M tokens',
      'Claude Haiku 4.5 · $6.00 per 1M tokens',
    ]);
    expect(await offeredModels('writer')).toHaveLength(5);
    expect(section().getByText('Researcher is limited to $12.50 per 1M tokens by your workspace.')).toBeTruthy();
    expect(section().queryByText(/^Sub-agent models are limited/)).toBeNull();
  });

  it('QA37-X2: the `default` ceiling bounds every role and is said once; a role takes the least of its own and it', async () => {
    modelAccess.access.mockResolvedValue(
      accessView({ modelChoices: ALL_ALLOWED, roleCostCeilings: { default: 12.5, researcher: 6, writer: 500 } }),
    );
    await renderNewAgent();
    fireEvent.click(section().getByRole('button', { name: 'Set a model for all sub-agents' }));
    addRole('researcher');
    addRole('writer');

    expect(await offeredModels('All sub-agents')).toHaveLength(3);
    expect(await offeredModels('researcher')).toEqual(['Same as the agent', 'Claude Haiku 4.5 · $6.00 per 1M tokens']);
    expect(await offeredModels('writer')).toHaveLength(3);
    expect(section().getAllByText('Sub-agent models are limited to $12.50 per 1M tokens by your workspace.')).toHaveLength(1);
    expect(section().getByText('researcher is limited to $6.00 per 1M tokens by your workspace.')).toBeTruthy();
    expect(section().queryByText(/^writer is limited/)).toBeNull();
  });

  it('QA37-X2: a stored choice that is no longer allowed is shown with a warning and kept until the author changes it', async () => {
    modelAccess.access.mockResolvedValue(
      accessView({ modelChoices: [choice(GPT, 12.5), choice(HAIKU, 6), choice(OPUS, 90, false)] }),
    );
    await renderExistingAgent({ default: OPUS, writer: HAIKU });
    await waitFor(() => expect(rows()).toHaveLength(2));

    const warning = await section().findByRole('alert');
    expect(warning.textContent).toBe(
      'No longer allowed by your workspace: sub-agents with this role will not start until you choose another model.',
    );
    expect(within(rows()[0]!).getByRole('alert')).toBe(warning);
    expect((await modelSelect('All sub-agents')).textContent).toBe('Claude Opus 4.5 (no longer allowed)');
    const options = await offeredModels('All sub-agents');
    expect(options).toContain('Claude Opus 4.5 (no longer allowed)');

    rename();
    const kept = await save(agentsApi.updateAgent, 'publish');
    expect(kept.roleModels).toEqual({ default: OPUS, writer: HAIKU });
    fireEvent.click(await screen.findByRole('button', { name: 'Continue editing' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    await chooseModel('All sub-agents', /^GPT-4o/);
    expect(section().queryByRole('alert')).toBeNull();
    expect(await offeredModels('All sub-agents')).not.toContain('Claude Opus 4.5 (no longer allowed)');
  });

  it('QA37-X2: a stored choice above a ceiling set since is warned about the same way', async () => {
    modelAccess.access.mockResolvedValue(accessView({ modelChoices: ALL_ALLOWED, roleCostCeilings: { default: 10 } }));
    await renderExistingAgent({ writer: OPUS });

    expect((await section().findByRole('alert')).textContent).toMatch(/^No longer allowed by your workspace/);
  });

  it('QA37-X2: an older server (no model list in the view) falls back to the configured models, all offered', async () => {
    modelAccess.access.mockResolvedValue(accessView());
    await renderExistingAgent({ default: OPUS });

    expect(await offeredModels('All sub-agents')).toEqual([
      'Same as the agent',
      'GPT-4o',
      'Claude Haiku 4.5',
      'Claude Opus 4.5',
      'Local Llama',
    ]);
    expect(section().queryByRole('alert')).toBeNull();
  });

  it('QA37-X2: the view failing to load falls back the same way', async () => {
    modelAccess.access.mockRejectedValue({ kind: 'unavailable', status: 503, message: null });
    await renderNewAgent();
    fireEvent.click(section().getByRole('button', { name: 'Set a model for all sub-agents' }));

    expect(await offeredModels('All sub-agents')).toHaveLength(5);
    await chooseModel('All sub-agents', 'Local Llama');
    expect((await save(agentsApi.createAgent, /create agent/i)).roleModels).toEqual({ default: LOCAL });
  });

  it('QA37-X2: the ceiling of a role is the least of its own and `default`; at the ceiling is allowed, unpriced is not', () => {
    expect(roleCostCeiling({}, 'writer')).toBeNull();
    expect(roleCostCeiling({ default: 10 }, 'writer')).toBe(10);
    expect(roleCostCeiling({ writer: 4 }, 'writer')).toBe(4);
    expect(roleCostCeiling({ default: 10, writer: 40 }, 'writer')).toBe(10);
    expect(roleCostCeiling({ default: 10, writer: 4 }, 'writer')).toBe(4);
    expect(roleCostCeiling({ default: 0 }, 'writer')).toBe(0);

    const catalog = {
      choices: [
        { ...GPT, label: 'a', usdPerMTok: 10, allowed: true },
        { ...HAIKU, label: 'b', usdPerMTok: 10.01, allowed: true },
        { ...LOCAL, label: 'c', usdPerMTok: null, allowed: true },
        { ...OPUS, label: 'd', usdPerMTok: 1, allowed: false },
      ],
      ceilings: { writer: 10 },
    };
    expect(choicesForRole(catalog, 'writer').map((c) => c.label)).toEqual(['a']);
    expect(choicesForRole(catalog, 'other').map((c) => c.label)).toEqual(['a', 'b', 'c']);
  });

  it('QA37-X2: the access view keeps the model list and ceilings field by field, and stays without them when absent', () => {
    const view = toAccessView(
      accessView({
        modelChoices: [
          { ...GPT, usdPerMTok: 12.5, allowed: true, apiKey: 'sk-secret' },
          { ...HAIKU, usdPerMTok: 'six', allowed: 'yes' },
          { modelKey: 'k' },
        ],
        roleCostCeilings: { default: 10, writer: 'cheap', reader: -1 },
      }),
    );
    expect(view.modelChoices).toEqual([
      { ...GPT, usdPerMTok: 12.5, allowed: true },
      { ...HAIKU, usdPerMTok: null, allowed: false },
    ]);
    expect(view.roleCostCeilings).toEqual({ default: 10 });
    expect(Object.keys(toAccessView(accessView()))).toEqual(['version', 'offering', 'policy', 'rules']);
  });
});

describe('QA37-X1: the role key a name is saved under', () => {
  const vector = JSON.parse(
    readFileSync(
      join(__dirname, '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'child-model.json'),
      'utf8',
    ),
  ) as { roleKeys: Array<{ subAgent: { role: string; domainAgent?: string } | null; keys: string[] }> };
  const roleOnly = vector.roleKeys.filter((row) => row.subAgent !== null && row.subAgent.domainAgent === undefined);

  it('QA37-X1: the shared vector has role-only rows to hold the normaliser to', () => {
    expect(roleOnly.length).toBeGreaterThanOrEqual(8);
  });

  it.each(roleOnly.map((row) => [row.subAgent!.role, row.keys] as const))(
    'QA37-X1: role %j is looked up under %j',
    (role, keys) => {
      const key = normalizeRoleKey(role);
      expect([...new Set([key, 'default'])].filter(Boolean)).toEqual(keys);
    },
  );
});

describe('QAFINAL-FU-PW113-2: an agent that starts no sub-agents', () => {
  it('PW113-2-UI-1: its sub-agent models say they are not used and a Deep turn is answered by the agent', async () => {
    const agent = agentDetail({ maxChildren: 0 });
    agentsApi.getAgent.mockResolvedValue({ agent });
    renderInTheme(<AgentBuilder agentKey={agent._key} />);
    await waitFor(() => expect(nameField()).toHaveProperty('value', agent.name));
    expect((await section().findByTestId('sub-agent-models-none')).textContent).toContain(
      'This agent starts no sub-agents',
    );
  });

  it('PW113-2-UI-2: an agent whose mode decides, or that grants some, shows no such note', async () => {
    await renderExistingAgent();
    expect(section().queryByTestId('sub-agent-models-none')).toBeNull();
    cleanup();
    const agent = agentDetail({ maxChildren: 3 });
    agentsApi.getAgent.mockResolvedValue({ agent });
    renderInTheme(<AgentBuilder agentKey={agent._key} />);
    await waitFor(() => expect(nameField()).toHaveProperty('value', agent.name));
    expect(section().queryByTestId('sub-agent-models-none')).toBeNull();
  });
});
