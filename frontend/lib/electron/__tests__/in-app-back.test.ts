import { describe, it, expect, vi, afterEach } from 'vitest';

const isElectronMock = vi.fn(() => true);
vi.mock('../is-electron', () => ({ isElectron: () => isElectronMock() }));

import { canGoBackInApp } from '../in-app-back';

describe('canGoBackInApp', () => {
  afterEach(() => {
    isElectronMock.mockReturnValue(true);
    vi.restoreAllMocks();
  });

  it('offers Back in the desktop app when there is history', () => {
    vi.spyOn(window.history, 'length', 'get').mockReturnValue(3);
    expect(canGoBackInApp()).toBe(true);
  });

  it('hides Back on a page the desktop app opened first', () => {
    vi.spyOn(window.history, 'length', 'get').mockReturnValue(1);
    expect(canGoBackInApp()).toBe(false);
  });

  it('hides Back on the web, which opens these pages in a new tab', () => {
    isElectronMock.mockReturnValue(false);
    vi.spyOn(window.history, 'length', 'get').mockReturnValue(3);
    expect(canGoBackInApp()).toBe(false);
  });
});
