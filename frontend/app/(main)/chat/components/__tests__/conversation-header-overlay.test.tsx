import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ConversationHeaderOverlay } from '../conversation-header-overlay';

const viewport = vi.hoisted(() => ({ isMobile: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => viewport.isMobile }));

const chip = <span data-testid="chip">Ada</span>;
const share = <span data-testid="share">Share</span>;

function box(testId: string): HTMLElement {
  return screen.getByTestId(testId).parentElement as HTMLElement;
}

describe('ConversationHeaderOverlay', () => {
  afterEach(() => {
    cleanup();
    viewport.isMobile = false;
  });

  it('leaves the agent chip out on a phone, where it would cover the header', () => {
    viewport.isMobile = true;
    render(<ConversationHeaderOverlay conversationId="c1" agentChip={chip} shareGroup={share} />);

    expect(screen.queryByTestId('chip')).toBeNull();
    expect(screen.getByTestId('share')).toBeTruthy();
  });

  it('keeps the agent chip left of the share group, both pinned top-right', () => {
    render(<ConversationHeaderOverlay conversationId="c1" agentChip={chip} shareGroup={share} />);

    expect(box('chip').style.position).toBe('absolute');
    expect(box('chip').style.top).toBe('10px');
    expect(box('chip').style.right).toBe('200px');
    expect(box('share').style.top).toBe('12px');
    expect(box('share').style.right).toBe('16px');
  });

  it('moves the chip to the corner when there is no share group', () => {
    render(<ConversationHeaderOverlay conversationId="c1" agentChip={chip} shareGroup={null} />);

    expect(box('chip').style.right).toBe('16px');
  });

  it('renders nothing when there is neither', () => {
    const { container } = render(
      <ConversationHeaderOverlay conversationId={null} agentChip={null} shareGroup={null} />,
    );

    expect(container.innerHTML).toBe('');
  });
});
