import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));
// jsdom has no matchMedia.
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

// The store persists to localStorage; stand in for the selectors the page reads.
let user = { isAdmin: true as boolean | null, isProfileInitialized: true };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof user) => unknown) => selector(user),
  selectIsAdmin: (s: typeof user) => s.isAdmin,
  selectRefreshedIsAdmin: (s: typeof user) => (s.isProfileInitialized ? s.isAdmin : null),
  selectIsProfileInitialized: (s: typeof user) => s.isProfileInitialized,
}));

import { EnterprisePlaceholderPage } from '../enterprise-placeholder-page';
import { ENTERPRISE_GLIMPSES } from '../enterprise-glimpses';

afterEach(() => {
  cleanup();
  replace.mockClear();
  user = { isAdmin: true, isProfileInitialized: true };
});

function renderPage(glimpse: React.ComponentProps<typeof EnterprisePlaceholderPage>['glimpse'] = ENTERPRISE_GLIMPSES.usage) {
  return render(
    <Theme>
      <EnterprisePlaceholderPage
        title="Usage"
        heading="AI usage and cost are available in the Enterprise Edition"
        message="See what AI costs your organization."
        contactLabel="Talk to Sales"
        glimpse={glimpse}
      />
    </Theme>,
  );
}

describe('EnterprisePlaceholderPage', () => {
  it('says what is available and offers one way to get it: booking a call', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Usage' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /available in the Enterprise Edition/ })).toBeTruthy();
    expect(screen.getByText('See what AI costs your organization.')).toBeTruthy();

    // The icon's ligature text stays out of the link's name.
    const contact = screen.getByRole('link', { name: 'Talk to Sales' });
    expect(contact.getAttribute('href')).toBe('https://calendly.com/rishabh-pipeshub/30min');
    expect(contact.getAttribute('target')).toBe('_blank');
    expect(contact.className).toContain('rt-Button');
    expect(contact.querySelector('[aria-hidden="true"]')?.textContent).toBe('headset_mic');
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(replace).not.toHaveBeenCalled();
  });

  it('keeps the decorative premium icon away from screen readers', () => {
    renderPage();

    const icon = screen.getByText('workspace_premium');
    expect(icon.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it('sends a member who opens the URL to General settings, showing nothing', () => {
    user = { isAdmin: false, isProfileInitialized: true };
    const { container } = renderPage();

    expect(replace).toHaveBeenCalledWith('/workspace/general');
    expect(container.textContent).toBe('');
  });

  it('sends the user away when their role could not be loaded', () => {
    user = { isAdmin: null, isProfileInitialized: true };
    renderPage();

    expect(replace).toHaveBeenCalledWith('/workspace/general');
  });

  it('shows nothing, and sends no one away, until it is known who the user is', () => {
    user = { isAdmin: null, isProfileInitialized: false };
    const { container } = renderPage();

    expect(container.textContent).toBe('');
    expect(replace).not.toHaveBeenCalled();
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
