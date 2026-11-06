/**
 * The Projects page's rail never blanks its list: a skeleton only while no project is listed, nothing asked again
 * within 30 s, and a reload (a version bump, or a stale list) that swaps the rows in place and keeps them if it fails.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

// `t` stays the same function across renders, as react-i18next's does: the rail lists it as a hook dependency.
const h = vi.hoisted(() => ({
  list: vi.fn(),
  t: (key: string) => key,
  userId: 'user-a' as string | null,
  // Who is signed in changes through the auth store; the rail's sign-out watch listens to it.
  authListeners: new Set<() => void>(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: h.t, i18n: { language: 'en-US' } }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/sidebar', () => ({
  SidebarBase: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ICON_SIZE_DEFAULT: 16,
}));
vi.mock('@/app/components/ui/list-skeleton', () => ({ ListSkeleton: () => <i data-testid="skeleton" /> }));
vi.mock('@/config', () => ({
  ChatSidebarFooter: () => null,
  useAuthStore: {
    subscribe: (listener: () => void) => {
      h.authListeners.add(listener);
      return () => h.authListeners.delete(listener);
    },
  },
}));
vi.mock('@/chat/sidebar/header', () => ({ ChatSidebarHeader: () => null }));
vi.mock('@/chat/sidebar/chat-section-header', () => ({ ChatSectionHeader: () => null }));
vi.mock('@/chat/sidebar/sidebar-item', () => ({
  SidebarItem: ({ label, href }: { label: React.ReactNode; href?: string }) => <a href={href}>{label}</a>,
}));
vi.mock('@/chat/sidebar/dialogs', () => ({ CreateProjectDialog: () => null }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: { list: h.list } }));
vi.mock('@/chat/utils/current-user-id', () => ({ getCurrentUserId: () => h.userId }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/lib/store/mobile-sidebar-store', () => ({
  useMobileSidebarStore: (selector: (s: { close: () => void; isOpen: boolean }) => unknown) =>
    selector({ close: vi.fn(), isOpen: false }),
}));

const { useChatStore } = await import('@/chat/store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { ProjectsSidebar } = await import('../sidebar');

const project = (id: string) => ({ _id: id, name: `Project ${id}`, isPinned: false, lastActivityAt: 1, color: '' }) as never;
const renderRail = () =>
  render(
    <Theme>
      <ProjectsSidebar />
    </Theme>,
  );
const names = () => screen.queryAllByRole('link').map((a) => a.textContent).filter((t) => t?.startsWith('Project '));

beforeEach(() => {
  vi.clearAllMocks();
  h.userId = 'user-a';
  useChatStore.getState().reset();
  useFeatureFlagsStore.setState({ flags: { ENABLE_PROJECTS: true } } as never);
  h.list.mockResolvedValue({ projects: [project('a'), project('b')] });
});
afterEach(() => cleanup());

async function visitOnce() {
  const first = renderRail();
  await waitFor(() => expect(names()).toEqual(['Project a', 'Project b']));
  first.unmount();
}

describe('the Projects rail keeps its list', () => {
  it('mounted again within 30 s: the same projects at once, no skeleton, no request', async () => {
    await visitOnce();

    renderRail();

    expect(names()).toEqual(['Project a', 'Project b']);
    expect(screen.queryByTestId('skeleton')).toBeNull();
    expect(h.list).toHaveBeenCalledTimes(1);
  });

  it('a version bump reloads in place, and a failed reload keeps the rows', async () => {
    renderRail();
    await waitFor(() => expect(names()).toEqual(['Project a', 'Project b']));
    h.list.mockRejectedValue(new Error('500'));

    act(() => useChatStore.getState().bumpProjectsVersion());

    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(2));
    await act(async () => {});
    expect(screen.queryByTestId('skeleton')).toBeNull();
    expect(names()).toEqual(['Project a', 'Project b']);
    expect(screen.queryByText('chat.projects.failedToLoad')).toBeNull();
  });

  it('an empty list that loaded is shown as it is: no skeleton and no request the second time', async () => {
    h.list.mockResolvedValue({ projects: [] });
    const first = renderRail();
    await waitFor(() => expect(screen.getByText('projects.emptyState')).toBeTruthy());
    first.unmount();

    renderRail();

    expect(screen.getByText('projects.emptyState')).toBeTruthy();
    expect(screen.queryByTestId('skeleton')).toBeNull();
    expect(h.list).toHaveBeenCalledTimes(1);
  });

  it('after a sign-out (the lists cleared) the next person waits on a skeleton again', async () => {
    await visitOnce();
    act(() => useChatStore.getState().clearSidebarLists());
    h.list.mockReturnValue(new Promise(() => {}));

    renderRail();

    expect(names()).toEqual([]);
    await waitFor(() => expect(screen.getAllByTestId('skeleton').length).toBeGreaterThan(0));
  });

  it('a reload behind the list on screen fails quietly; only the first load may say it failed', async () => {
    renderRail();
    await waitFor(() => expect(names()).toEqual(['Project a', 'Project b']));
    expect(h.list).toHaveBeenLastCalledWith(expect.anything(), { suppressErrorToast: false });

    act(() => useChatStore.getState().bumpProjectsVersion());

    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(2));
    expect(h.list).toHaveBeenLastCalledWith(expect.anything(), { suppressErrorToast: true });
  });

  it("a load that lands after someone else signed in is dropped: the next person never sees these projects", async () => {
    let answer: (value: unknown) => void = () => {};
    h.list.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const first = renderRail();
    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(1));
    first.unmount();

    // Signed out and someone else signed in (the desktop app does not reload): the lists were cleared.
    h.userId = 'user-b';
    act(() => useChatStore.getState().clearSidebarLists());
    await act(async () => answer({ projects: [project('secret')] }));

    expect(useChatStore.getState().projects).toEqual([]);
    expect(useChatStore.getState().projectsLoadedAt).toBeNull();
  });

  it('sign-out clears the projects, in a session that never opened a chat list', async () => {
    await visitOnce();
    expect(useChatStore.getState().projects).toHaveLength(2);

    // Someone else signs in, with no page load between (the desktop app).
    h.userId = 'user-b';
    act(() => h.authListeners.forEach((listener) => listener()));

    expect(useChatStore.getState().projects).toEqual([]);
    expect(useChatStore.getState().projectsLoadedAt).toBeNull();
  });

  it("the last person's first load landing late leaves the next person's skeleton on", async () => {
    let answerA: (value: unknown) => void = () => {};
    h.list.mockReturnValueOnce(new Promise((resolve) => (answerA = resolve)));
    const first = renderRail();
    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(1));
    first.unmount();

    // Someone else signs in (the desktop app does not reload): the lists were cleared, and their rail loads.
    h.userId = 'user-b';
    act(() => useChatStore.getState().clearSidebarLists());
    let answerB: (value: unknown) => void = () => {};
    h.list.mockReturnValueOnce(new Promise((resolve) => (answerB = resolve)));
    renderRail();
    await waitFor(() => expect(h.list).toHaveBeenCalledTimes(2));
    await act(async () => answerA({ projects: [project('secret')] }));

    expect(useChatStore.getState().isProjectsLoading).toBe(true);
    expect(screen.queryByText('projects.emptyState')).toBeNull();

    await act(async () => answerB({ projects: [project('b')] }));
    expect(useChatStore.getState().isProjectsLoading).toBe(false);
    expect(names()).toEqual(['Project b']);
  });

  it('a sign-out with nobody signing in leaves no skeleton on when the last load lands', async () => {
    let answer: (value: unknown) => void = () => {};
    h.list.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const first = renderRail();
    await waitFor(() => expect(useChatStore.getState().isProjectsLoading).toBe(true));
    first.unmount();

    h.userId = null;
    act(() => useChatStore.getState().clearSidebarLists());
    await act(async () => answer({ projects: [project('a')] }));

    expect(useChatStore.getState().isProjectsLoading).toBe(false);
    expect(useChatStore.getState().projects).toEqual([]);
  });

  it('the first load shows the skeleton while nothing is listed', async () => {
    h.list.mockReturnValue(new Promise(() => {}));

    renderRail();

    await waitFor(() => expect(screen.getAllByTestId('skeleton').length).toBeGreaterThan(0));
  });
});
