import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { en } from '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { useUserStore } from '@/lib/store/user-store';
import type { OAuthClient } from '../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

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

async function openPanelFor(createdBy: string) {
  installBrowserShims();
  useUserStore.setState({
    profile: {
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

  it('keeps everything editable for the app\'s creator', async () => {
    await openPanelFor(ME);

    expect(screen.queryByText(text.manageApplication.creatorOnlyNotice)).toBeNull();
    expect(screen.getByText(text.manageApplication.generateSecret)).toBeTruthy();
    expect(nameInput().matches(':disabled')).toBe(false);
  });
});
