import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useToolsetOauthPopupFlow } from '../hooks/use-toolset-oauth-popup-flow';

function fakePopup() {
  return { closed: false, location: { href: 'about:blank' }, focus: vi.fn(), close: vi.fn() };
}

function setup() {
  const onOpenError = vi.fn();
  const { result, unmount } = renderHook(() =>
    useToolsetOauthPopupFlow({
      t: ((key: string) => key) as never,
      verifyAuthenticated: async () => false,
      onVerified: vi.fn(),
      onIncomplete: vi.fn(),
    })
  );
  const begin = (fetchUrl: () => Promise<string>) =>
    result.current.beginOAuth('oauth_user_toolset', fetchUrl, { onTimeout: vi.fn(), onOpenError });
  return { begin, onOpenError, result, unmount };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useToolsetOauthPopupFlow.beginOAuth', () => {
  it('opens the popup before the sign-in address arrives, then sends it there', async () => {
    const popup = fakePopup();
    const open = vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    let resolveUrl: (url: string) => void = () => undefined;
    const { begin, onOpenError, unmount } = setup();

    let started: Promise<void> = Promise.resolve();
    act(() => {
      started = begin(() => new Promise((resolve) => (resolveUrl = resolve)));
    });

    // Still inside the click: a window opened after an await is blocked by strict browsers.
    expect(open).toHaveBeenCalledWith('about:blank', 'oauth_user_toolset', expect.any(String));
    await act(async () => {
      resolveUrl('https://auth.example/start');
      await started;
    });
    expect(popup.location.href).toBe('https://auth.example/start');
    expect(onOpenError).not.toHaveBeenCalled();
    unmount();
  });

  it('reports a blocked popup without asking for the address', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    const fetchUrl = vi.fn(async () => 'https://auth.example/start');
    const { begin, onOpenError, result } = setup();

    await act(() => begin(fetchUrl));

    expect(fetchUrl).not.toHaveBeenCalled();
    expect((onOpenError.mock.calls[0]![0] as Error).message).toBe('agentBuilder.oauthPopupBlocked');
    expect(result.current.authenticating).toBe(false);
  });

  it('closes the popup when the address cannot be fetched', async () => {
    const popup = fakePopup();
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    const { begin, onOpenError, result } = setup();

    await act(() => begin(async () => Promise.reject(new Error('down'))));

    expect(popup.close).toHaveBeenCalled();
    expect((onOpenError.mock.calls[0]![0] as Error).message).toBe('down');
    expect(result.current.authenticating).toBe(false);
  });

  it('never sends the popup to an address that is not a web page', async () => {
    const popup = fakePopup();
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    const { begin, onOpenError } = setup();

    await act(() => begin(async () => 'javascript:alert(1)'));

    expect(popup.location.href).toBe('about:blank');
    expect(popup.close).toHaveBeenCalled();
    expect((onOpenError.mock.calls[0]![0] as Error).message).toBe('agentBuilder.oauthUrlFailed');
  });

  it('stops quietly when the person closes the popup before it loads', async () => {
    const popup = fakePopup();
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
    const { begin, onOpenError, result } = setup();

    await act(() =>
      begin(async () => {
        popup.closed = true;
        return 'https://auth.example/start';
      })
    );

    expect(popup.location.href).toBe('about:blank');
    expect(onOpenError).not.toHaveBeenCalled();
    expect(result.current.authenticating).toBe(false);
  });
});
