import React, { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, renderHook, screen } from '@testing-library/react';

import { toastBottom, useToastCornerCard, useToastCornerStack } from '../toast-corner';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** A toast card of `height` px that a host puts in the corner while `shown`; it prints what it sits above (px+gaps). */
function Card({ name, height, shown = true }: { name: string; height: number; shown?: boolean }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const above = useToastCornerCard(shown ? el : null);
  return (
    <div ref={setEl} data-height={height} data-testid={name}>
      {`${above.px}+${above.gaps}`}
    </div>
  );
}

const above = (name: string) => screen.getByTestId(name).textContent;

describe('the toast corner', () => {
  it('stacks cards upward in the order they arrived, a --space-3 gap apart, with the app toast above them all', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return Number(this.dataset.height ?? 0);
    });
    const stack = renderHook(() => useToastCornerStack());
    const page = render(<Card key="first" name="first" height={88} />);
    expect(above('first')).toBe('0+0');
    expect(stack.result.current).toEqual({ px: 88, gaps: 1 });

    page.rerender(
      <>
        <Card key="first" name="first" height={88} />
        <Card key="second" name="second" height={120} />
      </>,
    );
    expect([above('first'), above('second')]).toEqual(['0+0', '88+1']);
    expect(stack.result.current).toEqual({ px: 208, gaps: 2 });

    // The first one goes: the other drops to the corner.
    page.rerender(<Card key="second" name="second" height={120} />);
    expect(above('second')).toBe('0+0');
    expect(stack.result.current).toEqual({ px: 120, gaps: 1 });
  });

  it('keeps a card that is on its way out where it was, without holding room for it', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return Number(this.dataset.height ?? 0);
    });
    const stack = renderHook(() => useToastCornerStack());
    const page = render(
      <>
        <Card key="first" name="first" height={88} />
        <Card key="second" name="second" height={120} />
      </>,
    );
    expect(above('second')).toBe('88+1');
    act(() => {
      page.rerender(
        <>
          <Card key="first" name="first" height={88} />
          <Card key="second" name="second" height={120} shown={false} />
        </>,
      );
    });
    expect(above('second')).toBe('88+1');
    expect(stack.result.current).toEqual({ px: 88, gaps: 1 });
  });

  it('writes the bottom edge as the corner, plus the stack below when there is one', () => {
    expect(toastBottom()).toBe('max(var(--space-4), env(safe-area-inset-bottom, 0px))');
    expect(toastBottom({ px: 208, gaps: 2 })).toBe(
      'calc(max(var(--space-4), env(safe-area-inset-bottom, 0px)) + 208px + 2 * var(--space-3))',
    );
  });
});
