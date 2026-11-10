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
