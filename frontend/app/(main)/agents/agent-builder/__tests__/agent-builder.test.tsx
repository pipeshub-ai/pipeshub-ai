import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useToastStore } from '@/lib/store/toast-store';
import type { AgentFormPayload } from '../types';
import {
  agentDetail,
  apiFailure,
  dragPaletteItemToCanvas,
  installBrowserShims,
  knowledgeBase,
  model,
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
vi.mock('@/config', () => ({
  useUserPermission: (key: string) => !permissions.denied.has(key),
  PermissionLockIcon: () => <span>Locked</span>,
  InheritedConfigNotice: () => null,
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
  getAllKnowledgeBasesForBuilder: vi.fn(),
  getAllKnowledgeHubAppNodes: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api')>()),
  AgentsApi: agentsApi,
}));

const fetchAvailableLlms = vi.hoisted(() => vi.fn());
vi.mock('@/chat/api', () => ({ ChatApi: { fetchAvailableLlms } }));

const toolsetsApi = vi.hoisted(() => ({
  getAllMyToolsets: vi.fn(),
  getAllAgentToolsets: vi.fn(),
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
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({
  McpServersApi: {
    getMyMcpServers: vi.fn(async () => ({ instances: [] })),
    getAgentMcpServers: vi.fn(async () => ({ instances: [] })),
  },
}));
vi.mock('@/app/(main)/workspace/web-search/api', () => ({
  WebSearchApi: { getConfig: vi.fn(async () => ({ providers: [] })) },
}));
const getUsersByIds = vi.hoisted(() => vi.fn());
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds } }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AgentBuilder } from '../agent-builder';

function lastSavedPayload(fn: ReturnType<typeof vi.fn>): AgentFormPayload {
  const call = fn.mock.calls[fn.mock.calls.length - 1];
  if (!call) throw new Error('Nothing was saved');
  return call[call.length - 1] as AgentFormPayload;
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

function saveButton(name: RegExp = /create agent|save changes/i) {
  return screen.getByRole('button', { name });
}

function toastMessages() {
  return useToastStore.getState().toasts.map((t) => [t.title, t.description].filter(Boolean).join(' — '));
}

beforeEach(() => {
  installBrowserShims();
  permissions.denied.clear();
  useFeatureFlagsStore.setState({ flags: { ENABLE_ACTIONS: true, ENABLE_MCP: false, ENABLE_SKILLS: false } });
  useToastStore.setState({ toasts: [] });
  agentsApi.getAllKnowledgeBasesForBuilder.mockResolvedValue({ knowledgeBases: [knowledgeBase()] });
  agentsApi.getAllKnowledgeHubAppNodes.mockResolvedValue([]);
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
  fetchAvailableLlms.mockResolvedValue([model()]);
  toolsetsApi.getAllMyToolsets.mockResolvedValue({ toolsets: [toolset()] });
  toolsetsApi.getAllAgentToolsets.mockResolvedValue([]);
  getUsersByIds.mockResolvedValue([{ name: 'Priya Owner', email: 'priya@example.com' }]);
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

function openSection(title: string) {
  fireEvent.click(screen.getByText(title, { selector: 'span, p, div' }).closest('[role="button"]') as HTMLElement);
}

async function editAgentPrompts(fields: { instructions?: string; systemPrompt?: string }) {
  fireEvent.click(canvas().getByRole('button', { name: 'Edit prompts' }));
  const dialog = await screen.findByRole('dialog', { name: 'Configure Agent Prompts' });
  const [systemPrompt, instructions] = within(dialog).getAllByRole('textbox');
  if (fields.systemPrompt !== undefined) fireEvent.change(systemPrompt, { target: { value: fields.systemPrompt } });
  if (fields.instructions !== undefined) fireEvent.change(instructions, { target: { value: fields.instructions } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Configure Agent Prompts' })).toBeNull());
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
    toolsetsApi.getAllMyToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
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
    agentsApi.getAllKnowledgeBasesForBuilder.mockResolvedValue({ knowledgeBases: [] });
    toolsetsApi.getAllMyToolsets.mockResolvedValue({ toolsets: [] });
    await renderNewAgent();

    expect(await screen.findByText('No connectors configured')).toBeTruthy();
    expect(screen.getByText('No collections available')).toBeTruthy();
    expect(screen.getByText('No toolsets available.')).toBeTruthy();
  });

  it('keeps the palette in its loading state until the builder data arrives', async () => {
    let finishLoading: (models: unknown[]) => void = () => {};
    fetchAvailableLlms.mockReturnValue(new Promise((resolve) => { finishLoading = resolve; }));
    renderInTheme(<AgentBuilder agentKey={null} />);

    expect(screen.queryByText('Sales playbook')).toBeNull();
    expect(document.querySelectorAll('.react-flow__node')).toHaveLength(0);

    await act(async () => finishLoading([model()]));
    expect(await screen.findByText('Sales playbook')).toBeTruthy();
    await waitFor(() => expect(canvasNode('Chat input')).toBeTruthy());
  });

  it('tells the person when the builder could not load', async () => {
    fetchAvailableLlms.mockRejectedValue(apiFailure(503));
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
    expect(lastSavedPayload(agentsApi.createAgent)).toMatchObject({
      name: 'Ops bot',
      isServiceAccount: true,
      shareWithOrg: true,
    });
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
    toolsetsApi.getAllAgentToolsets.mockResolvedValue([toolset({ isAuthenticated: false })]);
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    await renderExistingAgent({ isServiceAccount: true, shareWithOrg: true });

    expect(screen.getByText('Service agent')).toBeTruthy();
    const shareSwitch = screen.getByRole('switch');
    expect(shareSwitch.getAttribute('aria-checked')).toBe('true');
    expect(shareSwitch).toHaveProperty('disabled', true);

    fireEvent.click(await screen.findByRole('button', { name: 'Set agent credentials for this toolset' }));

    expect(await screen.findByRole('dialog', { name: /agent toolset credentials/i })).toBeTruthy();
    expect(toolsetsApi.getAllAgentToolsets).toHaveBeenCalledWith('agent-1', expect.anything());
  });
});

describe('editing an agent', () => {
  it('shows what the agent already has and who created it', async () => {
    await renderExistingAgent({
      models: [{ ...model(), modelFriendlyName: 'GPT-4o' }],
    });

    expect(await screen.findByText('Priya Owner')).toBeTruthy();
    expect(canvas().queryByText('Using organization default model')).toBeNull();
    expect(canvas().getAllByText('GPT-4o').length).toBeGreaterThan(0);
  });

  it('only allows saving once something has changed, then confirms the update', async () => {
    const agent = await renderExistingAgent();
    expect(saveButton(/save changes/i)).toHaveProperty('disabled', true);

    fireEvent.change(nameField(), { target: { value: 'Sales helper v2' } });
    await editAgentPrompts({ instructions: 'Always cite the playbook page.' });
    expect(saveButton(/save changes/i)).toHaveProperty('disabled', false);
    fireEvent.click(saveButton(/save changes/i));

    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(agentsApi.updateAgent.mock.calls[0][0]).toBe(agent._key);
    expect(lastSavedPayload(agentsApi.updateAgent)).toMatchObject({
      name: 'Sales helper v2',
      instructions: 'Always cite the playbook page.',
      systemPrompt: 'You are a helpful sales assistant.',
    });
    const done = await screen.findByRole('dialog', { name: 'Agent updated' });
    expect(within(done).getByText('Your changes have been saved.')).toBeTruthy();
    fireEvent.click(within(done).getByRole('button', { name: 'Open in chat' }));
    expect(router.push).toHaveBeenCalledWith('/chat/?agentId=agent-1');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(saveButton(/save changes/i)).toHaveProperty('disabled', true);
  });

  it("shows the server's reason when saving changes fails", async () => {
    agentsApi.updateAgent.mockRejectedValue(
      apiFailure(403, { message: 'Only the owner and editors can change this agent.' }),
    );
    await renderExistingAgent();

    fireEvent.change(nameField(), { target: { value: 'Renamed' } });
    fireEvent.click(saveButton(/save changes/i));

    expect(await screen.findByText('Only the owner and editors can change this agent.')).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Agent updated' })).toBeNull();
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
    expect(saveButton(/save changes/i)).toHaveProperty('disabled', true);

    fireEvent.click(screen.getByRole('button', { name: 'Remove Deprecated Tools' }));

    await waitFor(() => expect(screen.queryByText(/old_search \(Team Jira\)/)).toBeNull());
    expect(saveButton(/save changes/i)).toHaveProperty('disabled', false);
    fireEvent.click(saveButton(/save changes/i));
    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(lastSavedPayload(agentsApi.updateAgent).toolsets).toEqual([
      expect.objectContaining({ tools: [expect.objectContaining({ name: 'create_issue' })] }),
    ]);
  });
});

describe('sharing and permissions', () => {
  it('shares the agent with everyone when the owner turns the switch on', async () => {
    await renderExistingAgent();

    fireEvent.click(screen.getByRole('switch'));
    fireEvent.click(saveButton(/save changes/i));

    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(lastSavedPayload(agentsApi.updateAgent).shareWithOrg).toBe(true);
  });

  it('locks the share switch for people without the share permission', async () => {
    permissions.denied.add('shareAgent');
    await renderExistingAgent();

    expect(screen.getByRole('switch')).toHaveProperty('disabled', true);
  });

  it('shows a non-owner a read-only builder with no way to save or delete', async () => {
    await renderExistingAgent({ can_edit: false, can_delete: false, user_role: 'VIEWER', shareWithOrg: true });

    expect(
      screen.getByText(/View-only: you can’t change this agent’s configuration/),
    ).toBeTruthy();
    expect(nameField()).toHaveProperty('disabled', true);
    expect(saveButton(/save changes/i)).toHaveProperty('disabled', true);
    expect(screen.getByRole('switch')).toHaveProperty('disabled', true);
    expect(screen.queryByRole('button', { name: 'Agent menu' })).toBeNull();
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
    toolsetsApi.getAllMyToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    await renderNewAgent();

    fireEvent.click(screen.getByRole('button', { name: 'Authenticate this toolset' }));

    const dialog = await screen.findByRole('dialog', { name: /configure toolset/i });
    expect(within(dialog).getByText('Team Jira')).toBeTruthy();
  });

  it('still lets a view-only person connect a toolset with their own sign-in', async () => {
    toolsetsApi.getAllMyToolsets.mockResolvedValue({ toolsets: [toolset({ isAuthenticated: false })] });
    toolsetsApi.getToolsetRegistrySchema.mockResolvedValue({ toolset: { config: { auth: { schemas: {} } } } });
    await renderExistingAgent({ can_edit: false, can_delete: false, user_role: 'VIEWER', shareWithOrg: true });

    fireEvent.click(screen.getByRole('button', { name: 'Authenticate this toolset' }));

    expect(await screen.findByRole('dialog', { name: /configure toolset/i })).toBeTruthy();
  });
});
