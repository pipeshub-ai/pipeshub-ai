import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { MaterialIcon } from '../MaterialIcon';

// WP-84 (WP-80 open item 1): the icon font draws a glyph from its ligature text, so without aria-hidden that text
// ("chevron_right") became part of every control's accessible name.

afterEach(() => {
  cleanup();
});

describe('MaterialIcon accessibility', () => {
  it('WP84-ICON-1: a decorative icon is hidden from assistive technology', () => {
    const { container } = render(<MaterialIcon name="chevron_right" />);
    const glyph = container.querySelector('span');
    expect(glyph?.getAttribute('aria-hidden')).toBe('true');
    expect(glyph?.getAttribute('role')).toBeNull();
  });

  it('WP84-ICON-2: a button with an icon and words is named by its words alone', () => {
    render(
      <button type="button">
        <MaterialIcon name="chevron_right" />
        Engine settings
      </button>,
    );
    expect(screen.getByRole('button', { name: 'Engine settings' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /chevron_right/ })).toBeNull();
  });

  it('WP84-ICON-3: an icon that means something carries a real label, not its ligature', () => {
    render(<MaterialIcon name="warning" label="Needs attention" />);
    const img = screen.getByRole('img', { name: 'Needs attention' });
    expect(img.getAttribute('aria-hidden')).toBeNull();
    expect(screen.queryByRole('img', { name: /warning/ })).toBeNull();
  });

  it('WP84-ICON-4: an icon-only button is named only by its own label', () => {
    render(
      <button type="button" aria-label="Close">
        <MaterialIcon name="close" />
      </button>,
    );
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
  });
});
