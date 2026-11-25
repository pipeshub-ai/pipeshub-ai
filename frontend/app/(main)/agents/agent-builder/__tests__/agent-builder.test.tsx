import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useToastStore } from '@/lib/store/toast-store';
import type { AgentFormPayload } from '../types';
import type { KnowledgeHubAppNode } from '../../types';
import { clearSessionCache } from '@/lib/api/session-cache';
import {
  OWNER_USER_ID,
  agentDetail,
  apiFailure,
  dragPaletteItemToCanvas,
  installBrowserShims,
  knowledgeBase,
  model,
  publishEdits,
  renderInTheme,
  startDraggingPaletteItem,
  toolset,
} from './agent-builder-harness';

// Each test mounts the whole builder, React Flow canvas included; under
// coverage on a busy machine that can pass Vitest's 5 s default.
vi.setConfig({ testTimeout: 20_000 });

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/agents/edit',
}));

const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/config')>()),
  useUserPermission: (key: string) => !permissions.denied.has(key),
  PermissionLockIcon: () => <span>Locked</span>,
  InheritedConfigNotice: () => null,
  McpAgentApprovalsNotice: () => null,
}));

// Nothing here may reach the network: every API module below is faked, and
// this catches anything that slips through.
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

const fetchOrgModels = vi.hoisted(() => vi.fn());
vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: fetchOrgModels,
  invalidateModelsForContext: vi.fn(),
}));

const toolsetsApi = vi.hoisted(() => ({
  getMyToolsets: vi.fn(),
  getAgentToolsets: vi.fn(),
  getToolsetRegistrySchema: vi.fn(),
  findAgentToolsetByInstanceId: vi.fn(),
  authenticateAgentToolset: vi.fn(),
  updateAgentToolsetCredentials: vi.fn(),
}));
vi.mock('@/app/(main)/toolsets/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/toolsets/api')>()),
  ToolsetsApi: toolsetsApi,
}));

vi.mock('@/app/(main)/workspace/skills/personal/api', () => ({
  SkillsApi: { listAssignableSkills: vi.fn(async () => []) },
}));
const mcpApi = vi.hoisted(() => ({
  getMyMcpServers: vi.fn(async () => ({ instances: [] as unknown[] })),
  getAgentMcpServers: vi.fn(async () => ({ instances: [] as unknown[] })),
}));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: mcpApi }));
vi.mock('@/app/(main)/workspace/web-search/api', () => ({
  // A member only sees the search providers an admin has set up.
  WebSearchApi: {
    getConfig: vi.fn(async () => ({
      providers: [{ providerKey: 'ddg-1', provider: 'duckduckgo', configuration: {}, isDefault: true }],
    })),
  },
}));
const getUsersByIds = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds } }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const modelAccess = vi.hoisted(() => ({ access: vi.fn(), codingEngine: vi.fn() }));
vi.mock('@/app/(main)/workspace/ai-accounts/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/workspace/ai-accounts/api')>()),
  ModelAccountsApi: modelAccess,
}));

import { AgentBuilder } from '../agent-builder';

function lastSavedPayload(fn: ReturnType<typeof vi.fn>): AgentFormPayload {
  const call = fn.mock.calls[fn.mock.calls.length - 1];
  if (!call) throw new Error('Nothing was saved');
  // updateAgent(key, payload, options) or createAgent(payload).
  return (typeof call[0] === 'string' ? call[1] : call[0]) as AgentFormPayload;
}

async function renderNewAgent() {
  renderInTheme(<AgentBuilder agentKey={null} />);
  await screen.findByText('Agent', { selector: 'span' });
}

async function renderExistingAgent(overrides: Parameters<typeof agentDetail>[0] = {}) {
  const agent = agentDetail(overrides);
  agentsApi.getAgent.mockResolvedValue({ agent });
  renderInTheme(<AgentBuilder agentKey={agent._key} />);
  await waitFor(() => expect(nameField()).toHaveProperty('value', agent.name));
  return agent;
}

function nameField() {
  return screen.getByPlaceholderText('e.g. Support bot') as HTMLInputElement;
}

function saveButton(name: RegExp = /create agent|publish$/i) {
  return screen.getByRole('button', { name });
}

function toastMessages() {
  return useToastStore.getState().toasts.map((t) => [t.title, t.description].filter(Boolean).join(' — '));
}

beforeEach(() => {
  installBrowserShims();
  clearSessionCache();
  permissions.denied.clear();
  // The agent harness on for the org (ADR-OPS-08): agents have versions there. No new turn is admitted (kill switch),
  // which does not change what the builder offers.
  useFeatureFlagsStore.setState({
    flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false, ENABLE_SKILLS: false },
    agentHarness: { useHarness: false, reason: 'kill_switch', harnessMajor: 2, resumeInFlightRuns: true, harnessOn: true },
  } as never);
  useToastStore.setState({ toasts: [] });
  agentsApi.knowledgeBasePage.mockResolvedValue({ items: [knowledgeBase()], next: null });
  agentsApi.knowledgeHubAppPage.mockResolvedValue({ items: [], next: null });
  const savedAgent = (key: string, payload: AgentFormPayload) =>
    agentDetail({
      _key: key,
      id: key,
      name: payload.name,
      instructions: payload.instructions,
      shareWithOrg: payload.shareWithOrg ?? false,
      isServiceAccount: payload.isServiceAccount ?? false,
    });
  agentsApi.createAgent.mockImplementation(async (payload: AgentFormPayload) => savedAgent('new-agent', payload));
  agentsApi.updateAgent.mockImplementation(async (key: string, payload: AgentFormPayload) => savedAgent(key, payload));
  agentsApi.deleteAgent.mockResolvedValue(undefined);
  fetchOrgModels.mockResolvedValue([model()]);
  toolsetsApi.getMyToolsets.mockResolvedValue({ toolsets: [toolset()] });
  toolsetsApi.getAgentToolsets.mockResolvedValue({ toolsets: [] });
  getUsersByIds.mockResolvedValue([{ userId: OWNER_USER_ID, name: 'Priya Owner', email: 'priya@example.com' }]);
  const row = (id: string, accountKind: string) => ({
    id, provider: 'openai', accountKind, route: 'gateway', engines: null, status: 'allowed',
    consumer: false, source: '', built: true, outcome: 'allowed',
  });
  modelAccess.access.mockResolvedValue({
    version: 'v',
    offering: 'managed',
    policy: { disabledRules: [], consumerPlans: false },
    rules: [row('openai-api-key-org', 'org_api_key'), row('openai-api-key-user', 'user_api_key')],
  });
  modelAccess.codingEngine.mockResolvedValue({
    engines: [
      { kind: 'opencode', available: true, reason: null },
      { kind: 'codex', available: true, reason: null },
      { kind: 'claude_code', available: false, reason: 'offering_not_permitted' },
    ],
    orgDefault: null,
    choice: 'org_default',
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function canvas() {
  return within(document.querySelector('.react-flow__nodes') as HTMLElement);
}

/** The canvas node (other than the agent itself) whose card shows this label. */
function canvasNode(label: string): HTMLElement | null {
  const nodes = Array.from(document.querySelectorAll<HTMLElement>('.react-flow__node'));
  return nodes.find((n) => n.getAttribute('data-id') !== 'agent-core-1' && n.textContent?.includes(label)) ?? null;
}

async function openAgentMenu() {
  const trigger = screen.getByRole('button', { name: 'Agent menu' });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  return screen.findByRole('menuitem', { name: /delete agent/i });
}

/** A palette section's header is one button named by its title, saying whether it is open (QAFINAL-FU-AXE). */
function openSection(title: string) {
  const header = screen.getByRole('button', { name: title, expanded: false });
  fireEvent.click(header);
  expect(header.getAttribute('aria-expanded')).toBe('true');
}

async function editAgentPrompts(fields: { instructions?: string; systemPrompt?: string }) {
  fireEvent.click(canvas().getByRole('button', { name: 'Edit prompts' }));
  const dialog = await screen.findByRole('dialog', { name: 'Configure agent prompts' });
  const [systemPrompt, instructions] = within(dialog).getAllByRole('textbox');
  if (fields.systemPrompt !== undefined) fireEvent.change(systemPrompt, { target: { value: fields.systemPrompt } });
  if (fields.instructions !== undefined) fireEvent.change(instructions, { target: { value: fields.instructions } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Configure agent prompts' })).toBeNull());
}

describe('building a new agent', () => {
  it('starts with a chat input, the agent and a chat output, using the organization default model', async () => {
    await renderNewAgent();

    expect(canvasNode('Chat input')).toBeTruthy();
    expect(canvasNode('Chat output')).toBeTruthy();
    expect(canvas().getByText('Using organization default model')).toBeTruthy();
    expect(saveButton(/create agent/i)).toHaveProperty('disabled', false);
  });

  it('asks for a name instead of saving an agent without one', async () => {
    await renderNewAgent();

    fireEvent.click(saveButton(/create agent/i));

    expect(await screen.findByText('Enter a name to continue.')).toBeTruthy();
    expect(nameField().getAttribute('aria-invalid')).toBe('true');
    expect(agentsApi.createAgent).not.toHaveBeenCalled();

    fireEvent.change(nameField(), { target: { value: 'Sales helper' } });
    await waitFor(() => expect(screen.queryByText('Enter a name to continue.')).toBeNull());
  });

  it('saves the name, instructions, model, knowledge, toolset and web search the person put together', async () => {
    await renderNewAgent();

    fireEvent.change(nameField(), { target: { value: '  Sales helper  ' } });
    await editAgentPrompts({ instructions: 'Answer from the sales playbook first.' });
    openSection('AI models');
    dragPaletteItemToCanvas('GPT-4o');
    dragPaletteItemToCanvas('Sales playbook');
    dragPaletteItemToCanvas('Team Jira');
    dragPaletteItemToCanvas('DuckDuckGo');
    await waitFor(() => expect(canvas().queryByText('Using organization default model')).toBeNull());

    fireEvent.click(saveButton(/create agent/i));

    await waitFor(() => expect(agentsApi.createAgent).toHaveBeenCalledTimes(1));
    const payload = lastSavedPayload(agentsApi.createAgent);
    expect(payload).toMatchObject({
      name: 'Sales helper',
      instructions: 'Answer from the sales playbook first.',
      shareWithOrg: false,
      isServiceAccount: false,
      models: [{ provider: 'openAI', modelName: 'gpt-4o', modelKey: 'model-key-1', isReasoning: false }],
      knowledge: [{ connectorId: 'kb-1' }],
      webSearch: { provider: 'duckduckgo' },
    });
    expect(payload.toolsets).toEqual([
      expect.objectContaining({
        instanceId: 'jira-instance-1',
        tools: [expect.objectContaining({ name: 'create_issue', fullName: 'jira.create_issue' })],
      }),
    ]);
    expect(await screen.findByText('Agent created')).toBeTruthy();
    expect(router.replace).toHaveBeenCalledWith('/agents/edit?agentKey=new-agent');
  });

  it("shows the server's reason when the agent cannot be created", async () => {
    agentsApi.createAgent.mockRejectedValue(
      apiFailure(409, { message: 'An agent called Sales helper already exists. Choose another name.' }),
    );
    await renderNewAgent();

    fireEvent.change(nameField(), { target: { value: 'Sales helper' } });
    fireEvent.click(saveButton(/create agent/i));

    expect(await screen.findByText('An agent called Sales helper already exists. Choose another name.')).toBeTruthy();
    expect(router.replace).not.toHaveBeenCalled();
    expect(saveButton(/create agent/i)).toHaveProperty('disabled', false);
  });


  it('falls back to a general message when the failure has no reason a person can use', async () => {
    agentsApi.createAgent.mockRejectedValue(apiFailure(500, { message: "KeyError: 'toolsets'" }));
    await renderNewAgent();

    fireEvent.change(nameField(), { target: { value: 'Sales helper' } });
    fireEvent.click(saveButton(/create agent/i));

    expect(await screen.findByText('Save failed')).toBeTruthy();
    expect(screen.queryByText(/KeyError/)).toBeNull();
  });

  it('refuses a toolset that is not signed in yet and says why', async () => {
    toolsetsApi.getMyToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
    await renderNewAgent();

    startDraggingPaletteItem('Team Jira');

    expect(
      await screen.findByText('Team Jira is not authenticated. Configure it before dragging onto the canvas.'),
    ).toBeTruthy();
  });

  it('removes a node from the canvas only after the person confirms', async () => {
    await renderNewAgent();
    dragPaletteItemToCanvas('Sales playbook');
    await waitFor(() => expect(canvasNode('Sales playbook')).toBeTruthy());
    const kbNode = canvasNode('Sales playbook') as HTMLElement;

    fireEvent.click(within(kbNode).getByRole('button', { name: 'Remove node' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove node?' });
    expect(within(dialog).getByText('Remove Sales playbook and its connections?')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(canvasNode('Sales playbook')).toBeTruthy();

    fireEvent.click(within(kbNode).getByRole('button', { name: 'Remove node' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(canvasNode('Sales playbook')).toBeNull());
  });

  it('shows plain empty states when nothing is connected yet', async () => {
    agentsApi.knowledgeBasePage.mockResolvedValue({ items: [], next: null });
    toolsetsApi.getMyToolsets.mockResolvedValue({ toolsets: [] });
    await renderNewAgent();

    expect(await screen.findByText('No connectors configured')).toBeTruthy();
    expect(screen.getByText('No collections available')).toBeTruthy();
    expect(screen.getByText('No toolsets available.')).toBeTruthy();
  });

  it('keeps the palette in its loading state until the builder data arrives', async () => {
    let finishLoading: (models: unknown[]) => void = () => {};
    fetchOrgModels.mockReturnValue(new Promise((resolve) => { finishLoading = resolve; }));
    renderInTheme(<AgentBuilder agentKey={null} />);

    expect(screen.queryByText('Sales playbook')).toBeNull();
    expect(document.querySelectorAll('.react-flow__node')).toHaveLength(0);

    await act(async () => finishLoading([model()]));
    expect(await screen.findByText('Sales playbook')).toBeTruthy();
    await waitFor(() => expect(canvasNode('Chat input')).toBeTruthy());
  });

  it('tells the person when the builder could not load', async () => {
    fetchOrgModels.mockRejectedValue(apiFailure(503));
    renderInTheme(<AgentBuilder agentKey={null} />);

    expect(await screen.findByText('Failed to load builder resources')).toBeTruthy();
  });
});

describe('service agents', () => {
  it('asks for a name before creating a service agent', async () => {
    await renderNewAgent();

    fireEvent.click(screen.getByRole('button', { name: 'Create as service agent' }));

    expect(toastMessages()).toContain(
      'Enter a name to continue. — Please enter an agent name before enabling service agent mode.',
    );
    expect(await screen.findByText('Enter a name to continue.')).toBeTruthy();
  });

  it('explains that toolset nodes must go before the agent can become a service agent', async () => {
    await renderNewAgent();
    fireEvent.change(nameField(), { target: { value: 'Ops bot' } });
    dragPaletteItemToCanvas('Team Jira');

    fireEvent.click(screen.getByRole('button', { name: 'Create as service agent' }));

    await waitFor(() => expect(toastMessages()[0]).toMatch(/^Remove toolset nodes from the canvas first — /));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('creates a service agent once every acknowledgement is ticked', async () => {
    await renderNewAgent();
    fireEvent.change(nameField(), { target: { value: 'Ops bot' } });

    fireEvent.click(screen.getByRole('button', { name: 'Create as service agent' }));
    const dialog = await screen.findByRole('dialog', { name: /create service agent/i });
    const confirm = within(dialog).getByRole('button', { name: 'Create service agent' });
    expect(confirm).toHaveProperty('disabled', true);
    within(dialog).getAllByRole('checkbox').forEach((box) => fireEvent.click(box));
    expect(confirm).toHaveProperty('disabled', false);
    fireEvent.click(confirm);

    await waitFor(() => expect(agentsApi.createAgent).toHaveBeenCalledTimes(1));
    const saPayload = lastSavedPayload(agentsApi.createAgent);
    expect(saPayload).toMatchObject({
      name: 'Ops bot',
      isServiceAccount: true,
      shareWithOrg: true,
    });
    // This is the call that sent null and was refused: create takes a string,
    // not a nullable one. toMatchObject alone stays green with null present,
    // so the field has to be checked for absence explicitly.
    expect('serviceAccountUserId' in saPayload).toBe(false);
    expect(router.replace).toHaveBeenCalledWith('/agents/edit?agentKey=new-agent&sa=1');
  });

  it('locks the service agent button for people without that permission', async () => {
    permissions.denied.add('accessServiceAgent');
    await renderNewAgent();

    const button = screen.getByRole('button', { name: /create as service agent/i });
    expect(button).toHaveProperty('disabled', true);
    expect(within(button).getByText('Locked')).toBeTruthy();
  });

  it("opens the agent's own credentials for a toolset on a service agent", async () => {
    toolsetsApi.getAgentToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    await renderExistingAgent({ isServiceAccount: true, shareWithOrg: true });

    expect(screen.getByText('Service agent')).toBeTruthy();
    const shareSwitch = screen.getByRole('switch', { name: /share with everyone/i });
    expect(shareSwitch.getAttribute('aria-checked')).toBe('true');
    expect(shareSwitch).toHaveProperty('disabled', true);

    fireEvent.click(await screen.findByRole('button', { name: 'Set agent credentials for this toolset' }));

    expect(await screen.findByRole('dialog', { name: /agent toolset credentials/i })).toBeTruthy();
    expect(toolsetsApi.getAgentToolsets).toHaveBeenCalledWith('agent-1', expect.anything());
  });
});

describe('knowledge a service agent is offered', () => {
  const appNode = (id: string, name: string, sharingStatus: 'personal' | 'team') =>
    ({ id, name, connector: 'DRIVE', sharingStatus }) as unknown as KnowledgeHubAppNode;

  beforeEach(() => {
    agentsApi.knowledgeHubAppPage.mockResolvedValue({
      items: [appNode('drive-mine', 'My Drive', 'personal'), appNode('drive-team', 'Team Drive', 'team')],
      next: null,
    });
  });

  it('offers no personal connector, and no collection while the agent is not bound', async () => {
    await renderExistingAgent({ isServiceAccount: true, shareWithOrg: true });
    expect(await screen.findByText('Team Drive')).toBeTruthy();
    expect(screen.queryByText('My Drive')).toBeNull();
    expect(screen.queryByText('Sales playbook')).toBeNull();
    expect(
      screen.getByText('Collections can be added once this service agent is bound to a service account.'),
    ).toBeTruthy();
  });

  it('offers collections once the agent is bound to a service account', async () => {
    await renderExistingAgent({ isServiceAccount: true, shareWithOrg: true, serviceAccountUserId: 'sa-live' });
    expect(await screen.findByText('Sales playbook')).toBeTruthy();
    expect(screen.queryByText('My Drive')).toBeNull();
  });

  it('offers an agent a person runs their own connectors and collections', async () => {
    await renderExistingAgent();
    expect(await screen.findByText('My Drive')).toBeTruthy();
    expect(screen.getByText('Sales playbook')).toBeTruthy();
  });
});

describe('editing an agent', () => {
  it('says the agent could not be loaded, and never offers to save an empty graph over it', async () => {
    agentsApi.getAgent.mockRejectedValue(apiFailure(500));
    renderInTheme(<AgentBuilder agentKey="agent-1" />);

    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText("Couldn't load this agent")).toBeTruthy();
    // The panel says it once; the global error toast stays quiet.
    expect(agentsApi.getAgent).toHaveBeenCalledWith('agent-1', { quiet: true });
    // An edit saves as a draft or publishes (OWD-14); neither is offered over an agent that is not loaded.
    expect(saveButton(/save draft/i)).toHaveProperty('disabled', true);
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);
    expect(screen.queryByRole('button', { name: /create agent/i })).toBeNull();
    fireEvent.click(within(alert).getByRole('button', { name: 'Back to chat' }));
    expect(router.push).toHaveBeenCalledWith('/chat');
  });

  it('loads the agent when the person retries after a failed load', async () => {
    const agent = agentDetail();
    agentsApi.getAgent.mockRejectedValueOnce(apiFailure(500)).mockResolvedValue({ agent });
    renderInTheme(<AgentBuilder agentKey={agent._key} />);

    fireEvent.click(within(await screen.findByRole('alert')).getByRole('button', { name: 'Try again' }));

    await waitFor(() => expect(nameField()).toHaveProperty('value', agent.name));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(canvas().getAllByText('Chat input').length).toBeGreaterThan(0);
  });

  it('shows a loader, not the create-agent header, while the agent loads', async () => {
    agentsApi.getAgent.mockReturnValue(new Promise(() => {}));
    renderInTheme(<AgentBuilder agentKey="agent-1" />);

    expect(await screen.findByRole('status', { name: 'Loading agent…' })).toBeTruthy();
    // An edit saves as a draft or publishes (OWD-14); neither is offered over an agent that is not loaded.
    expect(saveButton(/save draft/i)).toHaveProperty('disabled', true);
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);
    expect(screen.queryByRole('button', { name: /create agent/i })).toBeNull();
    expect(nameField()).toHaveProperty('disabled', true);
  });

  it('shows what the agent already has and who created it', async () => {
    await renderExistingAgent({
      models: [{ ...model(), modelFriendlyName: 'GPT-4o' }],
    });

    expect(await screen.findByText('Priya Owner')).toBeTruthy();
    expect(canvas().queryByText('Using organization default model')).toBeNull();
    expect(canvas().getAllByText('GPT-4o').length).toBeGreaterThan(0);
  });

  it('leaves an untouched binding off an ordinary edit', async () => {
    // The server re-resolves any id it is sent and refuses one whose account
    // has been switched off, so re-sending an unchanged binding would make a
    // rename fail — and fail saying the account was not found.
    await renderExistingAgent({
      isServiceAccount: true,
      shareWithOrg: true,
      serviceAccountUserId: 'sa-live',
    });

    fireEvent.change(nameField(), { target: { value: 'Ops bot v2' } });
    await publishEdits();

    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    const payload = lastSavedPayload(agentsApi.updateAgent);
    expect(payload.name).toBe('Ops bot v2');
    expect('serviceAccountUserId' in payload).toBe(false);
  });

  it('only allows saving once something has changed, then confirms the update', async () => {
    const agent = await renderExistingAgent();
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);

    fireEvent.change(nameField(), { target: { value: 'Sales helper v2' } });
    await editAgentPrompts({ instructions: 'Always cite the playbook page.' });
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', false);
    await publishEdits();

    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(agentsApi.updateAgent.mock.calls[0][0]).toBe(agent._key);
    expect(lastSavedPayload(agentsApi.updateAgent)).toMatchObject({
      name: 'Sales helper v2',
      instructions: 'Always cite the playbook page.',
      systemPrompt: 'You are a helpful sales assistant.',
    });
    const done = await screen.findByRole('dialog', { name: 'Published' });
    expect(within(done).getByText('Chats with this agent use it from now on.')).toBeTruthy();
    fireEvent.click(within(done).getByRole('button', { name: 'Open in chat' }));
    expect(router.push).toHaveBeenCalledWith('/chat/?agentId=agent-1');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);
  });

  it("shows the server's reason when saving changes fails", async () => {
    agentsApi.updateAgent.mockRejectedValue(
      apiFailure(403, { message: 'Only the owner and editors can change this agent.' }),
    );
    await renderExistingAgent();

    fireEvent.change(nameField(), { target: { value: 'Renamed' } });
    await publishEdits();

    expect(await screen.findByText('Only the owner and editors can change this agent.')).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Published' })).toBeNull();
  });


  it('asks before leaving with unsaved changes', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    await renderExistingAgent();
    fireEvent.change(nameField(), { target: { value: 'Half-finished rename' } });

    confirmSpy.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: 'Go back' }));
    expect(confirmSpy).toHaveBeenCalledWith('You have unsaved changes. Are you sure you want to leave?');
    expect(router.push).not.toHaveBeenCalled();

    confirmSpy.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole('button', { name: 'Go back' }));
    expect(router.push).toHaveBeenCalledWith('/chat');
    confirmSpy.mockRestore();
  });

  it('blocks saving while deprecated tools remain, and removes them on request', async () => {
    await renderExistingAgent({
      toolsets: [
        {
          _key: 'ts-1',
          name: 'jira',
          displayName: 'Jira',
          instanceName: 'Team Jira',
          instanceId: 'jira-instance-1',
          type: 'app',
          selectedTools: null,
          tools: [
            { _key: 't-1', name: 'create_issue', fullName: 'jira.create_issue', description: '', toolsetName: 'jira' },
            { _key: 't-2', name: 'old_search', fullName: 'jira.old_search', description: '', toolsetName: 'jira', deprecated: true },
          ],
        },
      ],
    });

    expect(await screen.findByText(/old_search \(Team Jira\)/)).toBeTruthy();
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);

    fireEvent.click(screen.getByRole('button', { name: 'Remove deprecated tools' }));

    await waitFor(() => expect(screen.queryByText(/old_search \(Team Jira\)/)).toBeNull());
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', false);
    await publishEdits();
    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(lastSavedPayload(agentsApi.updateAgent).toolsets).toEqual([
      expect.objectContaining({ tools: [expect.objectContaining({ name: 'create_issue' })] }),
    ]);
  });
});

describe("an agent's MCP servers", () => {
  const attached = {
    _key: 'mcp-1',
    instanceId: 'inst-1',
    name: 'github',
    displayName: 'GitHub',
    typeId: 'github',
    allTools: false,
    tools: [{ name: 'list_issues', fullName: 'mcp_github_list_issues', description: '' }],
  };
  const live = {
    _id: 'inst-1', orgId: 'org-1', createdBy: 'u-1', name: 'github', typeId: 'github',
    transport: 'streamable_http', authMode: 'none', useAdminAuth: false, args: [], requiredEnv: [],
    optionalEnv: [], scopes: [], isCustom: false, createdAt: 1, updatedAt: 1, isAuthenticated: true,
    tools: [
      { name: 'list_issues', namespacedName: 'mcp_github_list_issues', inputSchema: {} },
      { name: 'create_pr', namespacedName: 'mcp_github_create_pr', inputSchema: {} },
    ],
  };

  beforeEach(() => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: true, ENABLE_SKILLS: false } });
  });

  it("the live server's tools don't count as an unsaved change", async () => {
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [live] });
    await renderExistingAgent({ mcpServers: [attached] });

    await waitFor(() => expect(canvasNode('Github')).toBeTruthy());
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);
  });

  it('a server missing from the live list is marked on its node', async () => {
    mcpApi.getMyMcpServers.mockResolvedValue({ instances: [] });
    await renderExistingAgent({ mcpServers: [attached] });

    await waitFor(() => expect(within(canvasNode('Github')!).getByText('Not available')).toBeTruthy());
  });

  it("a failed load doesn't mark the agent's servers as gone", async () => {
    mcpApi.getMyMcpServers.mockRejectedValue(new Error('network'));
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await renderExistingAgent({ mcpServers: [attached] });

    await waitFor(() => expect(canvasNode('Github')).toBeTruthy());
    expect(within(canvasNode('Github')!).queryByText('Not available')).toBeNull();
  });
});

describe('sharing and permissions', () => {
  it('shares the agent with everyone when the owner turns the switch on', async () => {
    await renderExistingAgent();

    fireEvent.click(screen.getByRole('switch', { name: /share with everyone/i }));
    await publishEdits();

    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(lastSavedPayload(agentsApi.updateAgent).shareWithOrg).toBe(true);
  });

  it('locks the share switch for people without the share permission', async () => {
    permissions.denied.add('shareAgent');
    await renderExistingAgent();

    expect(screen.getByRole('switch', { name: /share with everyone/i })).toHaveProperty('disabled', true);
  });

  it('shows a non-owner a read-only builder with no way to save or delete', async () => {
    await renderExistingAgent({ can_edit: false, can_delete: false, user_role: 'VIEWER', shareWithOrg: true });

    expect(
      screen.getByText(/View-only: you can’t change this agent’s configuration/),
    ).toBeTruthy();
    expect(nameField()).toHaveProperty('disabled', true);
    expect(saveButton(/publish$/i)).toHaveProperty('disabled', true);
    expect(screen.getByRole('switch', { name: /share with everyone/i })).toHaveProperty('disabled', true);
    // C4.08: anyone who can open the agent may export it, so the menu holds Export only.
    const trigger = screen.getByRole('button', { name: 'Agent menu' });
    await act(async () => {
      trigger.focus();
      trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    expect(await screen.findByRole('menuitem', { name: /export configuration/i })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: /delete agent/i })).toBeNull();
    await act(async () => {
      fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    });
    expect(screen.queryByRole('button', { name: /service agent/i })).toBeNull();
    expect(canvas().queryByRole('button', { name: 'Edit prompts' })).toBeNull();

    startDraggingPaletteItem('Sales playbook');
    expect(await screen.findByText('View only. Flow is locked; authenticate toolsets under Tools.')).toBeTruthy();
  });

  it('tells a viewer of an organization service agent that nothing can be changed', async () => {
    await renderExistingAgent({ can_edit: false, can_delete: false, isServiceAccount: true, shareWithOrg: true });

    expect(screen.getByText(/this organization service agent is locked/)).toBeTruthy();
    expect(screen.getByText('View only. Palette and toolset setup are locked for this org service agent.')).toBeTruthy();
  });
});

describe('deleting an agent', () => {
  async function openDeleteDialog() {
    fireEvent.click(await openAgentMenu());
    return screen.findByRole('dialog', { name: 'Delete this agent?' });
  }

  it('deletes only after the person types DELETE, then leaves the builder', async () => {
    await renderExistingAgent();

    const dialog = await openDeleteDialog();
    expect(within(dialog).getByText("'Sales helper'")).toBeTruthy();
    const confirm = within(dialog).getByRole('button', { name: 'Delete' });
    expect(confirm).toHaveProperty('disabled', true);

    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    expect(confirm).toHaveProperty('disabled', false);
    fireEvent.click(confirm);

    await waitFor(() => expect(agentsApi.deleteAgent).toHaveBeenCalledWith('agent-1'));
    expect(router.replace).toHaveBeenCalledWith('/chat/');
  });


  it('starts the confirmation over after the person cancels', async () => {
    await renderExistingAgent();

    let dialog = await openDeleteDialog();
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    dialog = await openDeleteDialog();
    expect(within(dialog).getByPlaceholderText('DELETE')).toHaveProperty('value', '');
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toHaveProperty('disabled', true);
    expect(agentsApi.deleteAgent).not.toHaveBeenCalled();
  });
  it("keeps the agent and shows the server's reason when deleting fails", async () => {
    agentsApi.deleteAgent.mockRejectedValue(apiFailure(403, { message: 'Only the owner can delete this agent.' }));
    await renderExistingAgent();

    const dialog = await openDeleteDialog();
    fireEvent.change(within(dialog).getByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByText('Only the owner can delete this agent.')).toBeTruthy();
    expect(router.replace).not.toHaveBeenCalled();
  });



  it('shows the delete option locked for people without the delete permission', async () => {
    permissions.denied.add('deleteAgent');
    await renderExistingAgent();

    const item = await openAgentMenu();
    expect(item.getAttribute('aria-disabled')).toBe('true');
    expect(within(item).getByText('Locked')).toBeTruthy();
  });
});

describe('toolset credential setup from the palette', () => {
  it('lets the person sign in to a toolset that is set up but not authenticated', async () => {
    toolsetsApi.getMyToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    await renderNewAgent();

    fireEvent.click(screen.getByRole('button', { name: 'Authenticate this toolset' }));

    const dialog = await screen.findByRole('dialog', { name: /configure toolset/i });
    expect(within(dialog).getByText('Team Jira')).toBeTruthy();
  });

  it('still lets a view-only person connect a toolset with their own sign-in', async () => {
    toolsetsApi.getMyToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    await renderExistingAgent({ can_edit: false, can_delete: false, user_role: 'VIEWER', shareWithOrg: true });

    fireEvent.click(screen.getByRole('button', { name: 'Authenticate this toolset' }));

    expect(await screen.findByRole('dialog', { name: /configure toolset/i })).toBeTruthy();
  });
});

async function engineSelect() {
  return (await canvas().findByRole('combobox', { name: 'Coding engine' })) as HTMLSelectElement;
}

async function saveAgent() {
  await publishEdits();
  await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
  return lastSavedPayload(agentsApi.updateAgent);
}

describe('an agent that runs on an engine (U2)', () => {
  it('AG-5: a loop agent shows no account choice and its saved payload carries no engineProfile', async () => {
    await renderExistingAgent();
    await engineSelect();
    expect(canvas().queryByRole('button', { name: 'Engine settings' })).toBeNull();
    expect(modelAccess.access).not.toHaveBeenCalled();

    fireEvent.change(nameField(), { target: { value: 'Sales helper v2' } });
    expect(await saveAgent()).not.toHaveProperty('engineProfile');
  });

  it('AG-4/AG-5: an engine agent chooses its account kind; the save carries it with the other dimensions', async () => {
    await renderExistingAgent({ engineProfile: { mode: 'plan' } });
    fireEvent.click(canvas().getByRole('button', { name: 'Engine settings' }));
    const group = await canvas().findByRole('radiogroup', { name: 'AI account' });
    fireEvent.click(within(group).getByRole('radio', { name: 'Personal key' }));
    expect((within(group).getByRole('radio', { name: 'Organization cloud account' }) as HTMLButtonElement).disabled).toBe(true);

    expect(saveButton(/publish$/i)).toHaveProperty('disabled', false);
    expect((await saveAgent()).engineProfile).toEqual({ mode: 'plan', accountKinds: ['user_api_key'] });
  });
});

describe('how the agent runs, together on its card (UX-07)', () => {
  it('UX-07 RT-0: Model and Coding engine sit in the "How it runs" group, before the safety checks and knowledge', async () => {
    await renderExistingAgent();
    await engineSelect();
    const group = canvas().getByRole('group', { name: 'How it runs' });
    const titles = Array.from(group.querySelectorAll('span')).map((el) => el.textContent);
    expect(titles.indexOf('Model')).toBeGreaterThanOrEqual(0);
    expect(titles.indexOf('Coding engine')).toBeGreaterThan(titles.indexOf('Model'));
    expect(within(group).queryByText('Knowledge')).toBeNull();
    expect(within(group).queryByText('Sub-agent models')).toBeNull();
  });
});

describe('the coding engine on the agent card (ENGPICK2)', () => {
  it('EB-0: the row sits directly under Model, and is no longer in the prompts dialog', async () => {
    await renderExistingAgent();
    await engineSelect();
    const titles = Array.from(document.querySelectorAll('.flow-node-card'))
      .flatMap((card) => Array.from(card.querySelectorAll('span')))
      .map((el) => el.textContent);
    const model = titles.indexOf('Model');
    expect(model).toBeGreaterThanOrEqual(0);
    expect(titles.slice(model + 1)).toContain('Coding engine');
    expect(titles.slice(model + 1).find((text) => text === 'Coding engine' || text === 'Sub-agent models')).toBe('Coding engine');

    fireEvent.click(canvas().getByRole('button', { name: 'Edit prompts' }));
    const dialog = await screen.findByRole('dialog', { name: 'Configure agent prompts' });
    expect(within(dialog).queryByRole('combobox', { name: 'Coding engine' })).toBeNull();
    expect(within(dialog).queryByText('Engine settings')).toBeNull();
  });

  it('EB-1: lists none and the allowed engines; one the org cannot use is disabled with its reason; a loading status first', async () => {
    let release!: () => void;
    modelAccess.codingEngine.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({ engines: [{ kind: 'codex', available: true, reason: null }, { kind: 'claude_code', available: false, reason: 'offering_not_permitted' }], orgDefault: null, choice: 'org_default' });
        }),
    );
    await renderExistingAgent();
    expect(canvas().getByRole('status').textContent).toContain('Loading');
    await act(async () => release());
    const select = await engineSelect();
    expect(select.value).toBe('none');
    const options = within(select).getAllByRole('option') as HTMLOptionElement[];
    expect(options.map((o) => [o.textContent, o.disabled])).toEqual([
      ['None – built-in agent loop', false],
      ['Codex', false],
      ["Claude Code – Not available in this kind of installation with your organization's models", true],
    ]);
    const helper = canvas().getByText('Used when a task needs code, files or many steps of work; other questions are answered directly.');
    expect(select.getAttribute('aria-describedby')).toBe(helper.id);
    expect(canvas().queryByRole('button', { name: 'Engine settings' })).toBeNull();
  });

  it('EB-2: picking an engine makes a loop agent an engine agent; its settings open under it and the save carries it', async () => {
    await renderExistingAgent();
    fireEvent.change(await engineSelect(), { target: { value: 'codex' } });
    const disclosure = await canvas().findByRole('button', { name: 'Engine settings' });
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(canvas().queryByRole('radiogroup', { name: 'AI account' })).toBeNull();
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    expect(await canvas().findByRole('radiogroup', { name: 'AI account' })).toBeTruthy();
    expect((await saveAgent()).engineProfile).toEqual({ engines: ['codex'] });
  });

  it('EB-3: None turns an engine agent back into a loop agent: the save sends engineProfile null', async () => {
    await renderExistingAgent({ engineProfile: { engines: ['opencode'], mode: 'plan' } });
    const select = await engineSelect();
    expect(select.value).toBe('opencode');
    fireEvent.change(select, { target: { value: 'none' } });
    await waitFor(() => expect(canvas().queryByRole('button', { name: 'Engine settings' })).toBeNull());
    expect((await saveAgent()).engineProfile).toBeNull();
  });

  it('EB-4: a save the server refuses shows its typed reason in the builder’s error slot', async () => {
    agentsApi.updateAgent.mockRejectedValue(
      apiFailure(403, { message: 'This offering may not use this account and route' }),
    );
    await renderExistingAgent();
    fireEvent.change(await engineSelect(), { target: { value: 'codex' } });
    await publishEdits();
    expect(await screen.findByText('This offering may not use this account and route')).toBeTruthy();
  });

  it('EB-5: when the engines cannot be loaded the card says so and offers to try again', async () => {
    modelAccess.codingEngine.mockRejectedValueOnce(new Error('down'));
    await renderExistingAgent();
    expect(await canvas().findByText("The coding engines can't be loaded right now.")).toBeTruthy();
    fireEvent.click(canvas().getByRole('button', { name: 'Try again' }));
    expect(await engineSelect()).toBeTruthy();
  });

  it('EB-6: a viewer sees the engine as text, with no select, and None reads "None – built-in loop"', async () => {
    await renderExistingAgent({ can_edit: false, can_delete: false, user_role: 'VIEWER', shareWithOrg: true });
    expect(canvas().getByText('Coding engine')).toBeTruthy();
    expect(canvas().getByText('None – built-in loop')).toBeTruthy();
    expect(canvas().queryByRole('combobox', { name: 'Coding engine' })).toBeNull();
    expect(modelAccess.codingEngine).not.toHaveBeenCalled();
  });

  it('EB-7: a viewer of an engine agent sees its engine and can open its settings without editing them', async () => {
    await renderExistingAgent({ can_edit: false, can_delete: false, user_role: 'VIEWER', shareWithOrg: true, engineProfile: { engines: ['codex'] } });
    expect(canvas().getByText('Codex')).toBeTruthy();
    fireEvent.click(canvas().getByRole('button', { name: 'Engine settings' }));
    const group = await canvas().findByRole('radiogroup', { name: 'AI account' });
    for (const radio of within(group).getAllByRole('radio')) expect((radio as HTMLButtonElement).disabled).toBe(true);
  });
});
