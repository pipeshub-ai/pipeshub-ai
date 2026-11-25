import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

vi.mock('../../../api', () => ({
  ConnectorsApi: {
    getOAuthAuthorizationUrl: vi.fn(async () => ({ authorizationUrl: 'https://idp.example/auth' })),
    getConnectorSchema: vi.fn(async () => ({ schema: {} })),
    getConnectorConfig: vi.fn(async () => ({})),
  },
}));

import { useConnectorOAuthPopup } from '../use-connector-oauth-popup';
import { useConnectorsStore } from '../../../store';
import { useToastStore } from '@/lib/store/toast-store';

const titles = () => useToastStore.getState().toasts.map((x) => x.title);

function stubPopup() {
  const popup = { closed: false, close: vi.fn(), focus: vi.fn(), location: { href: '' } };
  vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  return popup;
}

async function start() {
  const hook = renderHook(() => useConnectorOAuthPopup());
  await act(async () => {
    await hook.result.current.startOAuthPopup();
  });
  return hook;
}

beforeEach(() => {
  vi.useFakeTimers();
  useToastStore.setState({ toasts: [] });
  useConnectorsStore.setState({
    isPanelOpen: true,
    panelConnectorId: 'c1',
    panelConnector: { type: 'Slack' },
  } as never);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useConnectorOAuthPopup', () => {
  it('warns when the popup closes and the sign-in cannot be confirmed', async () => {
    const popup = stubPopup();
    await start();

    popup.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000 + 5 * 1500);
    });

    expect(titles()).toEqual([expect.stringContaining('could not confirm the sign-in')]);
    expect(useConnectorsStore.getState().authState).toBe('empty');
  });

  it('stays silent when the panel closes while the sign-in is being confirmed', async () => {
    const popup = stubPopup();
    const { unmount } = await start();

    popup.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000 + 1500);
    });
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 1500);
    });

    expect(titles()).toEqual([]);
  });

  it('stays silent when the panel was closed but the popup flow is still running', async () => {
    const popup = stubPopup();
    await start();

    act(() => {
      useConnectorsStore.setState({ isPanelOpen: false } as never);
    });
    popup.closed = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000 + 10 * 1500);
    });

    expect(titles()).toEqual([]);
  });

  it('closes the popup and warns after the five-minute wait', async () => {
    const popup = stubPopup();
    await start();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300 * 1000);
    });

    expect(popup.close).toHaveBeenCalled();
    expect(titles()).toEqual([expect.stringContaining('timed out after 5 minutes')]);
  });
});
