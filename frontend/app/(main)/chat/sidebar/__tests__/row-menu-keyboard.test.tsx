/**
 * A chat row's menu shows on hover and also when keyboard focus is inside the row, so Rename,
 * Archive and Delete can be reached without a mouse; on a touch screen it is always there.
 */
import React, { useState } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { SidebarItem } from '../sidebar-item';
import { ChatItemMenu } from '../chat-item-menu';

function ChatRow({ onArchive }: { onArchive: () => void }) {
  const [hovered, setHovered] = useState(false);
  return (
    <>
      <SidebarItem
        label="Q3 plan"
        href="/chat/?conversationId=c1"
        onHoverChange={setHovered}
        rightSlot={
          <ChatItemMenu isParentHovered={hovered} onRename={() => {}} onArchive={onArchive} onDelete={() => {}} />
        }
      />
      <button type="button">Elsewhere</button>
    </>
  );
}

const menuButton = () => screen.queryByRole('button', { name: 'More options' });

afterEach(() => {
  cleanup();
});

describe('a chat row menu from the keyboard', () => {
  it('shows when focus enters the row, and opens and acts from the keyboard', async () => {
    installBrowserShims();
    const onArchive = vi.fn();
    renderInTheme(<ChatRow onArchive={onArchive} />);
    expect(menuButton()).toBeNull();

    act(() => screen.getByRole('link', { name: 'Q3 plan' }).focus());
    const trigger = menuButton();
    expect(trigger).toBeTruthy();

    act(() => trigger!.focus());
    fireEvent.keyDown(trigger!, { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));

    expect(onArchive).toHaveBeenCalledTimes(1);
  });

  it('hides again once focus leaves the row', () => {
    renderInTheme(<ChatRow onArchive={() => {}} />);

    act(() => screen.getByRole('link', { name: 'Q3 plan' }).focus());
    expect(menuButton()).toBeTruthy();
    act(() => screen.getByRole('button', { name: 'Elsewhere' }).focus());

    expect(menuButton()).toBeNull();
  });

  it('still shows on hover and hides when the pointer leaves', () => {
    renderInTheme(<ChatRow onArchive={() => {}} />);
    const row = screen.getByRole('link', { name: 'Q3 plan' }).parentElement!;

    fireEvent.mouseEnter(row);
    expect(menuButton()).toBeTruthy();
    // A pointer's target is the button itself.
    expect(menuButton()!.querySelector('[data-hit-area]')).toBeNull();
    fireEvent.mouseLeave(row);

    expect(menuButton()).toBeNull();
  });

  it('on a screen with no hover (touch) it is always there, so a tap can reach it', async () => {
    const matchMedia = window.matchMedia;
    window.matchMedia = vi.fn((query: string) => ({ matches: query === '(hover: none)', media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    try {
      const onArchive = vi.fn();
      renderInTheme(<ChatRow onArchive={onArchive} />);
      const trigger = menuButton();
      expect(trigger).toBeTruthy();
      // A 44 px target round the 22 px button: 11 px past each edge.
      expect((trigger!.querySelector('[data-hit-area]') as HTMLElement).style.inset).toBe('-11px');
      fireEvent.keyDown(trigger!, { key: 'Enter' });
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
      expect(onArchive).toHaveBeenCalledTimes(1);
    } finally {
      window.matchMedia = matchMedia;
    }
  });
});
