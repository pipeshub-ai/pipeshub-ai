import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));
const api = vi.hoisted(() => ({
  checkHandle: vi.fn(),
  createAgent: vi.fn(),
  getKnowledgeBasesForBuilder: vi.fn(),
  getKnowledgeHubAppNodes: vi.fn(),
  getAllMyToolsets: vi.fn(),
}));
vi.mock('@/app/(main)/agents/api', () => ({
  AgentsApi: { checkHandle: api.checkHandle, createAgent: api.createAgent, getKnowledgeBasesForBuilder: api.getKnowledgeBasesForBuilder, getKnowledgeHubAppNodes: api.getKnowledgeHubAppNodes },
}));
vi.mock('@/app/(main)/toolsets/api', () => ({ ToolsetsApi: { getAllMyToolsets: api.getAllMyToolsets } }));

import { AgentDraftPlaceholder } from '../agent-draft-placeholder';
import { HANDLE_CHECK_DEBOUNCE_MS } from '../use-handle-availability';
import { getToastRenderDescription, useToastStore } from '@/lib/store/toast-store';
import type { AgentDraft } from '../../../types';

const CONV = 'c'.repeat(24);
const MSG = 'm'.repeat(24);

const draft: AgentDraft = {
  draftId: 'd1',
  name: 'Offer drafter',
  handleSuggestion: 'offer-drafter',
  description: 'Drafts offer letters',
  instructions: 'Use the price list',
  knowledge: ['kb-1', 'conn-1', 'conn-2'],
  toolsets: [],
  suggestedTools: ['search_issues', 'send_email'],
  provenance: 'sender',
  requestedBy: 'u-a',
};

const jira = {
  name: 'jira', normalized_name: 'jira', displayName: 'Jira', description: '', iconPath: '', category: 'app', toolCount: 1,
  tools: [{ name: 'search_issues', fullName: 'jira.search_issues', description: 'find issues' }],
  isConfigured: true, isAuthenticated: true, isFromRegistry: false, instanceId: 'inst-1', instanceName: 'Main Jira', toolsetType: 'app',
};

function card(over: Partial<React.ComponentProps<typeof AgentDraftPlaceholder>> = {}, d: AgentDraft = draft) {
  return render(
    <Theme>
      <AgentDraftPlaceholder draft={d} conversationId={CONV} messageId={MSG} builderEnabled {...over} />
    </Theme>,
  );
}

const flush = async (ms = HANDLE_CHECK_DEBOUNCE_MS + 10) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
const input = (id: string) => screen.getByTestId(id) as HTMLInputElement;
const checkbox = (id: string) => screen.getByTestId(id) as HTMLButtonElement;
const type = (id: string, value: string) => fireEvent.change(input(id), { target: { value } });
const create = () => screen.getByTestId('agent-draft-create') as HTMLButtonElement;

beforeEach(() => {
  vi.useFakeTimers();
  replace.mockReset();
  api.checkHandle.mockReset().mockResolvedValue({ available: true });
  api.createAgent.mockReset().mockResolvedValue({ _key: 'agent-9', handle: 'offer-drafter' });
  api.getKnowledgeBasesForBuilder.mockReset().mockResolvedValue({ knowledgeBases: [{ id: 'kb-1', connectorId: 'kb-1', name: 'Price lists' }] });
  api.getKnowledgeHubAppNodes.mockReset().mockResolvedValue({ nodes: [{ id: 'conn-1', name: 'Sales Drive' }], hasNext: false });
  api.getAllMyToolsets.mockReset().mockResolvedValue({ toolsets: [jira] });
  useToastStore.getState().clearAll();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('AgentDraftCard states', () => {
  it('sender provenance: knowledge pre-ticked, tools unticked, no banner, private line', async () => {
    card();
    await flush();
    expect(checkbox('agent-draft-knowledge-kb-1').getAttribute('aria-checked')).toBe('true');
    expect(checkbox('agent-draft-knowledge-conn-2').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Price lists')).toBeTruthy();
    expect(screen.getByText('Sales Drive')).toBeTruthy();
    expect(checkbox('agent-draft-tool-search_issues').getAttribute('aria-checked')).toBe('false');
    expect(checkbox('agent-draft-tool-send_email').getAttribute('aria-checked')).toBe('false');
    expect(screen.queryByTestId('agent-draft-content-banner')).toBeNull();
    expect(screen.getByText('Private - only you can use it')).toBeTruthy();
  });

  it('content provenance: banner and nothing pre-ticked', async () => {
    card({}, { ...draft, provenance: 'content' });
    await flush();
    expect(screen.getByRole('note').textContent).toContain('Suggested from document content');
    expect(checkbox('agent-draft-knowledge-kb-1').getAttribute('aria-checked')).toBe('false');
    expect(checkbox('agent-draft-knowledge-conn-2').getAttribute('aria-checked')).toBe('false');
  });

  it('AB-04: a tool with no authenticated toolset is disabled and says so', async () => {
    card();
    await flush();
    expect(checkbox('agent-draft-tool-search_issues').disabled).toBe(false);
    expect(checkbox('agent-draft-tool-send_email').disabled).toBe(true);
    expect(screen.getByText('Not set up for you')).toBeTruthy();
  });

  it('a tool named the way the model sees it (app__tool) resolves to its signed-in toolset', async () => {
    card({}, { ...draft, suggestedTools: ['jira__search_issues', 'jira.search_issues', 'gmail__send_email'] });
    await flush();
    expect(checkbox('agent-draft-tool-jira__search_issues').disabled).toBe(false);
    expect(checkbox('agent-draft-tool-jira.search_issues').disabled).toBe(false);
    expect(checkbox('agent-draft-tool-gmail__send_email').disabled).toBe(true);
  });

  it('an unauthenticated instance does not count as set up', async () => {
    api.getAllMyToolsets.mockResolvedValue({ toolsets: [{ ...jira, isAuthenticated: false }] });
    card();
    await flush();
    expect(checkbox('agent-draft-tool-search_issues').disabled).toBe(true);
  });

  it('flag off: read-only with the notice and no Create', () => {
    card({ builderEnabled: false });
    expect(screen.getByText('Agent builder is turned off')).toBeTruthy();
    expect(screen.queryByTestId('agent-draft-create')).toBeNull();
    expect(input('agent-draft-name').disabled).toBe(true);
    expect(api.checkHandle).not.toHaveBeenCalled();
  });

  it('a live draft without a stored row cannot be created yet', async () => {
    card({ messageId: null });
    await flush();
    expect(screen.getByText('Saving draft…')).toBeTruthy();
    expect(create().disabled).toBe(true);
  });

  it('non-requesters get the redacted view', () => {
    render(<Theme><AgentDraftPlaceholder draft={{ redacted: true, authorId: 'u-b' }} authorName="Bea" /></Theme>);
    expect(screen.getByTestId('agent-draft-redacted').textContent).toBe('Bea drafted an agent');
    expect(screen.queryByTestId('agent-draft-create')).toBeNull();
  });
});

describe('handle availability', () => {
  it('debounces, shows checking then available', async () => {
    card();
    await flush(HANDLE_CHECK_DEBOUNCE_MS - 50);
    expect(screen.getByText('Checking availability…')).toBeTruthy();
    expect(api.checkHandle).not.toHaveBeenCalled();
    await flush(100);
    expect(api.checkHandle).toHaveBeenCalledTimes(1);
    expect(api.checkHandle.mock.calls[0][0]).toBe('offer-drafter');
    expect(screen.getByText('Available')).toBeTruthy();
  });

  it('typing aborts the request before it and ignores a stale answer', async () => {
    let resolveFirst: (v: unknown) => void = () => undefined;
    api.checkHandle.mockImplementationOnce(() => new Promise((r) => { resolveFirst = r; }));
    card();
    await flush();
    const firstSignal = api.checkHandle.mock.calls[0][1].signal as AbortSignal;
    type('agent-draft-handle', 'second-try');
    expect(firstSignal.aborted).toBe(true);
    await flush();
    expect(api.checkHandle.mock.calls[1][0]).toBe('second-try');
    await act(async () => resolveFirst({ available: false, reason: 'taken', suggestion: 'old-2' }));
    expect(screen.queryByText('@offer-drafter is already taken')).toBeNull();
    expect(screen.getByText('Available')).toBeTruthy();
  });

  it('taken: message, suggestion button re-checks and blocks Create meanwhile', async () => {
    api.checkHandle.mockResolvedValueOnce({ available: false, reason: 'taken', suggestion: 'offer-drafter-2' });
    card();
    await flush();
    expect(screen.getByRole('alert').textContent).toBe('@offer-drafter is already taken');
    expect(create().disabled).toBe(true);
    fireEvent.click(screen.getByTestId('agent-draft-use-suggestion'));
    expect(input('agent-draft-handle').value).toBe('offer-drafter-2');
    await flush();
    expect(api.checkHandle.mock.calls[1][0]).toBe('offer-drafter-2');
    expect(create().disabled).toBe(false);
  });

  it('invalid and reserved are answered locally without a request', async () => {
    card();
    await flush();
    api.checkHandle.mockClear();
    type('agent-draft-handle', 'Bad Handle');
    await flush();
    expect(screen.getByRole('alert').textContent).toContain('2-40 lowercase');
    type('agent-draft-handle', 'assistant');
    await flush();
    expect(screen.getByRole('alert').textContent).toBe('@assistant is reserved');
    expect(api.checkHandle).not.toHaveBeenCalled();
    expect(create().disabled).toBe(true);
  });
});

describe('creating', () => {
  const click = async () => {
    await act(async () => {
      fireEvent.click(create());
    });
  };

  it('sends the edited spec and the draftRef, never a service account or org sharing', async () => {
    card();
    await flush();
    type('agent-draft-name', '  Offer writer ');
    fireEvent.click(checkbox('agent-draft-knowledge-conn-2'));
    fireEvent.click(checkbox('agent-draft-tool-search_issues'));
    await click();
    expect(api.createAgent).toHaveBeenCalledTimes(1);
    const [payload, options] = api.createAgent.mock.calls[0];
    expect(payload).toEqual({
      name: 'Offer writer',
      handle: 'offer-drafter',
      description: 'Drafts offer letters',
      instructions: 'Use the price list',
      startMessage: '',
      systemPrompt: '',
      models: [],
      tags: [],
      knowledge: [
        { connectorId: 'kb-1', filters: { recordGroups: [], records: [] } },
        { connectorId: 'conn-1', filters: { recordGroups: [], records: [] } },
      ],
      toolsets: [{
        id: 'inst-1', instanceId: 'inst-1', instanceName: 'Main Jira', name: 'jira', displayName: 'Jira', type: 'app',
        tools: [{ name: 'search_issues', fullName: 'jira.search_issues', description: 'find issues' }],
      }],
      draftRef: { conversationId: CONV, messageId: MSG },
    });
    expect(options).toEqual({ suppressErrorToast: true });
    expect(payload).not.toHaveProperty('isServiceAccount');
    expect(payload).not.toHaveProperty('shareWithOrg');
  });

  it('names the toolset the way the API validates it, as the builder does', async () => {
    api.getAllMyToolsets.mockResolvedValue({
      toolsets: [{ ...jira, name: 'Jira Data Center', normalized_name: 'Jira Data Center', toolsetType: 'Jira Data Center', category: 'app' }],
    });
    card();
    await flush();
    fireEvent.click(checkbox('agent-draft-tool-search_issues'));
    await click();
    const [payload] = api.createAgent.mock.calls[0];
    expect(payload.toolsets[0]).toMatchObject({ name: 'jiradatacenter', type: 'app' });
  });

  it('a double click creates once', async () => {
    let resolve: (v: unknown) => void = () => undefined;
    api.createAgent.mockImplementation(() => new Promise((r) => { resolve = r; }));
    card();
    await flush();
    await act(async () => {
      fireEvent.click(create());
      fireEvent.click(create());
    });
    expect(api.createAgent).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ _key: 'agent-9', handle: 'offer-drafter' }));
  });

  it('AB-10: success collapses the card; the toast and the card open the agent chat, and nothing offers a mention until M2', async () => {
    card();
    await flush();
    await click();
    expect(screen.getByTestId('agent-draft-created').textContent).toBe('Created @offer-drafter (private)');
    const toast = useToastStore.getState().toasts[0];
    expect(toast.title).toBe('Created @offer-drafter (private)');
    expect(toast.action?.label).toBe('Open agent chat');
    expect(getToastRenderDescription(toast.id)).toBeUndefined();
    toast.action?.onClick?.();
    expect(replace).toHaveBeenCalledWith('/chat/?agentId=agent-9');

    replace.mockClear();
    fireEvent.click(screen.getByTestId('agent-draft-open-chat'));
    expect(replace).toHaveBeenCalledWith('/chat/?agentId=agent-9');
    expect(screen.queryByRole('button', { name: 'Mention here' })).toBeNull();
  });

  it('HANDLE_TAKEN from the server is inline with a suggestion and raises no toast', async () => {
    api.createAgent.mockRejectedValue({ code: 'HANDLE_TAKEN', details: { suggestion: 'offer-drafter-2' } });
    card();
    await flush();
    await click();
    expect(screen.getByRole('alert').textContent).toBe('@offer-drafter is already taken');
    expect(useToastStore.getState().toasts).toHaveLength(0);
    expect(api.createAgent.mock.calls[0][1]).toEqual({ suppressErrorToast: true });
    fireEvent.click(screen.getByTestId('agent-draft-use-suggestion'));
    expect(input('agent-draft-handle').value).toBe('offer-drafter-2');
  });

  it('INVALID_KNOWLEDGE unticks and flags only the listed ids', async () => {
    api.createAgent.mockRejectedValue({ code: 'INVALID_KNOWLEDGE', details: { ids: ['conn-2'] } });
    card();
    await flush();
    await click();
    expect(checkbox('agent-draft-knowledge-conn-2').getAttribute('aria-checked')).toBe('false');
    expect(checkbox('agent-draft-knowledge-conn-2').disabled).toBe(true);
    expect(checkbox('agent-draft-knowledge-kb-1').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('alert').textContent).toBe('No longer available to you');
    expect(create().disabled).toBe(false);
  });

  it('INVALID_TOOLSET unticks and flags the tool of that instance', async () => {
    api.createAgent.mockRejectedValue({ code: 'INVALID_TOOLSET', details: { ids: ['inst-1'] } });
    card();
    await flush();
    fireEvent.click(checkbox('agent-draft-tool-search_issues'));
    await click();
    expect(checkbox('agent-draft-tool-search_issues').getAttribute('aria-checked')).toBe('false');
    expect(checkbox('agent-draft-tool-search_issues').disabled).toBe(true);
    expect(screen.getByRole('alert').textContent).toBe('No longer available to you');
  });

  it.each([
    ['SERVICE_ACCOUNT_NOT_ALLOWED'],
    ['CONVERSATION_NOT_FOUND'],
    [undefined],
  ])('%s shows one generic alert', async (code) => {
    api.createAgent.mockRejectedValue({ code });
    card();
    await flush();
    await click();
    expect(screen.getByTestId('agent-draft-error').textContent).toBe('The agent could not be created. Please try again.');
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('needs a name', async () => {
    card();
    await flush();
    type('agent-draft-name', '   ');
    expect(create().disabled).toBe(true);
    expect(api.createAgent).not.toHaveBeenCalled();
  });
});
