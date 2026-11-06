import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { CHAT_TOP_FADE_HEIGHT_PX, CHAT_TOP_FADE_INSET, ChatTopBar } from '../chat-top-bar';

afterEach(cleanup);

describe('ChatTopBar — the fade under it', () => {
  it('lays a static 96 px band over the transcript, into the canvas top colour, that never takes a click', () => {
    render(
      <Theme>
        <ChatTopBar testId="bar" leading={<span>Title</span>} actions={<button type="button">Share</button>} />
      </Theme>,
    );
    const bar = screen.getByTestId('bar');
    const band = bar.querySelector(':scope > [aria-hidden="true"]') as HTMLElement;
    expect(CHAT_TOP_FADE_HEIGHT_PX).toBe(96);
    expect(band.style.height).toBe('96px');
    expect(band.style.top).toBe('0px');
    expect(band.style.pointerEvents).toBe('none');
    expect(band.style.transition).toBe('');
    // Behind the bar's own controls.
    expect(band.style.zIndex).toBe('-1');
    // A plain fade into the canvas's top colour (fading into the foot's drew a band under the title): no backdrop blur to redraw on every frame the transcript moves under it.
    expect(band.style.background).toContain('var(--surface-subtle)');
    expect(band.childElementCount).toBe(0);
    expect(band.style.getPropertyValue('backdrop-filter')).toBe('');
    // The bar passes the pointer through, except to its two clusters.
    expect(bar.style.pointerEvents).toBe('none');
    expect((screen.getByRole('button', { name: 'Share' }).parentElement as HTMLElement).style.pointerEvents).toBe('auto');
  });

  it('starts a list that runs under it 20 px above the fade\'s end, less the column\'s own top padding', () => {
    expect(CHAT_TOP_FADE_INSET).toBe('calc(96px - var(--space-4) - var(--space-1) - var(--space-4))');
  });
});
