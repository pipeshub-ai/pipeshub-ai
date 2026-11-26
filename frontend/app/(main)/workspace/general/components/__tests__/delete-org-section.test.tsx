import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { enT } from '@/lib/org-deletion/__tests__/test-i18n';

const { deleteOrg, logoutAndRedirect, toastError } = vi.hoisted(() => ({
  deleteOrg: vi.fn(),
  logoutAndRedirect: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('../../api', () => ({ OrgApi: { deleteOrg: (...a: unknown[]) => deleteOrg(...a) } }));
vi.mock('@/config', () => ({ logoutAndRedirect: () => logoutAndRedirect() }));
vi.mock('@/lib/store/toast-store', () => ({ toast: { error: (...a: unknown[]) => toastError(...a), success: vi.fn() } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: enT, i18n: { language: 'en-US' } }) }));

import { DeleteOrgSection } from '../delete-org-section';

const NAME = 'Acme Corporation Ltd';
const renderSection = (confirmName = NAME) =>
  render(
    <Theme>
      <DeleteOrgSection orgName="Acme" confirmName={confirmName} />
    </Theme>,
  );

const openDialog = async () => {
  fireEvent.click(screen.getByRole('button', { name: /Delete organization/ }));
  return screen.findByRole('dialog');
};

describe('General › Delete organization', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it('says the delete is at once and cannot be undone, with no grace settings', () => {
    renderSection();
    expect(screen.getByText('Delete Acme')).toBeTruthy();
    expect(screen.getByText('Everyone is signed out at once, and nobody can cancel it.')).toBeTruthy();
    expect(screen.queryByLabelText('Grace period')).toBeNull();
  });

  it('the dialog says what happens; Delete stays off until the exact name is typed', async () => {
    renderSection();
    const dialog = await openDialog();
    expect(within(dialog).getByText(/Acme and its data are deleted at once/)).toBeTruthy();
    expect(within(dialog).getByText(`Type ${NAME} to confirm`)).toBeTruthy();
    const confirm = within(dialog).getByRole('button', { name: /Delete Acme/ });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: NAME.toLowerCase() } });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    expect(within(dialog).getByText("Doesn't match the organization name")).toBeTruthy();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: NAME } });
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    expect(within(dialog).getByRole('button', { name: 'Keep Acme' })).toBeTruthy();
  });

  it('a confirmed delete sends the typed name and signs out to the sign-in page', async () => {
    deleteOrg.mockResolvedValue(undefined);
    renderSection();
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: NAME } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Delete Acme/ }));
    await waitFor(() => expect(logoutAndRedirect).toHaveBeenCalled());
    expect(deleteOrg).toHaveBeenCalledWith(NAME);
  });

  it('a name the server refuses is explained and nothing signs out', async () => {
    deleteOrg.mockRejectedValue({ response: { status: 400, data: { error: { reasonCode: 'org_delete_name_mismatch' } } } });
    renderSection();
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: NAME } });
    fireEvent.click(within(dialog).getByRole('button', { name: /Delete Acme/ }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Couldn't delete the organization", {
        description: "The name you typed doesn't match. Nothing was deleted.",
      }),
    );
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('with no name to type the delete button is off', () => {
    renderSection('');
    expect((screen.getByRole('button', { name: /Delete organization/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
