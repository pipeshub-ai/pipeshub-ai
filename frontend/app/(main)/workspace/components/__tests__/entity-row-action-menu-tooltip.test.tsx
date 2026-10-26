import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { EntityRowActionMenu } from '../entity-row-action-menu';

// jsdom lacks ResizeObserver, which the tooltip's positioning needs once it opens.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

afterEach(() => cleanup());

describe('EntityRowActionMenu tooltip on desktop', () => {
  it('leaves Escape for the panel an item opened', async () => {
    const onTokens = vi.fn();
    render(
      <Theme>
        <EntityRowActionMenu actions={[{ icon: 'key', label: 'Tokens', onClick: onTokens }]} />
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'More options' }));
    await act(async () => {
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Tokens' }));
    });
    expect(onTokens).toHaveBeenCalledOnce();
    // Browsers hand focus back to the trigger when the menu closes (jsdom doesn't).
    const trigger = screen.getByRole('button', { name: 'More options' });
    await act(async () => trigger.focus());
    expect(screen.queryByRole('tooltip')).toBeNull();

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    trigger.dispatchEvent(escape);
    expect(escape.defaultPrevented).toBe(false);
  });

  it('stays closed when an item is picked by keyboard with the pointer still on the trigger', async () => {
    render(
      <Theme>
        <EntityRowActionMenu actions={[{ icon: 'key', label: 'Tokens', onClick: () => {} }]} />
      </Theme>,
    );
    const trigger = screen.getByRole('button', { name: 'More options' });
    fireEvent.pointerEnter(trigger);
    fireEvent.pointerMove(trigger);
    fireEvent.click(trigger);
    const item = await screen.findByRole('menuitem', { name: 'Tokens' });
    await act(async () => {
      fireEvent.keyDown(item, { key: 'Enter' });
    });
    await act(async () => trigger.focus());
    expect(screen.queryByRole('tooltip')).toBeNull();

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    trigger.dispatchEvent(escape);
    expect(escape.defaultPrevented).toBe(false);
  });

  it('still opens on a fresh hover', async () => {
    render(
      <Theme>
        <EntityRowActionMenu actions={[{ icon: 'key', label: 'Tokens', onClick: () => {} }]} />
      </Theme>,
    );
    const trigger = screen.getByRole('button', { name: 'More options' });
    await act(async () => {
      fireEvent.pointerEnter(trigger, { pointerType: 'mouse' });
      fireEvent.pointerMove(trigger, { pointerType: 'mouse' });
    });
    expect(await screen.findByRole('tooltip', {}, { timeout: 3000 })).toBeTruthy();
  });
});
