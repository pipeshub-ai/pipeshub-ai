import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import { EnterpriseBadge, EnterpriseFeatureNotice } from '../enterprise-badge';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
let showEnterprise = true;
vi.mock('@/lib/hooks/use-enterprise-prompt', () => ({ useEnterprisePrompt: () => showEnterprise }));
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
  showEnterprise = true;
});

describe('for someone who is not an admin', () => {
  it('renders neither the badge nor the notice', () => {
    showEnterprise = false;
    const { container } = render(
      <Theme>
        <EnterpriseBadge />
        <EnterpriseFeatureNotice title="Organization approval rules" description="Decide which tools run." />
      </Theme>
    );
    expect(screen.queryByText(en.common.enterprise.label)).toBeNull();
    expect(screen.queryByTestId('enterprise-feature-notice')).toBeNull();
    expect(container.textContent).toBe('');
  });
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

  it('shows the action it is given, such as a contact link', () => {
    render(
      <Theme>
        <EnterpriseFeatureNotice
          title="SAML"
          description="Sign in through your identity provider."
          action={<a href="mailto:sales@pipeshub.com">Talk to sales</a>}
        />
      </Theme>
    );
    const link = screen.getByRole('link', { name: 'Talk to sales' });
    expect(link.getAttribute('href')).toBe('mailto:sales@pipeshub.com');
    // The given action replaces the default link, so the notice offers one next step.
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it('links to the Enterprise Edition page when it is given no action', () => {
    render(
      <Theme>
        <EnterpriseFeatureNotice title="SAML" description="Sign in through your identity provider." />
      </Theme>
    );
    const link = screen.getByRole('link', { name: en.workspace.enterprise.seeWhatsInEnterprise });
    // next/link adds the app's trailing slash only under its own config, which tests don't load.
    expect(link.getAttribute('href')?.replace(/\/$/, '')).toBe('/workspace/enterprise');
  });

  it('offers no next step when told to show none', () => {
    render(
      <Theme>
        <EnterpriseFeatureNotice title="SAML" description="Sign in through your identity provider." action={null} />
      </Theme>
    );
    expect(screen.queryByRole('link')).toBeNull();
  });
});
