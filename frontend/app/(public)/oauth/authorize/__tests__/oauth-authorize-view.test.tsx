import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

const NOT_GRANTED_HEADING = en.oauthConsent.notGrantedHeading;
const OFFLINE_ACCESS_WARNING = en.oauthConsent.notGrantedOfflineAccess;
const OWNER_HINT = en.oauthConsent.notGrantedOwnerHint;

const params = new URLSearchParams({
  client_id: 'cid',
  redirect_uri: 'https://claude.ai/api/mcp/auth_callback',
  scope: 'offline_access kb:read agent:read',
  state: 's1',
});

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => params,
}));

vi.mock('@/config', () => ({
  useAuthStore: (fn: (s: { isHydrated: boolean; isAuthenticated: boolean }) => unknown) =>
    fn({ isHydrated: true, isAuthenticated: true }),
}));

const get = vi.fn();
const post = vi.fn();
vi.mock('@/lib/api', () => ({
  apiClient: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

vi.mock('@/lib/api/api-error', () => ({
  extractApiErrorMessage: () => '',
  processError: () => ({ message: 'error' }),
}));

// One `t` for every render, as i18next gives; a new one each render re-runs the page's load effect.
const t = (key: string) => {
  const parts = key.split('.');
  let cur: unknown = en;
  for (const part of parts) {
    if (typeof cur !== 'object' || cur === null || !(part in cur)) {
      return key;
    }
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === 'string' ? cur : key;
};
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t }),
}));

vi.mock('@/app/components/ui/lottie-loader', () => ({
  LottieLoader: () => null,
}));

import { OAuthAuthorizeView } from '../oauth-authorize-view';

const h = React.createElement;

type Scope = { name: string; description: string; category: string };

function consentResponse(
  notGrantedScopes: Scope[],
  isDynamic = false,
  scopes: Scope[] = [{ name: 'kb:read', description: 'Read knowledge bases', category: 'Knowledge Base' }],
) {
  return {
    data: {
      requiresConsent: true,
      consentData: {
        app: { name: 'Claude', isDynamic },
        scopes,
        notGrantedScopes,
        user: { email: 'u@e.com' },
        redirectUri: 'https://claude.ai/api/mcp/auth_callback',
        state: 's1',
      },
    },
  };
}

describe('OAuthAuthorizeView not-granted scopes', () => {
  beforeEach(() => {
    get.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('lists requested scopes the app will not get, with the offline_access warning', async () => {
    get.mockResolvedValue(
      consentResponse([
        { name: 'offline_access', description: 'Refresh tokens', category: 'Identity' },
        { name: 'agent:read', description: 'Read agents', category: 'Agents' },
      ]),
    );

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    expect(await screen.findByText(NOT_GRANTED_HEADING)).toBeTruthy();
    expect(screen.getByText('agent:read')).toBeTruthy();
    expect(screen.getByText(OFFLINE_ACCESS_WARNING)).toBeTruthy();
    expect(screen.getByText(OWNER_HINT)).toBeTruthy();
  });

  it('does not tell users of a dynamically registered app to edit it, because nobody can', async () => {
    get.mockResolvedValue(
      consentResponse([{ name: 'agent:read', description: 'Read agents', category: 'Agents' }], true),
    );

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    expect(await screen.findByText(NOT_GRANTED_HEADING)).toBeTruthy();
    expect(screen.queryByText(OWNER_HINT)).toBeNull();
  });

  it('shows no not-granted section when every requested scope is granted', async () => {
    get.mockResolvedValue(consentResponse([]));

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    // The granted scope's category header, collapsed.
    expect(await screen.findByText('Knowledge Base')).toBeTruthy();
    expect(screen.queryByText(NOT_GRANTED_HEADING)).toBeNull();
    expect(screen.queryByText(OFFLINE_ACCESS_WARNING)).toBeNull();
  });
});

describe('OAuthAuthorizeView layout', () => {
  afterEach(() => {
    cleanup();
  });

  // body is overflow: hidden, so a minHeight wrapper grows past the screen and the buttons get clipped.
  it('makes the page wrapper a fixed-height scroll container', async () => {
    get.mockResolvedValue(consentResponse([]));

    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    let wrapper: HTMLElement | null = await screen.findByText('Claude');
    while (wrapper && wrapper.style.overflowY !== 'auto') {
      wrapper = wrapper.parentElement;
    }
    expect(wrapper).toBeTruthy();
    expect(wrapper.style.height).toBe('100dvh');
    expect(wrapper.style.minHeight).toBe('');
  });
});

describe('OAuthAuthorizeView scope choice', () => {
  const scopes: Scope[] = [
    { name: 'offline_access', description: 'Refresh tokens', category: 'Identity' },
    { name: 'kb:read', description: 'Read knowledge bases', category: 'Knowledge Base' },
    { name: 'kb:write', description: 'Write knowledge bases', category: 'Knowledge Base' },
    { name: 'team:read', description: 'Read teams', category: 'Teams' },
  ];
  // The checkbox in the label that holds this scope name.
  const box = (text: string) =>
    screen.getByText(text).closest('label')!.querySelector('[role="checkbox"]')!;
  const groupBox = (category: string) => screen.getByRole('checkbox', { name: category });
  const header = (category: string) => screen.getByRole('button', { name: new RegExp(`^${category}`) });
  const expand = async (...categories: string[]) => {
    await screen.findByRole('button', { name: /^Identity/ });
    for (const c of categories) fireEvent.click(header(c));
  };
  const state = (text: string) => box(text).getAttribute('aria-checked');
  const allow = () => screen.getByRole('button', { name: en.oauthConsent.allow }) as HTMLButtonElement;

  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    post.mockResolvedValue({ data: { redirectUrl: 'https://claude.ai/api/mcp/auth_callback?code=c' } });
  });

  afterEach(() => {
    cleanup();
  });

  it('groups scopes by category in the OAuth app form order, collapsed, with every requested scope ticked', async () => {
    get.mockResolvedValue(consentResponse([], false, scopes));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand();
    const groups = ['Identity', 'Knowledge Base', 'Teams'];
    const text = document.body.textContent ?? '';
    expect(groups.map((g) => text.indexOf(g))).toEqual(
      [...groups].map((g) => text.indexOf(g)).sort((a, b) => a - b),
    );
    // Collapsed: the headers show their checkbox and count, and the scopes stay hidden.
    expect(groups.map((g) => header(g).getAttribute('aria-expanded'))).toEqual(['false', 'false', 'false']);
    expect(screen.queryByText('kb:write')).toBeNull();
    expect(groups.map((g) => groupBox(g).getAttribute('aria-checked'))).toEqual(['true', 'true', 'true']);
    expect(screen.getByText('2/2')).toBeTruthy();

    groups.forEach((g) => fireEvent.click(header(g)));
    expect(['offline_access', 'kb:read', 'kb:write', 'team:read'].map(state)).toEqual([
      'true',
      'true',
      'true',
      'true',
    ]);
    expect(screen.queryAllByRole('checkbox')).toHaveLength(7);
  });

  it('unticks and ticks a whole category from its checkbox, without opening it', async () => {
    get.mockResolvedValue(consentResponse([], false, scopes));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand();
    fireEvent.click(groupBox('Knowledge Base'));
    expect(groupBox('Knowledge Base').getAttribute('aria-checked')).toBe('false');
    expect(header('Knowledge Base').getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('0/2')).toBeTruthy();

    fireEvent.click(header('Knowledge Base'));
    fireEvent.click(box('kb:read'));
    expect(groupBox('Knowledge Base').getAttribute('aria-checked')).toBe('mixed');
    fireEvent.click(groupBox('Knowledge Base'));
    expect(['kb:read', 'kb:write'].map(state)).toEqual(['true', 'true']);
  });

  it('keeps the choices made in a category after it is collapsed again', async () => {
    get.mockResolvedValue(consentResponse([], false, scopes));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand('Knowledge Base');
    fireEvent.click(box('kb:write'));
    fireEvent.click(header('Knowledge Base'));
    expect(screen.queryByText('kb:write')).toBeNull();
    expect(screen.getByText('1/2')).toBeTruthy();
  });

  it('unticks scopes one by one and posts what the person kept', async () => {
    get.mockResolvedValue(consentResponse([], false, scopes));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand('Identity', 'Knowledge Base');
    fireEvent.click(box('kb:write'));
    expect(state('kb:write')).toBe('false');
    fireEvent.click(box('offline_access'));
    fireEvent.click(allow());

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect((post.mock.calls[0][1] as { scope: string }).scope).toBe('kb:read team:read');
  });

  it('posts every requested scope when the person changes nothing', async () => {
    get.mockResolvedValue(consentResponse([], false, scopes));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand();
    fireEvent.click(allow());

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect((post.mock.calls[0][1] as { scope: string }).scope).toBe('offline_access kb:read kb:write team:read');
  });

  it('keeps openid ticked, so an OIDC sign-in always gets it', async () => {
    const withOpenid: Scope[] = [
      { name: 'openid', description: 'OpenID Connect authentication', category: 'Identity' },
      ...scopes,
    ];
    get.mockResolvedValue(consentResponse([], false, withOpenid));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand('Identity');
    expect(state('openid')).toBe('true');
    fireEvent.click(box('openid'));
    expect(state('openid')).toBe('true');
    fireEvent.click(groupBox('Identity'));
    expect(state('openid')).toBe('true');
    fireEvent.click(allow());

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect((post.mock.calls[0][1] as { scope: string }).scope).toBe('openid kb:read kb:write team:read');
  });

  it('disables Allow when every scope is unticked', async () => {
    get.mockResolvedValue(consentResponse([], false, scopes));
    render(h(Theme, null, h(OAuthAuthorizeView, null)));

    await expand('Identity', 'Knowledge Base', 'Teams');
    ['offline_access', 'kb:read', 'kb:write', 'team:read'].forEach((name) => fireEvent.click(box(name)));
    expect(allow().disabled).toBe(true);
  });
});
