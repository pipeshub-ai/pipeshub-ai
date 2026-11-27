import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
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

let isAdmin: boolean | null = true;
vi.mock('@/lib/hooks/use-enterprise-prompt', () => ({ useEnterprisePrompt: () => isAdmin === true }));

import { WorkspaceEditionSection } from '../edition-section';

afterEach(() => {
  cleanup();
  isAdmin = true;
});

function renderSection() {
  return render(
    <Theme>
      <WorkspaceEditionSection />
    </Theme>,
  );
}

describe('WorkspaceEditionSection', () => {
  it('tells an admin this is the Community Edition and links to what Enterprise adds', () => {
    renderSection();

    expect(screen.getByText(en.workspace.general.edition.title)).toBeTruthy();
    expect(screen.getByText(en.workspace.general.edition.name)).toBeTruthy();
    const link = screen.getByRole('link', { name: en.workspace.enterprise.seeWhatsInEnterprise });
    // next/link adds the app's trailing slash only under its own config, which tests don't load.
    expect(link.getAttribute('href')?.replace(/\/$/, '')).toBe('/workspace/enterprise');
  });

  it.each([false, null])('shows nothing to someone who is not an admin (%s)', (role) => {
    isAdmin = role;
    const { container } = renderSection();

    expect(container.textContent).toBe('');
  });
});
