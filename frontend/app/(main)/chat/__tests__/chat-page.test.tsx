import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ConversationMessage } from '../types';

// ── Module-boundary mocks ──────────────────────────────────────────

let searchParams = new URLSearchParams();
const routerPush = vi.fn();
const routerReplace = vi.fn();
vi.mock('next/navigation', () => ({
  useSearchParams: () => searchParams,
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
}));

const append = vi.fn();
vi.mock('@assistant-ui/react', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@assistant-ui/react')>()),
  AssistantRuntimeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useExternalStoreRuntime: () => ({}),
  useThreadRuntime: () => ({ append }),
}));

vi.mock('../components', async () => {
  const { useChatStore } = await import('../store');
  return {
    // Stands in for the real list: shows the text of each message in the open thread.
    MessageList: () => {
      const messages = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.messages : undefined));
      return (
        <ul aria-label="Messages">
          {(messages ?? []).map((m, i) => (
            <li key={i}>
              {(m.content as readonly { type: string; text?: string }[])
                .map((p) => p.text ?? '')
                .join('')}
            </li>
          ))}
        </ul>
      );
    },
    ChatInputWrapper: () => <textarea aria-label="Message composer" />,
    SearchResultsView: () => <div>Search results</div>,
    DemoSuggestions: ({ onPick }: { onPick: (s: { id: string; text: string }) => void }) => (
      <button type="button" onClick={() => onPick({ id: 'demo-1', text: 'What is our refund policy?' })}>
        What is our refund policy?
      </button>
    ),
  };
});

let demoDataActive = false;
vi.mock('@/app/(main)/workspace/connectors/demo-data/use-demo-data', () => ({
  useDemoDataActive: () => demoDataActive,
  // The chat page also reads this person's demo switch; unknown reads as shown.
  useDemoDataStatus: () => null,
}));
vi.mock('@/app/(main)/workspace/connectors/demo-data/components', () => ({
  DemoDataRemovalNotice: () => null,
}));

vi.mock('@/config', () => ({
  AgentChatHeader: ({ displayName }: { displayName: string | null }) => (
    <header aria-label="Agent header">{displayName ?? 'Agent'}</header>
  ),
}));

const fetchConversations = vi.fn();
const fetchConversation = vi.fn();
vi.mock('@/chat/api', () => ({
  ChatApi: {
    fetchConversations: (...args: unknown[]) => fetchConversations(...args),
    fetchConversation: (...args: unknown[]) => fetchConversation(...args),
  },
}));

const getAgent = vi.fn();
const fetchAgentConversation = vi.fn();
vi.mock('@/app/(main)/agents/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/app/(main)/agents/api')>()),
  AgentsApi: {
    getAgent: (...args: unknown[]) => getAgent(...args),
    fetchAgentConversation: (...args: unknown[]) => fetchAgentConversation(...args),
  },
}));

const fetchModelsForContext = vi.fn();
vi.mock('@/chat/utils/fetch-models-for-context', () => ({
  fetchModelsForContext: (...args: unknown[]) => fetchModelsForContext(...args),
}));

vi.mock('@/app/components/file-preview', () => ({
  FilePreviewInlinePanel: ({ file, onClose }: { file: { name: string }; onClose: () => void }) => (
    <aside aria-label="Preview panel">
      {file.name}
      <button type="button" onClick={onClose}>Close preview</button>
    </aside>
  ),
  FilePreviewFullscreen: ({ file }: { file: { name: string } }) => <div aria-label="Fullscreen preview">{file.name}</div>,
}));

const getSharedMembers = vi.fn();
vi.mock('../share-adapter', () => ({
  createChatShareAdapter: () => ({ getSharedMembers: () => getSharedMembers() }),
}));
vi.mock('@/app/components/share', () => ({
  ShareSidebar: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="Share conversation" /> : null),
  ShareHeaderGroup: ({ members, onShareClick }: { members: { name: string }[]; onShareClick: () => void }) => (
    <div>
      {members.map((m) => <span key={m.name}>{m.name}</span>)}
      <button type="button" onClick={onShareClick}>Share</button>
    </div>
  ),
}));

vi.mock('../components/search', () => ({
  ChatSearch: ({ open }: { open: boolean }) => (open ? <div role="dialog" aria-label="Search chats" /> : null),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: ({ showLabel }: { showLabel?: boolean }) => (showLabel ? <div role="status">Loading</div> : null),
}));
vi.mock('@/app/components/workspace-menu/hooks/use-github-stars', () => ({ useGitHubStars: () => '1.2k' }));
vi.mock('@/app/components/sidebar/sidebar-expand-button', () => ({ SidebarExpandButton: () => null }));
vi.mock('@/app/components/ui/service-gate', () => ({
  ServiceGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const getUsersByIds = vi.fn();
vi.mock('@/app/(main)/workspace/users/api', () => ({
  UsersApi: { getUsersByIds: (...args: unknown[]) => getUsersByIds(...args) },
}));

const getProject = vi.fn();
vi.mock('@/chat/project-api', () => ({ ProjectApi: { get: (...args: unknown[]) => getProject(...args) } }));
vi.mock('@/chat/hooks/use-project-scope-hydration', () => ({ useProjectScopeHydration: () => {} }));

const toastError = vi.fn();
vi.mock('@/lib/store/toast-store', () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    info: vi.fn(),
    success: vi.fn(),
    loading: vi.fn(),
    warning: vi.fn(),
  },
  useToastStore: { getState: () => ({ toasts: [] }) },
}));

import ChatPage from '../page';
import { AxiosError, AxiosHeaders } from 'axios';
import { processError } from '@/lib/api/api-error';
import { showErrorToast } from '@/lib/api/error-toast';
import { useChatStore } from '../store';
import { useUserStore } from '@/lib/store/user-store';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useCommandStore } from '@/lib/store/command-store';

// ── Fixtures ───────────────────────────────────────────────────────

const PAGINATION = { page: 1, limit: 20, totalCount: 2, totalPages: 1, hasNextPage: false, hasPrevPage: false };

function apiMessage(overrides: Partial<ConversationMessage>): ConversationMessage {
  return {
    _id: 'm',
    messageType: 'user_query',
    content: '',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: { modelKey: 'k', modelName: 'gpt-x', modelProvider: 'openai', chatMode: 'standard' },
    createdAt: '2026-09-01T10:00:00.000Z',
    updatedAt: '2026-09-01T10:00:00.000Z',
    feedback: [],
    ...overrides,
  } as ConversationMessage;
}

function conversationDetail({ isOwner = true, messages }: { isOwner?: boolean; messages?: ConversationMessage[] } = {}) {
  const msgs = messages ?? [
    apiMessage({ _id: 'u1', messageType: 'user_query', content: 'How many vacation days do I get?' }),
    apiMessage({ _id: 'b1', messageType: 'bot_response', content: 'You get 25 days a year.' }),
  ];
  return {
    conversation: {
      id: 'conv-1',
      title: 'Vacation',
      initiator: 'u',
      messages: msgs,
      status: 'complete',
      modelInfo: undefined,
      isShared: false,
      sharedWith: [],
      access: { isOwner, accessLevel: isOwner ? 'owner' : 'read' },
    },
    messages: msgs,
    pagination: PAGINATION,
  };
}

function renderPage(params = '') {
  searchParams = new URLSearchParams(params);
  return render(
    <Theme>
      <ChatPage />
    </Theme>,
  );
}

function ctrl(key: string, shiftKey = false) {
  fireEvent.keyDown(window, { key, ctrlKey: true, metaKey: true, shiftKey });
}

afterEach(() => cleanup());

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.getState().reset();
  useUserStore.setState({ profile: null });
  usePendingChatStore.setState({ pending: null });
  useFeatureFlagsStore.setState({ flags: {} } as Partial<ReturnType<typeof useFeatureFlagsStore.getState>>);
  useServicesHealthStore.setState({ apiServerReachable: true });
  demoDataActive = false;
  window.history.replaceState(null, '', '/chat/');
  fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
  fetchModelsForContext.mockResolvedValue(undefined);
  getSharedMembers.mockResolvedValue([]);
  getUsersByIds.mockResolvedValue([]);
});

// ── Tests ──────────────────────────────────────────────────────────

describe('Chat page — new chat', () => {
  it("greets the user by name and offers the composer", async () => {
    useUserStore.setState({
      profile: {
        userId: 'u', firstName: 'Asha', lastName: 'Rao', fullName: 'Asha Rao', email: 'asha@acme.test',
        isAdmin: false, avatarUrl: null, hasLoggedIn: true,
      },
    });
    renderPage();

    expect(screen.getByText('Hey, Asha Rao 👋')).toBeTruthy();
    expect(screen.getByText('What do you want to explore today?')).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Message composer' })).toBeTruthy();
    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'owned' });
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'shared' });
  });

  it('falls back to the email name, then to a neutral greeting', () => {
    useUserStore.setState({
      profile: {
        userId: 'u', firstName: null, lastName: null, fullName: null, email: 'sam.lee@acme.test',
        isAdmin: false, avatarUrl: null, hasLoggedIn: true,
      },
    });
    renderPage();
    expect(screen.getByText('Hey, sam.lee 👋')).toBeTruthy();
    cleanup();

    useUserStore.setState({ profile: null });
    renderPage();
    expect(screen.getByText('Hey, there 👋')).toBeTruthy();
  });

  it('marks the conversation list as failed when it cannot be loaded', async () => {
    fetchConversations.mockRejectedValue(new Error('Request failed with status code 500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderPage();
    await waitFor(() => expect(useChatStore.getState().conversationsError).toBeTruthy());
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });

  it('starts a thread and asks the demo question the user picked', () => {
    demoDataActive = true;
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: 'What is our refund policy?' }));

    expect(useChatStore.getState().activeSlotId).not.toBeNull();
    expect(append).toHaveBeenCalledWith({
      role: 'user',
      content: [{ type: 'text', text: 'What is our refund policy?' }],
      startRun: true,
    });
  });

  it('sends the question handed over from the chat widget, scoped to its collections', async () => {
    usePendingChatStore.getState().setPending({
      message: 'Summarise the Q3 plan',
      pageContext: { collections: [{ id: 'kb-1', name: 'Plans' }] },
      referrerPage: '/knowledge-base',
    } as Parameters<ReturnType<typeof usePendingChatStore.getState>['setPending']>[0]);

    renderPage();

    await waitFor(() => expect(append).toHaveBeenCalledTimes(1));
    expect(append.mock.calls[0][0]).toMatchObject({
      role: 'user',
      content: [{ type: 'text', text: 'Summarise the Q3 plan' }],
      metadata: { custom: { collections: [{ id: 'kb-1', name: 'Plans' }] } },
      startRun: true,
    });
    const state = useChatStore.getState();
    expect(state.settings.filters.apps).toEqual(['kb-1']);
    expect(state.collectionNamesCache['kb-1']).toBe('Plans');
    expect(usePendingChatStore.getState().pending).toBeNull();
  });
});

describe('Chat page — opening a conversation', () => {
  it('shows a loader until the history arrives, then the messages', async () => {
    let resolve: (v: ReturnType<typeof conversationDetail>) => void = () => {};
    fetchConversation.mockImplementation(() => new Promise((r) => { resolve = r; }));

    renderPage('conversationId=conv-1');

    expect(screen.getByRole('status').textContent).toBe('Loading');
    await waitFor(() => expect(fetchConversation).toHaveBeenCalledWith('conv-1'));

    await act(async () => resolve(conversationDetail()));

    expect(await screen.findByText('How many vacation days do I get?')).toBeTruthy();
    expect(screen.getByText('You get 25 days a year.')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Message composer' })).toBeTruthy();
  });

  it('restores the collections the last question was scoped to', async () => {
    fetchConversation.mockResolvedValue(
      conversationDetail({
        messages: [
          apiMessage({
            _id: 'u1',
            content: 'What changed?',
            appliedFilters: {
              apps: [
                { id: 'legacy-kb-root', name: 'Collections', nodeType: 'app', connector: 'KB' },
                { id: 'drive-1', name: 'Drive', nodeType: 'app', connector: 'DRIVE' },
              ],
              kb: [{ id: 'kb-9', name: 'Handbook', nodeType: 'recordGroup', connector: 'KB' }],
            },
          }),
          apiMessage({ _id: 'b1', messageType: 'bot_response', content: 'Two policies.' }),
        ],
      }),
    );

    renderPage('conversationId=conv-1');

    await screen.findByText('Two policies.');
    const { settings, collectionNamesCache } = useChatStore.getState();
    expect(settings.filters).toEqual({ apps: ['drive-1'], kb: ['kb-9'] });
    expect(collectionNamesCache).toMatchObject({ 'drive-1': 'Drive', 'kb-9': 'Handbook' });
  });

  it("hides the composer on a conversation someone shared with the user", async () => {
    fetchConversation.mockResolvedValue(conversationDetail({ isOwner: false }));
    renderPage('conversationId=conv-1');

    await screen.findByText('You get 25 days a year.');
    expect(screen.queryByRole('textbox', { name: 'Message composer' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Share' })).toBeNull();
  });

  it('lets the owner see who it is shared with and open sharing', async () => {
    fetchConversation.mockResolvedValue(conversationDetail({ isOwner: true }));
    getSharedMembers.mockResolvedValue([
      { id: 'me', name: 'Asha Rao', isOwner: true, type: 'user', avatarUrl: '' },
      { id: 'u2', name: 'Ben Cho', isOwner: false, type: 'user', avatarUrl: '' },
    ]);
    renderPage('conversationId=conv-1');

    expect(await screen.findByText('Ben Cho')).toBeTruthy();
    expect(screen.queryByText('Asha Rao')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Share' }));
    expect(screen.getByRole('dialog', { name: 'Share conversation' })).toBeTruthy();
  });

  it('shows a single message when the server refuses to open the conversation', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    // What the shared API client does on an HTTP error: it shows its own
    // message, then rejects with the processed error.
    fetchConversation.mockImplementation(async () => {
      const failure = new AxiosError('Request failed with status code 500');
      failure.response = { status: 500, statusText: '', data: {}, headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() } } as never;
      const processed = processError(failure as AxiosError<never>);
      showErrorToast(processed);
      throw processed;
    });

    renderPage('conversationId=conv-1');

    await waitFor(() => expect(fetchConversation).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(toastError).toHaveBeenCalledTimes(1);
    expect(toastError.mock.calls[0][0]).toBe('Server Error');
  });

  it('tells the user when the conversation cannot be read, instead of showing an empty chat', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchConversation.mockRejectedValue(new TypeError("Cannot read properties of undefined (reading 'page')"));

    renderPage('conversationId=conv-1');

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastError).toHaveBeenCalledWith(
      "We couldn't open this conversation. Refresh the page to try again.",
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('stays quiet about the conversation when the whole server is unreachable', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    useServicesHealthStore.setState({ apiServerReachable: false });
    fetchConversation.mockRejectedValue(new Error('Network Error'));

    renderPage('conversationId=conv-1');

    await waitFor(() => expect(fetchConversation).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(toastError).not.toHaveBeenCalled();
  });

  it('reuses the already-loaded thread when the user comes back to it', async () => {
    fetchConversation.mockResolvedValue(conversationDetail());
    const view = renderPage('conversationId=conv-1');
    await screen.findByText('You get 25 days a year.');

    searchParams = new URLSearchParams('');
    view.rerender(<Theme><ChatPage /></Theme>);
    expect(await screen.findByText('What do you want to explore today?')).toBeTruthy();

    searchParams = new URLSearchParams('conversationId=conv-1');
    view.rerender(<Theme><ChatPage /></Theme>);

    expect(await screen.findByText('You get 25 days a year.')).toBeTruthy();
    expect(fetchConversation).toHaveBeenCalledTimes(1);
  });
});

describe('Chat page — keyboard shortcuts and the address bar', () => {
  it('opens and closes the chat search with Ctrl/Cmd+K', () => {
    renderPage();
    ctrl('k');
    expect(screen.getByRole('dialog', { name: 'Search chats' })).toBeTruthy();
    ctrl('k');
    expect(screen.queryByRole('dialog', { name: 'Search chats' })).toBeNull();
  });

  it('opens the chat search when another part of the app asks for it', () => {
    renderPage();
    act(() => useCommandStore.getState().dispatch('openCommandPalette'));
    expect(screen.getByRole('dialog', { name: 'Search chats' })).toBeTruthy();
  });

  it('starts a new chat with Ctrl/Cmd+N and with Ctrl/Cmd+Shift+K', () => {
    renderPage();
    act(() => {
      const store = useChatStore.getState();
      const id = store.createSlot(null);
      store.updateSlot(id, {
        messages: [{ role: 'user', content: [{ type: 'text', text: 'Draft question' }] }],
      });
      store.setActiveSlot(id);
    });
    expect(screen.getByText('Draft question')).toBeTruthy();

    ctrl('n');
    expect(useChatStore.getState().activeSlotId).toBeNull();
    expect(routerReplace).toHaveBeenLastCalledWith('/chat/');
    expect(screen.getByText('What do you want to explore today?')).toBeTruthy();

    ctrl('K', true);
    expect(routerReplace).toHaveBeenCalledTimes(2);
  });

  it('keeps a new agent chat on the agent when starting over', () => {
    getAgent.mockResolvedValue({ agent: { name: 'Support Bot', toolsets: [] }, toolFullNames: [] });
    window.history.replaceState(null, '', '/chat/?agentId=agent-7');
    renderPage('agentId=agent-7');

    ctrl('n');

    expect(routerReplace).toHaveBeenLastCalledWith('/chat/?agentId=agent-7');
  });

  it('puts the new conversation id in the address bar once the server assigns it', async () => {
    renderPage();
    await act(() => new Promise<void>((r) => requestAnimationFrame(() => r())));

    let slotId = '';
    act(() => {
      const store = useChatStore.getState();
      slotId = store.createSlot(null);
      store.setActiveSlot(slotId);
    });
    act(() => useChatStore.getState().resolveSlotConvId(slotId, 'conv-new'));

    expect(window.location.pathname + window.location.search).toBe('/chat/?conversationId=conv-new');
  });
});

describe('Chat page — agent chats', () => {
  it("shows the agent's name and who built it, and warns about tools that no longer exist", async () => {
    getAgent.mockResolvedValue({
      agent: {
        _key: 'agent-7',
        name: 'Support Bot',
        createdBy: 'creator-1',
        toolsets: [{ name: 'jira', tools: [{ name: 'jira.search', fullName: 'jira.search', deprecated: true }] }],
      },
      toolFullNames: ['jira.search'],
    });
    getUsersByIds.mockResolvedValue([{ name: 'Priya Shah', email: 'priya@acme.test' }]);

    renderPage('agentId=agent-7');

    expect(await screen.findByText('Support Bot')).toBeTruthy();
    expect(await screen.findByText('Priya Shah')).toBeTruthy();
    expect(getUsersByIds).toHaveBeenCalledWith(['creator-1']);
    expect(toastError).toHaveBeenCalledWith(
      'This agent has tools that are no longer available. Open the Agent Builder to remove them.',
      expect.objectContaining({ action: expect.objectContaining({ label: 'Open Agent Builder' }) }),
    );
    const { action } = toastError.mock.calls[0][1] as { action: { onClick: () => void } };
    action.onClick();
    expect(routerPush).toHaveBeenCalledWith('/agents/edit?agentKey=agent-7');
    expect(fetchModelsForContext).toHaveBeenCalledWith(expect.any(String), { force: true });
  });

  it('opens an agent conversation through the agent API and never offers sharing', async () => {
    getAgent.mockResolvedValue({ agent: { name: 'Support Bot', toolsets: [] }, toolFullNames: [] });
    fetchAgentConversation.mockResolvedValue(conversationDetail({ isOwner: true }));

    renderPage('agentId=agent-7&conversationId=conv-1');

    expect(await screen.findByText('You get 25 days a year.')).toBeTruthy();
    expect(fetchAgentConversation).toHaveBeenCalledWith('agent-7', 'conv-1');
    expect(fetchConversation).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Share' })).toBeNull();
  });

  it('still opens the chat when the agent cannot be loaded', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    getAgent.mockRejectedValue(new Error('Request failed with status code 404'));

    renderPage('agentId=agent-7');

    await waitFor(() => expect(fetchModelsForContext).toHaveBeenCalled());
    expect(screen.getByRole('textbox', { name: 'Message composer' })).toBeTruthy();
    expect(useChatStore.getState().agentContextDisplayName).toBeNull();
  });
});

describe('Chat page — projects and previews', () => {
  it("shows the project's name on a project's new chat and links to its settings", async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as Partial<ReturnType<typeof useFeatureFlagsStore.getState>>);
    getProject.mockResolvedValue({ id: 'p-1', name: 'Launch plan', color: '#f00' });

    renderPage('projectId=p-1');

    fireEvent.click(await screen.findByText('Launch plan'));
    expect(routerPush).toHaveBeenCalledWith('/projects/?projectId=p-1');
    expect(useChatStore.getState().activeProjectId).toBe('p-1');
  });

  it('says so when the project cannot be loaded', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as Partial<ReturnType<typeof useFeatureFlagsStore.getState>>);
    getProject.mockRejectedValue(new Error('boom'));

    renderPage('projectId=p-1');

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Failed to load this project'));
  });

  it('ignores the project in the address bar when projects are turned off', () => {
    renderPage('projectId=p-1');
    expect(getProject).not.toHaveBeenCalled();
    expect(screen.getByText('What do you want to explore today?')).toBeTruthy();
  });

  it('opens a cited file beside the chat and closes it again', () => {
    renderPage();
    act(() =>
      useChatStore.setState({
        previewFile: { id: 'f1', name: 'handbook.pdf', url: '', type: 'application/pdf', size: 1 },
        previewMode: 'sidebar',
      } as Partial<ReturnType<typeof useChatStore.getState>>),
    );

    expect(screen.getByRole('complementary', { name: 'Preview panel' }).textContent).toContain('handbook.pdf');
    expect(screen.getByRole('separator', { name: 'Resize chat and preview panels' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Close preview' }));
    expect(screen.queryByRole('complementary', { name: 'Preview panel' })).toBeNull();
  });

  it('shows the fullscreen preview when asked for it', () => {
    renderPage();
    act(() =>
      useChatStore.setState({
        previewFile: { id: 'f1', name: 'handbook.pdf', url: '', type: 'application/pdf', size: 1 },
        previewMode: 'fullscreen',
      } as Partial<ReturnType<typeof useChatStore.getState>>),
    );
    expect(screen.getByLabelText('Fullscreen preview').textContent).toBe('handbook.pdf');
  });
});
