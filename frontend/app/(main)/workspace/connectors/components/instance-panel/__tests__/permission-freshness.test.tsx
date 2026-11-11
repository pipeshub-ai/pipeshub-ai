import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, within } from '@testing-library/react';
import { SWRConfig } from 'swr';
import '@/lib/__tests__/test-i18n';

const getStatus = vi.fn();
vi.mock('../../../api', () => ({
  ConnectorsApi: { getPermissionStatus: (...a: unknown[]) => getStatus(...a) },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { PermissionFreshness } from '../permission-freshness';
import type { PermissionStatus } from '../../../types';
import { installDomShims, renderInTheme, makeInstance, signInAs, signOut } from '../../../__tests__/fixtures';

const status = (over: Partial<PermissionStatus> = {}): PermissionStatus => ({
  asOf: '2026-11-11T12:00:00+00:00',
  state: 'healthy',
  reachWithinMinutes: 360,
  serving: { current: true, served: true },
  lastPermissionSync: { finishedAt: '2026-11-11T11:02:09+00:00', outcome: 'succeeded' },
  pending: { roots: 0, unreadable: false, oldestChangesAgo: null },
  coverage: { records: 120, stale: 0 },
  attention: [],
  warnings: [],
  ...over,
});

function renderCard(props: { onSyncNow?: () => void; syncBusy?: boolean; scope?: 'team' | 'personal' } = {}) {
  const { scope = 'team', ...rest } = props;
  return renderInTheme(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <PermissionFreshness instance={makeInstance({ scope })} {...rest} />
    </SWRConfig>
  );
}

beforeEach(() => {
  installDomShims();
  signInAs('admin');
  getStatus.mockReset().mockResolvedValue(status());
});
afterEach(() => {
  cleanup();
  signOut();
});

describe('PermissionFreshness: who sees it', () => {
  it('renders nothing, and asks nothing, for a member', () => {
    signInAs('member');
    renderCard();
    expect(screen.queryByTestId('permission-freshness')).toBeNull();
    expect(getStatus).not.toHaveBeenCalled();
  });

  it('renders nothing for a personal connector', () => {
    renderCard({ scope: 'personal' });
    expect(screen.queryByTestId('permission-freshness')).toBeNull();
    expect(getStatus).not.toHaveBeenCalled();
  });
});

describe('PermissionFreshness: loading, error, empty', () => {
  it('announces loading, then shows the card', async () => {
    renderCard();
    expect(screen.getByRole('status').textContent).toBe('Loading access details…');
    await screen.findByText('Nothing is waiting.');
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('says it could not load, announced, and the retry loads it', async () => {
    getStatus.mockRejectedValueOnce(new Error('boom'));
    renderCard();
    expect((await screen.findByRole('alert')).textContent).toContain('We could not load access details.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByText('Nothing is waiting.');
    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it('healthy and empty: a labelled region, the reach line, and calm "nothing" rows', async () => {
    renderCard();
    const region = await screen.findByRole('region', { name: 'Access' });
    expect(within(region).getByText(/Jira \(Engineering\) reach PipesHub within 6 hours\./)).toBeTruthy();
    expect(within(region).getByText(/Last refreshed/)).toBeTruthy();
    expect(within(region).getByText('Nothing is waiting.')).toBeTruthy();
    expect(within(region).getByText('Nothing needs attention.')).toBeTruthy();
    expect(within(region).queryByRole('list')).toBeNull();
  });

  it('says the next sync when the connector is not scheduled', async () => {
    getStatus.mockResolvedValue(status({ reachWithinMinutes: null }));
    renderCard();
    await screen.findByText(/reach PipesHub on its next sync\./);
  });
});

describe('PermissionFreshness: one state each', () => {
  it('running', async () => {
    getStatus.mockResolvedValue(status({ state: 'running', lastPermissionSync: { finishedAt: null, outcome: 'running' } }));
    renderCard();
    await screen.findByText('Refreshing permissions now.');
  });

  it('never: content stays hidden until the first sync', async () => {
    getStatus.mockResolvedValue(status({ state: 'never', lastPermissionSync: { finishedAt: null, outcome: 'never' } }));
    renderCard();
    await screen.findByText(/Permissions have not been refreshed yet\. Content from this connector stays hidden/);
  });

  it('failed: says nothing was shown that should not be, in text and not only colour', async () => {
    getStatus.mockResolvedValue(status({ state: 'failed', lastPermissionSync: { finishedAt: '2026-11-11T11:02:09+00:00', outcome: 'failed' } }));
    renderCard();
    expect(await screen.findByText(/The last permission refresh failed/)).toBeTruthy();
  });

  it.each(['stopped', 'skipped'] as const)('a run that was %s is not called refreshed', async (outcome) => {
    getStatus.mockResolvedValue(status({ lastPermissionSync: { finishedAt: '2026-11-11T11:02:09+00:00', outcome } }));
    renderCard();
    await screen.findByText('The last permission refresh did not run to the end.');
    expect(screen.queryByText(/Last refreshed/)).toBeNull();
  });

  it('pending: counts the folders, singular and plural', async () => {
    getStatus.mockResolvedValue(status({ state: 'pending', pending: { roots: 2, unreadable: false, oldestChangesAgo: 3 } }));
    const { unmount } = renderCard();
    await screen.findByText(/2 folders are being updated\. They are hidden from everyone/);
    unmount();
    getStatus.mockResolvedValue(status({ state: 'pending', pending: { roots: 1, unreadable: false, oldestChangesAgo: 0 } }));
    renderCard();
    await screen.findByText(/1 folder is being updated\. It is hidden from everyone/);
  });

  it('an unreadable waiting list is not "nothing is waiting": it says it is not known, and why in the attention list', async () => {
    getStatus.mockResolvedValue(status({ pending: { roots: 0, unreadable: true, oldestChangesAgo: null }, attention: ['pending_unreadable'] }));
    renderCard();
    await screen.findByText(/Not known: the list of folders waiting to update can't be read/);
    expect(screen.queryByText('Nothing is waiting.')).toBeNull();
    expect(screen.queryByText(/folders? (is|are) being updated/)).toBeNull();
    expect(screen.getByRole('listitem').textContent).toContain("The list of folders waiting to update can't be read. Ask whoever runs PipesHub");
  });

  it('a connector that needs a full rebuild says search still checks each result, not that a change is being applied', async () => {
    getStatus.mockResolvedValue(status({ attention: ['needs_full_pass'] }));
    renderCard();
    expect((await screen.findByRole('listitem')).textContent).toBe("This connector's permission index needs a full rebuild. Until then search checks each result, so nothing is hidden by it.");
    expect(screen.getByText('Nothing is waiting.')).toBeTruthy();
  });

  it('overflow: says the whole connector is being updated', async () => {
    getStatus.mockResolvedValue(status({ state: 'overflow', attention: ['needs_full_pass'] }));
    renderCard();
    await screen.findByText(/A change reached the whole connector, so its permission index needs to be rebuilt in full\. Until then search checks each result/);
    expect(screen.queryByText('Nothing is waiting.')).toBeNull();
  });
});

describe('PermissionFreshness: needs attention', () => {
  it('words each code, groups the ones that read alike, and words an unknown code generally', async () => {
    getStatus.mockResolvedValue(status({
      attention: ['pending_unreadable', 'open_write_expired', 'stale_points', 'sweep_failing', 'something_new'],
    }));
    renderCard();
    await screen.findByText('Needs attention (5)');
    const items = within(screen.getByRole('list')).getAllByRole('listitem').map((li) => li.textContent);
    expect(items).toEqual([
      expect.stringContaining("waiting to update can't be read"),
      expect.stringContaining('did not finish cleanly'),
      expect.stringContaining('could not be checked by the faster search'),
      expect.stringContaining('keep failing'),
      expect.stringContaining('needs a person to look at it'),
    ]);
    expect(screen.queryByText(/pending_unreadable|sweep_failing/)).toBeNull();
  });

  it('lists the connector\'s warnings with the fix, and collapses past five', async () => {
    const warnings = Array.from({ length: 7 }, (_, i) => ({
      code: 'scheme_unreadable', items: 10 + i, container: `Project ${i}`, fix: { vendor: 'Jira', permission: 'Administer Projects' },
    }));
    getStatus.mockResolvedValue(status({ warnings }));
    renderCard();
    await screen.findByText('Needs attention (7)');
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByText(/10 items in Project 0 are hidden\./)).toBeTruthy();
    expect(screen.getAllByText(/give the sync account Administer Projects in Jira/)).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: 'Show 2 more' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(7);
    expect(screen.queryByRole('button', { name: /Show .* more/ })).toBeNull();
  });

  it('names no container when the server sends none', async () => {
    getStatus.mockResolvedValue(status({ warnings: [{ code: 'scheme_unreadable', items: 4 }] }));
    renderCard();
    await screen.findByText('Some items are hidden because the sync account cannot read their permissions.');
  });
});

describe('PermissionFreshness: sync now and accessibility', () => {
  it('"Sync permissions now" calls the sync handler; without a handler there is no button', async () => {
    const sync = vi.fn();
    const { unmount } = renderCard({ onSyncNow: sync });
    fireEvent.click(await screen.findByRole('button', { name: 'Sync permissions now' }));
    expect(sync).toHaveBeenCalledTimes(1);
    unmount();
    renderCard();
    await screen.findByText('Nothing is waiting.');
    expect(screen.queryByRole('button', { name: 'Sync permissions now' })).toBeNull();
  });

  it('while a sync is starting the button is disabled and says it is busy', async () => {
    renderCard({ onSyncNow: vi.fn(), syncBusy: true });
    const button = await screen.findByRole('button', { name: 'Sync permissions now' });
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps touch targets at 44px and hides the icon font text from screen readers', async () => {
    getStatus.mockResolvedValue(status({ attention: ['stale_points'], lastPermissionSync: { finishedAt: null, outcome: 'failed' }, state: 'failed' }));
    renderCard({ onSyncNow: vi.fn() });
    const sync = await screen.findByRole('button', { name: 'Sync permissions now' });
    expect(sync.style.minHeight).toBe('44px');
    const region = screen.getByRole('region', { name: 'Access' });
    expect(region.querySelectorAll('[aria-hidden="true"]').length).toBe(2);
  });

  it('the heading names the region', async () => {
    renderCard();
    const region = await screen.findByRole('region', { name: 'Access' });
    expect(within(region).getByRole('heading', { name: 'Access' })).toBeTruthy();
  });
});
