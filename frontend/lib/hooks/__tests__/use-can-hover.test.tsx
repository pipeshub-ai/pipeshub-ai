import { describe, it, expect, afterEach, vi } from 'vitest';
import React, { act } from 'react';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { renderHook } from '@testing-library/react';
import { useCanHover } from '../use-can-hover';

afterEach(() => vi.unstubAllGlobals());

const stub = (matches: boolean) =>
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches,
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));

describe('useCanHover', () => {
  it('is false when hover is not supported', () => {
    stub(false);
    expect(renderHook(() => useCanHover()).result.current).toBe(false);
  });

  it('is true when hover and a fine pointer are available', () => {
    stub(true);
    expect(renderHook(() => useCanHover()).result.current).toBe(true);
  });

  it('assumes hover where matchMedia is missing', () => {
    vi.stubGlobal('matchMedia', undefined);
    expect(renderHook(() => useCanHover()).result.current).toBe(true);
  });

  it('hydrates prerendered HTML without a mismatch, then reports a touch device', async () => {
    function Probe() {
      return <span>{useCanHover() ? 'hover' : 'touch'}</span>;
    }
    vi.stubGlobal('matchMedia', undefined);
    const container = document.createElement('div');
    container.innerHTML = renderToString(<Probe />);
    expect(container.textContent).toBe('hover');

    stub(false);
    const onRecoverableError = vi.fn();
    await act(async () => {
      hydrateRoot(container, <Probe />, { onRecoverableError });
    });
    expect(onRecoverableError).not.toHaveBeenCalled();
    expect(container.textContent).toBe('touch');
  });
});
