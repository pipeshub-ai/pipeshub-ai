/**
 * WP-26 GIT-FLOW in the composer: the + menu's "Work on a repository" row, the picker, the chip before the first
 * message, and the repository rows of an engine run's timeline.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

// Radix's Select measures its trigger; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const push = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const api = vi.hoisted(() => ({
  connections: vi.fn(),
  repositories: vi.fn(),
  branches: vi.fn(),
  put: vi.fn(),
  remove: vi.fn(),
  get: vi.fn(),
}));
vi.mock('@/chat/repository-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/chat/repository-api')>();
  return { ...actual, RepositoryApi: api };
});

import { PlusMenuContent, type PlusMenuCapabilities } from '../plus-menu-button';
import { RepositoryPickerDialog, CONNECT_GITHUB_HREF } from '../repository-picker-dialog';
import { RepositoryChip } from '../repository-chip';
import { EngineRepoRow } from '../../message-area/engine-repo-row';
import { useRepositoryStore } from '@/chat/repository-store';

const REPOS = [
  { fullName: 'acme/widgets', url: 'https://github.com/acme/widgets', private: true, defaultBranch: 'main', updatedAt: new Date(Date.now() - 2 * 3600_000).toISOString() },
  { fullName: 'acme/widgets-docs', url: 'https://github.com/acme/widgets-docs', private: false, defaultBranch: 'main', updatedAt: new Date(Date.now() - 86400_000).toISOString() },
  { fullName: 'octo-dev/sandbox', url: 'https://github.com/octo-dev/sandbox', private: false, defaultBranch: 'trunk', updatedAt: new Date(Date.now() - 8 * 86400_000).toISOString() },
];

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  push.mockReset();
  useRepositoryStore.setState({ views: {}, pending: null, pickerOpen: false, engines: null });
  api.connections.mockResolvedValue([{ instanceId: 'gh-1', name: 'GitHub' }]);
  api.repositories.mockImplementation(async (_id: string, search: string) => ({
    repositories: REPOS.filter((r) => r.fullName.includes(search.trim())),
    page: 1,
    hasMore: false,
  }));
  api.branches.mockResolvedValue([
    { name: 'main', default: true },
    { name: 'release/1.x', default: false },
  ]);
});

afterEach(() => cleanup());

function menu(repository: PlusMenuCapabilities['repository']) {
  render(
    <Theme>
      <PlusMenuContent
        onAttachFiles={vi.fn()}
        internalSearch
        webSearch
        onToggleInternalSearch={vi.fn()}
        onToggleWebSearch={vi.fn()}
        repository={repository}
      />
    </Theme>,
  );
}

describe('the + menu row', () => {
  it('opens the picker when it can be used', () => {
    const onSelect = vi.fn();
    menu({ reason: null, onSelect });
    const row = screen.getByTestId('plus-menu-repository');
    expect(row.textContent).toContain('Work on a repository');
    expect(row.textContent).toContain('Clone a GitHub repository into this chat');
    expect(row.getAttribute('aria-disabled')).toBeNull();
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it.each([
    'Needs a coding engine. Pick one first.',
    "Your organization hasn't turned on coding engines.",
    'This chat already works on acme/widgets. Start a new chat for another repository.',
  ])('is greyed with its reason as the subtitle: %s', (reason) => {
    const onSelect = vi.fn();
    menu({ reason, onSelect });
    const row = screen.getByTestId('plus-menu-repository');
    expect(row.getAttribute('aria-disabled')).toBe('true');
    expect(screen.getByTestId('plus-menu-repository-subtitle').textContent).toBe(reason);
    fireEvent.click(row);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('is absent where the chat is not on the harness or not the viewer\'s', () => {
    menu(undefined);
    expect(screen.queryByTestId('plus-menu-repository')).toBeNull();
  });
});

function picker(session: { agentId?: string; sessionId: string } | null = null) {
  useRepositoryStore.setState({ pickerOpen: true });
  render(
    <Theme>
      <RepositoryPickerDialog session={session} agentId={null} />
    </Theme>,
  );
}

describe('the picker', () => {
  it('lists the repositories with a private tag and when each was updated, and filters by search', async () => {
    picker();
    await waitFor(() => expect(screen.getAllByTestId('repo-picker-row')).toHaveLength(3));
    const first = screen.getAllByTestId('repo-picker-row')[0];
    expect(first.textContent).toContain('acme/widgets');
    expect(first.textContent).toContain('private');
    expect(first.textContent).toMatch(/updated .*2 hours ago/);
    expect(screen.getAllByTestId('repo-picker-private')).toHaveLength(1);

    fireEvent.change(screen.getByTestId('repo-picker-search').querySelector('input') ?? screen.getByTestId('repo-picker-search'), {
      target: { value: 'widgets' },
    });
    await waitFor(() => expect(screen.getAllByTestId('repo-picker-row')).toHaveLength(2));
    expect(api.repositories).toHaveBeenLastCalledWith('gh-1', 'widgets', 1);
  });

  it('marks the default branch and fills the note with the repository and branch', async () => {
    picker();
    await waitFor(() => expect(screen.getAllByTestId('repo-picker-row').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('repo-picker-row')[0]);
    await waitFor(() => expect(screen.getByTestId('repo-picker-branch').textContent).toBe('main (default)'));
    const note = screen.getByTestId('repo-picker-note').textContent ?? '';
    expect(note).toContain("PipesHub copies acme/widgets into this chat's workspace before the engine starts.");
    expect(note).toContain("It can't push to main, tags or other branches.");
    expect(note).toContain('A pull request opens into main only when you ask for one.');
  });

  it('a new chat holds the choice for its first message', async () => {
    picker(null);
    await waitFor(() => expect(screen.getAllByTestId('repo-picker-row').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('repo-picker-row')[0]);
    await waitFor(() => expect(screen.getByTestId('repo-picker-branch').textContent).toBe('main (default)'));
    fireEvent.click(screen.getByTestId('repo-picker-use'));
    expect(useRepositoryStore.getState().pending).toEqual({
      url: 'https://github.com/acme/widgets',
      baseRef: 'main',
      toolsetInstanceId: 'gh-1',
      agentId: null,
      fullName: 'acme/widgets',
    });
    expect(useRepositoryStore.getState().pickerOpen).toBe(false);
    expect(api.put).not.toHaveBeenCalled();
  });

  it('a saved chat saves it; a refusal is a plain line in the dialog', async () => {
    api.put.mockResolvedValueOnce({ refused: 'HTTP_AGENT_HARNESS_REPOSITORY_NEEDS_ENGINE' });
    picker({ sessionId: 's1' });
    await waitFor(() => expect(screen.getAllByTestId('repo-picker-row').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('repo-picker-row')[0]);
    await waitFor(() => expect(screen.getByTestId('repo-picker-branch').textContent).toBe('main (default)'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('repo-picker-use'));
    });
    expect(api.put).toHaveBeenCalledWith({ sessionId: 's1' }, { url: 'https://github.com/acme/widgets', baseRef: 'main', toolsetInstanceId: 'gh-1' });
    expect(screen.getByTestId('repo-picker-refusal').textContent).toMatch(/A repository needs a coding engine/);
    expect(useRepositoryStore.getState().pickerOpen).toBe(true);

    const view = { url: 'https://github.com/acme/widgets', baseRef: 'main', sessionBranch: 'pipeshub/session-1', cloned: false };
    api.put.mockResolvedValueOnce({ view });
    await act(async () => {
      fireEvent.click(screen.getByTestId('repo-picker-use'));
    });
    expect(useRepositoryStore.getState().views.s1).toEqual(view);
    expect(useRepositoryStore.getState().pickerOpen).toBe(false);
  });

  it('without a signed-in GitHub connection, asks to connect GitHub first', async () => {
    api.connections.mockResolvedValueOnce([]);
    picker();
    await waitFor(() => expect(screen.getByTestId('repo-picker-connect')).toBeTruthy());
    expect(screen.getByTestId('repo-picker-connect').textContent).toContain('Connect GitHub first.');
    fireEvent.click(screen.getByTestId('repo-picker-connect-github'));
    expect(push).toHaveBeenCalledWith(CONNECT_GITHUB_HREF);
    expect(api.repositories).not.toHaveBeenCalled();
  });
});

describe('the chip before the first message', () => {
  const renderChip = (session: { sessionId: string } | null) =>
    render(
      <Theme>
        <RepositoryChip session={session} agentId={null} />
      </Theme>,
    );

  it("shows a new chat's held repository and its branch; × drops it", () => {
    useRepositoryStore.setState({
      pending: { url: 'https://github.com/acme/widgets', baseRef: 'main', toolsetInstanceId: 'gh-1', agentId: null, fullName: 'acme/widgets' },
    });
    renderChip(null);
    expect(screen.getByTestId('repository-chip').textContent).toContain('acme/widgets from main');
    fireEvent.click(screen.getByRole('button', { name: "Don't use this repository" }));
    expect(useRepositoryStore.getState().pending).toBeNull();
  });

  it("is only the held chat's agent's", () => {
    useRepositoryStore.setState({
      pending: { url: 'https://github.com/acme/widgets', toolsetInstanceId: 'gh-1', agentId: 'agent-1', fullName: 'acme/widgets' },
    });
    renderChip(null);
    expect(screen.queryByTestId('repository-chip')).toBeNull();
  });

  it('shows a saved repository until the engine copied it, and × deletes it on the server', async () => {
    useRepositoryStore.setState({
      views: { s1: { url: 'https://github.com/acme/widgets', baseRef: 'release/1.x', sessionBranch: 'pipeshub/session-1', cloned: false } },
    });
    api.remove.mockResolvedValueOnce({ ok: true });
    renderChip({ sessionId: 's1' });
    expect(screen.getByTestId('repository-chip').textContent).toContain('acme/widgets from release/1.x');
    await act(async () => {
      fireEvent.click(screen.getByTestId('repository-chip-clear'));
    });
    expect(api.remove).toHaveBeenCalledWith({ sessionId: 's1' });
    expect(useRepositoryStore.getState().views.s1).toBeNull();
  });

  it('is gone once the copy was made', () => {
    useRepositoryStore.setState({
      views: { s1: { url: 'https://github.com/acme/widgets', baseRef: 'main', sessionBranch: 'pipeshub/session-1', cloned: true } },
    });
    renderChip({ sessionId: 's1' });
    expect(screen.queryByTestId('repository-chip')).toBeNull();
  });
});

describe('repository rows in the timeline', () => {
  const row = (repo: React.ComponentProps<typeof EngineRepoRow>['repo']) =>
    render(
      <Theme>
        <EngineRepoRow repo={repo} />
      </Theme>,
    );

  it('copied and pushed read plainly', () => {
    row({ action: 'copied', repository: 'acme/widgets', branch: 'pipeshub/session-4f2c' });
    expect(screen.getByTestId('engine-repo').textContent).toBe('Copied acme/widgets · branch pipeshub/session-4f2c');
    cleanup();
    row({ action: 'pushed', branch: 'pipeshub/session-4f2c' });
    expect(screen.getByTestId('engine-repo').textContent).toBe('Pushed to pipeshub/session-4f2c');
  });

  it.each([
    ['otherBranch', 'this chat can only push its own branch'],
    ['overwrite', 'it can only add to its branch, never overwrite it'],
    ['delete', "it can't delete branches"],
    ['tags', "it can't push tags"],
    ['other', "the push didn't go through"],
  ] as const)('a refused push is one row with the reason (%s)', (reason, words) => {
    row({ action: 'pushRefused', target: 'main', reason });
    expect(screen.getByTestId('engine-repo').textContent).toBe(`Couldn't push to main · ${words}`);
  });

  it('an opened pull request links to GitHub, only a github.com link', () => {
    row({ action: 'prOpened', repository: 'acme/widgets', number: 7, url: 'https://github.com/acme/widgets/pull/7' });
    expect(screen.getByTestId('engine-repo').textContent).toContain('Opened pull request #7 in acme/widgets');
    expect(screen.getByTestId('engine-repo-pr-link').getAttribute('href')).toBe('https://github.com/acme/widgets/pull/7');
    cleanup();
    row({ action: 'prOpened', repository: 'acme/widgets', number: 7, url: 'https://evil.example/acme/widgets/pull/7' });
    expect(screen.queryByTestId('engine-repo-pr-link')).toBeNull();
    cleanup();
    row({ action: 'prFailed' });
    expect(screen.getByTestId('engine-repo').textContent).toBe("Couldn't open the pull request");
  });
});
