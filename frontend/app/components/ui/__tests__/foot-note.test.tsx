import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import { FootNote } from '../foot-note';

afterEach(() => {
  cleanup();
});

function renderNote(props: Partial<React.ComponentProps<typeof FootNote>> = {}) {
  return render(
    <Theme>
      <FootNote {...props}>{props.children ?? 'Only admins can change this.'}</FootNote>
    </Theme>,
  );
}

describe('FootNote', () => {
  it('keeps neutral text grey and colours caution and danger text', () => {
    renderNote({ children: 'neutral note' });
    expect(screen.getByText('neutral note').style.color).toBe('var(--gray-11)');
    cleanup();
    renderNote({ tone: 'caution', children: 'caution note' });
    expect(screen.getByText('caution note').style.color).toBe('var(--orange-11)');
    cleanup();
    renderNote({ tone: 'danger', children: 'danger note' });
    expect(screen.getByText('danger note').style.color).toBe('var(--red-11)');
  });

  it('uses the info mark by default, error for danger, and a passed glyph over both', () => {
    const { container } = renderNote();
    expect(container.textContent).toContain('info');
    cleanup();
    const danger = renderNote({ tone: 'danger' });
    expect(danger.container.textContent).toContain('error');
    cleanup();
    const custom = renderNote({ icon: 'group' });
    expect(custom.container.textContent).toContain('group');
  });

  it('renders the action outside the text paragraph', () => {
    renderNote({ action: <button type="button">Retry</button>, children: 'Could not load.' });
    const button = screen.getByRole('button', { name: 'Retry' });
    expect(screen.getByText('Could not load.').contains(button)).toBe(false);
  });
});
