import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { EnterpriseBadge, EnterpriseFeatureNotice } from '../enterprise-badge';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

afterEach(() => {
  cleanup();
});

describe('EnterpriseBadge', () => {
  it('says "Available in EE" as text', () => {
    render(
      <Theme>
        <EnterpriseBadge />
      </Theme>
    );
    expect(screen.getByText(en.common.enterprise.label)).toBeTruthy();
  });
});

describe('EnterpriseFeatureNotice', () => {
  it('names the feature, says what it does, and where it is available', () => {
    render(
      <Theme>
        <EnterpriseFeatureNotice title="Organization approval rules" description="Decide which tools run." />
      </Theme>
    );
    const notice = screen.getByTestId('enterprise-feature-notice');
    expect(notice.textContent).toContain('Organization approval rules');
    expect(notice.textContent).toContain('Decide which tools run.');
    expect(notice.textContent).toContain(en.common.enterprise.label);
  });
});
