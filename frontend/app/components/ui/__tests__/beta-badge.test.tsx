import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { BetaBadge } from '../beta-badge';

const BETA_LABEL = en.common.beta.label;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const parts = key.split('.');
      let cur: unknown = en;
      for (const part of parts) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) {
          return key;
        }
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

afterEach(() => {
  cleanup();
});

function renderBadge(props: React.ComponentProps<typeof BetaBadge> = {}) {
  return render(
    <Theme>
      <BetaBadge {...props} />
    </Theme>,
  );
}

describe('BetaBadge', () => {
  it('renders the "Beta" label as visible text (not color-only)', () => {
    renderBadge();
    expect(screen.getByText(BETA_LABEL)).toBeTruthy();
  });

  it('accepts a custom tooltip override without throwing', () => {
    expect(() => renderBadge({ tooltip: 'Custom beta note' })).not.toThrow();
    expect(screen.getByText(BETA_LABEL)).toBeTruthy();
  });
});
