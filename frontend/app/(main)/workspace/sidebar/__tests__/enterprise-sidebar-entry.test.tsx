import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));

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

import { EnterpriseSidebarEntry } from '../enterprise-sidebar-entry';
import { useEnterprisePromptsStore } from '@/app/(main)/workspace/enterprise/store';

const CARD = en.workspace.enterprise.sidebarCard;
// next/link adds the app's trailing slash only under its own config, which tests don't load.
const route = (a: Element) => a.getAttribute('href')?.replace(/\/$/, '');

beforeEach(() => {
  useEnterprisePromptsStore.setState({ sidebarCardDismissed: false });
});

afterEach(cleanup);

function renderEntry(isActive = false) {
  return render(
    <Theme>
      <EnterpriseSidebarEntry isActive={isActive} />
    </Theme>,
  );
}

describe('EnterpriseSidebarEntry', () => {
  it('shows a card that leads to the Enterprise Edition page', () => {
    renderEntry();

    expect(screen.getByTestId('enterprise-sidebar-card')).toBeTruthy();
    expect(screen.getByText(CARD.body)).toBeTruthy();
    expect(route(screen.getByRole('link', { name: CARD.action }))).toBe('/workspace/enterprise');
  });

  it('turns into a one-line link once closed, and stays closed', () => {
    renderEntry();

    fireEvent.click(screen.getByRole('button', { name: CARD.dismiss }));

    expect(screen.queryByTestId('enterprise-sidebar-card')).toBeNull();
    const link = screen.getByRole('link', { name: new RegExp(`${en.workspace.sidebar.nav.enterprise}$`) });
    expect(route(link)).toBe('/workspace/enterprise');
    expect(useEnterprisePromptsStore.getState().sidebarCardDismissed).toBe(true);

    cleanup();
    renderEntry();
    expect(screen.queryByTestId('enterprise-sidebar-card')).toBeNull();
  });

  it('keeps only the closed state when it saves', () => {
    useEnterprisePromptsStore.getState().dismissSidebarCard();

    const saved = useEnterprisePromptsStore.persist.getOptions().partialize?.(useEnterprisePromptsStore.getState());
    expect(saved).toEqual({ sidebarCardDismissed: true });
  });
});
