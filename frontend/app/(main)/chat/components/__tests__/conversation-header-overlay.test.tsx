import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ConversationHeaderOverlay } from '../conversation-header-overlay';

const share = <span data-testid="share">Share</span>;

function box(testId: string): HTMLElement {
  return screen.getByTestId(testId).parentElement as HTMLElement;
}

describe('ConversationHeaderOverlay', () => {
  afterEach(() => {
    cleanup();
  });

  it('pins the share group top-right', () => {
    render(<ConversationHeaderOverlay conversationId="c1" shareGroup={share} />);

    const row = box('share');
    expect(row.style.position).toBe('absolute');
    expect(row.style.top).toBe('12px');
    expect(row.style.right).toBe('16px');
    expect(row.lastElementChild).toBe(screen.getByTestId('share'));
  });

  it('renders nothing when there is neither', () => {
    const { container } = render(
      <ConversationHeaderOverlay conversationId={null} shareGroup={null} />,
    );

    expect(container.innerHTML).toBe('');
  });
});
