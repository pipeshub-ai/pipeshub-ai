/**
 * WP-21 (OWD-14) in the agent builder: the version chats run and whether there are unpublished changes, "Save draft"
 * and "Publish" in place of "Save changes", the versions panel (who published each version and when), and rollback,
 * which resets the draft and says when it discarded unpublished changes.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore, type AgentHarnessRollout } from '@/lib/store/feature-flags-store';
import { HARNESS_ORG_ANSWERS, SURFACES_SHOWN } from '@/lib/store/__tests__/harness-org-answers';
import { useToastStore } from '@/lib/store/toast-store';
import type { AgentFormPayload } from '../types';
import type { AgentDetail, AgentVersionSummary, AgentVersionsList } from '../../types';
import { agentDetail, apiFailure, installBrowserShims, knowledgeBase, model, publishEdits, renderInTheme, toolset } from './agent-builder-harness';

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
  PermissionLockIcon: () => <span>Locked</span>,
  InheritedConfigNotice: () => null,
  McpAgentApprovalsNotice: () => null,
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
  listAgentVersions: vi.fn(),
  getAgentVersion: vi.fn(),
  publishAgentVersion: vi.fn(),
  rollbackAgentVersion: vi.fn(),
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
const modelAccess = vi.hoisted(() => ({ access: vi.fn(), codingEngine: vi.fn() }));
vi.mock('@/app/(main)/workspace/ai-accounts/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/workspace/ai-accounts/api')>()),
  ModelAccountsApi: modelAccess,
}));

import { AgentBuilder } from '../agent-builder';

const PRIYA = 'user-priya';
const ARJUN = 'user-arjun';

function summary(version: number, overrides: Partial<AgentVersionSummary> = {}): AgentVersionSummary {
  return {
    agentKey: 'agent-1',
    version,
    digest: `sha256:${String(version).padStart(64, '0')}`,
    source: 'publish',
    publishedBy: PRIYA,
    publishedAtTimestamp: Date.UTC(2026, 9, 20 + version, 10, 42),
    rolledBackFrom: null,
    note: null,
    ...overrides,
  };
}

const HISTORY: AgentVersionSummary[] = [
  summary(4, { source: 'rollback', rolledBackFrom: 2, note: 'Version 3 sent replies too early.' }),
  summary(3, { publishedBy: ARJUN, note: 'Reply to customers directly from Slack.' }),
  summary(2),
  summary(1, { source: 'migration', publishedBy: null }),
];

function versionsList(overrides: Partial<AgentVersionsList> = {}): AgentVersionsList {
  return { versions: HISTORY, latestVersion: 4, latestDigest: HISTORY[0].digest, unpublishedChanges: false, ...overrides };
}

const versioned = (unpublishedChanges: boolean, latestVersion = 4): Partial<AgentDetail> => ({
  versioning: { latestVersion, latestDigest: 'sha256:x', unpublishedChanges },
});

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
  agentsApi.listAgentVersions.mockResolvedValue(versionsList());
  fetchAvailableLlms.mockResolvedValue([model()]);
  toolsetsApi.getMyToolsets.mockResolvedValue({ toolsets: [toolset()] });
  toolsetsApi.getAgentToolsets.mockResolvedValue({ toolsets: [] });
  getUsersByIds.mockImplementation(async (ids: string[]) =>
    ids.map((id) => ({ id, name: id === PRIYA ? 'Priya Shah' : id === ARJUN ? 'Arjun Rao' : 'Owner', email: '' })),
  );
  modelAccess.access.mockResolvedValue({ version: 'v', offering: 'managed', policy: { disabledRules: [], consumerPlans: false }, rules: [] });
  modelAccess.codingEngine.mockResolvedValue({ engines: [], orgDefault: null, choice: 'org_default' });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderAgent(overrides: Partial<AgentDetail> = {}) {
  const agent = agentDetail(overrides);
  agentsApi.getAgent.mockResolvedValue({ agent });
  renderInTheme(<AgentBuilder agentKey={agent._key} />);
  await waitFor(() => expect(nameField()).toHaveProperty('value', agent.name));
  return agent;
}

const nameField = () => screen.getByPlaceholderText('e.g. Support bot') as HTMLInputElement;
const button = (name: RegExp) => screen.getByRole('button', { name });
const status = () => screen.getByTestId('agent-version-status');

async function openVersions() {
  fireEvent.click(button(/versions$/i));
  const panel = await screen.findByTestId('agent-versions-panel');
  await waitFor(() => expect(within(panel).getAllByTestId('agent-version-row').length).toBeGreaterThan(0));
  return panel;
}

const row = (panel: HTMLElement, version: number) =>
  within(panel).getAllByTestId('agent-version-row').find((r) => r.getAttribute('data-version') === String(version))!;

describe('GATE: versions and the coding engine follow the harness for the org (ADR-OPS-08)', () => {
  for (const [answer, rollout] of Object.entries(HARNESS_ORG_ANSWERS) as Array<[keyof typeof HARNESS_ORG_ANSWERS, AgentHarnessRollout | null]>) {
    const shown = SURFACES_SHOWN[answer];
    it(`${answer}: versions and the coding engine are ${shown ? 'shown' : 'not shown; one "Save changes" as before'}`, async () => {
      useFeatureFlagsStore.setState({ agentHarness: rollout });
      await renderAgent(versioned(false));
      expect(screen.queryByTestId('agent-version-status') !== null).toBe(shown);
      expect(screen.queryByRole('button', { name: /save draft$/i }) !== null).toBe(shown);
      if (shown) await waitFor(() => expect(modelAccess.codingEngine).toHaveBeenCalled());
      else expect(modelAccess.codingEngine).not.toHaveBeenCalled();
    });
  }
});

describe('WP-21 OWD-14: where the agent stands', () => {
  it('OWD-14 UI-1: a new agent says it is not published yet, and creating it is its first publish', async () => {
    renderInTheme(<AgentBuilder agentKey={null} />);
    await screen.findByText('Agent', { selector: 'span' });
    expect(status().textContent).toBe('Not published yet');
    expect(screen.queryByRole('button', { name: /save draft/i })).toBeNull();
    expect(button(/create agent/i)).toBeTruthy();
  });

  it('OWD-14 UI-2: an agent shows the version chats run, with no unpublished changes and nothing to publish', async () => {
    await renderAgent(versioned(false));
    expect(within(status()).getByText('Live: version 4')).toBeTruthy();
    expect(within(status()).queryByText('Unpublished changes')).toBeNull();
    expect(button(/publish$/i)).toHaveProperty('disabled', true);
    expect(button(/save draft$/i)).toHaveProperty('disabled', true);
  });

  it('OWD-14 UI-3: a saved draft that differs from the live version can be published as it is', async () => {
    agentsApi.publishAgentVersion.mockResolvedValue({ version: summary(5), created: true });
    await renderAgent(versioned(true));
    expect(within(status()).getByText('Unpublished changes')).toBeTruthy();
    expect(button(/save draft$/i)).toHaveProperty('disabled', true);
    expect(button(/publish$/i)).toHaveProperty('disabled', false);

    fireEvent.click(button(/publish$/i));
    const dialog = await screen.findByRole('alertdialog', { name: 'Publish version 5?' });
    expect(within(dialog).getByText(/Chats with this agent use version 5 from now on/)).toBeTruthy();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'What changed (optional)' }), {
      target: { value: 'Escalate P1 tickets.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /^publish$/i }));

    await waitFor(() => expect(agentsApi.publishAgentVersion).toHaveBeenCalledWith('agent-1', 'Escalate P1 tickets.'));
    expect(agentsApi.updateAgent).not.toHaveBeenCalled();
    expect(await screen.findByRole('dialog', { name: 'Version 5 published' })).toBeTruthy();
  });

  it('OWD-14 UI-4: an edit makes the agent show unpublished changes; "Save draft" keeps it a draft', async () => {
    agentsApi.updateAgent.mockImplementation(async (key: string, payload: AgentFormPayload) =>
      agentDetail({ _key: key, name: payload.name, ...versioned(true) }),
    );
    await renderAgent(versioned(false));
    fireEvent.change(nameField(), { target: { value: 'Support triage' } });
    expect(within(status()).getByText('Unpublished changes')).toBeTruthy();

    fireEvent.click(button(/save draft$/i));
    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    const [key, payload, options] = agentsApi.updateAgent.mock.calls[0];
    expect(key).toBe('agent-1');
    expect(payload).toMatchObject({ name: 'Support triage' });
    expect(options).toEqual({ publish: false });
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.title)).toContain(
        'Draft saved. Chats keep running version 4 until you publish.',
      ),
    );
    expect(within(status()).getByText('Unpublished changes')).toBeTruthy();
    expect(button(/save draft$/i)).toHaveProperty('disabled', true);
    expect(button(/publish$/i)).toHaveProperty('disabled', false);
  });

  it('OWD-14 UI-5: publishing unsaved edits saves and publishes them in one request, with the note', async () => {
    agentsApi.updateAgent.mockImplementation(async (key: string, payload: AgentFormPayload) =>
      agentDetail({ _key: key, name: payload.name, ...versioned(false, 5) }),
    );
    await renderAgent(versioned(false));
    fireEvent.change(nameField(), { target: { value: 'Support triage' } });
    fireEvent.click(button(/publish$/i));
    const dialog = await screen.findByRole('alertdialog', { name: 'Publish version 5?' });
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Renamed.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /^publish$/i }));

    await waitFor(() => expect(agentsApi.updateAgent).toHaveBeenCalledTimes(1));
    expect(agentsApi.updateAgent.mock.calls[0][2]).toEqual({ versionNote: 'Renamed.' });
    expect(agentsApi.publishAgentVersion).not.toHaveBeenCalled();
    expect(await screen.findByRole('dialog', { name: 'Version 5 published' })).toBeTruthy();
    expect(within(status()).getByText('Live: version 5')).toBeTruthy();
    expect(within(status()).queryByText('Unpublished changes')).toBeNull();
  });

  it('OWD-14 UI-6: a save whose publish failed says the edits are kept as a draft', async () => {
    const { AgentPublishIncompleteError } = await vi.importActual<typeof import('../../api')>('../../api');
    agentsApi.updateAgent.mockRejectedValue(
      new AgentPublishIncompleteError('', agentDetail({ name: 'Support triage', ...versioned(true) })),
    );
    await renderAgent(versioned(false));
    fireEvent.change(nameField(), { target: { value: 'Support triage' } });
    await publishEdits();

    expect(
      await screen.findByText('Your edits are saved as a draft, but publishing them failed. Try Publish again.'),
    ).toBeTruthy();
    expect(within(status()).getByText('Unpublished changes')).toBeTruthy();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('OWD-14 UI-7: a refused publish keeps the dialog open with the reason', async () => {
    agentsApi.publishAgentVersion.mockRejectedValue(
      apiFailure(409, { message: 'Another version was published at the same moment. Try again.' }),
    );
    await renderAgent(versioned(true));
    await publishEdits();
    const dialog = await screen.findByRole('alertdialog');
    expect(await within(dialog).findByText('Another version was published at the same moment. Try again.')).toBeTruthy();
  });
});

describe('WP-21 OWD-14: the versions panel', () => {
  it('OWD-14 UI-8: lists every version newest first with who published it and when; the live one is marked', async () => {
    await renderAgent(versioned(false));
    const panel = await openVersions();

    const rows = within(panel).getAllByTestId('agent-version-row');
    expect(rows.map((r) => r.getAttribute('data-version'))).toEqual(['4', '3', '2', '1']);
    expect(within(rows[0]).getByText('Live')).toBeTruthy();
    expect(within(rows[0]).getByText('Rolled back from version 2')).toBeTruthy();
    expect(within(rows[0]).getByText('Version 3 sent replies too early.')).toBeTruthy();
    await waitFor(() => expect(within(rows[1]).getByText(/^Arjun Rao · /)).toBeTruthy());
    expect(within(rows[0]).getByText(/^Priya Shah · /)).toBeTruthy();
    expect(within(rows[3]).getByText('As it was before versions')).toBeTruthy();
    expect(within(rows[3]).getByText(/^Created when versions were turned on · /)).toBeTruthy();
    expect(getUsersByIds).toHaveBeenCalledWith([PRIYA, ARJUN]);
    // The live version is what a rollback would publish again; it offers none.
    expect(within(rows[0]).queryByRole('button', { name: /roll back/i })).toBeNull();
    expect(within(rows[1]).getByRole('button', { name: /roll back to this version/i })).toBeTruthy();
  });

  it('OWD-14 UI-9: a version says what it does only when asked, without prompts or ids', async () => {
    agentsApi.getAgentVersion.mockResolvedValue({
      ...summary(3),
      definition: {
        models: ['key-1_gpt-4o'],
        toolsets: [{ _key: 't1', name: 'jira', displayName: 'Jira' }, { name: 'slack', displayName: 'Slack' }],
        knowledge: [{ connectorId: 'kb-1' }],
        instructions: 'Reply politely.',
        systemPrompt: 'secret system prompt',
      },
    });
    await renderAgent(versioned(false));
    const panel = await openVersions();
    const third = row(panel, 3);
    const toggle = within(third).getByRole('button', { name: /what this version does/i });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(agentsApi.getAgentVersion).not.toHaveBeenCalled();

    fireEvent.click(toggle);
    expect(await within(third).findByText('Models: gpt-4o')).toBeTruthy();
    expect(within(third).getByText('Tools: Jira, Slack')).toBeTruthy();
    expect(within(third).getByText('Knowledge sources: 1')).toBeTruthy();
    expect(within(third).getByText('Instructions: 15 characters')).toBeTruthy();
    expect(within(third).queryByText(/secret system prompt/)).toBeNull();
    expect(agentsApi.getAgentVersion).toHaveBeenCalledWith('agent-1', 3);
  });

  it('OWD-14 UI-10: unpublished changes are called out, with a way to publish them', async () => {
    agentsApi.listAgentVersions.mockResolvedValue(versionsList({ unpublishedChanges: true }));
    await renderAgent(versioned(true));
    const panel = await openVersions();
    const callout = within(panel).getByTestId('agent-versions-unpublished');
    expect(callout.textContent).toContain('You have unpublished changes. Rolling back to an earlier version discards them.');
    fireEvent.click(within(callout).getByRole('button', { name: 'Publish as version 5' }));
    expect(await screen.findByRole('alertdialog', { name: 'Publish version 5?' })).toBeTruthy();
  });

  it('OWD-14 UI-11: a viewer sees the versions but cannot roll back or publish', async () => {
    agentsApi.listAgentVersions.mockResolvedValue(versionsList({ unpublishedChanges: true }));
    await renderAgent({ ...versioned(true), can_edit: false, user_role: 'VIEWER', shareWithOrg: true });
    const panel = await openVersions();
    expect(within(panel).queryByRole('button', { name: /roll back/i })).toBeNull();
    expect(within(panel).queryByRole('button', { name: /publish as/i })).toBeNull();
  });

  it('OWD-14 UI-12: a list that cannot be read says so and can be retried; an empty one says how to start', async () => {
    agentsApi.listAgentVersions
      .mockRejectedValueOnce(apiFailure(500, { message: 'The versions could not be loaded.' }))
      .mockResolvedValueOnce(versionsList({ versions: [] }));
    await renderAgent(versioned(false));
    fireEvent.click(button(/versions$/i));
    const panel = await screen.findByTestId('agent-versions-panel');
    expect(await within(panel).findByText('The versions could not be loaded.')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Try again' }));
    expect(await within(panel).findByTestId('agent-versions-empty')).toBeTruthy();
  });
});

describe('WP-21 OWD-14: rollback resets the draft', () => {
  it('OWD-14 UI-13: rolling back with unpublished changes warns first, then reports them discarded and reloads the draft', async () => {
    agentsApi.listAgentVersions.mockResolvedValueOnce(versionsList({ unpublishedChanges: true }));
    agentsApi.rollbackAgentVersion.mockResolvedValue({
      version: summary(5, { source: 'rollback', rolledBackFrom: 3 }),
      created: true,
      discardedUnpublishedChanges: true,
    });
    await renderAgent({ ...versioned(true), name: 'Draft name' });
    const panel = await openVersions();

    fireEvent.click(within(row(panel, 3)).getByRole('button', { name: /roll back to this version/i }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Roll back to version 3?' });
    expect(within(dialog).getByText(/sets the agent back to version 3 and publishes it as version 5/)).toBeTruthy();
    expect(within(dialog).getByText('Your unpublished changes will be discarded.')).toBeTruthy();

    // The server rewrote the draft to version 3; the builder must show that, not the old draft.
    agentsApi.getAgent.mockResolvedValue({ agent: agentDetail({ name: 'Version three', ...versioned(false, 5) }) });
    agentsApi.listAgentVersions.mockResolvedValue(
      versionsList({ versions: [summary(5, { source: 'rollback', rolledBackFrom: 3 }), ...HISTORY], latestVersion: 5 }),
    );
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Why (optional)' }), { target: { value: 'Too eager.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /^roll back$/i }));

    await waitFor(() => expect(agentsApi.rollbackAgentVersion).toHaveBeenCalledWith('agent-1', 3, 'Too eager.'));
    expect((await within(panel).findByTestId('agent-rollback-outcome')).textContent).toBe(
      'Rolled back to version 3. It is live as version 5. Your unpublished changes were discarded.',
    );
    await waitFor(() => expect(nameField().value).toBe('Version three'));
    expect(within(status()).getByText('Live: version 5')).toBeTruthy();
    expect(within(status()).queryByText('Unpublished changes')).toBeNull();
    await waitFor(() => expect(row(panel, 5)).toBeTruthy());
  });

  it('OWD-14 UI-14: edits not saved yet are discarded too, and say so even when the saved draft had none', async () => {
    agentsApi.rollbackAgentVersion.mockResolvedValue({
      version: summary(5, { source: 'rollback', rolledBackFrom: 2 }),
      created: true,
      discardedUnpublishedChanges: false,
    });
    await renderAgent(versioned(false));
    fireEvent.change(nameField(), { target: { value: 'Unsaved rename' } });
    const panel = await openVersions();
    fireEvent.click(within(row(panel, 2)).getByRole('button', { name: /roll back to this version/i }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Roll back to version 2?' });
    expect(within(dialog).getByText('Your unpublished changes will be discarded.')).toBeTruthy();
    agentsApi.getAgent.mockResolvedValue({ agent: agentDetail({ name: 'Version two', ...versioned(false, 5) }) });
    fireEvent.click(within(dialog).getByRole('button', { name: /^roll back$/i }));

    expect((await within(panel).findByTestId('agent-rollback-outcome')).textContent).toContain(
      'Your unpublished changes were discarded.',
    );
    await waitFor(() => expect(nameField().value).toBe('Version two'));
  });

  it('OWD-14 UI-15: rolling back to what is already live publishes nothing and says so; no warning without changes', async () => {
    agentsApi.rollbackAgentVersion.mockResolvedValue({
      version: summary(4, { source: 'rollback', rolledBackFrom: 2 }),
      created: false,
      discardedUnpublishedChanges: false,
    });
    await renderAgent(versioned(false));
    const panel = await openVersions();
    fireEvent.click(within(row(panel, 2)).getByRole('button', { name: /roll back to this version/i }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Roll back to version 2?' });
    expect(within(dialog).queryByText('Your unpublished changes will be discarded.')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: /^roll back$/i }));
    expect((await within(panel).findByTestId('agent-rollback-outcome')).textContent).toBe(
      'Version 4 already matches version 2, so nothing new was published.',
    );
  });

  it('OWD-14 UI-16: a rollback that fails keeps the dialog open with the reason, and the editor as it was', async () => {
    agentsApi.rollbackAgentVersion.mockRejectedValue(
      apiFailure(500, { message: 'The rollback did not finish and the live version is unchanged. Try again.' }),
    );
    await renderAgent(versioned(false));
    const panel = await openVersions();
    fireEvent.click(within(row(panel, 2)).getByRole('button', { name: /roll back to this version/i }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Roll back to version 2?' });
    fireEvent.click(within(dialog).getByRole('button', { name: /^roll back$/i }));
    expect(
      await within(dialog).findByText('The rollback did not finish and the live version is unchanged. Try again.'),
    ).toBeTruthy();
    expect(agentsApi.getAgent).toHaveBeenCalledTimes(1);
    expect(within(panel).queryByTestId('agent-rollback-outcome')).toBeNull();
  });
});
