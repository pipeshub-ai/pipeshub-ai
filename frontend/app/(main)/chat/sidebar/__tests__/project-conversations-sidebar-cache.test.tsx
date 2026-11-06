/**
 * A project's rail shown again lists that project's chats at once: no skeleton, and no request while they are
 * fresh. The rows are the signed-in person's only.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

const h = vi.hoisted(() => ({
  t: (key: string) => key,
  get: vi.fn(),
  listConversations: vi.fn(),
  userId: { current: 'user-a' as string | null },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/chat/',
  useSearchParams: () => new URLSearchParams('projectId=p1'),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: h.t, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/chat-star-icon', () => ({ ChatStarIcon: () => null }));
vi.mock('@/app/components/sidebar', () => ({
  SidebarBase: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ICON_SIZE_DEFAULT: 16,
}));
vi.mock('@/config', async () => {
  const ext = await vi.importActual<typeof import('@/app/(main)/chat/chat-extension')>('@/app/(main)/chat/chat-extension');
  return { ChatSidebarFooter: () => null, chatSlots: ext.defaultChatSlots, chatExtension: ext.defaultChatExtension };
});
vi.mock('@/chat/utils/current-user-id', () => ({ getCurrentUserId: () => h.userId.current }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/lib/store/mobile-sidebar-store', () => ({
  useMobileSidebarStore: (selector: (s: { close: () => void; isOpen: boolean }) => unknown) =>
    selector({ close: vi.fn(), isOpen: false }),
}));
vi.mock('@/chat/project-api', async (importActual) => ({
  ...(await importActual<typeof import('@/chat/project-api')>()),
  ProjectApi: { get: h.get, listConversations: h.listConversations },
}));
vi.mock('../header', () => ({ ChatSidebarHeader: () => null }));
vi.mock('../sidebar-item', () => ({ SidebarItem: ({ label }: { label: React.ReactNode }) => <span>{label}</span> }));
vi.mock('../chat-section-element', () => ({
  ChatSectionElement: ({ conversation }: { conversation: { title: string } }) => <a href="#">{conversation.title}</a>,
  ChatItemSkeleton: () => <i data-testid="skeleton" />,
  GeneratingTitleItem: () => null,
}));

const { useChatStore } = await import('@/chat/store');
const { ProjectConversationsSidebar } = await import('../project-conversations-sidebar');
const { SIDEBAR_LISTS_STALE_MS } = await import('../../constants');
const { holdTitle, resetHeldTitlesForTests } = await import('@/chat/utils/held-titles');

const row = (id: string) => ({
  _id: id,
  title: `Chat ${id}`,
  sessionType: 'chat',
  userId: 'user-a',
  initiator: 'user-a',
  isShared: false,
  isArchived: false,
  status: 'complete',
  lastActivityAt: Date.now(),
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
});

const listed = (ids: string[]) => ({
  conversations: ids.map(row),
  pagination: { page: 1, limit: 10, totalCount: ids.length, totalPages: 1 },
});

const renderRail = (projectId = 'p1') =>
  render(
    <Theme>
      <ProjectConversationsSidebar projectId={projectId} />
    </Theme>,
  );

// The rail's chat rows; the project's name above them links to the project.
const titles = () =>
  screen
    .queryAllByRole('link')
    .filter((a) => a.getAttribute('href') === '#')
    .map((a) => a.textContent);

let visit = 0;

beforeEach(() => {
  vi.clearAllMocks();
  // The rail's cache is the module's and keyed by person: a new person per test starts each one with nothing cached.
  visit += 1;
  h.userId.current = `user-${visit}`;
  useChatStore.getState().reset();
  h.get.mockResolvedValue({ _id: 'p1', name: 'Launch' });
  h.listConversations.mockResolvedValue(listed(['a', 'b']));
});
afterEach(() => cleanup());

/** A first visit that loads, so the next mount has the rail to show. */
async function visitOnce(projectId = 'p1') {
  const first = renderRail(projectId);
  await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
  first.unmount();
}

describe('the project rail shown again', () => {
  it('lists the same chats at once, with no skeleton and no request, within 30 s', async () => {
    await visitOnce();

    renderRail();

    expect(titles()).toEqual(['Chat a', 'Chat b']);
    expect(screen.queryByTestId('skeleton')).toBeNull();
    expect(screen.getByText('Launch')).toBeTruthy();
    expect(h.listConversations).toHaveBeenCalledTimes(1);
  });

  it('once stale, loads again with the rows on screen', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date'] });
    try {
      await visitOnce();
      vi.setSystemTime(Date.now() + SIDEBAR_LISTS_STALE_MS + 1);
      h.listConversations.mockResolvedValue(listed(['c']));

      renderRail();

      expect(titles()).toEqual(['Chat a', 'Chat b']);
      expect(screen.queryByTestId('skeleton')).toBeNull();
      await waitFor(() => expect(titles()).toEqual(['Chat c']));
    } finally {
      vi.useRealTimers();
    }
  });

  it('a version bump loads again in place', async () => {
    renderRail();
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    h.listConversations.mockResolvedValue(listed(['b']));

    act(() => useChatStore.getState().bumpConversationsVersion());

    expect(screen.queryByTestId('skeleton')).toBeNull();
    await waitFor(() => expect(titles()).toEqual(['Chat b']));
    expect(h.listConversations).toHaveBeenCalledTimes(2);
  });

  it("someone else signed in sees none of the last person's rows", async () => {
    await visitOnce();
    h.userId.current = 'someone-else';
    h.listConversations.mockReturnValue(new Promise(() => {}));

    renderRail();

    expect(titles()).toEqual([]);
    expect(screen.getAllByTestId('skeleton').length).toBeGreaterThan(0);
  });

  it("a load that lands after someone else signed in is neither shown nor kept", async () => {
    let answer: (value: unknown) => void = () => {};
    h.listConversations.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const first = renderRail();
    await waitFor(() => expect(h.listConversations).toHaveBeenCalledTimes(1));
    first.unmount();
    h.userId.current = 'next-person';

    await act(async () => answer(listed(['a'])));
    h.listConversations.mockReturnValue(new Promise(() => {}));
    renderRail();

    expect(titles()).toEqual([]);
  });

  it("a late answer for the project left behind never shows under the next one", async () => {
    let answerP1: (value: unknown) => void = () => {};
    h.listConversations.mockReturnValueOnce(new Promise((resolve) => (answerP1 = resolve)));
    const view = renderRail('p1');
    await waitFor(() => expect(h.listConversations).toHaveBeenCalledTimes(1));
    h.listConversations.mockResolvedValueOnce(listed(['x']));
    view.rerender(
      <Theme>
        <ProjectConversationsSidebar projectId="p2" />
      </Theme>,
    );
    await waitFor(() => expect(titles()).toEqual(['Chat x']));

    await act(async () => answerP1(listed(['a', 'b'])));

    expect(titles()).toEqual(['Chat x']);
  });

  it('another project in the same rail never shows the last project’s rows', async () => {
    const view = renderRail('p1');
    await waitFor(() => expect(titles()).toEqual(['Chat a', 'Chat b']));
    h.listConversations.mockReturnValue(new Promise(() => {}));

    view.rerender(
      <Theme>
        <ProjectConversationsSidebar projectId="p2" />
      </Theme>,
    );

    expect(titles()).toEqual([]);
    expect(screen.getAllByTestId('skeleton').length).toBeGreaterThan(0);
  });
});

describe('two loads out at once', () => {
  /** The first load waits for the test; a version bump then starts a second one, answered at once. */
  async function twoLoads(newer: unknown) {
    let answerOlder: (value: unknown) => void = () => {};
    let failOlder: (error: unknown) => void = () => {};
    h.listConversations.mockReturnValueOnce(
      new Promise((resolve, reject) => {
        answerOlder = resolve;
        failOlder = reject;
      }),
    );
    const view = renderRail();
    await waitFor(() => expect(h.listConversations).toHaveBeenCalledTimes(1));
    h.listConversations.mockReturnValueOnce(newer);
    act(() => useChatStore.getState().bumpConversationsVersion());
    await waitFor(() => expect(h.listConversations).toHaveBeenCalledTimes(2));
    return { view, answerOlder, failOlder };
  }

  it('the older one landing last is dropped: the newer rows stay, and are the ones shown again', async () => {
    const { view, answerOlder } = await twoLoads(Promise.resolve(listed(['new'])));
    await waitFor(() => expect(titles()).toEqual(['Chat new']));

    await act(async () => answerOlder(listed(['old-a', 'old-b'])));

    expect(titles()).toEqual(['Chat new']);
    view.unmount();
    renderRail();
    expect(titles()).toEqual(['Chat new']);
    expect(h.listConversations).toHaveBeenCalledTimes(2);
  });

  it('the older one failing last leaves the newer rows, with no error', async () => {
    const { failOlder } = await twoLoads(Promise.resolve(listed(['new'])));
    await waitFor(() => expect(titles()).toEqual(['Chat new']));

    await act(async () => failOlder(new Error('502')));

    expect(titles()).toEqual(['Chat new']);
    expect(screen.queryByText('chat.failedToLoad')).toBeNull();
  });

  it('the older one landing first shows until the newer one lands', async () => {
    let answerNewer: (value: unknown) => void = () => {};
    const { answerOlder } = await twoLoads(new Promise((resolve) => (answerNewer = resolve)));

    await act(async () => answerOlder(listed(['old'])));
    expect(titles()).toEqual(['Chat old']);

    await act(async () => answerNewer(listed(['new'])));
    expect(titles()).toEqual(['Chat new']);
  });
});

describe('a rename and the project rail shown again with stale rows', () => {
  /** A list the test answers when it wants to. */
  const heldList = () => {
    let answer: (ids: string[]) => void = () => {};
    const promise = new Promise((resolve) => {
      answer = (ids) => resolve(listed(ids));
    });
    return { promise, answer };
  };
  const renamed = (id: string, title: string) => ({ ...listed([id]), conversations: [{ ...row(id), title }] });

  beforeEach(() => resetHeldTitlesForTests());

  it('a read answered while the rename is out keeps the new title', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date'] });
    try {
      await visitOnce();
      vi.setSystemTime(Date.now() + SIDEBAR_LISTS_STALE_MS + 1);
      holdTitle('a', 'Budget');
      const stale = heldList();
      h.listConversations.mockReturnValue(stale.promise);

      renderRail();
      await act(async () => stale.answer(['a', 'b']));

      await waitFor(() => expect(titles()).toEqual(['Budget', 'Chat b']));
    } finally {
      vi.useRealTimers();
    }
  });

  it('a read that left before the save and lands after the reload the save made is dropped: the new title stays', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date'] });
    try {
      await visitOnce();
      vi.setSystemTime(Date.now() + SIDEBAR_LISTS_STALE_MS + 1);
      // Shown again with stale rows: a read leaves, from a server without the rename yet.
      const stale = heldList();
      h.listConversations.mockReturnValue(stale.promise);
      renderRail();
      await waitFor(() => expect(h.listConversations).toHaveBeenCalledTimes(2));

      // The rename is saved, and the save reloads the project's rail, which reads the new title.
      holdTitle('a', 'Budget').saved();
      h.listConversations.mockResolvedValue(renamed('a', 'Budget'));
      act(() => useChatStore.getState().bumpProjectsVersion());
      await waitFor(() => expect(titles()).toEqual(['Budget']));

      // The first read lands last, with the old title and rows: older than the rows shown, so it is dropped.
      await act(async () => stale.answer(['a', 'b']));

      expect(titles()).toEqual(['Budget']);
    } finally {
      vi.useRealTimers();
    }
  });
});
