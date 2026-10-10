import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { MentionPopover, type MentionPopoverProps } from '../mention-popover';
import type { Mentionable } from '../use-mentionables';

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const items: Mentionable[] = [
  { ref: { type: 'assistant', id: 'self' }, label: 'Assistant', group: 'assistant' },
  { ref: { type: 'agent', id: 'a1' }, label: 'Assistant', group: 'agent' },
  { ref: { type: 'user', id: 'u1' }, label: 'Bob', group: 'people' },
  { ref: { type: 'team', id: 't1' }, label: 'Sales', group: 'teams' },
];

function renderPopover(props: Partial<MentionPopoverProps> = {}) {
  const handlers = { onSelect: vi.fn(), onActiveChange: vi.fn(), onDismiss: vi.fn(), onEscape: vi.fn() };
  render(
    <Theme>
      <MentionPopover
        open
        anchorRect={{ left: 10, top: 20, width: 1, height: 16 }}
        items={items}
        activeIndex={0}
        listboxId="lb"
        {...handlers}
        {...props}
      />
    </Theme>,
  );
  return handlers;
}

describe('MentionPopover', () => {
  it('names an own agent by its @handle; other agents read "agent"', () => {
    renderPopover({
      items: [
        { ref: { type: 'agent', id: 'a1' }, label: 'Helpdesk', group: 'agent' },
        { ref: { type: 'agent', id: 'a2' }, label: 'Offer drafter', group: 'agent', handle: 'offer-drafter' },
      ],
    });
    const [plain, own] = screen.getAllByRole('option');
    const text = (o: HTMLElement) => [...o.querySelectorAll('[data-testid="mention-label"],[data-testid="mention-suffix"]')].map((n) => n.textContent).join(' ');
    expect(text(plain)).toBe('Helpdesk agent');
    expect(text(own)).toBe('Offer drafter @offer-drafter');
    expect(own.querySelector('[data-testid="mention-agent-avatar"]')).toBeTruthy();
  });

  it('scrolls the keyboard-active option into view, and only the list scrolls so the footer tip stays visible', () => {
    const scrolled: string[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this.id);
    };
    try {
      renderPopover({ activeIndex: 3 });
      expect(scrolled).toContain('lb-opt-3');
      const listbox = screen.getByRole('listbox', { name: 'Mention suggestions' });
      expect(listbox.style.overflowY).toBe('auto');
      expect(listbox.contains(screen.getByTestId('composer-tip'))).toBe(false);
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it('lists grouped options in a labelled listbox with the active one exposed', () => {
    renderPopover({ activeIndex: 2 });
    const listbox = screen.getByRole('listbox', { name: 'Mention suggestions' });
    expect(listbox.getAttribute('aria-activedescendant')).toBe('lb-opt-2');
    const options = within(listbox).getAllByRole('option');
    const labelOf = (o: HTMLElement) => o.querySelector('[data-testid="mention-label"]')?.textContent ?? o.textContent;
    expect(options.map(labelOf)).toEqual(['Assistant', 'Assistant', 'Bob', 'Sales']);
    expect(options.map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true', 'false']);
    expect(screen.getByText('People in this chat')).toBeTruthy();
  });

  it('MN-17: a legacy agent named "Assistant" is a separate row that selects an agent ref', () => {
    const { onSelect } = renderPopover();
    fireEvent.click(screen.getAllByRole('option')[1]);
    expect(onSelect).toHaveBeenCalledWith(items[1]);
  });

  it('keeps editor focus on mouse down and tracks hover', () => {
    const { onActiveChange } = renderPopover();
    const option = screen.getAllByRole('option')[3];
    const notPrevented = fireEvent.mouseDown(option);
    expect(notPrevented).toBe(false);
    fireEvent.mouseMove(option);
    expect(onActiveChange).toHaveBeenCalledWith(3);
  });

  it('says so when nothing matches, and while searching', () => {
    const { rerender } = render(
      <Theme>
        <MentionPopover open anchorRect={{ left: 0, top: 0, width: 1, height: 1 }} items={[]} activeIndex={0} listboxId="lb" onSelect={() => {}} onActiveChange={() => {}} onDismiss={() => {}} onEscape={() => {}} />
      </Theme>,
    );
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByRole('status').textContent).toBe('No matches');
    rerender(
      <Theme>
        <MentionPopover open loading anchorRect={{ left: 0, top: 0, width: 1, height: 1 }} items={[]} activeIndex={0} listboxId="lb" onSelect={() => {}} onActiveChange={() => {}} onDismiss={() => {}} onEscape={() => {}} />
      </Theme>,
    );
    expect(screen.getByRole('status').textContent).toBe('Searching…');
  });

  it('renders nothing without an anchor', () => {
    renderPopover({ anchorRect: null });
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('drops the heading over a lone assistant or agent row, and keeps the people and team headings', () => {
    renderPopover();
    const listbox = screen.getByRole('listbox', { name: 'Mention suggestions' });
    expect(within(listbox).queryByText('Agent')).toBeNull();
    expect(within(listbox).getAllByText('Assistant')).toHaveLength(2);
    expect(within(listbox).getByText('People in this chat')).toBeTruthy();
    expect(within(listbox).getByText('Teams')).toBeTruthy();
    expect(within(listbox).getAllByRole('option')).toHaveLength(4);
  });
});
