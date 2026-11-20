import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { AxiosError, AxiosHeaders } from 'axios';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({
  createGroup: vi.fn(),
  updateGroup: vi.fn(),
  addUsersToGroups: vi.fn(),
  removeUsersFromGroups: vi.fn(),
  getGroup: vi.fn(),
  getGroupUsers: vi.fn(),
  deleteGroup: vi.fn(),
}));
vi.mock('../api', () => ({ GroupsApi: api }));

// The panel chrome and pickers are covered elsewhere; these tests are about the save toast.
vi.mock('../../components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components')>();
  const ReactModule = await import('react');
  return {
    ...actual,
    WorkspaceRightPanel: ({
      open,
      primaryLabel,
      primaryDisabled,
      onPrimaryClick,
      children,
    }: {
      open: boolean;
      primaryLabel: string;
      primaryDisabled?: boolean;
      onPrimaryClick: () => void;
      children: React.ReactNode;
    }) =>
      open ? (
        <div>
          <button type="button" disabled={primaryDisabled} onClick={onPrimaryClick}>
            {primaryLabel}
          </button>
          {children}
        </div>
      ) : null,
    SearchableCheckboxDropdown: () => null,
    AvatarCell: () => null,
    PaginatedMembersList: ReactModule.forwardRef(() => null),
  };
});
vi.mock('../../hooks/use-paginated-user-options', () => ({
  usePaginatedUserOptions: () => ({
    options: [],
    isLoading: false,
    hasMore: false,
    onSearch: () => {},
    onLoadMore: () => {},
  }),
}));
vi.mock('@/config', () => ({
  useAuthStore: (select: (s: { user: null }) => unknown) => select({ user: null }),
}));

import { processError } from '@/lib/api/api-error';
import { CreateGroupSidebar } from '../components/create-group-sidebar';
import { GroupDetailSidebar } from '../components/group-detail-sidebar';
import { useGroupsStore } from '../store';
import { isGroupSaveRefusal } from '../save-error';
import { installDomShims, renderInTheme, toasts, clearToasts } from '../../connectors/__tests__/fixtures';

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

const duplicateName = () =>
  httpError(400, { error: { code: 'HTTP_BAD_REQUEST', message: 'Group already exists' } });

beforeEach(() => {
  installDomShims();
  clearToasts();
  for (const fn of Object.values(api)) fn.mockReset();
});

afterEach(() => {
  cleanup();
  useGroupsStore.setState({ isCreatePanelOpen: false, isDetailPanelOpen: false, isEditMode: false });
});

const duplicateNameOnField = () =>
  httpError(400, {
    error: {
      code: 'HTTP_BAD_REQUEST',
      message: 'Group already exists',
      fieldErrors: [{ field: 'name', message: 'Group already exists', code: 'DUPLICATE_NAME' }],
    },
  });

describe('saving a group with a name that is already taken', () => {
  it('marks the name field and shows no toast when the server names the field (create)', async () => {
    api.createGroup.mockRejectedValue(duplicateNameOnField());
    useGroupsStore.setState({ isCreatePanelOpen: true, createGroupName: 'Engineering', createGroupUserIds: [] });
    renderInTheme(<CreateGroupSidebar />);

    fireEvent.click(screen.getByRole('button', { name: 'Create group' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Group already exists');
    const name = screen.getByLabelText(/^Group name/i);
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(name.getAttribute('aria-describedby')).toContain(alert.id);
    expect(toasts()).toHaveLength(0);
  });

  it('marks the name field and shows no toast when the server names the field (rename)', async () => {
    api.updateGroup.mockRejectedValue(duplicateNameOnField());
    api.getGroupUsers.mockResolvedValue({ users: [], totalCount: 0 });
    useGroupsStore.setState({
      isDetailPanelOpen: true,
      isEditMode: true,
      detailGroup: { _id: 'g1', name: 'Design', type: 'custom', orgId: 'o1', users: [] } as never,
      editGroupName: 'Engineering',
      editAddUserIds: [],
    });
    renderInTheme(<GroupDetailSidebar />);

    fireEvent.click(screen.getByRole('button', { name: 'Save edits' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Group already exists');
    expect(toasts()).toHaveLength(0);
  });


  it('shows the API message when creating', async () => {
    api.createGroup.mockRejectedValue(duplicateName());
    useGroupsStore.setState({ isCreatePanelOpen: true, createGroupName: 'Engineering', createGroupUserIds: [] });
    renderInTheme(<CreateGroupSidebar />);

    fireEvent.click(screen.getByRole('button', { name: 'Create group' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Failed to create group',
      description: 'Group already exists',
    });
  });

  it('shows the API message when renaming', async () => {
    api.updateGroup.mockRejectedValue(duplicateName());
    api.getGroupUsers.mockResolvedValue({ users: [], totalCount: 0 });
    useGroupsStore.setState({
      isDetailPanelOpen: true,
      isEditMode: true,
      detailGroup: { _id: 'g1', name: 'Design', type: 'custom', orgId: 'o1', users: [] } as never,
      editGroupName: 'Engineering',
      editAddUserIds: [],
    });
    renderInTheme(<GroupDetailSidebar />);

    fireEvent.click(screen.getByRole('button', { name: 'Save edits' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(api.updateGroup).toHaveBeenCalledWith('g1', { name: 'Engineering' });
    expect(toasts()[0]).toMatchObject({
      variant: 'error',
      title: 'Failed to update group',
      description: 'Group already exists',
    });
  });

  it('keeps the generic toast with no description for other failures', async () => {
    api.createGroup.mockRejectedValue(httpError(500, { error: { message: 'Error publishing to Kafka topic' } }));
    useGroupsStore.setState({ isCreatePanelOpen: true, createGroupName: 'Engineering', createGroupUserIds: [] });
    renderInTheme(<CreateGroupSidebar />);

    fireEvent.click(screen.getByRole('button', { name: 'Create group' }));

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(toasts()[0].title).toBe('Failed to create group');
    expect(toasts()[0].description).toBeUndefined();
  });

  it('refuses a name over 100 characters before sending, the same limit as a team', () => {
    useGroupsStore.setState({ isCreatePanelOpen: true, createGroupName: 'a'.repeat(101), createGroupUserIds: [] });
    renderInTheme(<CreateGroupSidebar />);

    expect((screen.getByRole('button', { name: 'Create group' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.blur(screen.getByLabelText(/^Group name/i));
    expect(screen.getByRole('alert').textContent).toBe('Name must be at most 100 characters.');
    expect(api.createGroup).not.toHaveBeenCalled();
  });

  it('does not ask for a description, as a team does not', () => {
    useGroupsStore.setState({ isCreatePanelOpen: true, createGroupName: 'Engineering', createGroupUserIds: [] });
    renderInTheme(<CreateGroupSidebar />);
    expect((screen.getByRole('button', { name: 'Create group' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('leaves only 400s to the sidebars, so other errors still get the global toast', () => {
    expect(isGroupSaveRefusal(duplicateName())).toBe(true);
    expect(isGroupSaveRefusal(httpError(500, {}))).toBe(false);
    expect(isGroupSaveRefusal(httpError(403, {}))).toBe(false);
  });
});
