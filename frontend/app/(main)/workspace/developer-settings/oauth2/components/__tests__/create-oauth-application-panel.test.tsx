import React from 'react';
import { Theme } from '@radix-ui/themes';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { getI18n } from 'react-i18next';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

const scope = vi.hoisted(() => (name: string, category: string) => ({
  name,
  description: `${name} description`,
  category,
  requiresUserConsent: false,
}));

// agent:execute is left out to stand in for a default the API does not list for this user.
vi.mock('../../api', () => ({
  Oauth2Api: {
    getScopes: vi.fn().mockResolvedValue({
      scopes: {
        Access: [scope('offline_access', 'Access')],
        'Knowledge Base': [scope('kb:read', 'Knowledge Base'), scope('kb:write', 'Knowledge Base')],
        Semantic: [scope('semantic:read', 'Semantic'), scope('semantic:write', 'Semantic')],
        Conversations: [scope('conversation:chat', 'Conversations')],
        Agents: [scope('agent:read', 'Agents'), scope('agent:write', 'Agents')],
        Connectors: [scope('connector:read', 'Connectors')],
        Organization: [scope('org:admin', 'Organization')],
      },
    }),
    createApp: vi.fn(),
  },
}));
vi.mock('../../../../service-accounts/api', () => ({
  ServiceAccountsApi: { list: vi.fn().mockResolvedValue({ serviceAccounts: [] }) },
}));

import { Oauth2Api } from '../../api';
import { CreateOAuthApplicationPanel } from '../create-oauth-application-panel';

afterEach(async () => {
  cleanup();
  await getI18n().changeLanguage('en-US');
});

/** The General tab must be valid before the later tabs open. */
function fillGeneralTab() {
  fireEvent.change(screen.getByLabelText(/^App name/), { target: { value: 'My app' } });
  fireEvent.change(screen.getByPlaceholderText('https://yourapp.com/callback'), {
    target: { value: 'https://example.com/callback' },
  });
}

async function renderOnScopesTab() {
  installBrowserShims();
  renderInTheme(<CreateOAuthApplicationPanel open onOpenChange={() => {}} />);
  await screen.findAllByRole('tab');
  fillGeneralTab();
  const scopesTab = document.querySelector('[role="tab"][id$="-trigger-scopes"]') as HTMLElement;
  fireEvent.mouseDown(scopesTab);
  fireEvent.click(scopesTab);
}

async function openScopesTab() {
  await renderOnScopesTab();
  await screen.findByText('kb:read');
  await waitFor(() => expect(scopeChecked('kb:read')).toBe('checked'));
}

function toggle(name: string) {
  const row = screen.getByText(name).closest('div')?.parentElement;
  fireEvent.click(row?.querySelector('[role="checkbox"]') as HTMLElement);
}

function scopeChecked(name: string): string | null {
  const row = screen.getByText(name).closest('div')?.parentElement;
  return row?.querySelector('[role="checkbox"]')?.getAttribute('data-state') ?? null;
}

describe('CreateOAuthApplicationPanel default scopes', () => {
  it('ticks the scopes MCP chat needs, and only the ones offered', async () => {
    await openScopesTab();
    for (const name of [
      'offline_access',
      'connector:read',
      'semantic:write',
      'conversation:chat',
      'agent:read',
    ]) {
      expect(scopeChecked(name), name).toBe('checked');
    }
    for (const name of ['kb:write', 'semantic:read', 'agent:write', 'org:admin']) {
      expect(scopeChecked(name), name).toBe('unchecked');
    }
    expect(screen.queryByText('agent:execute')).toBeNull();
  });

  it('keeps the user\'s edits when the scope list reloads', async () => {
    await openScopesTab();
    toggle('kb:read');
    toggle('kb:write');
    expect(scopeChecked('kb:read')).toBe('unchecked');
    expect(scopeChecked('kb:write')).toBe('checked');

    // A language change gives useTranslation a new `t`, which reloads the scopes.
    const loads = vi.mocked(Oauth2Api.getScopes).mock.calls.length;
    await act(async () => {
      await getI18n().changeLanguage('de-DE');
    });
    await waitFor(() => expect(vi.mocked(Oauth2Api.getScopes).mock.calls.length).toBeGreaterThan(loads));
    await screen.findByText('kb:read');

    expect(scopeChecked('kb:read')).toBe('unchecked');
    expect(scopeChecked('kb:write')).toBe('checked');
  });

  it('ticks the defaults when the list loads on Retry', async () => {
    vi.mocked(Oauth2Api.getScopes).mockRejectedValueOnce(new Error('offline'));
    await renderOnScopesTab();

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    await screen.findByText('kb:read');
    await waitFor(() => expect(scopeChecked('kb:read')).toBe('checked'));
    expect(scopeChecked('conversation:chat')).toBe('checked');
    expect(scopeChecked('kb:write')).toBe('unchecked');
  });

  it('ticks the defaults again when the form is closed and reopened', async () => {
    installBrowserShims();
    const panel = (open: boolean) => (
      <Theme>
        <CreateOAuthApplicationPanel open={open} onOpenChange={() => {}} />
      </Theme>
    );
    const { rerender } = render(panel(true));
    await screen.findAllByRole('tab');
    fillGeneralTab();
    let scopesTab = document.querySelector('[role="tab"][id$="-trigger-scopes"]') as HTMLElement;
    fireEvent.mouseDown(scopesTab);
    fireEvent.click(scopesTab);
    await waitFor(() => expect(scopeChecked('kb:read')).toBe('checked'));
    toggle('kb:read');
    expect(scopeChecked('kb:read')).toBe('unchecked');

    rerender(panel(false));
    rerender(panel(true));
    await screen.findAllByRole('tab');
    fillGeneralTab();
    scopesTab = document.querySelector('[role="tab"][id$="-trigger-scopes"]') as HTMLElement;
    fireEvent.mouseDown(scopesTab);
    fireEvent.click(scopesTab);
    await waitFor(() => expect(scopeChecked('kb:read')).toBe('checked'));
  });
});

describe('CreateOAuthApplicationPanel General tab validation (one pattern for create forms)', () => {
  async function renderGeneral() {
    installBrowserShims();
    renderInTheme(<CreateOAuthApplicationPanel open onOpenChange={() => {}} />);
    await screen.findAllByRole('tab');
  }
  const next = () => screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement;

  it('marks the required fields and keeps Next disabled until they are valid', async () => {
    await renderGeneral();
    expect(screen.getByLabelText(/^App name/).getAttribute('aria-required')).toBe('true');
    expect(next().disabled).toBe(true);
    fillGeneralTab();
    expect(next().disabled).toBe(false);
  });

  it('shows an invalid redirect URI under the field once it is left, and marks it', async () => {
    await renderGeneral();
    const uri = screen.getByPlaceholderText('https://yourapp.com/callback');
    fireEvent.change(uri, { target: { value: 'not a url' } });
    expect(screen.queryByText('Each redirect URI must be a valid http(s) URL.')).toBeNull();
    fireEvent.blur(uri);
    const alert = screen.getByText('Each redirect URI must be a valid http(s) URL.');
    expect(alert.getAttribute('role')).toBe('alert');
    expect(uri.getAttribute('aria-invalid')).toBe('true');
    expect(uri.getAttribute('aria-describedby')).toBe(alert.id);
    expect(next().disabled).toBe(true);
  });

  it('marks a bad optional URL on its own field and disables Next', async () => {
    await renderGeneral();
    fillGeneralTab();
    const home = screen.getByLabelText(/^Home page URL/);
    fireEvent.change(home, { target: { value: 'bad' } });
    fireEvent.blur(home);
    expect(home.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Optional URLs must be valid http(s) addresses when provided.')).toBeTruthy();
    expect(next().disabled).toBe(true);
  });

  it('does not open the Permissions tab while General is invalid, and shows what is missing', async () => {
    await renderGeneral();
    const scopesTab = document.querySelector('[role="tab"][id$="-trigger-scopes"]') as HTMLElement;
    fireEvent.mouseDown(scopesTab);
    fireEvent.click(scopesTab);
    expect(scopesTab.getAttribute('aria-selected')).toBe('false');
    expect(screen.getByText('App name is required.')).toBeTruthy();
  });
});
