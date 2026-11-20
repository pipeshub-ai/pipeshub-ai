import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { SelectedCollections, type SelectedItem } from '../selected-collections';

afterEach(() => cleanup());

const items: SelectedItem[] = [
  { id: 'a', name: 'Handbook', kind: 'collection', nodeType: 'kb' },
  { id: 'b', name: 'Roadmap', kind: 'collection', nodeType: 'kb' },
  { id: 'c', name: 'Policies', kind: 'collection', nodeType: 'kb' },
];

function Harness({ onEmptied }: { onEmptied?: () => void }) {
  const [list, setList] = React.useState(items);
  return (
    <Theme>
      <SelectedCollections
        collections={list}
        removable
        onRemove={(id) => setList((prev) => prev.filter((item) => item.id !== id))}
        onEmptied={onEmptied}
      />
    </Theme>
  );
}

describe('SelectedCollections', () => {
  it('is one rail named Attachments of 28px chips, each with a named remove button', () => {
    render(<Harness />);
    const rail = screen.getByRole('group', { name: 'Attachments' });
    expect(rail.style.overflowX).toBe('auto');
    expect(screen.getByRole('button', { name: 'Remove Handbook' })).toBeTruthy();
    expect(screen.getByText('Roadmap').closest('[style*="height"]')).toBeTruthy();
  });

  it('shows no remove buttons on a sent message', () => {
    render(
      <Theme>
        <SelectedCollections collections={items} />
      </Theme>,
    );
    expect(screen.queryByRole('button', { name: /^Remove/ })).toBeNull();
  });

  it('hands focus to the remove button now in the same place after a keyboard remove', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] });
    try {
      render(<Harness />);
      // A keyboard press activates with a click of detail 0.
      fireEvent.click(screen.getByRole('button', { name: 'Remove Roadmap' }), { detail: 0 });
      await act(async () => {
        vi.runAllTimers();
      });
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove Policies' }));

      fireEvent.click(screen.getByRole('button', { name: 'Remove Policies' }), { detail: 0 });
      await act(async () => {
        vi.runAllTimers();
      });
      // The removed chip was last: focus goes to the new last chip.
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Remove Handbook' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('sends focus to the editor once no chip is left', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] });
    const onEmptied = vi.fn();
    try {
      render(<Harness onEmptied={onEmptied} />);
      for (const name of ['Handbook', 'Roadmap', 'Policies']) {
        fireEvent.click(screen.getByRole('button', { name: `Remove ${name}` }), { detail: 0 });
        await act(async () => {
          vi.runAllTimers();
        });
      }
      expect(onEmptied).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not re-measure on every parent render: typing beside a paste must not schedule work per keystroke', () => {
    const observe = vi.fn();
    vi.stubGlobal('ResizeObserver', class { observe = observe; unobserve() {} disconnect() {} });
    try {
      const view = (text: string) => (
        <Theme>
          <SelectedCollections collections={[]}>
            {[<span key="paste">{`Pasted text ${text.length}`}</span>]}
          </SelectedCollections>
        </Theme>
      );
      const { rerender } = render(view(''));
      // The composer re-renders the rail on every keystroke, with new children and a new collections array.
      for (const text of ['D', 'DF', 'DF-', 'DF-t']) rerender(view(text));
      expect(observe).toHaveBeenCalledTimes(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

