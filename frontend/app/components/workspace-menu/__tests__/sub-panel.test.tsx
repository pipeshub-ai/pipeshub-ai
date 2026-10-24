import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SubPanel } from '../sub-panel';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom has no layout: report the panel's unshifted top and height, as a browser would.
function layoutPanel(top: number, height: number) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const shift = -parseFloat(this.style.top || '0');
    return { top: top - shift, bottom: top - shift + height, height, left: 0, right: 250, width: 250, x: 0, y: top - shift, toJSON: () => ({}) } as DOMRect;
  });
}

const renderPanel = () =>
  render(
    <SubPanel isOpen title="Language" onBack={vi.fn()}>
      <span data-testid="row" />
    </SubPanel>,
  );

describe('SubPanel on desktop', () => {
  it('lifts a panel that would run past the bottom of the viewport', () => {
    window.innerHeight = 720;
    layoutPanel(386, 348);
    renderPanel();
    // 386 + 348 = 734, which must end 8px above the 720px viewport: lift by 22px.
    expect(screen.getByTestId('row').parentElement?.style.top).toBe('-22px');
  });

  it('stays put when it fits, and never lifts past the top gap', () => {
    window.innerHeight = 1080;
    layoutPanel(400, 348);
    renderPanel();
    expect(screen.getByTestId('row').parentElement?.style.top).toBe('0px');
    cleanup();

    window.innerHeight = 300;
    layoutPanel(100, 348);
    renderPanel();
    expect(screen.getByTestId('row').parentElement?.style.top).toBe('-92px');
  });
});
