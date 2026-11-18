import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));
// jsdom has no matchMedia.
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

import { EnterprisePlaceholderPage } from '../enterprise-placeholder-page';
import { ENTERPRISE_GLIMPSES } from '../enterprise-glimpses';

afterEach(() => cleanup());

function renderPage(glimpse: React.ComponentProps<typeof EnterprisePlaceholderPage>['glimpse']) {
  return render(
    <Theme>
      <EnterprisePlaceholderPage
        title="Usage"
        heading="AI usage and cost are available in the Enterprise Edition"
        message="See what AI costs your organization."
        contactLabel="Contact us"
        glimpse={glimpse}
      />
    </Theme>,
  );
}

describe('EnterprisePlaceholderPage', () => {
  it('says what is available and offers one way to get it: booking a call', () => {
    renderPage(ENTERPRISE_GLIMPSES.usage);

    expect(screen.getByRole('heading', { name: 'Usage' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /available in the Enterprise Edition/ })).toBeTruthy();
    expect(screen.getByText('See what AI costs your organization.')).toBeTruthy();

    // The icon's ligature text stays out of the link's name.
    const contact = screen.getByRole('link', { name: 'Contact us' });
    expect(contact.getAttribute('href')).toBe('https://calendly.com/rishabh-pipeshub/30min');
    expect(contact.getAttribute('target')).toBe('_blank');
    expect(contact.className).toContain('rt-Button');
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it.each(Object.entries(ENTERPRISE_GLIMPSES))(
    'shows a blurred look at the %s page that nobody can read out, focus or click',
    (_name, glimpse) => {
      const { container } = renderPage(glimpse);

      const preview = screen.getByTestId('enterprise-glimpse');
      expect(preview.getAttribute('aria-hidden')).toBe('true');
      expect(preview.hasAttribute('inert')).toBe(true);
      expect(preview.style.filter).toContain('blur');
      // Its switches, if any, are not reachable, and nothing in it reads as a real table.
      expect(screen.queryAllByRole('switch')).toHaveLength(0);
      expect(container.querySelectorAll('[role="row"], table')).toHaveLength(0);
    },
  );
});
