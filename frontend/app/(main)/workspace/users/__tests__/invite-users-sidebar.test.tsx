import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import '@/lib/__tests__/test-i18n';

const usersApi = vi.hoisted(() => ({ inviteUsers: vi.fn(), updateUser: vi.fn() }));
vi.mock('../api', () => ({ UsersApi: usersApi }));
const xlsxRead = vi.hoisted(() => ({ fail: null as Error | null }));
vi.mock('xlsx', async (importOriginal) => {
  const actual = await importOriginal<typeof import('xlsx')>();
  return {
    ...actual,
    read: (...args: Parameters<typeof actual.read>) => {
      if (xlsxRead.fail) throw xlsxRead.fail;
      return actual.read(...args);
    },
  };
});
vi.mock('../../groups/api', () => ({
  GroupsApi: { listGroups: vi.fn().mockResolvedValue({ groups: [] }) },
}));

// The panel chrome and the tag editor are covered elsewhere; these tests are
// about what the invite panel sends and what it tells the person afterwards.
vi.mock('../../components', () => ({
  WorkspaceRightPanel: ({
    open,
    primaryLabel,
    primaryDisabled,
    primaryTooltip,
    onPrimaryClick,
    headerActions,
    children,
  }: {
    open: boolean;
    primaryLabel: string;
    primaryDisabled?: boolean;
    primaryTooltip?: string;
    onPrimaryClick: () => void;
    headerActions?: React.ReactNode;
    children: React.ReactNode;
  }) =>
    open ? (
      <div>
        {headerActions}
        <button type="button" disabled={primaryDisabled} title={primaryTooltip} onClick={onPrimaryClick}>
          {primaryLabel}
        </button>
        {children}
      </div>
    ) : null,
  FormField: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TagInput: ({
    tags,
    error,
  }: {
    tags: Array<{ id: string; value: string; isValid?: boolean }>;
    error?: string;
  }) => (
    <div>
      {tags.map((tag) => (
        <span key={tag.id} data-testid="tag" data-invalid={tag.isValid === false ? 'true' : 'false'}>
          {tag.value}
        </span>
      ))}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  ),
  SelectDropdown: () => null,
  SearchableCheckboxDropdown: () => null,
}));

import { processError } from '@/lib/api/api-error';
import { InviteUsersSidebar } from '../components/invite-users-sidebar';
import { useUsersStore } from '../store';
import { installDomShims, renderInTheme, signInAs, toasts, clearToasts } from '../../connectors/__tests__/fixtures';

function httpError(status: number, data: unknown) {
  const error = new AxiosError(`Request failed with status code ${status}`);
  error.response = {
    status,
    statusText: '',
    data,
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  } as never;
  return processError(error as AxiosError<never>);
}

function tags(...values: Array<[string, boolean]>) {
  return values.map(([value, isValid], i) => ({ id: `t${i}`, value, isValid }));
}

function openPanel(emails: ReturnType<typeof tags>, inviteRole = 'Member') {
  useUsersStore.setState({
    isInvitePanelOpen: true,
    inviteEmails: emails,
    inviteRole,
    inviteGroupIds: [],
    editingInviteUser: null,
  });
  renderInTheme(<InviteUsersSidebar />);
}

function sendButton() {
  return screen.getByRole('button', { name: 'Send invite' });
}

function csvFile(name: string, text: string) {
  const bytes = new TextEncoder().encode(text);
  return { name, arrayBuffer: async () => bytes.buffer } as unknown as File;
}

async function importFile(file: File) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  await act(async () => {
    fireEvent.change(input);
  });
}

beforeEach(() => {
  installDomShims();
  clearToasts();
  signInAs('admin');
  usersApi.inviteUsers.mockReset();
  xlsxRead.fail = null;
});

afterEach(() => {
  cleanup();
  useUsersStore.setState({ isInvitePanelOpen: false, inviteEmails: [] });
});

describe('inviting admins', () => {
  const warning = /becomes a full org admin/;

  it('warns that everyone on the list becomes an admin when the role is Admin', () => {
    openPanel(tags(['a@x.com', true], ['b@x.com', true]), 'Admin');

    expect(screen.getByRole('alert').textContent).toMatch(warning);
  });

  it('shows no warning for members', () => {
    openPanel(tags(['a@x.com', true], ['b@x.com', true]));

    expect(screen.queryByText(warning)).toBeNull();
  });
});

describe('sending invites', () => {
  it('says nobody was invited when every address already belongs to a member', async () => {
    usersApi.inviteUsers.mockRejectedValue(
      httpError(409, { error: { code: 'HTTP_CONFLICT', message: 'All provided emails already have active accounts' } }),
    );
    openPanel(tags(['a@x.com', true], ['b@x.com', true]));

    fireEvent.click(sendButton());

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'info',
      title: 'Already members — no invites sent',
      description: 'Every address already belongs to an active member.',
    });
    expect(useUsersStore.getState().isInvitePanelOpen).toBe(true);
  });

  it('reports how many were invited and how many were already members', async () => {
    usersApi.inviteUsers.mockResolvedValue({
      queued: true,
      invited: 2,
      reinvited: 0,
      restored: 0,
      alreadyActive: 1,
    });
    openPanel(tags(['a@x.com', true], ['b@x.com', true], ['c@x.com', true]));

    fireEvent.click(sendButton());

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0].title).toBe('Invites queued');
    expect(toasts()[0].description).toContain('Emails to 2 users are being sent');
    expect(toasts()[0].description).toContain(
      '1 address already belongs to a member, so no invite was sent to it.',
    );
  });

  it('pluralises the invited count instead of "2 users has been invited"', async () => {
    usersApi.inviteUsers.mockResolvedValue({ queued: false, invited: 2, reinvited: 0, restored: 0, alreadyActive: 0 });
    openPanel(tags(['a@x.com', true], ['b@x.com', true]));

    fireEvent.click(sendButton());

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'success',
      title: 'Invite sent!',
      description: '2 users have been invited',
    });
  });

  it('will not send while an invalid address is listed', () => {
    openPanel(tags(['a@x.com', true], ['"user@example.com")', false]));

    expect((sendButton() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('alert').textContent).toBe(
      '1 invalid address is marked in red. Fix or remove it to send invites.',
    );
    fireEvent.click(sendButton());
    expect(usersApi.inviteUsers).not.toHaveBeenCalled();
  });
});

describe('importing a file', () => {
  it('refuses a file that is not CSV or Excel, whatever the picker allowed', async () => {
    openPanel([]);

    await importFile(csvFile('people.txt', 'a@x.com\n'));

    expect(toasts()).toHaveLength(1);
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      description: 'Only .csv, .xlsx and .xls files can be imported.',
    });
    expect(useUsersStore.getState().inviteEmails).toEqual([]);
  });

  it('marks a formula cell split at its comma as invalid', async () => {
    openPanel([]);

    await importFile(csvFile('people.csv', 'email\na@x.com\n=HYPERLINK("http://evil","user@example.com")\n'));

    await waitFor(() => expect(useUsersStore.getState().inviteEmails.length).toBeGreaterThan(0));
    const listed = useUsersStore.getState().inviteEmails;
    expect(listed.find((t) => t.value === 'a@x.com')?.isValid).toBe(true);
    const fromFormula = listed.filter((t) => t.value.includes('user@example.com'));
    expect(fromFormula.length).toBeGreaterThan(0);
    expect(fromFormula.every((t) => t.isValid === false)).toBe(true);
    expect((sendButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it('counts an over-long address among the invalid ones', async () => {
    openPanel([]);
    const long = `${'a'.repeat(250)}@example.com`;

    await importFile(csvFile('people.csv', `a@x.com\n${long}\n`));

    await waitFor(() => expect(toasts()).toHaveLength(2));
    const warning = toasts().find((t) => t.variant === 'warning');
    expect(warning?.title).toBe('1 invalid, 0 duplicate address(es)');
    expect(warning?.description).toBe('1 entry longer than 254 characters was left out.');
    expect(toasts().every((t) => t.placement === 'top')).toBe(true);
  });

  it('shows a plain message, not the parser error, for a file it cannot read', async () => {
    xlsxRead.fail = new Error('Unsupported ZIP encryption');
    openPanel([]);

    await importFile(csvFile('people.xlsx', 'not really a workbook'));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Import failed',
      description: 'Could not read the file. Upload a valid CSV or Excel file.',
    });
  });

  it('keeps its own message when the file has no addresses', async () => {
    openPanel([]);

    await importFile(csvFile('people.csv', 'name\nAlice\n'));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0].description).toBe('No email addresses found in the file');
  });
});
