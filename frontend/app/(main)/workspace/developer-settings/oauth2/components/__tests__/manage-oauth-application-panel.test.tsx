import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { en } from '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useUserStore } from '@/lib/store/user-store';
import type { OAuthClient } from '../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/chat/components/chat-panel/expansion-panels/connectors-collections', () => ({
  CollectionsTab: () => null,
}));

const getOAuthClient = vi.hoisted(() => vi.fn());
vi.mock('../../api', () => ({
  Oauth2Api: {
    getOAuthClient,
    getScopes: vi.fn().mockResolvedValue({ scopes: {} }),
    regenerateOAuthClientSecret: vi.fn(),
  },
}));
vi.mock('../../../../service-accounts/api', () => ({
  ServiceAccountsApi: { list: vi.fn().mockResolvedValue({ serviceAccounts: [] }) },
}));

import { ManageOAuthApplicationPanel } from '../manage-oauth-application-panel';

const ME = '507f1f77bcf86cd799439011';
const SOMEONE_ELSE = '507f1f77bcf86cd799439013';
const text = en.workspace.oauth2;

const app = (createdBy: string): OAuthClient => ({
  id: 'a1',
  clientId: 'cid',
  name: 'Payroll',
  redirectUris: ['https://app.example/cb'],
  allowedGrantTypes: ['authorization_code'],
  allowedScopes: [],
  status: 'active',
  createdBy,
});

async function openPanelFor(createdBy: string, { profileLoaded = true } = {}) {
  installBrowserShims();
  useUserStore.setState({
    profile: !profileLoaded ? null : {
      userId: ME,
      firstName: null,
      lastName: null,
      fullName: 'Admin',
      email: 'admin@example.com',
      isAdmin: true,
      avatarUrl: null,
      hasLoggedIn: true,
    },
  });
  getOAuthClient.mockResolvedValue(app(createdBy));
  renderInTheme(
    <ManageOAuthApplicationPanel open onOpenChange={() => {}} clientId="a1" onSaved={() => {}} />
  );
  await screen.findAllByRole('tab');
}

function nameInput(): HTMLInputElement {
  return screen.getByDisplayValue('Payroll') as HTMLInputElement;
}

describe('ManageOAuthApplicationPanel for an app someone else created', () => {
  afterEach(() => {
    cleanup();
    useUserStore.setState({ profile: null });
  });

  it('says only the creator can change it and offers no way to', async () => {
    await openPanelFor(SOMEONE_ELSE);

    expect(screen.getByText(text.manageApplication.creatorOnlyNotice)).toBeTruthy();
    expect(screen.queryByText(text.manageApplication.generateSecret)).toBeNull();
    // Switched off by the surrounding <fieldset disabled>, which jsdom reports through :disabled.
    expect(nameInput().matches(':disabled')).toBe(true);
    expect(screen.queryByText(text.manageApplication.saveChanges)).toBeNull();
  });

  it('still lets the admin suspend it', async () => {
    await openPanelFor(SOMEONE_ELSE);
    const advanced = document.querySelector('[role="tab"][id$="-trigger-advanced"]') as HTMLElement;
    fireEvent.mouseDown(advanced);
    fireEvent.click(advanced);

    const suspend = await screen.findByRole('button', {
      name: text.manageApplication.suspendApplicationButton,
    });
    expect((suspend as HTMLButtonElement).disabled).toBe(false);
  });

  it('shows it read-only until the signed-in profile has loaded', async () => {
    await openPanelFor(SOMEONE_ELSE, { profileLoaded: false });

    expect(screen.queryByText(text.manageApplication.generateSecret)).toBeNull();
    expect(nameInput().matches(':disabled')).toBe(true);
  });

  it('keeps everything editable for the app\'s creator', async () => {
    await openPanelFor(ME);

    expect(screen.queryByText(text.manageApplication.creatorOnlyNotice)).toBeNull();
    expect(screen.getByText(text.manageApplication.generateSecret)).toBeTruthy();
    expect(nameInput().matches(':disabled')).toBe(false);
  });
});

describe('ManageOAuthApplicationPanel when another app is opened in it', () => {
  afterEach(() => {
    cleanup();
    useUserStore.setState({ profile: null });
  });

  it("does not carry one app's picks to the next app", async () => {
    await openPanelFor(ME);
    getOAuthClient.mockReset();
    getOAuthClient.mockImplementation(async (id: string) =>
      id === 'a1'
        ? { ...app(ME), knowledgeLimit: { apps: ['app-1'], recordGroups: [], records: [] } }
        : { ...app(ME), id: 'a2', clientId: 'cid2', name: 'Docs', knowledgeLimit: undefined },
    );
    cleanup();
    const view = renderInTheme(
      <ManageOAuthApplicationPanel open onOpenChange={() => {}} clientId="a1" onSaved={() => {}} />
    );
    await screen.findByDisplayValue('Payroll');
    const granular = document.querySelector('[role="tab"][id$="-trigger-granular"]') as HTMLElement;
    fireEvent.mouseDown(granular);
    fireEvent.click(granular);
    const only = () => document.getElementById('oauth-app-manage-knowledge-limit-only') as HTMLInputElement;
    await waitFor(() => expect(only().checked).toBe(true));

    view.rerender(
      <Theme>
        <ManageOAuthApplicationPanel open onOpenChange={() => {}} clientId="a2" onSaved={() => {}} />
      </Theme>
    );
    // The name field is on another tab; the second app has loaded once "only" is no longer chosen.
    await waitFor(() => expect(getOAuthClient).toHaveBeenCalledWith('a2'));
    await waitFor(() => expect(only().checked).toBe(false));
    fireEvent.click(only());

    // Nothing is picked for this app yet.
    expect(screen.getAllByText(en.knowledgeLimit.pickOne).length).toBeGreaterThan(0);
  });
});
