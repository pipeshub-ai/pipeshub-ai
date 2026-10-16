import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, screen, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? _key }),
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { PlusMenuContent, hasNonDefaultSearchCapabilities, type PlusMenuCapabilities } from '../plus-menu-button';

function menu(overrides: Partial<PlusMenuCapabilities> = {}) {
  const props: PlusMenuCapabilities = {
    internalSearch: true,
    webSearch: true,
    onToggleInternalSearch: vi.fn(),
    onToggleWebSearch: vi.fn(),
    deepResearch: false,
    onToggleDeepResearch: vi.fn(),
    ...overrides,
  };
  render(
    <Theme>
      <PlusMenuContent onAttachFiles={vi.fn()} {...props} />
    </Theme>,
  );
  return props;
}

afterEach(cleanup);

describe('PlusMenuContent — deep research', () => {
  it('can be turned on with web search on', () => {
    const props = menu();
    const toggle = screen.getByRole('switch', { name: 'Deep research' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(toggle);
    expect(props.onToggleDeepResearch).toHaveBeenCalledWith(true);
  });

  it('is off and disabled without web search', () => {
    menu({ webSearch: false, deepResearch: true });
    const toggle = screen.getByRole('switch', { name: 'Deep research' });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(toggle.hasAttribute('disabled')).toBe(true);
  });

  it('is not offered where nothing handles it', () => {
    menu({ onToggleDeepResearch: undefined });
    expect(screen.queryByRole('switch', { name: 'Deep research' })).toBeNull();
  });
});

describe('hasNonDefaultSearchCapabilities', () => {
  it('counts deep research only while web search is on', () => {
    expect(hasNonDefaultSearchCapabilities(true, true)).toBe(false);
    expect(hasNonDefaultSearchCapabilities(true, true, true)).toBe(true);
    expect(hasNonDefaultSearchCapabilities(true, false, true)).toBe(true); // web search off is itself a change
    expect(hasNonDefaultSearchCapabilities(true, true, false)).toBe(false);
  });
});
