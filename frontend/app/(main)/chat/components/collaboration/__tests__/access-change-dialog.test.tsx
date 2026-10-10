import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const collab = vi.hoisted(() => ({ previewAccessChange: vi.fn(), getCollaborators: vi.fn() }));
vi.mock('../../../collaboration-api', async (orig) => ({
  ...(await orig<typeof import('../../../collaboration-api')>()),
  CollaborationApi: collab,
}));
const share = vi.hoisted(() => ({ listUserTeams: vi.fn(), getUsersByIds: vi.fn() }));
vi.mock('@/app/components/share/api', () => ({ ShareCommonApi: share }));

import { AccessChangeDialog } from '../access-change-dialog';
import type { AccessChange } from '../../../collaboration-types';

const REF = { kind: 'chat', id: 'c1' } as const;

const VIEW = {
  owner: { userId: 'me', displayName: 'Me' },
  collaborators: [
    { principalType: 'user', principalId: 'u2', displayName: 'Bob', accessLevel: 'write', state: 'active' },
    { principalType: 'team', principalId: 't1', displayName: 'Sales', accessLevel: 'read', state: 'active' },
  ],
  collaboratorCount: 2,
  settings: { editorsCanInvite: false, ownerContentShared: true },
};

function setFlag(on: boolean) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as never);
}

function renderDialog(change: AccessChange | null, over: { onApply?: () => Promise<void>; onClose?: () => void } = {}) {
  const onApply = over.onApply ?? vi.fn().mockResolvedValue(undefined);
  const onClose = over.onClose ?? vi.fn();
  render(
    <Theme>
      <AccessChangeDialog change={change} conversationRef={REF} onApply={onApply} onClose={onClose} />
    </Theme>,
  );
  return { onApply, onClose };
}

beforeEach(() => {
  vi.clearAllMocks();
  setFlag(true);
  collab.getCollaborators.mockResolvedValue(VIEW);
  share.listUserTeams.mockResolvedValue([]);
  share.getUsersByIds.mockResolvedValue([{ id: 'u9', name: 'Dana', isInOrg: true }]);
});
afterEach(cleanup);

describe('AccessChangeDialog', () => {
  it('lists who gains, who loses and who becomes read-only, with names', async () => {
    collab.previewAccessChange.mockResolvedValue({
      gains: [{ userId: 'u9', role: 'viewer' }],
      loses: [{ userId: 'u2', role: 'editor' }],
      becomesReadOnly: [{ teamId: 't1', role: 'viewer' }, { userId: 'u3', role: 'viewer' }],
      truncated: false,
    });
    renderDialog({ type: 'link', projectId: 'p1' });
    const gains = await screen.findByTestId('access-change-gains');
    expect(within(gains).getByText('Dana · Can view')).toBeTruthy();
    expect(within(screen.getByTestId('access-change-loses')).getByText('Bob · Can continue')).toBeTruthy();
    const readOnly = screen.getByTestId('access-change-readOnly');
    expect(within(readOnly).getByText('Sales · Can view')).toBeTruthy();
    expect(within(readOnly).getByText('Unknown user · Can view')).toBeTruthy();
    expect(screen.getByText('Will become read-only')).toBeTruthy();
    expect(collab.previewAccessChange).toHaveBeenCalledWith(REF, { type: 'link', projectId: 'p1' }, expect.anything());
  });

  it('shows the truncation notice and an empty state', async () => {
    collab.previewAccessChange.mockResolvedValue({ gains: [], loses: [], becomesReadOnly: [], truncated: true });
    renderDialog({ type: 'unlink' });
    expect(await screen.findByText("No one's access changes.")).toBeTruthy();
    expect(screen.getByTestId('access-change-truncated').textContent).toMatch(/incomplete/);
  });

  it('Apply is disabled while the preview loads, then calls onApply once and closes', async () => {
    let resolve!: (v: unknown) => void;
    collab.previewAccessChange.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { onApply, onClose } = renderDialog({ type: 'visibility', visibility: 'private' });
    const apply = screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    fireEvent.click(apply);
    expect(onApply).not.toHaveBeenCalled();

    resolve({ gains: [], loses: [{ userId: 'u2', role: 'viewer' }], becomesReadOnly: [], truncated: false });
    await screen.findByTestId('access-change-loses');
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it('Cancel closes without applying', async () => {
    collab.previewAccessChange.mockResolvedValue({ gains: [], loses: [], becomesReadOnly: [], truncated: false });
    const { onApply, onClose } = renderDialog({ type: 'unlink' });
    await screen.findByText("No one's access changes.");
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
  });

  it('fails closed on a preview error: message, Apply disabled, nothing applied', async () => {
    collab.previewAccessChange.mockRejectedValue({ statusCode: 500 });
    const { onApply } = renderDialog({ type: 'unlink' });
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/nothing was changed/);
    const apply = screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    fireEvent.click(apply);
    expect(onApply).not.toHaveBeenCalled();
  });

  it('a project the owner cannot see (404) says so and applies nothing', async () => {
    collab.previewAccessChange.mockRejectedValue({ statusCode: 404 });
    const { onApply } = renderDialog({ type: 'link', projectId: 'hidden' });
    expect((await screen.findByRole('alert')).textContent).toMatch(/isn't available to you/);
    expect(onApply).not.toHaveBeenCalled();
  });

  it('keeps the dialog open with an error when the apply itself fails', async () => {
    collab.previewAccessChange.mockResolvedValue({ gains: [], loses: [], becomesReadOnly: [], truncated: false });
    const { onClose } = renderDialog(
      { type: 'unlink' },
      { onApply: vi.fn().mockRejectedValue(new Error('boom')) },
    );
    await screen.findByText("No one's access changes.");
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect((await screen.findByText(/couldn't be applied/)).textContent).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('has a labelled dialog and puts initial focus on Cancel (the safe action)', async () => {
    collab.previewAccessChange.mockResolvedValue({ gains: [], loses: [], becomesReadOnly: [], truncated: false });
    renderDialog({ type: 'unlink' });
    const dialog = await screen.findByRole('dialog', { name: 'Review access changes' });
    await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' })));
    expect(screen.getByTestId('access-change-status').getAttribute('aria-live')).toBe('polite');
  });

  it('renders nothing and calls nothing with the flag off, or with no change', () => {
    setFlag(false);
    renderDialog({ type: 'unlink' });
    expect(screen.queryByRole('dialog')).toBeNull();
    cleanup();
    setFlag(true);
    renderDialog(null);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(collab.previewAccessChange).not.toHaveBeenCalled();
  });
});
