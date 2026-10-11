import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useCopyText } from '../use-copy-text';

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, 'clipboard', { value, configurable: true });
}

beforeEach(() => vi.useFakeTimers());

afterEach(() => {
  vi.useRealTimers();
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
  else Reflect.deleteProperty(navigator, 'clipboard');
});

describe('useCopyText', () => {
  it('copies the text and shows copied for two seconds', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    const { result } = renderHook(() => useCopyText());

    let ok = false;
    await act(async () => {
      ok = await result.current.copy('{"a":1}');
    });

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('{"a":1}');
    expect(result.current.copied).toBe(true);
    act(() => vi.advanceTimersByTime(2000));
    expect(result.current.copied).toBe(false);
  });

  it('does nothing on a page without the clipboard API', async () => {
    setClipboard(undefined);
    const { result } = renderHook(() => useCopyText());

    let ok = true;
    await act(async () => {
      ok = await result.current.copy('x');
    });

    expect(ok).toBe(false);
    expect(result.current.copied).toBe(false);
  });

  it('does not claim a copy the browser refused', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('denied')) });
    const { result } = renderHook(() => useCopyText());

    let ok = true;
    await act(async () => {
      ok = await result.current.copy('x');
    });

    expect(ok).toBe(false);
    expect(result.current.copied).toBe(false);
  });
});
