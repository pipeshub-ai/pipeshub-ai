import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore } from '@/lib/store/user-store';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const collab = vi.hoisted(() => ({
  explain: vi.fn(),
  getCollaborators: vi.fn(),
  previewAccessChange: vi.fn(),
}));
vi.mock('../../../collaboration-api', async (orig) => ({
  ...(await orig<typeof import('../../../collaboration-api')>()),
  CollaborationApi: collab,
}));
const project = vi.hoisted(() => ({ setConversationProjectVisibility: vi.fn() }));
vi.mock('../../../project-api', () => ({ ProjectApi: project }));
const share = vi.hoisted(() => ({ listUserTeams: vi.fn(), getUsersByIds: vi.fn() }));
vi.mock('@/app/components/share/api', () => ({ ShareCommonApi: share }));

import { AccessPanel, type AccessPanelProps } from '../access-panel';

const REF = { kind: 'chat', id: 'c1' } as const;
const VIEW = {
  owner: { userId: 'me', displayName: 'Me' },
  collaborators: [
    { principalType: 'user', principalId: 'u2', displayName: 'Bob', accessLevel: 'write', state: 'active' },
    { principalType: 'team', principalId: 't1', displayName: 'Sales', accessLevel: 'write', state: 'active' },
  ],
  collaboratorCount: 2,
  settings: { editorsCanInvite: false, ownerContentShared: true },
};

function setFlag(on: boolean) {
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: on } } as never);
}
function setUser(isAdmin: boolean) {
  useUserStore.setState({ profile: { userId: 'me', isAdmin } } as never);
}

function renderPanel(props: Partial<AccessPanelProps> = {}) {
  return render(
    <Theme>
      <AccessPanel conversationRef={REF} isOwner={false} {...props} />
    </Theme>,
  );
}

async function openPanel() {
  fireEvent.click(screen.getByRole('button', { name: 'Who can access this chat' }));
  return screen.findByRole('dialog', { name: 'Access to this chat' });
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} });
  vi.clearAllMocks();
  setFlag(true);
  setUser(false);
  collab.explain.mockResolvedValue({ role: 'owner', via: [{ type: 'owner', ref: null, role: 'owner' }] });
  collab.getCollaborators.mockResolvedValue(VIEW);
  share.listUserTeams.mockResolvedValue([]);
  share.getUsersByIds.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('AccessPanel explain sentences (PI-21)', () => {
  it('renders every via type as a sentence', async () => {
    collab.explain.mockResolvedValue({
      role: 'owner',
      via: [
        { type: 'owner', ref: null, role: 'owner' },
        { type: 'direct', ref: null, role: 'editor' },
        { type: 'team', ref: 't1', role: 'editor' },
        { type: 'project', ref: 'p1', role: 'viewer' },
      ],
    });
    renderPanel({ isOwner: true });
    const dialog = await openPanel();
    expect(await within(dialog).findByText('You own this chat.')).toBeTruthy();
    expect(within(dialog).getByText('You can continue this chat because it was shared with you directly.')).toBeTruthy();
    expect(await within(dialog).findByText('You can continue this chat through team Sales.')).toBeTruthy();
    expect(within(dialog).getByText('You can view this chat because it belongs to a project you have access to.')).toBeTruthy();
    expect(within(dialog).getByText('Your access: Owner')).toBeTruthy();
    expect(collab.explain).toHaveBeenCalledWith(REF, undefined, expect.anything());
  });

  it('shows a redacted team (ref null) as "a team you\'re not in"', async () => {
    collab.explain.mockResolvedValue({ role: 'editor', via: [{ type: 'team', ref: null, role: 'editor' }] });
    renderPanel();
    const dialog = await openPanel();
    expect(await within(dialog).findByText("You can continue this chat through a team you're not in.")).toBeTruthy();
  });

  it('falls back to a generic team sentence when a visible team has no resolvable name', async () => {
    collab.getCollaborators.mockRejectedValue({ statusCode: 403 });
    collab.explain.mockResolvedValue({ role: 'viewer', via: [{ type: 'team', ref: 'tX', role: 'viewer' }] });
    renderPanel();
    const dialog = await openPanel();
    expect(await within(dialog).findByText('You can view this chat through one of your teams.')).toBeTruthy();
  });

  it('says so when there is no path', async () => {
    collab.explain.mockResolvedValue({ role: 'none', via: [] });
    renderPanel();
    const dialog = await openPanel();
    expect(await within(dialog).findByText('You have no access to this chat.')).toBeTruthy();
  });

  it('announces results in a polite live region', async () => {
    renderPanel();
    const dialog = await openPanel();
    const region = within(dialog).getByTestId('access-explain');
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.getAttribute('role')).toBe('status');
  });

  it('shows an error with Retry that reloads', async () => {
    collab.explain.mockRejectedValueOnce({ statusCode: 500 });
    renderPanel();
    const dialog = await openPanel();
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Try again' }));
    expect(await within(dialog).findByText('You own this chat.')).toBeTruthy();
    expect(collab.explain).toHaveBeenCalledTimes(2);
  });
});

describe('AccessPanel subject picker (C-3)', () => {
  it('is shown to the owner with the collaborators, and explaining one passes subject and uses their name', async () => {
    collab.explain.mockImplementation(async (_ref: unknown, subject?: string) =>
      subject ? { role: 'editor', via: [{ type: 'direct', ref: null, role: 'editor' }] } : { role: 'owner', via: [{ type: 'owner', ref: null, role: 'owner' }] },
    );
    renderPanel({ isOwner: true });
    const dialog = await openPanel();
    const picker = (await within(dialog).findByLabelText('Check access for')) as HTMLSelectElement;
    await waitFor(() => expect(within(picker).getByRole('option', { name: 'Bob' })).toBeTruthy());
    expect(within(picker).queryByRole('option', { name: 'Sales' })).toBeNull();

    fireEvent.change(picker, { target: { value: 'u2' } });
    expect(await within(dialog).findByText('Bob can continue this chat because it was shared with them directly.')).toBeTruthy();
    expect(collab.explain).toHaveBeenLastCalledWith(REF, 'u2', expect.anything());
  });

  it('is shown to an org admin who is not the owner', async () => {
    setUser(true);
    renderPanel({ isOwner: false });
    const dialog = await openPanel();
    expect(await within(dialog).findByLabelText('Check access for')).toBeTruthy();
  });

  it('is hidden from everyone else, and they never list collaborators', async () => {
    renderPanel({ isOwner: false });
    const dialog = await openPanel();
    await within(dialog).findByText('You own this chat.');
    expect(within(dialog).queryByLabelText('Check access for')).toBeNull();
    expect(collab.getCollaborators).not.toHaveBeenCalledWith(REF);
  });

  it('shows the server refusal (403) when explaining another person is not allowed', async () => {
    setUser(true);
    collab.explain.mockImplementation(async (_ref: unknown, subject?: string) => {
      if (subject) throw { statusCode: 403 };
      return { role: 'none', via: [] };
    });
    renderPanel();
    const dialog = await openPanel();
    fireEvent.change(await within(dialog).findByLabelText('Check access for'), { target: { value: 'u2' } });
    await waitFor(() => expect(within(dialog).getByText("You can't view another person's access.")).toBeTruthy());
    expect(within(dialog).queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});

describe('AccessPanel visibility switch', () => {
  const projectProp = { projectId: 'p1', visibility: 'private' as const };

  it('is owner-only and needs a project link', async () => {
    renderPanel({ isOwner: true, project: null });
    let dialog = await openPanel();
    await within(dialog).findByText('You own this chat.');
    expect(within(dialog).queryByRole('switch')).toBeNull();
    cleanup();

    renderPanel({ isOwner: false, project: projectProp });
    dialog = await openPanel();
    await within(dialog).findByText('You own this chat.');
    expect(within(dialog).queryByRole('switch')).toBeNull();
  });

  it('previews first; Apply sets the visibility and reports it', async () => {
    collab.previewAccessChange.mockResolvedValue({
      gains: [{ userId: 'u7', role: 'viewer' }],
      loses: [],
      becomesReadOnly: [],
      truncated: false,
    });
    share.getUsersByIds.mockResolvedValue([{ id: 'u7', name: 'Pat', isInOrg: true }]);
    project.setConversationProjectVisibility.mockResolvedValue(undefined);
    const onVisibilityChanged = vi.fn();
    renderPanel({ isOwner: true, project: projectProp, onVisibilityChanged });
    const dialog = await openPanel();
    const toggle = within(dialog).getByRole('switch', { name: 'Visible to project members' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(toggle);

    const confirm = await screen.findByRole('dialog', { name: 'Review access changes' });
    expect(project.setConversationProjectVisibility).not.toHaveBeenCalled();
    expect(await within(confirm).findByText('Pat · Can view')).toBeTruthy();
    expect(collab.previewAccessChange).toHaveBeenCalledWith(REF, { type: 'visibility', visibility: 'project' }, expect.anything());
    fireEvent.click(within(confirm).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(project.setConversationProjectVisibility).toHaveBeenCalledWith('c1', 'project', {}));
    expect(onVisibilityChanged).toHaveBeenCalledWith('project');
  });

  it('applies nothing when the preview fails', async () => {
    collab.previewAccessChange.mockRejectedValue({ statusCode: 500 });
    renderPanel({ isOwner: true, project: projectProp });
    const dialog = await openPanel();
    fireEvent.click(within(dialog).getByRole('switch'));
    const confirm = await screen.findByRole('dialog', { name: 'Review access changes' });
    await within(confirm).findByRole('alert');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Apply' }));
    expect(project.setConversationProjectVisibility).not.toHaveBeenCalled();
  });

  it('uses the agent route for an agent chat', async () => {
    collab.previewAccessChange.mockResolvedValue({ gains: [], loses: [], becomesReadOnly: [], truncated: false });
    project.setConversationProjectVisibility.mockResolvedValue(undefined);
    render(
      <Theme>
        <AccessPanel conversationRef={{ kind: 'agent', agentKey: 'a1', id: 'c1' }} isOwner project={projectProp} />
      </Theme>,
    );
    const dialog = await openPanel();
    fireEvent.click(within(dialog).getByRole('switch'));
    const confirm = await screen.findByRole('dialog', { name: 'Review access changes' });
    await within(confirm).findByText("No one's access changes.");
    fireEvent.click(within(confirm).getByRole('button', { name: 'Apply' }));
    await waitFor(() =>
      expect(project.setConversationProjectVisibility).toHaveBeenCalledWith('c1', 'project', { agentKey: 'a1' }),
    );
  });
});

describe('AccessPanel keyboard and focus', () => {
  it('opens from the keyboard, moves focus into the popover, and returns focus to the button on Escape', async () => {
    renderPanel({ isOwner: true });
    const button = screen.getByRole('button', { name: 'Who can access this chat' });
    expect(button.getAttribute('aria-haspopup')).toBe('dialog');
    button.focus();
    fireEvent.keyDown(button, { key: 'Enter' });
    fireEvent.click(button);
    const dialog = await screen.findByRole('dialog', { name: 'Access to this chat' });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(button));
  });
});

describe('AccessPanel flag off', () => {
  it('renders nothing and makes no requests', () => {
    setFlag(false);
    const { container } = renderPanel({ isOwner: true, project: { projectId: 'p1', visibility: 'private' } });
    expect(screen.queryByRole('button', { name: 'Who can access this chat' })).toBeNull();
    expect(container.textContent).toBe('');
    expect(collab.explain).not.toHaveBeenCalled();
    expect(collab.getCollaborators).not.toHaveBeenCalled();
  });
});
