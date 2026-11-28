/**
 * The whole chat row opens the chat, not only the title: the gap before the trailing slot and the slot's own padding
 * pass the click on to the row's link. A click on the ⋯ only opens its menu.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SidebarItem } from '../sidebar-item';
import { ChatItemMenu } from '../chat-item-menu';

function renderRow() {
  const onOpen = vi.fn();
  const linkClicks = vi.fn();
  renderInTheme(
    <SidebarItem
      label="Q3 plan"
      href="/chat/?conversationId=c1"
      onClick={onOpen}
      rightSlot={<ChatItemMenu title="Q3 plan" isParentHovered onRename={() => {}} onArchive={() => {}} onDelete={() => {}} />}
    />,
  );
  const link = screen.getByRole('link', { name: 'Q3 plan' });
  // Stop jsdom from trying to navigate; count the clicks that reach the link instead.
  link.addEventListener('click', (e) => {
    linkClicks(e);
    e.preventDefault();
  });
  const trigger = screen.getByRole('button', { name: 'Actions for Q3 plan' });
  const slot = trigger.parentElement as HTMLElement;
  const row = link.parentElement as HTMLElement;
  return { onOpen, linkClicks, link, trigger, slot, row };
}

beforeEach(() => installBrowserShims());
afterEach(() => cleanup());

describe('a chat row with a trailing slot', () => {
  it('opens the chat from a click on the slot padding, right of the ⋯', () => {
    const { onOpen, linkClicks, slot } = renderRow();
    fireEvent.click(slot);
    expect(linkClicks).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('opens the chat from a click on the gap between the title and the slot', () => {
    const { onOpen, linkClicks, row } = renderRow();
    fireEvent.click(row);
    expect(linkClicks).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('passes the modifier keys on, so Ctrl-click still means a new tab', () => {
    const { linkClicks, slot } = renderRow();
    fireEvent.click(slot, { ctrlKey: true });
    expect((linkClicks.mock.calls[0][0] as MouseEvent).ctrlKey).toBe(true);
  });

  it('a click on the ⋯ opens nothing but its menu', () => {
    const { onOpen, linkClicks, trigger } = renderRow();
    fireEvent.click(trigger);
    expect(linkClicks).not.toHaveBeenCalled();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('a click on the title opens the chat once', () => {
    const { onOpen, linkClicks, link } = renderRow();
    fireEvent.click(link);
    expect(linkClicks).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
