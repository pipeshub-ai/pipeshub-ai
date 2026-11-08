import { describe, it, expect, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useSplitWidth } from '../use-split-width';

const originalWidth = window.innerWidth;

function resizeTo(width: number) {
  act(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    window.dispatchEvent(new Event('resize'));
  });
}

afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
});

describe('useSplitWidth', () => {
  it('caps the chosen width on a narrow window and restores it when the window widens', () => {
    resizeTo(1920);
    const { result } = renderHook(() => useSplitWidth(() => 900));
    expect(result.current[0]).toBe(900);

    resizeTo(1000);
    expect(result.current[0]).toBe(600);

    resizeTo(1920);
    expect(result.current[0]).toBe(900);
  });

  it('shows a newly dragged width', () => {
    resizeTo(1920);
    const { result } = renderHook(() => useSplitWidth(() => 420));
    act(() => result.current[1](700));
    expect(result.current[0]).toBe(700);
  });
});
