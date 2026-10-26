import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

vi.mock('../api', () => ({ McpServersApi: { getOAuthAuthorizationUrl: vi.fn() } }));

import {
  isWebUrl,
  mcpOAuthFailureMessage,
  useMcpOAuthPopup,
  type McpOAuthFailure,
} from '../hooks/use-mcp-oauth-popup';

interface FakePopup {
  closed: boolean;
  location: { href: string };
  focus: () => void;
  close: () => void;
}

function fakePopup(): FakePopup {
  const popup: FakePopup = {
    closed: false,
    location: { href: 'about:blank' },
    focus: vi.fn(),
    close: vi.fn(() => {
      popup.closed = true;
    }),
  };
  return popup;
}

function setup(options: {
  url?: string | Error;
  verify?: () => Promise<boolean>;
  popup?: FakePopup | null;
}) {
  const popup = options.popup === undefined ? fakePopup() : options.popup;
  const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  const onFailed = vi.fn<(failure: McpOAuthFailure) => void>();
  const onVerified = vi.fn();
  const hook = renderHook(() =>
    useMcpOAuthPopup({
      verifyAuthenticated: options.verify ?? (async () => false),
      onFailed,
      onVerified,
      getAuthorizationUrl: async () => {
        if (options.url instanceof Error) throw options.url;
        return { authorizationUrl: options.url ?? 'https://auth.example.com/authorize?x=1' };
      },
    }),
  );
  return { ...hook, popup, open, onFailed, onVerified };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('isWebUrl', () => {
  it.each([
    'https://auth.example.com/authorize?client_id=x',
    'http://keycloak.internal:8080/realms/acme/protocol/openid-connect/auth',
  ])('accepts %s', (url) => {
    expect(isWebUrl(url)).toBe(true);
  });

  it.each([
    'javascript:alert(document.domain)//',
    'JAVASCRIPT:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '/relative/authorize',
    '',
  ])('refuses %s', (url) => {
    expect(isWebUrl(url)).toBe(false);
  });
});

describe('useMcpOAuthPopup', () => {
  it('opens the popup before waiting on the server, then sends it to the sign-in address', async () => {
    const { result, open, popup, unmount } = setup({});

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });

    expect(open).toHaveBeenCalledOnce();
    expect(open.mock.calls[0][0]).toBe('about:blank');
    expect(popup!.location.href).toBe('https://auth.example.com/authorize?x=1');
    unmount();
  });

  it('never navigates to a sign-in address that is not a web address', async () => {
    const { result, popup, onFailed } = setup({ url: 'javascript:alert(document.domain)//' });

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });

    expect(popup!.location.href).toBe('about:blank');
    expect(popup!.close).toHaveBeenCalled();
    expect(onFailed).toHaveBeenCalledWith({ reason: 'unavailable' });
    expect(result.current.status).toBe('failed');
  });

  it('says the popup was blocked', async () => {
    const { result, onFailed } = setup({ popup: null });

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });

    expect(onFailed).toHaveBeenCalledWith({ reason: 'blocked' });
  });

  it('reports a closed popup as cancelled after one check, not after the retry loop', async () => {
    vi.useFakeTimers();
    const verify = vi.fn(async () => false);
    const { result, popup, onFailed } = setup({ verify });

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });
    popup!.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(verify).toHaveBeenCalledOnce();
    expect(onFailed).toHaveBeenCalledWith({ reason: 'cancelled' });
  });

  it('a closed popup that did connect counts as success', async () => {
    vi.useFakeTimers();
    const { result, popup, onVerified, onFailed } = setup({ verify: async () => true });

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });
    popup!.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(onVerified).toHaveBeenCalledOnce();
    expect(onFailed).not.toHaveBeenCalled();
  });

  it('passes on the error the callback page reported', async () => {
    const { result, popup, onFailed } = setup({});

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          origin: window.location.origin,
          data: { type: 'MCP_OAUTH_ERROR', error: 'access_denied: the user declined' },
        }),
      );
    });

    expect(onFailed).toHaveBeenCalledWith({ reason: 'provider_error', message: 'access_denied: the user declined' });
    expect(popup!.close).toHaveBeenCalled();
  });

  it('ignores a message from another window', async () => {
    const { result, onFailed } = setup({});

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          origin: window.location.origin,
          source: window,
          data: { type: 'MCP_OAUTH_ERROR', error: 'not ours' },
        }),
      );
    });

    expect(onFailed).not.toHaveBeenCalled();
  });

  it('a sign-in address the server could not produce closes the blank popup', async () => {
    const { result, popup, onFailed } = setup({ url: new Error('boom') });

    await act(async () => {
      await result.current.startOAuthPopup('inst-1');
    });

    expect(popup!.close).toHaveBeenCalled();
    expect(onFailed).toHaveBeenCalledWith(expect.objectContaining({ reason: 'unavailable' }));
  });
});

describe('mcpOAuthFailureMessage', () => {
  const t = ((key: string, values?: Record<string, string>) =>
    values ? `${key}:${JSON.stringify(values)}` : key) as unknown as Parameters<typeof mcpOAuthFailureMessage>[0];

  it('names each reason', () => {
    expect(mcpOAuthFailureMessage(t, { reason: 'blocked' })).toBe('agentBuilder.oauthPopupBlocked');
    expect(mcpOAuthFailureMessage(t, { reason: 'cancelled' })).toBe('agentBuilder.oauthSignInCancelled');
    expect(mcpOAuthFailureMessage(t, { reason: 'timeout' })).toBe('workspace.mcpServers.toasts.oauthTimedOut');
    expect(mcpOAuthFailureMessage(t, { reason: 'provider_error', message: 'denied' })).toContain('denied');
    expect(mcpOAuthFailureMessage(t, { reason: 'unavailable', message: 'Not configured' })).toBe('Not configured');
  });
});
