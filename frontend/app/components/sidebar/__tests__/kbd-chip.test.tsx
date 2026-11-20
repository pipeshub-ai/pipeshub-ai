import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { KbdChip } from '../kbd-chip';

afterEach(() => cleanup());

describe('KbdChip', () => {
  it('is a Radix Kbd at size 1 that joins keys with " + "', () => {
    const { container } = render(
      <Theme>
        <KbdChip keys={['⌘', 'Shift', 'K']} />
      </Theme>,
    );
    const chip = container.querySelector('.rt-Kbd');
    expect(chip?.classList.contains('rt-r-size-1')).toBe(true);
    expect(chip?.textContent).toBe('⌘ + Shift + K');
  });

  it('types no font size or colour, only theme roles', () => {
    const { container } = render(
      <Theme>
        <KbdChip keys={['Ctrl', 'K']} />
      </Theme>,
    );
    const style = (container.querySelector('.rt-Kbd') as HTMLElement).style;
    expect(style.fontSize).toBe('var(--font-size-1)');
    expect(style.color).toBe('var(--text-primary)');
    expect(style.border).toContain('var(--border-subtle)');
  });
});
