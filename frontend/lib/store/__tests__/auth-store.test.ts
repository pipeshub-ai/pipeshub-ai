import { describe, it, expect, vi, beforeEach } from 'vitest';

const isElectronMock = vi.fn();
const clearElectronLogoutServerStateMock = vi.fn();

vi.mock('@/lib/electron', () => ({
  isElectron: () => isElectronMock(),
}));

vi.mock('@/lib/electron/api-base-url-storage', () => ({
  clearElectronLogoutServerState: () => clearElectronLogoutServerStateMock(),
  persistElectronServerUrlOnLogin: vi.fn(),
}));

describe('requestElectronServerUrlChange', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Electron: clears the server ack and dispatches the navigation event', async () => {
    isElectronMock.mockReturnValue(true);
    const { requestElectronServerUrlChange, ELECTRON_SERVER_URL_NAVIGATION_EVENT } = await import(
      '../auth-store'
    );
    // Assert ordering from inside the listener — dispatch is synchronous, so
    // by the time it fires the ack must already be cleared. A plain
    // post-hoc call-count check would pass even if the event fired first.
    const listener = vi.fn(() => {
      expect(clearElectronLogoutServerStateMock).toHaveBeenCalledTimes(1);
    });
    window.addEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);

    requestElectronServerUrlChange();

    expect(clearElectronLogoutServerStateMock).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);
  });

  it('Web: is a no-op — no ack clear and no navigation event dispatched', async () => {
    isElectronMock.mockReturnValue(false);
    const { requestElectronServerUrlChange, ELECTRON_SERVER_URL_NAVIGATION_EVENT } = await import(
      '../auth-store'
    );
    const listener = vi.fn();
    window.addEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);

    requestElectronServerUrlChange();

    expect(clearElectronLogoutServerStateMock).not.toHaveBeenCalled();
    expect(listener).not.toHaveBeenCalled();

    window.removeEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);
  });
});
