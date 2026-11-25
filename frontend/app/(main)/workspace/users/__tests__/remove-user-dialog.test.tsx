/**
 * QA W4: the remove-user dialog names the org it removes the person from, never a fixed product name, and says
 * what removal does to their data (CLEANUP-G2).
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { fetchOrgWithLogo } from '@/chat/api';
import { RemoveUserDialog, removedUserDescription } from '../components/remove-user-dialog';
import type { User } from '../types';

vi.mock('@/chat/api', () => ({ fetchOrgWithLogo: vi.fn() }));

const ADA = { userId: 'u-ada', name: 'Ada Lovelace', email: 'ada@example.com' } as User;
const orgIs = (org: { shortName?: string; registeredName?: string } | null) =>
  vi.mocked(fetchOrgWithLogo).mockResolvedValue({ org: org as never, logoUrl: null });

const renderDialog = () =>
  render(
    <Theme>
      <RemoveUserDialog target={ADA} isLoading={false} onClose={() => {}} onConfirm={() => {}} />
    </Theme>,
  );

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Remove user dialog', () => {
  it('names the org by its display name, not "Pipeshub"', async () => {
    orgIs({ shortName: 'Acme', registeredName: 'Acme Corporation Ltd' });
    renderDialog();
    expect(await screen.findByText('Remove ada@example.com from Acme?')).toBeTruthy();
    expect(screen.queryByText(/Pipeshub/)).toBeNull();
  });

  it('falls back to the registered name, then to "this organization"', async () => {
    orgIs({ shortName: '', registeredName: 'Acme Corporation Ltd' });
    renderDialog();
    expect(await screen.findByText('Remove ada@example.com from Acme Corporation Ltd?')).toBeTruthy();
    cleanup();
    orgIs(null);
    renderDialog();
    expect(await screen.findByText('Remove ada@example.com from this organization?')).toBeTruthy();
  });

  it('says in plain words that private data is erased and shared chats move to an admin', async () => {
    orgIs({ shortName: 'Acme' });
    renderDialog();
    expect(await screen.findByText(/private chats, uploaded files, workspaces.*are erased/)).toBeTruthy();
    expect(screen.getByText(/can't be undone/)).toBeTruthy();
    expect(screen.getByText(/shared with others are not erased\. They move to an admin/)).toBeTruthy();
  });

  it('CMDUI3 W4: the success toast names the org exactly as the dialog did', async () => {
    const onConfirm = vi.fn();
    orgIs({ shortName: 'Acme' });
    render(
      <Theme>
        <RemoveUserDialog target={ADA} isLoading={false} onClose={() => {}} onConfirm={onConfirm} />
      </Theme>,
    );
    await screen.findByText('Remove ada@example.com from Acme?');
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(onConfirm).toHaveBeenCalledWith('Acme');
    const toast = removedUserDescription(i18n.t.bind(i18n), ADA, onConfirm.mock.calls[0][0]);
    expect(toast).toBe('Ada Lovelace was removed from Acme');
    expect(toast).not.toMatch(/workspace/);

    cleanup();
    onConfirm.mockClear();
    orgIs(null);
    render(
      <Theme>
        <RemoveUserDialog target={ADA} isLoading={false} onClose={() => {}} onConfirm={onConfirm} />
      </Theme>,
    );
    await screen.findByText('Remove ada@example.com from this organization?');
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(removedUserDescription(i18n.t.bind(i18n), ADA, onConfirm.mock.calls[0][0])).toBe(
      'Ada Lovelace was removed from this organization',
    );
  });
});
