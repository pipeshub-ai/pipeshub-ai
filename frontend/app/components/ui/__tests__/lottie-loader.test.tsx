import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';

const playerProps = vi.fn();

vi.mock('next/dynamic', () => ({
  default: () => (props: Record<string, unknown>) => {
    playerProps(props);
    return <canvas data-testid="lottie" />;
  },
}));
vi.mock('@/config', () => ({ BrandMark: () => <span data-testid="fallback" /> }));

import '@/lib/__tests__/test-i18n';
import { LottieLoader } from '../lottie-loader';

const fetchMock = vi.fn();

beforeEach(() => {
  playerProps.mockReset();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('LottieLoader', () => {
  it('fetches each animation once and hands the player the bytes, not a URL', async () => {
    const bytes = new ArrayBuffer(8);
    fetchMock.mockResolvedValue({ ok: true, arrayBuffer: () => Promise.resolve(bytes) });

    const first = render(<LottieLoader variant="thinking" />);
    first.unmount();
    const { findByTestId } = render(<LottieLoader variant="thinking" />);
    await findByTestId('lottie');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/lottie-files/Thinking.lottie');
    const props = playerProps.mock.calls.at(-1)?.[0];
    expect(props.data).toBe(bytes);
    expect(props).not.toHaveProperty('src');
  });

  it('shows the logo and tries again next time when the file fails to load', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    const first = render(<LottieLoader variant="still" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(first.getByTestId('fallback')).toBeTruthy();
    first.unmount();

    fetchMock.mockResolvedValueOnce({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(4)) });
    const { findByTestId } = render(<LottieLoader variant="still" />);
    await findByTestId('lottie');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('LottieLoader accessibility and reduced motion', () => {
  const setReduced = (matches: boolean) => {
    window.matchMedia = vi.fn().mockReturnValue({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() });
  };

  it('a bare loader is art: no live region, hidden from assistive technology', async () => {
    fetchMock.mockResolvedValue({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
    setReduced(false);
    const { container, queryByRole, findByTestId } = render(<LottieLoader />);
    await findByTestId('lottie');
    expect(queryByRole('status')).toBeNull();
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true');
  });

  it.each([
    ['announce', { announce: true }, 'Loading…'],
    ['a label', { label: 'Loading chats…' }, 'Loading chats…'],
  ] as const)('asked with %s, it is a status with that name', async (_n, props, name) => {
    fetchMock.mockResolvedValue({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
    setReduced(false);
    const { findByRole } = render(<LottieLoader {...props} />);
    const status = await findByRole('status');
    expect(status.getAttribute('aria-label')).toBe(name);
  });

  it('with a visible label, the label and the status agree', async () => {
    fetchMock.mockResolvedValue({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
    setReduced(false);
    const { findByRole } = render(<LottieLoader showLabel />);
    const status = await findByRole('status');
    expect(status.getAttribute('aria-label')).toBe('Loading…');
    expect(status.textContent).toContain('Loading…');
  });

  it('listening and thinking marks and decorative loaders add no live region, even with a label', () => {
    fetchMock.mockResolvedValue({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
    setReduced(false);
    const a = render(<LottieLoader variant="thinking" label="Thinking" />);
    expect(a.queryByRole('status')).toBeNull();
    a.unmount();
    const b = render(<LottieLoader decorative announce />);
    expect(b.queryByRole('status')).toBeNull();
  });

  it('under reduced motion shows the still frame, not playing', async () => {
    const bytes = new ArrayBuffer(8);
    fetchMock.mockResolvedValue({ ok: true, arrayBuffer: () => Promise.resolve(bytes) });
    setReduced(true);
    const { findByTestId } = render(<LottieLoader variant="listening" />);
    await findByTestId('lottie');
    // The still file may already be cached by an earlier test; the player is never asked to play.
    await waitFor(() => expect(playerProps.mock.calls.at(-1)?.[0].autoplay).toBe(false));
    expect(fetchMock).not.toHaveBeenCalledWith('/lottie-files/Listening.lottie');
    const props = playerProps.mock.calls.at(-1)?.[0];
    expect(props.loop).toBe(false);
  });
});
